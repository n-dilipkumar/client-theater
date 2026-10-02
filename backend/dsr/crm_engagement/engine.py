"""The queue worker: researched steps 2 through 5, in that order.

The research for WF-037 is a five-step flow, and this module is those five steps and
nothing else:

1. the buyer acts;
2. the room records a row in its own ``engagement`` table and **enqueues** a CRM write;
3. the worker **resolves the buyer's CRM record** using the field mapping / sync key;
4. the worker **calls the CRM create endpoint** with mapped properties;
5. on success it writes the returned ``crm_record_id`` into the local row and marks the
   event **synced**; on failure it **retries with backoff** and surfaces the failure in
   the Sync log.

Two design choices worth stating before the code, because both are visible in the
product and neither is a preference.

Nothing is sent by :meth:`EngagementSync.record_event` unless asked
------------------------------------------------------------------
Step 2 says the write is *enqueued*, and the automation note says the queue worker "fires
it without further user input". So the enqueue and the fire are separate methods, and
``record_event(..., fire_queue=True)`` runs the worker as the last thing the request does.
One user action - a buyer opening an asset - still results in the CRM write, which is what
"without further user input" asks for. ``fire_queue=False`` records the row and stops, and
:func:`dsr.crm_engagement.queue.SyncBook.pending_rows` leaves that row for
:meth:`drain`, which is the "Sync now" affordance. Nothing is lost either way: the queue
is a real record, not a counter, so a drain can run an hour later or a process later.

A plan, then an execute
------------------------
:meth:`_plan` resolves and maps and shapes the request; :meth:`_execute` sends it. The
split is what makes ``POST /preview`` honest: preview calls the planner and nothing else,
so a preview cannot write by construction rather than by discipline. It is also what keeps
a blocked row's reason available without attempting a write to discover it.

Everything a plan could not do is a named reason
------------------------------------------------
A rule that does not fall through is a bug someone hits in production, so
:func:`_block` names every ground on which this worker declines to send: no connector, a
disabled connector, two connectors and no field map to choose between them, an event type
nobody has mapped, a sync key that will not build, and a buyer neither W1 nor W2 gave a
CRM record id or an email for. Each becomes a ``blocked`` queue row carrying the reason,
and the Sync log and the feed both report it. The alternative - dropping the row, or
sending a half-built payload - loses events quietly, which is the one outcome a log
cannot then tell anyone about.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable, Mapping, Sequence

from dsr.crm_engagement import delivery, payloads
from dsr.crm_engagement.errors import (
    EngagementSyncError,
    InvalidConnector,
    SyncNotConfigured,
    UnknownRoom,
)
from dsr.crm_engagement.inferences import describe as describe_inferences
from dsr.crm_engagement.mapping import Mapped, as_text, map_event, read_source
from dsr.crm_engagement.queue import (
    CRM_RECORD_ID_FIELD,
    SYNC_STATE_FIELD,
    SyncBook,
)
from dsr.crm_engagement.vocabulary import (
    BLOCK_REASONS,
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    QUEUE_STATES,
    describe,
)
from dsr.store import RecordStore

#: The identity sources the researched flow names, in the order it implies: "buyer
#: identity (CRM record id or email from W1/W2)". A CRM record id is a direct answer and
#: an email is a lookup, so the id is tried first.
IDENTITY_SOURCES: tuple[str, ...] = ("buyer_crm_id", "buyer_email")


@dataclass
class Plan:
    """What one queue row would do, with nothing sent.

    ``blocked`` is the reason, or ``None``. When it is set, everything after it is absent
    and the reason is what the Sync log will show a rep.
    """

    queue_id: str
    room_id: str | None
    event_type: str
    engagement: dict[str, Any] | None = None
    blocked: str | None = None
    connector: dict[str, Any] | None = None
    field_map: dict[str, Any] | None = None
    mapped: Mapped | None = None
    request: payloads.CreateRequest | None = None
    resolution: dict[str, Any] = dataclass_field(default_factory=dict)
    notes: list[str] = dataclass_field(default_factory=list)

    @property
    def sendable(self) -> bool:
        return self.blocked is None and self.request is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "engagement_id": (self.engagement or {}).get("id"),
            "room_id": self.room_id,
            "type": self.event_type,
            "sendable": self.sendable,
            "blocked": self.blocked,
            "block_detail": BLOCK_REASONS.get(self.blocked or "", None),
            "connector": (
                {
                    "id": self.connector.get("id"),
                    "vendor": self.connector.get("vendor"),
                    "object": self.connector.get("object") or self.connector.get("entity_set"),
                }
                if self.connector
                else None
            ),
            "field_map_id": (self.field_map or {}).get("id"),
            "resolution": dict(self.resolution),
            "findings": list(self.mapped.findings) if self.mapped else [],
            "properties": dict(self.mapped.properties) if self.mapped else {},
            "request": self.request.to_dict() if self.request is not None else None,
            "notes": list(self.notes),
        }


def _block(plan: Plan, reason: str) -> Plan:
    """Mark a plan blocked, with the researched reason text attached as a note."""
    if reason not in BLOCK_REASONS:
        raise ValueError(f"{reason!r} is not a documented block reason")
    plan.blocked = reason
    plan.notes.append(BLOCK_REASONS[reason])
    plan.request = None
    return plan


class EngagementSync:
    """The engagement-to-CRM write path, over a schema-flexible audited store.

    Built per request from a dependency rather than hung on ``app.state``, which is what
    keeps the shared app untouched and leaves the transport an overridable seam the suite
    can drive without a socket.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        transport: delivery.Transport | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff: float = DEFAULT_BACKOFF,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.book = SyncBook(store)
        self.transport: delivery.Transport = transport or delivery.UrllibTransport()
        self.max_attempts = max_attempts
        self.backoff = backoff
        self.sleep = sleep

    # -- the researched contract, as data ----------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        return describe()

    def inferences(self) -> dict[str, Any]:
        """Every design decision the research does not fix, named and bounded.

        The other half of :meth:`vocabulary`: what the sources say, and what this build
        chose on top of them. Both are reachable from the engine so a client has one place
        to ask, and neither needs the store.
        """
        return describe_inferences()

    # -- configuration reads ------------------------------------------------ #

    def connectors(self) -> list[dict[str, Any]]:
        """Every connector, with the token withheld and its preferences explained."""
        return [self.book.connector_view(row) for row in self.book.connectors()]

    def connector(self, connector_id: str) -> dict[str, Any]:
        row = self.book.connector(connector_id)
        if row is None:
            raise InvalidConnector(f"connector {connector_id} not found")
        return self.book.connector_view(row)

    def event_types(self) -> list[dict[str, Any]]:
        return self.book.event_types()

    def event_type(self, event_type_id: str) -> dict[str, Any]:
        row = self.book.event_type(event_type_id)
        if row is None:
            raise EngagementSyncError(f"event type {event_type_id} not found")
        return row

    def field_maps(
        self, event_type: str | None = None, connector_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.book.field_maps(event_type=event_type, connector_id=connector_id)

    def field_map(self, field_map_id: str) -> dict[str, Any]:
        row = self.book.field_map(field_map_id)
        if row is None:
            raise EngagementSyncError(f"field map {field_map_id} not found")
        return row

    def effective_field_map(self, event_type: str, connector_id: str) -> dict[str, Any] | None:
        return self.book.field_map_for(event_type, connector_id)

    def configuration(self, room_id: str) -> dict[str, Any]:
        """Whether this room could sync at all, and what is missing if it could not.

        The readiness view. It answers the question a rep actually has - "why is nothing
        going across?" - without a drain, and it answers it in the same words the queue
        rows will carry, so the two cannot disagree. It resolves the connector the same
        way the worker does, room-scoped first, so it cannot report a room as ready when
        the worker would block every row on it.
        """
        self.book.require_room(room_id)
        connectors = self.book.connectors()
        scoped = [row for row in connectors if str(row.get("room_id") or "") == str(room_id)]
        unscoped = [row for row in connectors if not row.get("room_id")]
        candidates = scoped or unscoped
        usable = [row for row in candidates if row.get("enabled")]
        event_types = self.book.event_types()
        mapped = {str(row.get("event_type")) for row in self.book.field_maps()}
        missing: list[dict[str, str]] = []
        if not connectors:
            missing.append({"reason": "no_connector", "detail": BLOCK_REASONS["no_connector"]})
        elif not candidates:
            missing.append(
                {
                    "reason": "no_connector",
                    "detail": "No CRM connector is scoped to this room, and the installation has no default.",
                }
            )
        elif not usable:
            missing.append(
                {"reason": "connector_disabled", "detail": BLOCK_REASONS["connector_disabled"]}
            )
        unmapped = [
            str(row.get("event_type"))
            for row in event_types
            if str(row.get("event_type")) not in mapped
        ]
        for name in unmapped:
            missing.append({"reason": "event_type_unmapped", "detail": f"{name} has no field map."})
        # A type that has a map *somewhere* is not mapped *here*, if the map is for another
        # connector. Checking coverage globally would report a room ready while the worker
        # blocks every event of a type on it, which is the one thing this view exists to
        # prevent a reader from concluding.
        for row in event_types:
            name = str(row.get("event_type"))
            if name in unmapped:
                continue
            if usable and self.book.field_map_for(name, str(usable[0]["id"])) is None:
                missing.append(
                    {
                        "reason": "event_type_unmapped",
                        "detail": (
                            f"{name} is mapped, but not on {usable[0].get('label') or usable[0].get('vendor')}, "
                            "which is the CRM this room writes to."
                        ),
                    }
                )
        return {
            "room_id": str(room_id),
            "ready": not missing,
            "connectors": [self.book.connector_view(row) for row in connectors],
            "scoped_connector_ids": [str(row["id"]) for row in candidates],
            "event_types": event_types,
            "field_maps": self.book.field_maps(),
            "identity_sources": list(IDENTITY_SOURCES),
            "missing": missing,
        }

    # -- configuration writes ----------------------------------------------- #

    def register_connector(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        row = self.book.save_connector(payload, room_id=room_id, actor=actor, source=source)
        return self.book.connector_view(row)

    def patch_connector(
        self, connector_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        row = self.book.update_connector(connector_id, patch, actor=actor, source=source)
        return self.book.connector_view(row)

    def delete_connector(
        self, connector_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.book.delete_connector(connector_id, actor=actor, source=source)

    def add_event_type(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        return self.book.save_event_type(payload, room_id=room_id, actor=actor, source=source)

    def patch_event_type(
        self, event_type_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.book.update_event_type(event_type_id, patch, actor=actor, source=source)

    def delete_event_type(
        self, event_type_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.book.delete_event_type(event_type_id, actor=actor, source=source)

    def add_field_map(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        return self.book.save_field_map(payload, room_id=room_id, actor=actor, source=source)

    def patch_field_map(
        self, field_map_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.book.update_field_map(field_map_id, patch, actor=actor, source=source)

    def delete_field_map(
        self, field_map_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        return self.book.delete_field_map(field_map_id, actor=actor, source=source)

    # -- step 1 and 2: the buyer acts, the room records and enqueues --------- #

    def record_event(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        fire_queue: bool = True,
    ) -> dict[str, Any]:
        """Step 1 and step 2, in one call: record the engagement, enqueue, and optionally fire.

        The engagement row is written **before** anything is sent, so a CRM that never
        answers still leaves the room knowing what the buyer did. That is the researched
        order and it is the order that makes step 5 meaningful: there is a local row to
        write the ``crm_record_id`` into.

        ``fire_queue`` is the "asynchronous, without further user input" note made
        explicit. It defaults to true, so the caller's single action produces the CRM
        write; set it false to record and enqueue only, and the row waits for
        :meth:`drain`.
        """
        self.book.require_room(room_id)
        engagement = self.book.record_engagement(
            payload, room_id=room_id, actor=actor, source=source
        )
        queue_row = self.book.enqueue(engagement, actor=actor, source=source)
        queue_id = str(queue_row["id"])
        engagement_id = str(engagement["id"])
        body: dict[str, Any] = {
            "engagement": engagement,
            "queue": queue_row,
            "fired": False,
            "result": None,
        }
        if fire_queue:
            outcome = self._execute(
                self._plan(queue_row), actor=actor, source=source, trigger="fire_queue"
            )
            body["fired"] = True
            body["result"] = outcome
            body["queue"] = self.book.queue_row(queue_id) or queue_row
            # Re-read the event too. The worker settles two rows, and returning the
            # engagement as it was *before* the write would tell a client the event is
            # still pending while the queue row beside it in the same response says synced.
            body["engagement"] = self.book.engagement(engagement_id) or engagement
        return body

    # -- the plan: step 3, and the mapping half of step 4 ------------------- #

    def _resolve_connector(
        self, event_type: str, room_id: str | None
    ) -> tuple[dict[str, Any] | None, str | None, list[str]]:
        """Which CRM this event's create goes to, or why that cannot be decided.

        A room-scoped connector wins over an unscoped one, and the unscoped one is the
        fallback: an installation has a default CRM, and a room whose buyer lives in a
        different one has to be able to say so without changing the default for everyone.

        One enabled candidate is the normal case and needs no help. More than one is a
        real configuration - a room writing some engagement to a custom object and some
        to contacts - so the choice is taken from the field maps, which is where a team
        already says which CRM an event type belongs to. If no map says, the row is
        blocked rather than sent to whichever connector sorted first.
        """
        connectors = self.book.connectors()
        if not connectors:
            return None, "no_connector", []
        scoped = [row for row in connectors if str(row.get("room_id") or "") == str(room_id or "")]
        unscoped = [row for row in connectors if not row.get("room_id")]
        candidates = scoped or unscoped
        enabled = [row for row in candidates if row.get("enabled")]
        if not enabled:
            return None, ("connector_disabled" if candidates else "no_connector"), []
        if len(enabled) == 1:
            return enabled[0], None, []
        by_id = {str(row["id"]): row for row in enabled}
        chosen: list[str] = []
        for row in self.book.field_maps(event_type=event_type):
            connector_id = str(row.get("connector_id") or "")
            if connector_id and connector_id in by_id and connector_id not in chosen:
                chosen.append(connector_id)
        if len(chosen) == 1:
            return by_id[chosen[0]], None, []
        return None, "ambiguous_connector", chosen

    def _resolve_buyer(
        self, engagement: Mapping[str, Any], field_map: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Step 3: resolve the buyer's CRM record, using the field mapping.

        "A background worker resolves the buyer's CRM record using the field mapping /
        sync key." The two things it has to work with are on the event - a CRM record id
        or an email, which the research's data sources attribute to W1 and W2 - and the
        mapping is what says whether this event type even carries a buyer.

        So a map that sends no buyer field needs no resolution and gets none, while a map
        that does must produce one, and an event that produces neither is blocked on
        ``buyer_unresolved`` rather than sent as an engagement row belonging to nobody.
        """
        sources = [
            as_text(row.get("source"))
            for row in field_map.get("fields") or []
            if as_text(row.get("source")) in IDENTITY_SOURCES
            and as_text(row.get("direction")) in ("out", "both")
        ]
        if not sources:
            return {"required": False, "by": None, "value": None, "source": None, "searched": []}
        searched: list[str] = []
        for source in sources:
            found, value, located = read_source(dict(engagement), source)
            searched.append(f"{source}={'hit' if found and value is not None else 'miss'}")
            if found and value is not None and as_text(value):
                return {
                    "required": True,
                    "by": source,
                    "value": as_text(value),
                    "source": located,
                    "searched": searched,
                }
        return {"required": True, "by": None, "value": None, "source": None, "searched": searched}

    def _plan(self, queue_row: Mapping[str, Any]) -> Plan:
        """Resolve, map, and shape. Writes nothing, so ``/preview`` cannot write either."""
        queue_id = str(queue_row.get("id"))
        data = dict(queue_row.get("data") or {})
        engagement_id = str(data.get("engagement_id") or "")
        engagement = self.book.engagement(engagement_id)
        event_type = as_text((engagement or {}).get("data", {}).get("type")) or as_text(
            data.get("type")
        )

        plan = Plan(
            queue_id=queue_id,
            room_id=queue_row.get("room_id"),
            event_type=event_type,
            engagement=engagement,
        )
        if engagement is None:
            return _block(plan, "event_missing")

        connector, blocked, chosen = self._resolve_connector(event_type, plan.room_id)
        if blocked is not None:
            if blocked == "ambiguous_connector" and chosen:
                plan.notes.append(
                    "Field maps name these connectors for this event type: " + ", ".join(chosen)
                )
            return _block(plan, blocked)
        assert connector is not None  # narrowed by the branch above

        field_map = self.book.field_map_for(event_type, str(connector["id"]))
        if field_map is None:
            return _block(plan, "event_type_unmapped")
        if not field_map.get("fields"):
            return _block(plan, "field_map_empty")
        plan.connector = connector
        plan.field_map = field_map

        plan.resolution = self._resolve_buyer(engagement, field_map)
        if plan.resolution.get("required") and not plan.resolution.get("by"):
            return _block(plan, "buyer_unresolved")

        plan.mapped = map_event(engagement, field_map)
        if not plan.mapped.sync_key:
            return _block(plan, "sync_key_unresolved")

        try:
            plan.request = payloads.build_create(
                as_text(connector.get("vendor")), connector, plan.mapped.properties
            )
        except (KeyError, ValueError) as exc:
            # A connector that cannot address a create is a configuration problem, not a
            # request problem, and the reason is named rather than raised to the caller.
            plan.notes.append(f"the connector cannot be addressed: {exc}")
            return _block(plan, "connector_unaddressable")
        return plan

    # -- step 4 and 5: send, then settle ------------------------------------ #

    def _execute(
        self, plan: Plan, *, actor: str | None, source: str, trigger: str
    ) -> dict[str, Any]:
        """Send one planned create and settle the row. The researched step 4 and 5."""
        if not plan.sendable:
            assert plan.blocked is not None
            self.book.mark_blocked(
                plan.queue_id,
                block_reason=plan.blocked,
                resolution=plan.resolution,
                actor=actor,
                source=source,
            )
            return {"queue_id": plan.queue_id, "state": "blocked", "reason": plan.blocked}

        assert plan.request is not None and plan.connector is not None and plan.mapped is not None
        connector = plan.connector
        report = delivery.post_create(
            self.transport,
            plan.request,
            max_attempts=self.max_attempts,
            backoff=self.backoff,
            sleep=self.sleep,
        )
        payload = report.to_dict()
        findings = [dict(entry) for entry in plan.mapped.findings]
        actor_name = as_text(actor) or None

        if report.ok and report.record_id()["id"]:
            located = report.record_id()
            row = self.book.mark_synced(
                plan.queue_id,
                connector_id=str(connector["id"]),
                crm_record_id=located["id"],
                crm_record_id_from=payload["crm_record_id_from"],
                attempt_log=payload["attempt_log"],
                attempt_statuses=payload["attempt_statuses"],
                request=plan.request.to_dict(),
                findings=findings,
                resolution=plan.resolution,
                vendor_error=payload["vendor_error"],
                http_status=report.result.status,
                actor=actor_name,
                source=source,
            )
            # The feed has to be able to answer "is that really the row the CRM made", so
            # the provenance of the id is mirrored onto the engagement row with the id.
            engagement_id = str((plan.engagement or {}).get("id") or "")
            if engagement_id and self.book.engagement(engagement_id) is not None:
                self.book.store.update(
                    engagement_id,
                    {"crm_record_id_sourced": bool(located["sourced"])},
                    actor=actor_name,
                    source=source,
                )
            outcome = "synced"
            failure = None
        else:
            failure = self._failure_reason(report)
            row = self.book.mark_failed(
                plan.queue_id,
                connector_id=str(connector["id"]),
                failure_reason=failure,
                attempt_log=payload["attempt_log"],
                attempt_statuses=payload["attempt_statuses"],
                request=plan.request.to_dict(),
                findings=findings,
                resolution=plan.resolution,
                vendor_error=payload["vendor_error"],
                http_status=report.result.status,
                error=report.result.error,
                needs_manual_update=report.needs_manual_update or failure == "crm_id_absent",
                actor=actor_name,
                source=source,
            )
            outcome = "failed"

        # The Sync log row is written after the row settles, so it can carry the terminal
        # state rather than predicting it. It names the connector, never the token.
        self.book.log_attempt(
            queue_row=row,
            report=payload,
            outcome=outcome,
            trigger=trigger,
            findings=findings,
            resolution=plan.resolution,
            request=plan.request.to_dict(),
            actor=actor_name,
            source=source,
        )
        return {
            "queue_id": plan.queue_id,
            "state": outcome,
            "http_status": report.result.status,
            "attempts": payload["attempts"],
            "crm_record_id": payload["crm_record_id"],
            "crm_record_id_from": payload["crm_record_id_from"],
            "error": payload["error"],
            "failure_reason": failure,
            "needs_manual_update": payload["needs_manual_update"],
            "findings": findings,
        }

    def _failure_reason(self, report: delivery.CreateReport) -> str:
        """Why this create is not a synced event.

        The interesting one is ``crm_id_absent``: the vendor accepted the write and this
        build still cannot call the event synced, because the researched step 5 needs an
        id to store and none arrived. A 409 is the sync key working - the CRM rejecting a
        duplicate - which no retry can fix and which the researched step 5 has no answer
        for, since turning that into an update is a different workflow. A 2xx outside the
        vendor's documented create-success set is ``unmapped_status``: the write may well
        have landed, and calling it either way would be a guess. Everything else is the
        vendor refusing the request, or the network not delivering it.
        """
        if report.ok:
            return "crm_id_absent"
        status = report.result.status
        if status is None:
            return "transport_error"
        if status == 409:
            return "sync_key_collision"
        if 200 <= status < 300:
            return "unmapped_status"
        return "crm_refused"

    # -- the worker: drain, preview, retry ---------------------------------- #

    def drain(
        self,
        room_id: str,
        *,
        actor: str | None = None,
        source: str,
        limit: int = 100,
        only: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fire the queue. The researched "the room's queue worker fires it".

        Oldest first, because a worker that took the newest rows would starve the
        backlog - and the backlog is exactly what a buyer who engaged during an outage is
        waiting on. Already-terminal rows are not re-sent: ``synced`` would create a
        second CRM row and ``failed`` is waiting for a human, not for a loop. So a drain
        is idempotent, which is the property that lets it run on a timer.
        """
        self.book.require_room(room_id)
        results: list[dict[str, Any]] = []
        counts = {"synced": 0, "failed": 0, "blocked": 0, "considered": 0, "skipped": 0}
        for row in self.book.pending_rows(room_id, limit=limit):
            queue_id = str(row["id"])
            if only and queue_id not in set(only):
                counts["skipped"] += 1
                continue
            counts["considered"] += 1
            outcome = self._execute(self._plan(row), actor=actor, source=source, trigger="drain")
            results.append(outcome)
            state = str(outcome["state"])
            counts[state] = counts.get(state, 0) + 1
        return {"room_id": str(room_id), "counts": counts, "results": results}

    def preview(self, room_id: str, *, limit: int = 100) -> dict[str, Any]:
        """What a drain would do, and why - with nothing written.

        The same planner, so the fall-through can be seen before it happens: which rows
        would be sent, which would be blocked and on what named ground, and the exact
        request each create would carry. Because the planner writes nothing, this cannot
        leave a mark even if it is wrong.
        """
        self.book.require_room(room_id)
        plans = [self._plan(row).to_dict() for row in self.book.pending_rows(room_id, limit=limit)]
        counts = {
            "sendable": sum(1 for plan in plans if plan["sendable"]),
            "blocked": sum(1 for plan in plans if not plan["sendable"]),
        }
        for plan in plans:
            if not plan["sendable"]:
                counts[str(plan["blocked"])] = counts.get(str(plan["blocked"]), 0) + 1
        return {"room_id": str(room_id), "counts": counts, "plans": plans}

    def retry(self, queue_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Fire one row by hand, and say what happened.

        For the rows a drain will not touch: a failure, and a block. Both are re-planned
        from scratch rather than resumed, so fixing the configuration and retrying sends
        exactly what was waiting. Attempt history is appended, not overwritten - the reason
        a second attempt happened is the first one.
        """
        row = self.book.queue_row(queue_id)
        if row is None:
            raise EngagementSyncError(f"queue row {queue_id} not found")
        state = as_text((row.get("data") or {}).get("state"))
        if state not in ("failed", "blocked"):
            raise EngagementSyncError(
                f"queue row {queue_id} is {state!r}; only a failed or blocked row can be "
                "retried, because re-sending a synced row would create a second CRM row"
            )
        outcome = self._execute(self._plan(row), actor=actor, source=source, trigger="retry")
        return {"queue_id": queue_id, "previous_state": state, **outcome}

    # -- the two researched surfaces ---------------------------------------- #

    def feed(
        self,
        room_id: str,
        *,
        type: str | None = None,
        sync_state: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """The room's Analytics / Engagement feed, with each event's sync state beside it.

        The research names the surface - "Room **Analytics / Engagement feed**" - and the
        thing that makes it worth reading is that the room's own record and the CRM's copy
        are shown together. A rep asking "did we log that download" gets the local row and
        whether it reached the CRM, from one call, and an event that never arrived says so
        rather than being absent.
        """
        self.book.require_room(room_id)
        rows = self.book.engagements(room_id, type=type, sync_state=sync_state, limit=limit)
        queue_rows = self.book.queue_rows(room_id, limit=1000)
        by_event: dict[str, dict[str, Any]] = {}
        for queue_row in queue_rows:
            engagement_id = as_text((queue_row.get("data") or {}).get("engagement_id"))
            if engagement_id:
                by_event.setdefault(engagement_id, queue_row)
        items: list[dict[str, Any]] = []
        for row in rows:
            queue_row = by_event.get(str(row["id"]))
            data = dict(row.get("data") or {})
            # Read through the same resolver the field map uses, so the feed and the create
            # agree about what a field is called. A team whose rows say `person` and
            # `target` should not see a feed full of blanks beside a create that sent the
            # buyer's email.
            occurred = _read(engagement=data, row=row, source="occurred_at")
            items.append(
                {
                    "id": row["id"],
                    "room_id": row.get("room_id"),
                    "type": as_text(data.get("type")),
                    "occurred_at": as_text(occurred[1]),
                    "occurred_at_field": occurred[2],
                    "asset": as_text(_read(engagement=data, row=row, source="asset")[1]),
                    "dwell_seconds": _number(
                        _read(engagement=data, row=row, source="dwell_seconds")[1]
                    ),
                    "buyer_email": as_text(
                        _read(engagement=data, row=row, source="buyer_email")[1]
                    ),
                    "buyer_crm_id": as_text(
                        _read(engagement=data, row=row, source="buyer_crm_id")[1]
                    ),
                    CRM_RECORD_ID_FIELD: data.get(CRM_RECORD_ID_FIELD),
                    # Where the id came from, and whether the research says so. Three
                    # vendors answer this question differently and one of the differences
                    # is that a Dataverse id arrives in a header, so a rep asking "is that
                    # really the row the CRM made" cannot be answered without it.
                    "crm_record_id_from": data.get("crm_record_id_from"),
                    "crm_record_id_sourced": data.get("crm_record_id_sourced"),
                    SYNC_STATE_FIELD: as_text(data.get(SYNC_STATE_FIELD)) or "pending",
                    "failure_reason": data.get("failure_reason"),
                    "block_reason": data.get("block_reason"),
                    "needs_manual_update": bool(data.get("needs_manual_update")),
                    "queue_id": (queue_row or {}).get("id"),
                    "queue_state": as_text((queue_row or {}).get("data", {}).get("state")),
                    "findings": (queue_row or {}).get("data", {}).get("findings") or [],
                    "created_at": row.get("created_at"),
                }
            )
        counts = {state: 0 for state in QUEUE_STATES}
        for item in items:
            counts[item[SYNC_STATE_FIELD]] = counts.get(item[SYNC_STATE_FIELD], 0) + 1
        return {
            "room_id": str(room_id),
            "count": len(items),
            "summary": {
                "events": len(items),
                "by_sync_state": counts,
                "synced": counts.get("synced", 0),
                "blocked": counts.get("blocked", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "needs_manual_update": sum(1 for item in items if item["needs_manual_update"]),
                "dwell_seconds": sum(
                    float(item["dwell_seconds"] or 0)
                    for item in items
                    if isinstance(item.get("dwell_seconds"), (int, float))
                ),
                "by_type": _tally(as_text(item["type"]) for item in items),
            },
            "events": items,
        }

    def event_detail(self, engagement_id: str) -> dict[str, Any]:
        """One event, its queue row, and every write the worker made for it.

        The drill-in a rep needs when a row is not synced: which event it was, which
        connector it was headed for, the exact request that went out, and every attempt
        with the vendor's own explanation of each one.
        """
        engagement = self.book.engagement(engagement_id)
        if engagement is None:
            raise UnknownRoom(engagement_id)
        queue_rows = self.book.queue_rows(engagement_id=engagement_id, limit=100)
        log = self.book.sync_log(engagement_id=engagement_id, limit=100)
        return {
            "engagement": engagement,
            "queue": queue_rows,
            "sync_log": log,
            "crm_record_id": (engagement.get("data") or {}).get(CRM_RECORD_ID_FIELD),
            "sync_state": as_text((engagement.get("data") or {}).get(SYNC_STATE_FIELD))
            or "pending",
        }

    def sync_log(
        self,
        room_id: str | None = None,
        *,
        outcome: str | None = None,
        vendor: str | None = None,
        engagement_id: str | None = None,
        needs_manual_update: bool | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """The room's Sync log / Errors admin panel.

        The research's step 5 says a failure "surfaces the failure in the admin **Sync
        log** panel", so this is the panel, and it filters on the same JSON paths every
        other filter in this store uses - which means a new outcome or a new vendor needs
        no change here.
        """
        rows = self.book.sync_log(
            room_id,
            outcome=outcome,
            vendor=vendor,
            engagement_id=engagement_id,
            needs_manual_update=needs_manual_update,
            limit=limit,
        )
        summary: dict[str, int] = {}
        for row in rows:
            state = as_text((row.get("data") or {}).get("outcome"))
            summary[state] = summary.get(state, 0) + 1
        return {
            "room_id": room_id,
            "count": len(rows),
            "summary": {
                "synced": summary.get("synced", 0),
                "failed": summary.get("failed", 0),
                "needs_manual_update": sum(
                    1 for row in rows if (row.get("data") or {}).get("needs_manual_update")
                ),
            },
            "entries": rows,
        }

    def sync_log_entry(self, log_id: str) -> dict[str, Any]:
        row = self.book.sync_log_entry(log_id)
        if row is None:
            raise EngagementSyncError(f"sync log entry {log_id} not found")
        return row

    def require_configured(self, room_id: str) -> None:
        """Raise :class:`SyncNotConfigured` when a drain could not send anything.

        The distinct 428: "this installation is not set up to answer it yet" is a
        different message for a client than "you got the request wrong", and a page can
        only render the right one if the two are different statuses.
        """
        self.book.require_room(room_id)
        connectors = self.book.connectors()
        scoped = [row for row in connectors if str(row.get("room_id") or "") == str(room_id)]
        candidates = scoped or [row for row in connectors if not row.get("room_id")]
        if not candidates:
            raise SyncNotConfigured(
                "no CRM connector applies to this room, so there is nowhere to write; register "
                "one scoped to the room, or a default for the installation, first"
            )
        if not any(row.get("enabled") for row in candidates):
            raise SyncNotConfigured(
                "every CRM connector that applies to this room is switched off, so there is "
                "nowhere to write"
            )


def _tally(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value or "unknown"] = counts.get(value or "unknown", 0) + 1
    return counts


def _read(
    *, engagement: Mapping[str, Any], row: Mapping[str, Any], source: str
) -> tuple[bool, Any, str]:
    """Resolve one canonical field on a stored engagement row.

    The row is re-shaped into the ``{envelope, data}`` form :func:`read_source` expects, so
    the feed resolves a field the same way a field map does.
    """
    return read_source(
        {"id": row.get("id"), "room_id": row.get("room_id"), "data": dict(engagement)}, source
    )


def _number(value: Any) -> float | int | None:
    """A number for a figure, or ``None``. Booleans are not numbers here either."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = as_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return None
