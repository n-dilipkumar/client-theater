"""The facade the HTTP layer calls. Owns six collections and nothing else.

The rules live in :mod:`dsr.crm_integration.reconcile`, the values live in
:mod:`dsr.crm_integration.gap_vocabulary`, and this module is the seam between them
and :class:`~dsr.store.RecordStore`. It writes rows and reads rows. It decides
nothing that a function in ``reconcile`` could have decided.

The six collections
-------------------

``crm_gap_event``    one gap or overflow header the subscriber reported
``crm_gap_dirty``    one record marked dirty as of a gap's commit timestamp
``crm_gap_cursor``   one resumable position: a Replay ID or a delta link
``crm_gap_run``      one reconciliation: a per-record repair, or a whole entity
``crm_gap_log``      one numbered line of a run's sync log
``crm_gap_replica``  one reconciled record row, with a tombstone for a deletion

All six are namespaced to this workflow. This module writes into no collection
another feature owns, which is what ``test_wf043.py``'s
``test_no_other_feature_writes_into_a_collection_this_one_owns`` enforces for every
feature in the host. The reason is recorded as an inference in
:mod:`dsr.crm_integration.gap_inferences`.

The audit contract
------------------

Every writing method takes ``source`` as a keyword-only argument with no default,
so a caller cannot write a row without naming the route that served it. That is the
one thing this product's audit guarantee rests on, and making it impossible to omit
is cheaper than reviewing for it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.crm_integration import gap_inferences, gap_vocabulary as vocab, reconcile as rules
from dsr.crm_integration.gap_errors import (
    DeletedSourceVendorMismatch,
    MismatchedCursorVendor,
    NoDirtyRecord,
    UnknownCursorKind,
    UnknownDeletedSource,
    UnknownEntity,
    UnknownEvent,
    UnknownRun,
)
from dsr.crm_integration.gap_sources import SOURCE_ROWS, CrmReader, default_reader
from dsr.store import RecordStore

#: The collections this workflow owns, all namespaced to it.
GAP_EVENTS = "crm_gap_event"
DIRTY = "crm_gap_dirty"
CURSORS = "crm_gap_cursor"
RUNS = "crm_gap_run"
LOG = "crm_gap_log"
REPLICA = "crm_gap_replica"

#: The room's own copy of the vendor's entity tables, which a reconciliation
#: reads. Separate from the replica on purpose: the research names the vendor
#: tables and the replica as two data sources, and reading the replica to
#: repair the replica would make the repair agree with itself.
SOURCE = SOURCE_ROWS

OWNED_COLLECTIONS: tuple[str, ...] = (
    GAP_EVENTS,
    DIRTY,
    CURSORS,
    RUNS,
    LOG,
    REPLICA,
    SOURCE,
)

#: The state a room's subscription is in. The overflow procedure's step 1 is an
#: unsubscribe and its step 6 is a resubscribe, so the room has to remember which
#: one it is in.
SUBSCRIPTION_STATES: tuple[str, ...] = ("subscribed", "unsubscribed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReconcileEngine:
    """The change-stream repair workflow, over one room's records.

    ``reader`` is the read seam. ``clock`` is injected rather than mocked, because
    the seven-day rule is about how long *the room* has held a token and the room's
    clock is the one that has to be right.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        reader: CrmReader | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.reader = reader if reader is not None else default_reader(store)
        self.clock = clock if clock is not None else _now

    # ------------------------------------------------------------------ #
    # Served data
    # ------------------------------------------------------------------ #

    def vocabulary(self) -> dict[str, Any]:
        """Everything this workflow will accept, as one payload."""
        return vocab.describe()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call, with the sourced sentences it sits beside."""
        return gap_inferences.describe()

    # ------------------------------------------------------------------ #
    # Step 1: the subscriber reports a gap or an overflow
    # ------------------------------------------------------------------ #

    def report_gap_event(
        self, room_id: str, body: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Record a gap or overflow header, and do the first half of its repair.

        A gap marks each record it names dirty, "as of the date of the gap event",
        which is the whole of step 2 of the user flow.

        An overflow cannot be repaired per record, because it "include[s] header
        fields but no record data and no record ID". So the two first steps of the
        overflow procedure happen here: the room unsubscribes, and it stores the
        Replay ID "as the starting point for the data reconciliation".
        """
        vendor = str(body.get("vendor") or "salesforce") if isinstance(body, Mapping) else ""
        event = rules.normalise_gap_event(body, vendor=vendor)
        stamp = self.clock().astimezone(timezone.utc).isoformat()

        record = self.store.create(
            GAP_EVENTS,
            {**event, "reported_at": stamp, "subscription_state": "subscribed"},
            room_id=room_id,
            actor=actor,
            source=source,
        )

        marked: list[dict[str, Any]] = []
        if event["kind"] == "gap":
            for record_id in event["record_ids"]:
                marked.append(
                    self._mark_dirty(
                        room_id,
                        entity=event["entity"],
                        record_id=record_id,
                        gap_event_id=record["id"],
                        gap_commit_timestamp=event["commit_timestamp"],
                        change_type=event["change_type"],
                        actor=actor,
                        source=source,
                    )
                )
            # Re-read the row rather than patching the local dict: the view is
            # built from the stored row, and a stale one would report the state the
            # event had a moment ago rather than the one it is in now.
            stored = self.store.update(
                record["id"],
                {"subscription_state": "subscribed"},
                actor=actor,
                source=source,
            )
            return {
                "event": self._view(stored),
                "kind": "gap",
                "dirty_records": marked,
                "replay_id_stored": False,
            }

        cursor = self._store_cursor(
            room_id,
            kind=event["cursor_kind"] or "replay_id",
            vendor=event["vendor"],
            scope=vocab.CURSOR_SCOPE.get(event["cursor_kind"] or "replay_id", "entity"),
            scope_value=event["entity"],
            position=event["replay_id"],
            actor=actor,
            source=source,
        )
        stored = self.store.update(
            record["id"],
            {"subscription_state": "unsubscribed", "cursor_id": cursor["id"]},
            actor=actor,
            source=source,
        )
        return {
            "event": self._view(stored),
            "kind": "overflow",
            "dirty_records": [],
            "replay_id_stored": True,
            "cursor": self._cursor_view(cursor),
        }

    def gap_events(self, room_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """The gap and overflow events this room has reported, newest first."""
        rows = self.store.list(GAP_EVENTS, room_id=room_id, limit=limit)
        return [
            self._view(row) for row in sorted(rows, key=lambda row: row["created_at"], reverse=True)
        ]

    def gap_event(self, room_id: str, event_id: str) -> dict[str, Any]:
        """One event, or a refusal naming the room it was looked for in."""
        record = self.store.get(event_id)
        if record is None or record["collection"] != GAP_EVENTS or record["room_id"] != room_id:
            raise UnknownEvent(
                f"no gap or overflow event {event_id!r} in room {room_id!r}. "
                "The id is the one the report route returned."
            )
        return self._view(record)

    # ------------------------------------------------------------------ #
    # Step 2: a change event arrives while records may be dirty
    # ------------------------------------------------------------------ #

    def change_event(
        self, room_id: str, body: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Apply one incremental change, or drop it and say which rule dropped it.

        "If you receive change events for new changes for the same record before the
        data has been reconciled, don't process them." A drop is the research's
        prescribed behaviour rather than a caller's mistake, so this answers 200
        with ``applied: False`` and a reason rather than refusing the request.
        """
        entity = str(body.get("entity") or "").strip()
        record_id = str(body.get("record_id") or "").strip()
        if not entity or not record_id:
            return {
                "applied": False,
                "reason": "no_dirty_marker",
                "detail": (
                    "a change event must name the entity and the record it is about; "
                    "neither was sent, so nothing was applied"
                ),
                "entity": entity,
                "record_id": record_id,
            }

        marker = self._dirty_marker(room_id, entity, record_id)
        commit_timestamp = rules.parse_timestamp(
            body.get("commit_timestamp") or body.get("commitTimestamp"),
            field="change commit_timestamp",
        )
        record = self.reader.record(room_id, entity, record_id)
        applied, reason = rules.decide_change(
            # The view, not the stored row: the rule reads the marker's gap date, and
            # the date lives inside the row's payload rather than on the row.
            dirty_marker=self._dirty_view(marker) if marker is not None else None,
            commit_timestamp=commit_timestamp,
            last_modified=body.get(rules.vocab.LAST_MODIFIED_FIELD)
            or body.get("last_modified_date")
            or body.get("lastModifiedDate"),
            record=record,
        )
        stamp = self.clock().astimezone(timezone.utc).isoformat()
        if not applied:
            # Nothing is written to the replica on a drop, and the dirty marker is
            # left exactly as it was. Both are the point: the next re-read has to
            # see this change too.
            return {
                "applied": False,
                "reason": reason,
                "entity": entity,
                "record_id": record_id,
                "dirty": bool(marker),
                "commit_timestamp": commit_timestamp,
                "recorded_at": stamp,
                "detail": self._drop_detail(reason),
            }

        payload = dict(body.get("payload") or body.get("record") or {})
        if record:
            payload = {**record, **payload}
        row = self._write_replica(
            room_id,
            entity=entity,
            record_id=record_id,
            payload=payload,
            deleted=False,
            run_id=None,
            event_id=None,
            actor=actor,
            source=source,
        )
        return {
            "applied": True,
            "reason": reason,
            "entity": entity,
            "record_id": record_id,
            "dirty": bool(marker),
            "commit_timestamp": commit_timestamp,
            "replica_id": row["id"],
            "recorded_at": stamp,
            "detail": (
                "the record carries no dirty marker, so the change is applied as it arrived"
                if reason == rules.DROP_REASONS[0]
                else "the re-read already carried this change, so applying it would "
                "write the same row twice"
            ),
        }

    def dirty_records(
        self, room_id: str, *, state: str = "dirty", limit: int = 200
    ) -> list[dict[str, Any]]:
        """The data-health view: every record this room still owes a repair for.

        Sorted by the gap's commit timestamp rather than by insertion, because the
        question an operator asks is which gap happened first.
        """
        if state not in vocab.DIRTY_STATES:
            raise UnknownEntity(
                f"state must be one of {', '.join(vocab.DIRTY_STATES)}; got {state!r}"
            )
        rows = [
            row
            for row in self.store.list(DIRTY, room_id=room_id, limit=limit)
            if (row.get("data") or {}).get("state") == state
        ]
        return [
            self._dirty_view(row)
            for row in sorted(
                rows, key=lambda row: (row["data"]["gap_commit_timestamp"], row["id"])
            )
        ]

    # ------------------------------------------------------------------ #
    # Steps 3 and 5: the re-read, the overwrite, the delete diff, the flag clear
    # ------------------------------------------------------------------ #

    def reconcile(
        self, room_id: str, body: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Repair one dirty record, or one whole entity after an overflow.

        Two shapes, because the research's two shapes are different. A gap names a
        record and is repaired by one read: overwrite the replica row, or write a
        tombstone when the vendor no longer holds the record, then "clear the dirty
        flag on that record".

        An overflow names no record, so it is repaired by reading the whole entity
        and deriving the deleted set by one of the research's two methods.
        """
        event_id = str(body.get("event_id") or "").strip()
        event = self._require_event(room_id, event_id)
        scope_record = str(body.get("record_id") or "").strip()
        scope_entity = str(body.get("entity") or "").strip() or event["entity"]
        deleted_source = str(body.get("deleted_source") or "").strip() or (
            "dataverse_delta" if event["vendor"] == "dataverse" else "difference"
        )

        run = self.store.create(
            RUNS,
            {
                "state": "open",
                "kind": event["kind"],
                "vendor": event["vendor"],
                "entity": scope_entity,
                "event_id": event_id,
                "record_id": scope_record,
                "deleted_source": deleted_source,
                "opened_at": self.clock().astimezone(timezone.utc).isoformat(),
                "seq": 0,
                "counts": {},
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self._log(
            room_id,
            run["id"],
            "run_opened",
            detail=f"{event['kind']} on {scope_entity}",
            actor=actor,
            source=source,
        )
        if event["kind"] == "overflow":
            for line in ("unsubscribed", "replay_id_stored"):
                self._log(
                    room_id,
                    run["id"],
                    line,
                    detail=(
                        "the overflow procedure's first two steps"
                        if line == "unsubscribed"
                        else "the Replay ID is the starting point for the reconciliation"
                    ),
                    actor=actor,
                    source=source,
                )

        if event["kind"] == "gap":
            return self._reconcile_record(
                room_id, run, event, scope_entity, scope_record, actor=actor, source=source
            )
        return self._reconcile_entity(
            room_id,
            run,
            event,
            scope_entity,
            deleted_source,
            actor=actor,
            source=source,
        )

    def _reconcile_record(
        self,
        room_id: str,
        run: Mapping[str, Any],
        event: Mapping[str, Any],
        entity: str,
        record_id: str,
        *,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        if not record_id:
            record_id = event["record_ids"][0] if event["record_ids"] else ""
        if not record_id:
            raise NoDirtyRecord(
                f"the event {event['id']!r} is a {event['change_type']} with no record "
                "id, so there is nothing to re-read. An overflow is repaired against "
                "the entity, not a record."
            )
        marker = self._dirty_marker(room_id, entity, record_id)
        if marker is None:
            raise NoDirtyRecord(
                f"record {record_id!r} of {entity!r} is not dirty in room {room_id!r}, "
                "so there is no gap to reconcile. Repairing a clean record would "
                "apply a read and a delete diff the room has no reason to run."
            )

        record = self.reader.record(room_id, entity, record_id)
        self._log(
            room_id,
            run["id"],
            "record_read",
            detail=(
                f"the vendor returned {entity}/{record_id}"
                if record
                else f"the vendor no longer holds {entity}/{record_id}"
            ),
            actor=actor,
            source=source,
        )
        row = self._write_replica(
            room_id,
            entity=entity,
            record_id=record_id,
            payload=record or {},
            deleted=record is None,
            run_id=run["id"],
            event_id=event["id"],
            actor=actor,
            source=source,
        )
        self._log(
            room_id,
            run["id"],
            "replica_overwritten" if record else "replica_deleted",
            detail=f"{entity}/{record_id}",
            actor=actor,
            source=source,
        )
        self._clear_dirty(
            room_id,
            marker["id"],
            run_id=run["id"],
            event_id=event["id"],
            actor=actor,
            source=source,
        )
        self._log(
            room_id,
            run["id"],
            "dirty_flag_cleared",
            detail=f"{entity}/{record_id}",
            actor=actor,
            source=source,
        )
        self._close_run(
            room_id,
            run["id"],
            state="complete",
            counts={"written": 1 if record else 0, "deleted": 0 if record else 1},
            actor=actor,
            source=source,
        )
        return {
            "run": self.run(room_id, run["id"]),
            "replica": self._replica_view(row),
            "scope": "record",
            "dirty_cleared": True,
        }

    def _reconcile_entity(
        self,
        room_id: str,
        run: Mapping[str, Any],
        event: Mapping[str, Any],
        entity: str,
        deleted_source: str,
        *,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        if deleted_source not in vocab.DELETED_SOURCES:
            raise UnknownDeletedSource(
                f"deleted_source must be one of {', '.join(vocab.DELETED_SOURCES)}; "
                f"got {deleted_source!r}. The research offers exactly two methods for "
                "Salesforce and one shape for Dataverse, and a third would be a "
                "guess at what the room deletes from the replica."
            )
        wanted_vendor = vocab.DELETED_SOURCE_VENDOR[deleted_source]
        if wanted_vendor and wanted_vendor != event["vendor"]:
            raise DeletedSourceVendorMismatch(
                f"{deleted_source!r} is a {wanted_vendor} mechanism and this run "
                f"reads {event['vendor']}. Reading one vendor's deleted set from the "
                "other vendor's API returns an empty answer, and an empty answer read "
                "as 'nothing was deleted' would delete nothing when the truth is that "
                "the room never asked the right question."
            )

        cursor = self._cursor_for(room_id, event["vendor"], entity)
        verdict = rules.cursor_verdict(
            cursor.get("data") or cursor if cursor else None, self.clock()
        )
        if verdict["state"] == rules.CURSOR_EXPIRED:
            self._log(
                room_id,
                run["id"],
                "delta_link_expired",
                detail=verdict.get("reason", ""),
                actor=actor,
                source=source,
            )
            self._close_run(
                room_id,
                run["id"],
                state="expired_cursor",
                counts={"written": 0, "deleted": 0},
                actor=actor,
                source=source,
            )
            return {
                "run": self.run(room_id, run["id"]),
                "scope": "entity",
                "written": [],
                "deleted": [],
                "fallback": verdict.get("fallback", "full_reread"),
                "dirty_cleared": False,
            }

        if deleted_source == "recycle_bin":
            live = self.reader.live_records(room_id, entity)
            deleted_ids = rules.deleted_by_recycle_bin(self.reader.recycle_bin(room_id, entity))
        elif deleted_source == "dataverse_delta":
            page = rules.delta_page(
                self.reader.delta(
                    room_id,
                    entity,
                    (cursor.get("data") or cursor).get("cursor") if cursor else None,
                )
            )
            live = list(page["live"])
            deleted_ids = list(page["deleted"])
        else:
            live = self.reader.live_records(room_id, entity)
            held_ids = [
                str(row["data"]["record_id"])
                for row in self.store.list(REPLICA, room_id=room_id, limit=1000)
                if (row.get("data") or {}).get("entity") == entity
                and not (row.get("data") or {}).get("deleted")
            ]
            deleted_ids = rules.deleted_by_difference(held_ids, [_row_id_of(row) for row in live])

        self._log(
            room_id,
            run["id"],
            "record_read",
            detail=f"read {len(live)} live and {len(deleted_ids)} deleted {entity} rows",
            actor=actor,
            source=source,
        )

        written = []
        for row in live:
            record_id = _row_id_of(row)
            if not record_id:
                continue
            written.append(
                self._write_replica(
                    room_id,
                    entity=entity,
                    record_id=record_id,
                    payload=row,
                    deleted=False,
                    run_id=run["id"],
                    event_id=event["id"],
                    actor=actor,
                    source=source,
                )
            )
        if written:
            self._log(
                room_id,
                run["id"],
                "rows_written",
                detail=f"{len(written)} {entity} rows overwritten",
                actor=actor,
                source=source,
            )

        removed = []
        for record_id in deleted_ids:
            existing = self._replica_row(room_id, entity, record_id)
            if existing is not None and existing.get("data", {}).get("deleted"):
                continue
            removed.append(
                self._write_replica(
                    room_id,
                    entity=entity,
                    record_id=record_id,
                    payload={},
                    deleted=True,
                    run_id=run["id"],
                    event_id=event["id"],
                    actor=actor,
                    source=source,
                )
            )
        if removed:
            self._log(
                room_id,
                run["id"],
                "rows_deleted",
                detail=f"{len(removed)} {entity} rows tombstoned by {deleted_source}",
                actor=actor,
                source=source,
            )

        # The repair covered the whole entity, so every marker on it is cleared.
        # A marker left open after a whole-entity read would ask for a second read
        # of a record the run has just re-read in full.
        for marker in self._dirty_markers_for_entity(room_id, entity):
            self._clear_dirty(
                room_id,
                marker["id"],
                run_id=run["id"],
                event_id=event["id"],
                actor=actor,
                source=source,
            )
        self._close_run(
            room_id,
            run["id"],
            state="complete",
            counts={"written": len(written), "deleted": len(removed)},
            actor=actor,
            source=source,
        )
        return {
            "run": self.run(room_id, run["id"]),
            "scope": "entity",
            "written": [self._replica_view(row) for row in written],
            "deleted": [self._replica_view(row) for row in removed],
            "deleted_source": deleted_source,
            "dirty_cleared": True,
        }

    # ------------------------------------------------------------------ #
    # Step 6: resubscribe
    # ------------------------------------------------------------------ #

    def subscribe(
        self, room_id: str, body: Mapping[str, Any] | None = None, *, actor: str, source: str
    ) -> dict[str, Any]:
        """Record the resubscription and close the overflow run it finishes.

        "Room resubscribes and records a reconciliation event in the sync log for
        audit." The last step of the overflow procedure and the last step of the user
        flow are the same sentence.

        A full re-read leaves a resumable position behind, because a Replay ID is
        "the starting point for the data reconciliation": the position a room
        reconciles *from* is the one it must store, not the one the overflow
        carried.
        """
        payload = dict(body or {})
        entity = str(payload.get("entity") or "").strip()
        cursor = self._cursor_for(room_id, str(payload.get("vendor") or "salesforce"), entity)
        stamp = self.clock().astimezone(timezone.utc).isoformat()

        unsubscribed = self._unsubscribed_events(room_id)
        for event in unsubscribed:
            self.store.update(
                event["id"],
                {"subscription_state": "subscribed", "resubscribed_at": stamp},
                actor=actor,
                source=source,
            )

        # Every run of every overflow the room just came back from gets the line,
        # whether or not the repair had already finished. A run still open is closed
        # first, because a resubscription with a run left open would read as a repair
        # nobody finished.
        closed: list[dict[str, Any]] = []
        annotated: list[dict[str, Any]] = []
        for run in self._runs_of_events(room_id, [str(row["id"]) for row in unsubscribed]):
            if (run.get("data") or {}).get("state") == "open":
                self._close_run(
                    room_id,
                    run["id"],
                    state="complete",
                    counts=(run.get("data") or {}).get("counts") or {},
                    actor=actor,
                    source=source,
                )
                closed.append(self.run(room_id, run["id"]))
            self._log(
                room_id,
                run["id"],
                "resubscribed",
                detail="the room is subscribed again",
                actor=actor,
                source=source,
            )
            annotated.append(self.run(room_id, run["id"]))

        stored = None
        held = (cursor.get("data") or cursor) if cursor else None
        if held is None or not held.get("cursor"):
            stored = self._store_cursor(
                room_id,
                kind="replay_id",
                vendor=str(payload.get("vendor") or "salesforce"),
                scope="entity",
                scope_value=entity,
                position=rules.full_reread_cursor(self.clock())["cursor"],
                actor=actor,
                source=source,
            )
        return {
            "subscription_state": "subscribed",
            "resubscribed_at": stamp,
            "runs_closed": closed,
            "runs_annotated": annotated,
            "cursor": self._cursor_view(stored if stored is not None else held)
            if (stored is not None or held is not None)
            else None,
            "cursors": self.cursors(room_id),
        }

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def runs(self, room_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """The reconciliation runs, newest first."""
        rows = self.store.list(RUNS, room_id=room_id, limit=limit)
        return [
            self._run_view(row)
            for row in sorted(rows, key=lambda row: row["created_at"], reverse=True)
        ]

    def run(self, room_id: str, run_id: str) -> dict[str, Any]:
        """One run, with its log, or a refusal naming the room."""
        return {
            **self._run_view(self._require_run(room_id, run_id)),
            "log": self.run_log(room_id, run_id),
        }

    def _require_run(self, room_id: str, run_id: str) -> dict[str, Any]:
        """The run row itself, or a refusal. The guard both run reads share."""
        record = self.store.get(run_id) if run_id else None
        if record is None or record["collection"] != RUNS or record["room_id"] != room_id:
            raise UnknownRun(
                f"no reconciliation run {run_id!r} in room {room_id!r}. "
                "The id is the one the reconcile route returned."
            )
        return record

    def run_log(self, room_id: str, run_id: str) -> list[dict[str, Any]]:
        """A run's sync log, in the order it happened.

        Ordered by the ``seq`` stored on each line rather than by insertion order.
        SQLite breaks a tie on ``updated_at`` by insertion, so a log ordered by the
        database fails intermittently under parallel test runs; the number is the
        order.
        """
        self._require_run(room_id, run_id)
        rows = [
            row
            for row in self.store.list(LOG, room_id=room_id, limit=1000)
            if (row.get("data") or {}).get("run_id") == run_id
        ]
        return [
            dict(row["data"]) | {"id": row["id"]}
            for row in sorted(rows, key=lambda row: row["data"]["seq"])
        ]

    def cursors(self, room_id: str) -> list[dict[str, Any]]:
        """Both resumable positions this room holds, each with its verdict."""
        rows = self.store.list(CURSORS, room_id=room_id, limit=200)
        return [
            self._cursor_view(row, now=self.clock())
            for row in sorted(rows, key=lambda row: (row["data"].get("kind", ""), row["id"]))
        ]

    def replica(
        self, room_id: str, *, entity: str = "", include_deleted: bool = True, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The reconciled rows this workflow owns.

        Namespaced to this feature on purpose. Another feature owns the streaming
        replica and this one repairs its own view; see the register in
        :mod:`dsr.crm_integration.gap_inferences`.
        """
        rows = []
        for row in self.store.list(REPLICA, room_id=room_id, limit=limit):
            data = row.get("data") or {}
            if entity and data.get("entity") != entity:
                continue
            if not include_deleted and data.get("deleted"):
                continue
            rows.append(self._replica_view(row))
        return sorted(rows, key=lambda row: (str(row["entity"]), str(row["record_id"])))

    def health(self, room_id: str) -> dict[str, Any]:
        """One payload the data-health view reads.

        "Room **Data health** view showing dirty records", plus the sync log and the
        cursor stores the research names as this workflow's product surfaces.
        """
        dirty = self.dirty_records(room_id)
        runs = self.runs(room_id, limit=200)
        cursors = self.cursors(room_id)
        reconciled = sum(
            1
            for row in self.store.list(DIRTY, room_id=room_id, limit=1000)
            if (row.get("data") or {}).get("state") == "reconciled"
        )
        return {
            "room_id": room_id,
            "dirty_count": len(dirty),
            "reconciled_count": int(reconciled),
            "gap_event_count": int(self.store.db.count(GAP_EVENTS, room_id=room_id)),
            "open_run_count": sum(1 for row in runs if not row["terminal"]),
            "run_count": len(runs),
            "unresumable_cursors": [row["kind"] for row in cursors if not row["resumable"]],
            "cursors": cursors,
            "dirty_records": dirty,
            "clean": not dirty,
            "summary": (
                "no record is dirty; every gap this room saw has been repaired"
                if not dirty
                else f"{len(dirty)} record(s) are dirty and owe a full re-read"
            ),
        }

    # ------------------------------------------------------------------ #
    # The room's copy of the vendor's tables, which a reconciliation reads
    # ------------------------------------------------------------------ #

    def put_source(
        self,
        room_id: str,
        *,
        entity: str,
        record_id: str,
        payload: Mapping[str, Any],
        is_deleted: bool = False,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Write one row into the room's copy of the vendor's tables.

        The write side of the read seam. A room without an org connection populates
        this. A room with one never calls it: there the vendor is the writer, and this
        row would be the vendor's answer rather than the room's copy of it.
        """
        existing = self._source_row(room_id, entity, record_id)
        body = {
            "entity": entity,
            "record_id": record_id,
            "is_deleted": bool(is_deleted),
            "payload": dict(payload),
        }
        if existing is None:
            return self.store.create(SOURCE, body, room_id=room_id, actor=actor, source=source)
        return self.store.update(existing["id"], body, actor=actor, source=source)

    def remove_source(
        self, room_id: str, *, entity: str, record_id: str, actor: str, source: str
    ) -> dict[str, Any] | None:
        """Move a row into the Recycle Bin, which is a soft delete on the vendor's side."""
        existing = self._source_row(room_id, entity, record_id)
        if existing is None:
            return None
        return self.store.update(existing["id"], {"is_deleted": True}, actor=actor, source=source)

    def purge_source(
        self, room_id: str, *, entity: str, record_id: str, actor: str, source: str
    ) -> dict[str, Any] | None:
        """Remove a row entirely, so a read of it answers ``None``.

        The state a ``GAP_DELETE`` is repaired from: the vendor no longer holds the
        record at all, rather than holding it in the Recycle Bin.
        """
        existing = self._source_row(room_id, entity, record_id)
        if existing is None:
            return None
        return self.store.delete(existing["id"], actor=actor, source=source)

    def _source_row(self, room_id: str, entity: str, record_id: str) -> dict[str, Any] | None:
        for row in self.store.list(SOURCE, room_id=room_id, limit=1000):
            data = row.get("data") or {}
            if str(data.get("entity")) == entity and str(data.get("record_id")) == record_id:
                return row
        return None

    # ------------------------------------------------------------------ #
    # Internal writes
    # ------------------------------------------------------------------ #

    def _mark_dirty(
        self,
        room_id: str,
        *,
        entity: str,
        record_id: str,
        gap_event_id: str,
        gap_commit_timestamp: str,
        change_type: str,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Mark a record dirty as of the gap's date, or re-open a marker already held.

        Re-opening rather than creating a second marker is what keeps one dirty set
        per record: a room that sees two gaps on one record before repairing it owes
        one re-read, not two.
        """
        existing = self._dirty_marker(room_id, entity, record_id)
        if existing is not None:
            earliest = min(str(existing["data"]["gap_commit_timestamp"]), gap_commit_timestamp)
            self.store.update(
                existing["id"],
                {
                    "state": "dirty",
                    "gap_commit_timestamp": earliest,
                    # The latest gap's operation is the one that decides what the
                    # re-read should find, so the marker takes it. The earliest
                    # date is kept, because the window the repair has to cover
                    # starts at the first gap and not at the last one.
                    "change_type": change_type,
                    "gap_event_id": existing["data"].get("gap_event_id") or gap_event_id,
                    "reopened_at": self.clock().astimezone(timezone.utc).isoformat(),
                },
                actor=actor,
                source=source,
            )
            return self._dirty_view(self.store.require(existing["id"]))
        record = self.store.create(
            DIRTY,
            {
                "entity": entity,
                "record_id": record_id,
                "state": "dirty",
                "gap_commit_timestamp": gap_commit_timestamp,
                "gap_event_id": gap_event_id,
                "change_type": change_type,
                "marked_at": self.clock().astimezone(timezone.utc).isoformat(),
                "run_id": None,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._dirty_view(record)

    def _clear_dirty(
        self, room_id: str, marker_id: str, *, run_id: str, event_id: str, actor: str, source: str
    ) -> dict[str, Any]:
        """Clear the flag and keep the marker, so the run that cleared it is recorded."""
        record = self.store.update(
            marker_id,
            {
                "state": "reconciled",
                "cleared_at": self.clock().astimezone(timezone.utc).isoformat(),
                "run_id": run_id,
                "event_id": event_id,
            },
            actor=actor,
            source=source,
        )
        return self._dirty_view(record)

    def _dirty_marker(
        self, room_id: str, entity: str, record_id: str, *, state: str = "dirty"
    ) -> dict[str, Any] | None:
        for row in self.store.list(DIRTY, room_id=room_id, limit=1000):
            data = row.get("data") or {}
            if (
                data.get("entity") == entity
                and str(data.get("record_id")) == record_id
                and data.get("state") == state
            ):
                return row
        return None

    def _dirty_markers_for_entity(
        self, room_id: str, entity: str, *, state: str = "dirty"
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self.store.list(DIRTY, room_id=room_id, limit=1000)
            if (row.get("data") or {}).get("entity") == entity
            and (row.get("data") or {}).get("state") == state
        ]

    def _write_replica(
        self,
        room_id: str,
        *,
        entity: str,
        record_id: str,
        payload: Mapping[str, Any],
        deleted: bool,
        run_id: str | None,
        event_id: str | None,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Overwrite one replica row, or write its tombstone.

        One row per ``(entity, record_id)`` and overwritten rather than appended, so
        a repair cannot leave two rows that disagree about the same record.
        """
        stamp = self.clock().astimezone(timezone.utc).isoformat()
        existing = self._replica_row(room_id, entity, record_id)
        body = {
            "entity": entity,
            "record_id": record_id,
            "deleted": bool(deleted),
            "payload": dict(payload),
            "reconciled_at": stamp,
            "run_id": run_id,
            "event_id": event_id,
            "last_modified_date": payload.get(vocab.LAST_MODIFIED_FIELD),
            "reason": "reconciled" if run_id else "change_event",
        }
        if existing is None:
            return self.store.create(REPLICA, body, room_id=room_id, actor=actor, source=source)
        return self.store.update(existing["id"], body, actor=actor, source=source)

    def _replica_row(self, room_id: str, entity: str, record_id: str) -> dict[str, Any] | None:
        for row in self.store.list(REPLICA, room_id=room_id, limit=1000):
            data = row.get("data") or {}
            if data.get("entity") == entity and str(data.get("record_id")) == record_id:
                return row
        return None

    def _store_cursor(
        self,
        room_id: str,
        *,
        kind: str,
        vendor: str,
        scope: str,
        scope_value: str,
        position: str | None,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Store one resumable position, or move the one already held for its scope.

        The record shape is the one WF-045's backfill established, so a room reads
        one cursor vocabulary: ``{vendor, connectionId, cursor, updatedAt}``. The
        snake_case mirror is additive, because this product's listings are
        snake_case and a query has to resolve on both spellings.
        """
        if kind not in vocab.CURSOR_KINDS:
            raise UnknownCursorKind(
                f"kind must be one of {', '.join(vocab.CURSOR_KINDS)}; got {kind!r}. "
                f"{vocab.CURSOR_KINDS[kind] if kind in vocab.CURSOR_KINDS else ''}"
            )
        stamp = self.clock().astimezone(timezone.utc).isoformat()
        body = {
            "vendor": vendor,
            "kind": kind,
            "scope": scope,
            "scope_value": scope_value,
            "cursor": position,
            "updatedAt": stamp,
            "connectionId": f"{room_id}:{scope_value}" if scope_value else room_id,
            "connection_id": f"{room_id}:{scope_value}" if scope_value else room_id,
            "resumable": bool(position),
        }
        existing = None
        for row in self.store.list(CURSORS, room_id=room_id, limit=200):
            data = row.get("data") or {}
            if data.get("kind") == kind and data.get("scope_value") == scope_value:
                existing = row
                break
        if existing is None:
            return self.store.create(CURSORS, body, room_id=room_id, actor=actor, source=source)
        return self.store.update(existing["id"], body, actor=actor, source=source)

    def _cursor_for(self, room_id: str, vendor: str, entity: str) -> dict[str, Any] | None:
        """The cursor a run of this vendor and entity resumes from.

        Refuses rather than answering ``None`` when the room holds the *other*
        vendor's cursor. A silent ``None`` reads as "this room has no position",
        which the expiry path then treats as a reason to fall back to a full
        re-read - so pairing a Salesforce Replay ID with a Dataverse poll would
        quietly discard a position the room does have.
        """
        wanted = vocab.cursor_kind_for(vendor)
        rows = self.store.list(CURSORS, room_id=room_id, limit=200)
        for row in rows:
            data = row.get("data") or {}
            if data.get("kind") != wanted:
                continue
            if data.get("vendor") != vendor:
                raise MismatchedCursorVendor(
                    f"room {room_id!r} stores a {data.get('vendor')} {data.get('kind')} "
                    f"and this run reads {vendor}. A Salesforce Replay ID cannot resume "
                    "a Dataverse poll, and pairing them would skip or repeat every row "
                    "in between."
                )
            if not entity or data.get("scope_value") == entity:
                return row
        foreign = sorted(
            {
                str((row.get("data") or {}).get("vendor"))
                for row in rows
                if (row.get("data") or {}).get("vendor") != vendor
            }
        )
        if foreign:
            raise MismatchedCursorVendor(
                f"this run reads {vendor}, which resumes from a {wanted}, and room "
                f"{room_id!r} holds {' and '.join(foreign)} instead. Keeping the two "
                "cursors named separately is the point; pairing them is not."
            )
        return None

    def _cursor_view(
        self, record: Mapping[str, Any], *, now: datetime | None = None
    ) -> dict[str, Any]:
        data = record.get("data") or record
        verdict = rules.cursor_verdict(data, now or self.clock())
        return {
            "id": record.get("id"),
            "kind": data.get("kind", ""),
            "vendor": data.get("vendor", ""),
            "scope": data.get("scope", ""),
            "scope_value": data.get("scope_value", ""),
            "position": data.get("cursor"),
            "present": bool(data.get("cursor")),
            "held_since": data.get("updatedAt"),
            "age_days": verdict.get("age_days", 0.0),
            "expiry_days": verdict.get("expiry_days"),
            "applies": verdict.get("applies", False),
            "resumable": verdict.get("resumable", False),
            "state": verdict.get("state", "absent"),
            "fallback": verdict.get("fallback", ""),
            "reason": verdict.get("reason", ""),
        }

    def _log(
        self,
        room_id: str,
        run_id: str,
        event: str,
        *,
        detail: str,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Write one numbered line of a run's sync log.

        The number is the run's line count, so it is gapless and one-based. It is
        read back as the order, which is what keeps the log from being ordered by
        whatever order the database happened to write in.
        """
        if event not in vocab.LOG_EVENTS:
            raise UnknownEvent(f"{event!r} is not a sync-log line this workflow writes")
        run = self.store.require(run_id)
        seq = int(run["data"].get("seq") or 0) + 1
        self.store.update(run_id, {"seq": seq}, actor=actor, source=source)
        record = self.store.create(
            LOG,
            {
                "run_id": run_id,
                "seq": seq,
                "event": event,
                "detail": detail,
                "at": self.clock().astimezone(timezone.utc).isoformat(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return dict(record["data"]) | {"id": record["id"]}

    def _close_run(
        self,
        room_id: str,
        run_id: str,
        *,
        state: str,
        counts: Mapping[str, int],
        actor: str,
        source: str,
    ) -> None:
        if state not in vocab.TERMINAL_RUN_STATES:
            raise UnknownRun(f"{state!r} is not a terminal run state")
        self.store.update(
            run_id,
            {
                "state": state,
                "counts": {key: int(value) for key, value in counts.items()},
                "closed_at": self.clock().astimezone(timezone.utc).isoformat(),
            },
            actor=actor,
            source=source,
        )
        self._log(
            room_id,
            run_id,
            {
                "complete": "run_complete",
                "expired_cursor": "run_expired_cursor",
                "refused": "run_refused",
            }[state],
            detail=f"{state} with {dict(counts)}",
            actor=actor,
            source=source,
        )

    def _open_runs(self, room_id: str, kind: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.store.list(RUNS, room_id=room_id, limit=200)
            if (row.get("data") or {}).get("state") == "open"
            and (row.get("data") or {}).get("kind") == kind
        ]

    def _runs_of_events(self, room_id: str, event_ids: list[str]) -> list[dict[str, Any]]:
        """Every run opened by these events, oldest first.

        ``None`` is dropped from the wanted ids: a run whose event could not be read
        back should not make this method raise, because the resubscription has to
        happen either way.
        """
        wanted = {event_id for event_id in event_ids if event_id}
        rows = [
            row
            for row in self.store.list(RUNS, room_id=room_id, limit=200)
            if str((row.get("data") or {}).get("event_id")) in wanted
        ]
        return sorted(rows, key=lambda row: row["created_at"])

    def _unsubscribed_events(self, room_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.store.list(GAP_EVENTS, room_id=room_id, limit=200)
            if (row.get("data") or {}).get("subscription_state") == "unsubscribed"
        ]

    def _require_event(self, room_id: str, event_id: str) -> dict[str, Any]:
        record = self.store.get(event_id) if event_id else None
        if record is None or record["collection"] != GAP_EVENTS or record["room_id"] != room_id:
            raise UnknownEvent(
                f"reconcile needs the id of the gap or overflow event that opened the "
                f"repair; {event_id!r} is not one in room {room_id!r}."
            )
        return self._view(record)

    # ------------------------------------------------------------------ #
    # Views
    # ------------------------------------------------------------------ #

    def _view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "change_type": data.get("change_type"),
            "kind": data.get("kind"),
            "operation": data.get("operation"),
            "vendor": data.get("vendor"),
            "entity": data.get("entity"),
            "record_ids": list(data.get("record_ids") or []),
            "commit_timestamp": data.get("commit_timestamp"),
            "transaction_key": data.get("transaction_key"),
            "sequence_number": data.get("sequence_number"),
            "change_count": data.get("change_count"),
            "exceeds_overflow_threshold": bool(data.get("exceeds_overflow_threshold")),
            "cursor_kind": data.get("cursor_kind"),
            "replay_id": data.get("replay_id"),
            "cursor_id": data.get("cursor_id"),
            "subscription_state": data.get("subscription_state"),
            "reported_at": data.get("reported_at"),
            "resubscribed_at": data.get("resubscribed_at"),
        }

    def _dirty_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "entity": data.get("entity"),
            "record_id": data.get("record_id"),
            "state": data.get("state"),
            "dirty": data.get("state") == "dirty",
            "gap_commit_timestamp": data.get("gap_commit_timestamp"),
            "gap_event_id": data.get("gap_event_id"),
            "change_type": data.get("change_type"),
            "marked_at": data.get("marked_at"),
            "cleared_at": data.get("cleared_at"),
            "reopened_at": data.get("reopened_at"),
            "run_id": data.get("run_id"),
            "age_note": _age_note(data.get("gap_commit_timestamp"), self.clock()),
        }

    def _run_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        state = str(data.get("state") or "open")
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "state": state,
            "terminal": state in vocab.TERMINAL_RUN_STATES,
            "kind": data.get("kind"),
            "vendor": data.get("vendor"),
            "entity": data.get("entity"),
            "record_id": data.get("record_id"),
            "event_id": data.get("event_id"),
            "deleted_source": data.get("deleted_source"),
            "counts": dict(data.get("counts") or {}),
            "opened_at": data.get("opened_at"),
            "closed_at": data.get("closed_at"),
        }

    def _replica_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "entity": data.get("entity"),
            "record_id": data.get("record_id"),
            "deleted": bool(data.get("deleted")),
            "payload": dict(data.get("payload") or {}),
            "last_modified_date": data.get("last_modified_date"),
            "reconciled_at": data.get("reconciled_at"),
            "run_id": data.get("run_id"),
            "event_id": data.get("event_id"),
            "reason": data.get("reason"),
        }

    @staticmethod
    def _drop_detail(reason: str) -> str:
        return {
            rules.DROP_REASONS[1]: (
                "the change did not commit after the gap, so it is already inside the "
                "window the re-read is about to cover"
            ),
            rules.DROP_REASONS[3]: (
                "the change is newer than the record the vendor returned, so it has "
                "not reached this room and the dirty marker stays open for the next "
                "re-read"
            ),
        }.get(reason, "")


def _row_id_of(row: Mapping[str, Any]) -> str:
    for key in ("Id", "id", "recordId", "record_id"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def _age_note(gap_commit_timestamp: Any, now: datetime) -> str:
    if not gap_commit_timestamp:
        return ""
    try:
        aged = rules.age_days(gap_commit_timestamp, now)
    except Exception:
        return ""
    if aged < 1:
        hours = max(1, round(aged * 24))
        return f"dirty for {hours}h"
    return f"dirty for {aged:g}d"


__all__ = [
    "CURSORS",
    "DIRTY",
    "GAP_EVENTS",
    "LOG",
    "OWNED_COLLECTIONS",
    "REPLICA",
    "RUNS",
    "SOURCE",
    "SUBSCRIPTION_STATES",
    "ReconcileEngine",
]
