"""The façade the HTTP layer calls.

Everything the researched user flow does, in the order the flow does it:

    1. Admin picks a date range / full-history option         -> :meth:`BackfillEngine.start`
    2. Ask the CRM for a job, or a delta/paged read           -> the adapter's ``start``
    3. Store the job id or delta token as a cursor            -> :mod:`dsr.crm_backfill.cursors`
    4. Poll; the CRM returns batches; write rows in pages     -> :meth:`BackfillEngine.poll`
    5. Restart from the stored cursor: no duplicates, no gaps -> :meth:`BackfillEngine.resume`
    6. Progress as a percentage, with a per-run log           -> :meth:`progress`, :meth:`events`

The property the research insists on - "If the job or the room crashes mid-run,
the room restarts from the stored cursor - no duplicates, no gaps" - is not a
claim this module makes, it is an ordering it keeps. Within one page cycle:

    write rows -> then advance the cursor

A crash between those two steps replays the page, and the replay is absorbed
twice over: the replica is upserted on the vendor's key, and a row whose mapped
payload is unchanged is not written at all. A crash *after* the cursor write
loses nothing, because the page is already landed.

``source`` is a required keyword on every writing method. A hardcoded path inside
a domain method is a defect: an audit row naming a route the app has stopped
serving is worse than no audit row, because it looks authoritative, and that
class of bug has shipped in this codebase before.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from dsr.crm_backfill import cursors, quota, transform, vendors
from dsr.crm_backfill.errors import (
    CursorExpired,
    PlanError,
    QuotaExceeded,
    RunNotFound,
    RunStateError,
    UnknownConnection,
    UnkeyedRow,
    UnsupportedVendor,
)
from dsr.crm_backfill.inferences import describe as describe_inferences
from dsr.crm_backfill.plan import (
    bulk_threshold,
    choose_strategy,
    estimate_calls,
    normalise_direction,
    normalise_field_map,
    normalise_page_size,
    normalise_poll_interval,
    normalise_scope,
)
from dsr.crm_backfill.vocabulary import (
    NO_SLA_QUOTE,
    RUN_STATES,
    TERMINAL_STATES,
    VENDORS,
    describe as describe_vocabulary,
)
from dsr.store import RecordStore, parse_where

#: The collections this package owns. Named apart from every other feature's,
#: so a run can never be confused with a record another workflow wrote.
CONNECTIONS = "crm_backfill_connection"
RUNS = "crm_backfill_run"
EVENTS = "crm_backfill_event"
REPLICA = "crm_replica"
CURSORS_STORE = "crm_backfill_cursor"

#: The counters a run carries. Every one of them is a number an operator can act
#: on, and ``rows_unchanged`` is in the list on purpose: a run whose every row
#: came back unchanged is a run that proved there is nothing to do, and it should
#: be visible rather than indistinguishable from a run that wrote nothing.
COUNTERS: tuple[str, ...] = (
    "pages",
    "polls",
    "resumes",
    "replans",
    "rows_seen",
    "rows_written",
    "rows_created",
    "rows_updated",
    "rows_unchanged",
    "rows_rejected",
    "api_calls",
)

#: How many times a run may reopen its job because the volume it was promised
#: turned out to call for a different strategy. One: the decision is made from
#: the vendor's own record count, so a second disagreement would mean the count
#: is moving under the run, and reopening forever would be a loop.
MAX_REPLANS = 1


def new_counters() -> dict[str, Any]:
    counters: dict[str, Any] = {name: 0 for name in COUNTERS}
    counters["rows_total"] = None
    return counters


class BackfillEngine:
    """The workflow, over one audited store.

    Built per request from ``StoreDep`` rather than held on ``app.state``,
    because an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the plugin host exists to make unnecessary. It holds
    nothing but the store handle and the vendor registry, both of which are
    constructor arguments, so a test constructs one with rows of its own.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        registry: Mapping[str, vendors.VendorAdapter] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.registry = dict(registry) if registry is not None else vendors.default_registry()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- clock -------------------------------------------------------------- #

    def now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def _at(self) -> str:
        return self.now().isoformat()

    # -- reference data ----------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data."""
        return describe_vocabulary()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return describe_inferences()

    def catalog(self) -> dict[str, Any]:
        """The registered vendors, their strategies, and the volume rule.

        The research's extensibility note says a third party "can add a new
        vendor by implementing only 'create job' and 'read page'". This endpoint
        is where a reader sees which vendors exist and what each one implements,
        which is the difference between the note being true of the code and true
        only of the documentation.
        """
        listed = []
        for vendor, adapter in sorted(self.registry.items()):
            listed.append(
                {
                    "vendor": vendor,
                    "label": adapter.label,
                    "strategies": list(adapter.strategies),
                    "supports_push": adapter.supports_push,
                    "required_scopes": list(adapter.required_scopes),
                    "cursor_kind": adapter.cursor_kind,
                    "mechanism": VENDORS.get(vendor, {}).get("mechanism", ""),
                    "documented": vendor in VENDORS,
                }
            )
        return {
            "count": len(listed),
            "bulk_threshold_records": bulk_threshold(),
            "no_sla_quote": NO_SLA_QUOTE,
            "interface": ["start (create job)", "read_page (read page)", "submit_page (push only)"],
            "vendors": listed,
        }

    # -- connections -------------------------------------------------------- #

    def create_connection(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a CRM connection: which vendor, and what its token can reach.

        The fields the vendor's own rules need live here rather than on the run,
        because they are properties of the account and not of one backfill: a
        ``crm.export`` grant, which names count as standard objects, the daily
        call limit, and the time zone whose midnight the limit resets on.
        """
        data = _connection_payload(payload)
        record = self.store.create(CONNECTIONS, data, room_id=room_id, actor=actor, source=source)
        return self.connection_view(record)

    def connections(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        records = self.store.list(CONNECTIONS, room_id=room_id, limit=limit)
        return [self.connection_view(record) for record in records]

    def connection(self, connection_id: str) -> dict[str, Any]:
        return self.connection_view(self._connection_record(connection_id))

    def connection_view(self, record: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """A connection as the wizard reads it: what it can do, and what is left of today."""
        moment = now or self.now()
        data = record.get("data") or {}
        adapter = self.registry.get(str(data.get("vendor") or ""))
        return {
            **record,
            "supported": adapter is not None,
            "label": adapter.label if adapter else str(data.get("vendor") or ""),
            "strategies": list(adapter.strategies) if adapter else [],
            "supports_push": bool(adapter.supports_push) if adapter else False,
            "required_scopes": list(adapter.required_scopes) if adapter else [],
            "object_addressing": (
                "object_type_id" if (adapter and adapter.addresses_objects_by_id) else "object_name"
            ),
            "quota": quota.report(data, data.get("quota") or {}, moment),
        }

    # -- runs --------------------------------------------------------------- #

    def start(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Open a backfill. The wizard's **Start**.

        Connects three researched steps into one call, because doing them one
        route at a time would leave a room able to hold a half-opened backfill:
        the connection, the strategy, then the job, then the cursor that makes
        the job resumable. Each of those decisions is written to the run's log
        as it is taken, so the run record is a transcript rather than a summary.

        A live cursor for this connection is adopted rather than replaced, which
        is the researched behaviour: "the room restarts from the stored cursor".
        Pass ``from_scratch: true`` - or pick the full-history option - to
        deliberately ignore it.
        """
        if not str(room_id or "").strip():
            raise PlanError("room_id is required; a backfill lands in a room's replica")

        connection = self._connection_record(str(payload.get("connection_id") or ""))
        adapter = vendors.require_registered(
            str(connection["data"].get("vendor") or ""), self.registry
        )
        moment = self.now()
        data = connection["data"]

        direction = normalise_direction(payload.get("direction"))
        vendors.require_direction(adapter, direction)
        scope = normalise_scope(payload.get("scope"), moment)
        field_map = normalise_field_map(payload.get("field_map"))
        page_size = normalise_page_size(payload.get("page_size"), cursors.default_page_size())
        poll_interval = normalise_poll_interval(payload.get("poll_interval_seconds"))
        object_name, object_type_id, properties = _object(payload)
        estimated = _optional_int(payload.get("estimated_records"), "estimated_records")

        from_scratch = bool(payload.get("from_scratch"))
        if from_scratch and scope["kind"] != "full_history":
            raise PlanError(
                "from_scratch ignores the stored cursor, so pair it with the full_history scope; a "
                "range re-read from the beginning would write rows the cursor said were already read"
            )

        stored = self.stored_cursor(room_id, str(connection["id"]))
        if from_scratch:
            stored = None
        if stored and stored.get("cursor"):
            cursors.require_resumable(stored, moment, _expiry_days(connection))

        plan: dict[str, Any] = {
            "room_id": room_id,
            "vendor": adapter.vendor,
            "connection_id": str(connection["id"]),
            "direction": direction,
            "scope": scope,
            "object_name": object_name,
            "object_type_id": object_type_id,
            "properties": properties,
            "page_size": page_size,
            "poll_interval_seconds": poll_interval,
            "ready_after": _optional_int(payload.get("ready_after"), "ready_after") or 1,
            "attempt": 0,
            # The cursor this room already holds, offered to the adapter. Only a
            # change-stream token can be picked up by a *new* job - a Bulk job id
            # and an export id name a job that is finished or abandoned, so a new
            # run against those vendors opens a new one and the adoption shows up
            # in the strategy instead. See CURSOR_KINDS.
            "cursor": (stored or {}).get("cursor"),
        }

        findings = list(adapter.preflight(data, plan))
        strategy, reason = choose_strategy(adapter, estimated_records=estimated, live_cursor=stored)
        vendors.require_supported(adapter, strategy)

        allowance = quota.check(
            data,
            data.get("quota") or {},
            moment,
            estimated_calls=estimate_calls(total=estimated, page_size=page_size),
        )
        if allowance["verdict"] == "exceeds_remaining":
            raise QuotaExceeded(
                f"a run of {estimated} record(s) is about {allowance['estimated_calls']} vendor "
                f"call(s) and only {allowance['remaining']} of this connection's "
                f"{allowance['daily_limit']} daily calls are left. The limit resets at "
                f"{allowance['resets_at']}, and a job cut off mid-page would leave a cursor parked "
                "against a window that is about to close."
            )

        run_data: dict[str, Any] = {
            "run_key": "",
            "direction": direction,
            "strategy": strategy,
            "strategy_reason": reason,
            "vendor": adapter.vendor,
            "connection_id": str(connection["id"]),
            "object": {
                "object_name": object_name,
                "object_type_id": object_type_id,
                "properties": list(properties),
            },
            "scope": scope,
            "field_map": field_map,
            "page_size": page_size,
            "poll_interval_seconds": poll_interval,
            "ready_after": plan["ready_after"],
            "state": "created",
            "job": {},
            "cursor": None,
            "counters": new_counters(),
            "quota": allowance,
            "findings": findings,
            "adopted_cursor": bool(stored and stored.get("cursor")),
            "failure": None,
            "next_poll_at": None,
            "last_advanced_at": None,
            "completed_at": None,
            "started_at": self._at(),
        }
        record = self.store.create(RUNS, run_data, room_id=room_id, actor=actor, source=source)
        record = self.store.update(
            record["id"], {"run_key": record["id"]}, actor=actor, source=source
        )

        self.log(
            record,
            "run_created",
            f"backfill opened for {adapter.vendor} over {scope['kind']} "
            f"({scope['from'] or 'unbounded'} to {scope['to']})",
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "connection_checked",
            "; ".join(findings) if findings else f"connection {connection['id']} is usable",
            actor=actor,
            source=source,
            data={"findings": findings, "allowance": allowance},
        )
        self.log(
            record, "strategy_selected", reason["detail"], actor=actor, source=source, data=reason
        )
        if run_data["adopted_cursor"]:
            self.log(
                record,
                "cursor_adopted",
                f"continuing from the stored {stored.get('kind')} recorded at {stored.get('updatedAt')}",
                actor=actor,
                source=source,
                data={"cursor": stored},
            )

        if findings:
            # The run exists so the findings are on a record, and it is left in
            # `created` rather than driven forward: a job opened without the
            # grant would fail at the vendor after the fact, which is the state
            # the research's scope rule exists to prevent.
            return self.present(record)

        return self._open_job(record, connection, adapter, plan, actor=actor, source=source)

    def runs(
        self,
        *,
        room_id: str | None = None,
        state: str | None = None,
        where: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Runs, newest first. Every filter is a JSON path in the run's own payload."""
        filters: dict[str, Any] = {}
        if state:
            if state not in RUN_STATES:
                raise PlanError(f"state {state!r} is not one of {', '.join(RUN_STATES)}")
            filters["state"] = state
        if where:
            try:
                filters.update(parse_where(where))
            except ValueError as exc:
                raise PlanError(str(exc)) from exc
        if filters:
            records = self.store.find(RUNS, filters, limit=limit)
        else:
            records = self.store.list(RUNS, room_id=room_id, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return [self.present(record) for record in records]

    def run(self, room_id: str, run_id: str) -> dict[str, Any]:
        return self.present(self._read_run(run_id, room_id=room_id))

    def events(self, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """The per-run log, oldest first.

        Oldest first on purpose: a log an operator reads to answer "what happened
        to this backfill" is a story, and a story told backwards is not one. The
        newest ``limit`` lines are returned, so a run with a very long log shows
        its end rather than refusing to be read.

        Ordered by the line number :meth:`log` stamps, never by ``id``. ``id`` is
        a uuid4, so using it as the tie-break made equally-timestamped lines come
        back in an arbitrary order; the host's own tie-break is on ``rowid`` and
        cannot be reached from here, because this sorts the hydrated records
        itself. See the note in :meth:`log`.

        The number is read from ``data``, not from the envelope. ``seq`` is
        payload, like everything else a feature owns, and reading it off the
        envelope yields ``None`` for every line - so the tie-break is a constant
        zero, the sort is a no-op, and the order silently falls back to whatever
        the store returned. That is a quieter failure than a wrong order: the
        log still looks like a log.
        """
        records = self.store.find(EVENTS, {"run_id": run_id}, limit=limit)
        return sorted(
            records,
            key=lambda record: (
                str(record.get("created_at") or ""),
                int((record.get("data") or {}).get("seq") or 0),
            ),
        )

    # -- the page cycle ----------------------------------------------------- #

    def poll(
        self,
        room_id: str,
        run_id: str,
        *,
        actor: str | None = None,
        source: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """One step of the poller the research describes.

        "Dataverse's change-tracking token and Salesforce's job queue both
        require polling, so the room runs a poller on a fixed interval." Nothing
        here starts a thread: a scheduler calls this route, and a call made
        before the run's interval has elapsed is reported as not due rather than
        obeyed, because a room that could be polled without limit would be a room
        hammering a vendor that documents a daily limit.

        ``force`` skips the interval. It is the same flag ``resume`` sets, and it
        exists because the researched recovery story is a room that restarted,
        and a room that restarted has not been respecting anybody's interval.
        """
        record = self._read_run(run_id, room_id=room_id)
        data = record["data"]
        state = str(data.get("state"))
        if state in TERMINAL_STATES:
            raise RunStateError(
                f"run {run_id} is {state}; there is nothing left to poll. Its log and its rows are "
                "still there, and a new backfill is how to read the same history again."
            )

        if not force and self._not_due(data):
            return {
                "run": self.present(record),
                "advanced": False,
                "reason": "not_due",
                "next_poll_at": data.get("next_poll_at"),
                "detail": (
                    f"the poller runs on a fixed interval of {data.get('poll_interval_seconds')}s and "
                    "this run is not due yet"
                ),
            }

        connection = self._connection_record(str(data["connection_id"]))
        adapter = vendors.require_registered(
            str(connection["data"].get("vendor") or ""), self.registry
        )
        moment = self.now()

        if data.get("direction") == "push":
            return self._push_page(record, connection, adapter, moment, actor=actor, source=source)
        return self._pull_page(record, connection, adapter, moment, actor=actor, source=source)

    def resume(
        self,
        room_id: str,
        run_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Continue a run from the cursor it stored.

        The researched recovery step, made callable. It forces a poll and counts
        the resume, so a run that has been restarted four times says so in its
        own totals rather than in somebody's memory.

        A cursor the vendor has stopped answering for is refused here and the run
        is marked ``stalled``. Silently reading the whole range again would be
        the one thing that makes "no duplicates, no gaps" untrue, so this raises
        and says which backfill to open instead.
        """
        record = self._read_run(run_id, room_id=room_id)
        data = record["data"]
        state = str(data.get("state"))
        if state in TERMINAL_STATES:
            raise RunStateError(
                f"run {run_id} is {state}, so it cannot be resumed"
                + (
                    ". Its cursor is past the window the vendor will still answer for; open a new "
                    "full-history backfill to read the range again."
                    if state == "stalled"
                    else ""
                )
            )
        connection = self._connection_record(str(data["connection_id"]))
        held = data.get("cursor")
        if held and held.get("cursor"):
            try:
                cursors.require_resumable(held, self.now(), _expiry_days(connection))
            except CursorExpired as exc:
                self._stall(record, str(exc), actor=actor, source=source)
                raise

        record = self._patch(
            record,
            {
                "counters": {
                    **(data.get("counters") or new_counters()),
                    "resumes": int((data.get("counters") or {}).get("resumes") or 0) + 1,
                }
            },
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "run_resumed",
            (
                f"restarted from the stored {held.get('kind')} cursor at {held.get('cursor')}"
                if held and held.get("cursor")
                else "restarted with no stored cursor; the run reads from the beginning again"
            ),
            actor=actor,
            source=source,
            data={"cursor": held},
        )
        return self.poll(room_id, run_id, actor=actor, source=source, force=True)

    def cancel(
        self,
        room_id: str,
        run_id: str,
        *,
        actor: str | None = None,
        source: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Stop a run where it is. The cursor stays: a later backfill resumes from it.

        A stop of the run, deliberately not a delete. The rows already landed are
        real and stay; the cursor stays so that starting a new backfill against
        the same connection continues rather than re-reading, which is the
        property a cancellation must not destroy.
        """
        record = self._read_run(run_id, room_id=room_id)
        data = record["data"]
        state = str(data.get("state"))
        if state in TERMINAL_STATES:
            raise RunStateError(
                f"run {run_id} is already {state}; cancelling it would say something untrue about "
                "what happened"
            )
        record = self._patch(
            record,
            {"state": "cancelled", "completed_at": self._at(), "next_poll_at": None},
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "run_cancelled",
            reason
            or "cancelled by an operator; the stored cursor is kept so a later backfill resumes",
            actor=actor,
            source=source,
        )
        return self.present(record)

    # -- cursors, replica, summary ------------------------------------------ #

    def stored_cursor(self, room_id: str, connection_id: str) -> dict[str, Any] | None:
        """The cursor this room holds for this connection, in the researched shape."""
        record = self._cursor_record(room_id, connection_id)
        return dict(record["data"]) if record else None

    def cursors(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every stored cursor, each with its expiry verdict.

        The connection ids come from the connections rather than from the cursor
        rows, so a connection that no longer resolves does not leave a cursor
        looking like it has no expiry rule - it is reported as an unknown
        connection, which is a different thing and a visible one.
        """
        now = self.now()
        listed = []
        for record in self.store.list(CURSORS_STORE, room_id=room_id, limit=limit):
            data = dict(record.get("data") or {})
            connection = self._maybe_connection(str(data.get("connectionId") or ""))
            expiry = _expiry_days(connection) if connection is not None else None
            verdict = cursors.describe(data, now, expiry)
            verdict["connection_known"] = connection is not None
            listed.append({"id": record["id"], "room_id": record.get("room_id"), **data, "expiry": verdict})
        return listed

    def replica(
        self,
        *,
        room_id: str | None = None,
        connection_id: str | None = None,
        where: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The room's replica rows. Filterable on any JSON path, like every other listing."""
        filters: dict[str, Any] = {}
        if connection_id:
            filters["connection_id"] = connection_id
        if where:
            try:
                filters.update(parse_where(where))
            except ValueError as exc:
                raise PlanError(str(exc)) from exc
        if filters:
            records = self.store.find(REPLICA, filters, limit=limit)
        else:
            records = self.store.list(REPLICA, room_id=room_id, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the room, and the cursors they are waiting on.

        Counted over this room's own runs, so a room's header says what happened
        in that room rather than what happened in the product.
        """
        runs = self.runs(room_id=room_id, limit=200)
        by_state = {state: 0 for state in RUN_STATES}
        totals = {name: 0 for name in COUNTERS}
        stalled: list[dict[str, Any]] = []
        for run in runs:
            state = str(run["data"].get("state"))
            by_state[state] = by_state.get(state, 0) + 1
            counters = run["data"].get("counters") or {}
            for name in totals:
                totals[name] += int(counters.get(name) or 0)
            if state == "stalled":
                stalled.append({"id": run["id"], "cursor": run["data"].get("cursor")})
        listed_cursors = self.cursors(room_id=room_id)
        replica_limit = 1000
        replica_count = len(self.replica(room_id=room_id, limit=replica_limit))
        return {
            "room_id": room_id,
            "runs": len(runs),
            "by_state": by_state,
            "totals": totals,
            # The store caps a listing at a thousand rows, so this is a floor
            # rather than a count on a large room. Saying so beats a number that
            # quietly stops at 1,000 and reads as an exact one.
            "replica_rows": replica_count,
            "replica_rows_truncated": replica_count >= replica_limit,
            "stored_cursors": len(listed_cursors),
            "stalled": stalled,
            "no_sla_quote": NO_SLA_QUOTE,
        }

    # -- presentation ------------------------------------------------------- #

    def present(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A run as the wizard reads it: state, progress, cursor, and counts."""
        data = dict(record.get("data") or {})
        counters = dict(data.get("counters") or new_counters())
        state = str(data.get("state"))
        return {
            **record,
            "id": str(record.get("id")),
            "room_id": record.get("room_id"),
            "data": data,
            "counters": counters,
            "progress": progress(counters, state),
            "terminal": state in TERMINAL_STATES,
        }

    # -- internals: the page cycle ------------------------------------------ #

    def _pull_page(
        self,
        record: Mapping[str, Any],
        connection: Mapping[str, Any],
        adapter: vendors.VendorAdapter,
        moment: datetime,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        data = dict(record["data"])
        counters = {**(data.get("counters") or new_counters()), "polls": int((data.get("counters") or {}).get("polls") or 0) + 1}
        record = self._patch(record, {"counters": counters}, actor=actor, source=source)

        plan = self._plan(record)
        job = dict(data.get("job") or {})

        # The vendor's own expiry rule, applied before the call rather than after
        # the exception. Dataverse "throws an exception"; the room is trying not
        # to be the thing that finds out.
        if (data.get("cursor") or {}).get("kind") == "data_token" and data.get("cursor"):
            try:
                cursors.require_resumable(data.get("cursor"), moment, _expiry_days(connection))
            except CursorExpired as exc:
                self._stall(record, str(exc), actor=actor, source=source)
                raise

        spent = self._spend(connection, record, moment, reason="page.read", actor=actor, source=source)
        if spent is not None:
            return spent

        try:
            page = adapter.read_page(
                connection["data"],
                plan,
                job,
                (data.get("cursor") or {}).get("cursor"),
                int(counters.get("polls") or 0),
            )
        except CursorExpired as exc:
            self._stall(record, str(exc), actor=actor, source=source)
            raise
        except Exception as exc:  # a vendor that breaks is a run that failed, not a 500
            return self._vendor_broke(record, exc, actor=actor, source=source)

        job = {
            **job,
            "id": page.cursor or job.get("id"),
            "state": page.job_state,
            "total": page.total if page.total is not None else job.get("total"),
            "processed": page.processed if page.processed is not None else job.get("processed"),
            "ready_after": job.get("ready_after", plan.get("ready_after", 1)),
            "cursor_kind": adapter.cursor_kind,
        }

        if page.job_state in ("failed", "FAILED"):
            # Checked before the empty-page branch: a vendor that reports a
            # failure reports it on a page with no rows, and treating "no rows"
            # as "nothing more to read" would report a failed export as a
            # complete one.
            return self._vendor_failed(record, page, job, actor=actor, source=source)
        if not page.rows:
            if page.more:
                return self._not_ready(record, page, job, actor=actor, source=source)
            return self._complete(record, job, counters, actor=actor, source=source)

        return self._land(record, connection, adapter, page, job, counters, actor=actor, source=source)

    def _land(
        self,
        record: Mapping[str, Any],
        connection: Mapping[str, Any],
        adapter: vendors.VendorAdapter,
        page: vendors.VendorPage,
        job: dict[str, Any],
        counters: dict[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Write one page of rows, then advance the cursor. In that order."""
        data = dict(record["data"])
        room_id = str(record.get("room_id") or "")
        plan = self._plan(record)
        at = self._at()
        key_field = str(
            connection["data"].get("replica_key_field") or transform.DEFAULT_KEY_FIELD
        )

        counters = {**counters, "pages": int(counters.get("pages") or 0) + 1}
        counters["rows_seen"] = int(counters.get("rows_seen") or 0) + len(page.rows)
        counters["rows_total"] = page.total if page.total is not None else job.get("total")

        created: list[dict[str, Any]] = []
        updates: list[tuple[str, dict[str, Any]]] = []
        unchanged = 0
        rejected: list[dict[str, Any]] = []

        # One lookup for the whole page rather than one per row. This is a
        # measured fix, not a tidy-up: the store's `find` resolves a condition as
        # a correlated EXISTS over `record_index`, and with no index on
        # `(path, value_text)` that is a scan of every index row for every
        # candidate record. The seed profile put 188 of its 190 seconds in 2,410
        # such lookups - about 77ms each - and a page of 1,000 rows asked the
        # same question a thousand times. `list` is a plain ordered scan with a
        # LIMIT, so a handful of them per page is nothing.
        wanted = []
        seen_keys = set()
        for row in page.rows:
            try:
                key = transform.external_id(row, key_field)
            except UnkeyedRow:
                continue
            if key not in seen_keys:
                seen_keys.add(key)
                wanted.append(key)
        existing_by_key = self._replica_index(room_id, str(connection["id"]), seen_keys)

        for row in page.rows:
            try:
                incoming = transform.replica_row(
                    row,
                    field_map=plan.get("field_map") or {},
                    key_field=key_field,
                    vendor=adapter.vendor,
                    connection_id=str(connection["id"]),
                    room_id=room_id,
                    object_name=plan.get("object_name"),
                    run_id=str(record["id"]),
                    at=at,
                )
            except UnkeyedRow as exc:
                # The page carries on. One unkeyable row must not stall the whole
                # range for good, and the row is counted and named so the
                # operator can decide whether the data or the key field is wrong.
                rejected.append({"reason": str(exc), "row": _summarise_row(row)})
                continue
            existing = existing_by_key.get(str(incoming["external_id"]))
            # `existing` is the record; the merge compares *payloads*, and the
            # envelope around a payload is not part of what the row says.
            merged, changed = transform.merge(
                existing["data"] if existing is not None else None, incoming
            )
            if existing is None:
                created.append(merged)
            elif changed:
                updates.append((str(existing["id"]), merged))
            else:
                unchanged += 1

        if created:
            # One transaction, one audit row: a page is a page, and a log with
            # 5,000 entries in it says nothing a log with one entry does not.
            self.store.bulk_create(
                REPLICA, created, room_id=room_id or None, actor=actor, source=source
            )
        for record_id, merged in updates:
            self.store.update(record_id, merged, actor=actor, source=source)

        counters["rows_created"] = int(counters.get("rows_created") or 0) + len(created)
        counters["rows_updated"] = int(counters.get("rows_updated") or 0) + len(updates)
        counters["rows_unchanged"] = int(counters.get("rows_unchanged") or 0) + unchanged
        counters["rows_rejected"] = int(counters.get("rows_rejected") or 0) + len(rejected)
        counters["rows_written"] = int(counters.get("rows_written") or 0) + len(created) + len(updates)
        counters["api_calls"] = int(counters.get("api_calls") or 0) + 1

        more = bool(page.more)
        next_poll = (
            self.now() + timedelta(seconds=int(plan.get("poll_interval_seconds") or 0))
        ).isoformat()
        record = self._patch(
            record,
            {
                "counters": counters,
                "job": job,
                "state": "running" if more else "complete",
                "next_poll_at": next_poll if more else None,
                "last_advanced_at": at,
                "completed_at": None if more else at,
                "cursor": self._advance(
                    record, connection, adapter, page, counters, actor=actor, source=source
                ),
            },
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "page_written",
            page.detail
            or f"wrote {len(page.rows)} row(s): {len(created)} new, {len(updates)} changed, {unchanged} unchanged",
            actor=actor,
            source=source,
            data={
                "page": counters["pages"],
                "rows": len(page.rows),
                "created": len(created),
                "updated": len(updates),
                "unchanged": unchanged,
                "more": more,
            },
        )
        if rejected:
            self.log(
                record,
                "rows_rejected",
                f"{len(rejected)} row(s) carried no key and were not stored; the page carried on so "
                "one unkeyable row cannot stall the range",
                actor=actor,
                source=source,
                data={"rows": rejected[:20], "count": len(rejected)},
            )
        if more:
            self.log(
                record,
                "run_paused",
                f"waiting on the poller; next attempt at {next_poll}",
                actor=actor,
                source=source,
            )
        else:
            self.log(
                record,
                "run_complete",
                "every row in the scope has been read",
                actor=actor,
                source=source,
            )
        return {
            "run": self.present(record),
            "advanced": True,
            "reason": "page_written" if more else "run_complete",
            "page": {
                "rows": len(page.rows),
                "created": len(created),
                "updated": len(updates),
                "unchanged": unchanged,
                "rejected": len(rejected),
            },
            "cursor": record["data"].get("cursor"),
            "detail": page.detail,
        }

    def _push_page(
        self,
        record: Mapping[str, Any],
        connection: Mapping[str, Any],
        adapter: vendors.VendorAdapter,
        moment: datetime,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The researched reverse direction: room replica out to the CRM.

        The same page cycle with the source and the sink swapped, which is what
        the data flow's parenthetical asks for. Rows are read from the replica in
        a stable order, sent through the map in reverse, and acknowledged by the
        vendor's own submit. The cursor advances the same way, so a restart
        resumes at the same offset rather than sending the first page twice.
        """
        data = dict(record["data"])
        plan = self._plan(record)
        counters = {**(data.get("counters") or new_counters()), "polls": int((data.get("counters") or {}).get("polls") or 0) + 1}
        record = self._patch(record, {"counters": counters}, actor=actor, source=source)
        job = dict(data.get("job") or {})

        spent = self._spend(connection, record, moment, reason="page.submit", actor=actor, source=source)
        if spent is not None:
            return spent

        page_size = max(1, int(plan.get("page_size") or 1))
        offset = int(counters.get("rows_seen") or 0)
        stored = self.store.list(
            REPLICA,
            room_id=str(record.get("room_id") or "") or None,
            order_by="id",
            descending=False,
            limit=page_size,
            offset=offset,
        )
        if not stored:
            return self._complete(record, job, counters, actor=actor, source=source)

        rows = transform.outbox(
            stored,
            field_map=plan.get("field_map") or {},
            key_field=str(
                connection["data"].get("replica_key_field") or transform.DEFAULT_KEY_FIELD
            ),
        )
        page_number = int(counters.get("pages") or 0) + 1
        try:
            acknowledgement = adapter.submit_page(connection["data"], plan, job, rows, page_number)
        except Exception as exc:
            return self._vendor_broke(record, exc, actor=actor, source=source)

        counters = {**counters, "pages": page_number, "api_calls": int(counters.get("api_calls") or 0) + 1}
        counters["rows_seen"] = offset + len(rows)
        counters["rows_written"] = int(counters.get("rows_written") or 0) + len(rows)
        if counters.get("rows_total") is None:
            counters["rows_total"] = len(self.replica(room_id=str(record.get("room_id") or ""), limit=1000))
        next_poll = (moment + timedelta(seconds=int(plan.get("poll_interval_seconds") or 0))).isoformat()
        more = len(stored) == page_size
        page = vendors.VendorPage(
            cursor=str(counters["rows_seen"]),
            job_state="running" if more else "complete",
            total=counters.get("rows_total"),
            processed=counters["rows_seen"],
            more=more,
            detail=f"submitted {len(rows)} replica row(s) to {adapter.vendor} as page {page_number}",
        )
        record = self._patch(
            record,
            {
                "counters": counters,
                "job": {**job, **acknowledgement, "cursor_kind": "paging_cookie"},
                "state": "running" if more else "complete",
                "next_poll_at": next_poll if more else None,
                "last_advanced_at": self._at(),
                "completed_at": None if more else self._at(),
                "cursor": self._advance(
                    record,
                    connection,
                    adapter,
                    page,
                    counters,
                    actor=actor,
                    source=source,
                    kind="paging_cookie",
                ),
            },
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "page_written",
            page.detail,
            actor=actor,
            source=source,
            data={
                "page": page_number,
                "rows": len(rows),
                "more": more,
                "acknowledgement": acknowledgement,
            },
        )
        if more:
            self.log(
                record,
                "run_paused",
                f"waiting on the poller; next attempt at {next_poll}",
                actor=actor,
                source=source,
            )
        else:
            self.log(
                record,
                "run_complete",
                "every replica row has been submitted",
                actor=actor,
                source=source,
            )
        return {
            "run": self.present(record),
            "advanced": True,
            "reason": "page_written" if more else "run_complete",
            "page": {
                "rows": len(rows),
                "created": 0,
                "updated": len(rows),
                "unchanged": 0,
                "rejected": 0,
            },
            "cursor": record["data"].get("cursor"),
        }

    # -- internals: lifecycle ----------------------------------------------- #

    def _open_job(
        self,
        record: Mapping[str, Any],
        connection: Mapping[str, Any],
        adapter: vendors.VendorAdapter,
        plan: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Create the job, and take its first page if it already has one.

        The strategy is reconsidered here, once, against the vendor's own record
        count. The room said it did not know the volume, the vendor does, and the
        research's rule is written in terms of the volume - so a run that opened
        as an async job and turns out to hold forty rows reopens as a paged read
        rather than pretending the rule was satisfied. It happens only while
        nothing has been written, because after the first row lands there is
        nothing to reopen.
        """
        attempt = 0
        while True:
            attempt_plan = {**plan, "attempt": attempt}
            data = dict(record["data"])
            counters = dict(data.get("counters") or new_counters())
            moment = self.now()

            allowance = quota.report(
                connection["data"], connection["data"].get("quota") or {}, moment
            )
            if allowance["limited"] and int(allowance["remaining"] or 0) <= 0:
                raise QuotaExceeded(
                    f"{adapter.vendor} has no daily allowance left today and the window does not "
                    f"reset until {allowance['resets_at']}; a job opened now would be cut off "
                    "mid-page and leave a cursor parked against a closing window"
                )
            self._spend(connection, record, moment, reason="job.create", actor=actor, source=source)
            counters["api_calls"] = int(counters.get("api_calls") or 0) + 1
            record = self._patch(record, {"counters": counters}, actor=actor, source=source)

            try:
                page = adapter.start(connection["data"], attempt_plan)
            except Exception as exc:
                return self._vendor_broke(record, exc, actor=actor, source=source)

            job = {
                "id": page.cursor,
                "state": page.job_state,
                "total": page.total,
                "processed": page.processed or 0,
                "ready_after": int(attempt_plan.get("ready_after", 1)),
                "cursor_kind": adapter.cursor_kind,
                "attempt": attempt,
            }
            record = self._patch(
                record,
                {
                    "job": job,
                    "state": "running",
                    "next_poll_at": (
                        self.now() + timedelta(seconds=int(attempt_plan.get("poll_interval_seconds") or 0))
                    ).isoformat(),
                },
                actor=actor,
                source=source,
            )
            self.log(
                record,
                "job_created",
                page.detail or f"job {page.cursor} created",
                actor=actor,
                source=source,
                data=page.as_dict(),
            )

            wanted, why = _reconsider(record, adapter, page.total)
            if (
                wanted
                and attempt < MAX_REPLANS
                and not page.rows
                and int((record["data"].get("counters") or {}).get("rows_written") or 0) == 0
            ):
                self.log(
                    record,
                    "strategy_reconsidered",
                    why["detail"],
                    actor=actor,
                    source=source,
                    data=why,
                )
                record = self._patch(
                    record,
                    {
                        "strategy": wanted,
                        "strategy_reason": {"rule": "reconsidered_after_total", **why},
                        "findings": list(record["data"].get("findings") or []) + [why["detail"]],
                        "counters": {
                            **(record["data"].get("counters") or {}),
                            "replans": int((record["data"].get("counters") or {}).get("replans") or 0) + 1,
                        },
                    },
                    actor=actor,
                    source=source,
                )
                self.log(
                    record,
                    "run_replanned",
                    f"reopened as {wanted}; the previous job is abandoned and nothing was written "
                    "from it",
                    actor=actor,
                    source=source,
                )
                attempt += 1
                continue

            counters = dict(record["data"].get("counters") or {})
            if page.total is not None:
                # The vendor told us how much there is on the very first call, so
                # the percentage has a denominator from the moment the run opens
                # rather than from the second poll.
                counters["rows_total"] = page.total
                record = self._patch(record, {"counters": counters}, actor=actor, source=source)
            if page.rows:
                return self._land(
                    record,
                    connection,
                    adapter,
                    # The job's own sentence belongs on the job_created line, not
                    # repeated on the page line; an empty detail makes _land
                    # compose one that says what it wrote.
                    vendors.VendorPage(
                        rows=page.rows,
                        cursor=page.cursor,
                        job_state=page.job_state,
                        total=page.total,
                        processed=page.processed,
                        more=page.more,
                    ),
                    job,
                    counters,
                    actor=actor,
                    source=source,
                )["run"]
            if page.more:
                return self._not_ready(record, page, job, actor=actor, source=source)["run"]
            return self._complete(record, job, counters, actor=actor, source=source)["run"]

    def _not_ready(
        self,
        record: Mapping[str, Any],
        page: vendors.VendorPage,
        job: dict[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The job exists and has nothing to give yet. The researched poller waits."""
        data = dict(record["data"])
        next_poll = (
            self.now() + timedelta(seconds=int(data.get("poll_interval_seconds") or 0))
        ).isoformat()
        record = self._patch(
            record,
            {"job": {**job, "state": page.job_state}, "next_poll_at": next_poll, "state": "running"},
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "poll_job_not_ready",
            page.detail or f"the job is {page.job_state} and has no results yet",
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "run_paused",
            f"waiting on the poller; next attempt at {next_poll}",
            actor=actor,
            source=source,
        )
        return {
            "run": self.present(record),
            "advanced": False,
            "reason": "job_not_ready",
            "next_poll_at": next_poll,
            "detail": page.detail,
        }

    def _vendor_failed(
        self,
        record: Mapping[str, Any],
        page: vendors.VendorPage,
        job: dict[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self._patch(
            record,
            {
                "job": {**job, "state": page.job_state},
                "state": "failed",
                "failure": {
                    "reason": "vendor_reported_failure",
                    "detail": page.failure or page.detail,
                },
                "completed_at": self._at(),
                "next_poll_at": None,
            },
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "run_failed",
            page.failure or page.detail or "the vendor reported the job failed",
            actor=actor,
            source=source,
        )
        return {
            "run": self.present(record),
            "advanced": False,
            "reason": "vendor_reported_failure",
            "detail": page.failure or page.detail,
        }

    def _vendor_broke(
        self,
        record: Mapping[str, Any],
        exc: Exception,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """A vendor call that raised is a run that failed, not a 500.

        A CRM that answers with an error is an event in the workflow, and the
        researched flow ends with a per-run log precisely so that somebody can
        read what happened. Propagating would give the caller a stack trace and
        the operator nothing.
        """
        record = self._patch(
            record,
            {
                "state": "failed",
                "failure": {"reason": type(exc).__name__, "detail": str(exc)},
                "completed_at": self._at(),
                "next_poll_at": None,
            },
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "run_failed",
            f"the vendor call raised {type(exc).__name__}: {exc}",
            actor=actor,
            source=source,
        )
        return {
            "run": self.present(record),
            "advanced": False,
            "reason": "vendor_error",
            "detail": str(exc),
        }

    def _complete(
        self,
        record: Mapping[str, Any],
        job: Mapping[str, Any],
        counters: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """The researched last step of the data flow: the completion marker."""
        merged = dict(counters)
        if merged.get("rows_total") is None:
            merged["rows_total"] = job.get("total", merged.get("rows_seen"))
        record = self._patch(
            record,
            {
                "state": "complete",
                "completed_at": self._at(),
                "next_poll_at": None,
                "counters": merged,
                "job": {**dict(job), "state": "complete"},
            },
            actor=actor,
            source=source,
        )
        self.log(
            record,
            "run_complete",
            f"the range is fully read: {merged.get('rows_seen')} row(s) seen, "
            f"{merged.get('rows_written')} written, {merged.get('rows_unchanged')} already current",
            actor=actor,
            source=source,
        )
        return {
            "run": self.present(record),
            "advanced": False,
            "reason": "run_complete",
            "detail": "the run is complete; its cursor is kept for the next scheduled backfill",
        }

    def _stall(
        self,
        record: Mapping[str, Any],
        detail: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self._patch(
            record,
            {
                "state": "stalled",
                "next_poll_at": None,
                "failure": {"reason": "cursor_expired", "detail": detail},
            },
            actor=actor,
            source=source,
        )
        self.log(record, "run_stalled", detail, actor=actor, source=source)
        return record

    def _advance(
        self,
        record: Mapping[str, Any],
        connection: Mapping[str, Any],
        adapter: vendors.VendorAdapter,
        page: vendors.VendorPage,
        counters: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Store the cursor, in the researched shape, after the page has landed.

        The order is the whole point and it is not negotiable: the page write
        above already happened, and only now does the resume point move. A crash
        in between replays the page, and the replica's upsert absorbs the replay.
        """
        if page.cursor is None:
            return dict(record["data"].get("cursor") or {})
        minted = cursors.build(
            vendor=adapter.vendor,
            connection_id=str(connection["id"]),
            cursor=page.cursor,
            updated_at=self._at(),
            kind=kind or adapter.cursor_kind,
            run_id=str(record["id"]),
            pages_advanced=int(counters.get("pages") or 0),
            rows_written=int(counters.get("rows_written") or 0),
        )
        room_id = str(record.get("room_id") or "")
        existing = self._cursor_record(room_id, str(connection["id"]))
        if existing is None:
            self.store.create(
                CURSORS_STORE, minted, room_id=room_id or None, actor=actor, source=source
            )
        else:
            self.store.update(str(existing["id"]), minted, actor=actor, source=source)
        self.log(
            record,
            "cursor_saved",
            f"cursor advanced to {page.cursor} ({minted['kind']}); a restart from here reads nothing twice",
            actor=actor,
            source=source,
            data={"cursor": minted},
        )
        return minted

    def _spend(
        self,
        connection: Mapping[str, Any],
        record: Mapping[str, Any],
        moment: datetime,
        *,
        reason: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any] | None:
        """Charge one call against today's allowance.

        Returns a refusal when the window is spent, and ``None`` otherwise - the
        awkward shape of a function whose interesting outcome is the exception
        case, kept because it lets every caller read as "spend, then carry on".
        A connection with no declared limit is never charged: the research
        documents a daily limit for HubSpot and nothing for the others, and
        inventing one would be a claim nobody could check.
        """
        data = connection["data"]
        if quota.limit_for(data) is None:
            return None
        used = dict(data.get("quota") or {})
        started = quota.window_start(moment, quota.offset_for(data))
        calls = int(used.get("calls") or 0)
        if str(used.get("window_started_at") or "") != started.isoformat():
            # The window rolled over since the last charge, which is exactly what
            # happens across local midnight. Yesterday's calls are gone.
            calls = 0
        state = quota.report(
            data, {"window_started_at": started.isoformat(), "calls": calls}, moment
        )
        if int(state["remaining"] or 0) <= 0:
            waiting = quota.next_reset(moment, quota.offset_for(data))
            record = self._patch(
                record, {"next_poll_at": waiting.isoformat()}, actor=actor, source=source
            )
            self.log(
                record,
                "quota_refused",
                f"today's allowance is spent; the run waits for the window to reset at "
                f"{waiting.isoformat()} rather than failing",
                actor=actor,
                source=source,
            )
            return {
                "run": self.present(record),
                "advanced": False,
                "reason": "quota_spent",
                "next_poll_at": waiting.isoformat(),
                "detail": "the vendor's daily limit is spent; the poller will try again after the reset",
            }
        charged = {"window_started_at": started.isoformat(), "calls": calls + 1}
        self.store.update(
            str(connection["id"]), {"quota": charged}, actor=actor, source=source
        )
        self.log(
            record,
            "quota_charged",
            f"{reason} used call {charged['calls']} of {state['daily_limit']} today; the window "
            f"resets at {state['resets_at']}",
            actor=actor,
            source=source,
            data={"reason": reason, **charged},
        )
        return None

    # -- internals: lookups and writes -------------------------------------- #

    def _connection_record(self, connection_id: str) -> dict[str, Any]:
        if not connection_id:
            raise UnknownConnection(
                "connection_id is required; name the CRM the history comes from"
            )
        record = self.store.get(connection_id)
        if record is None or record["collection"] != CONNECTIONS:
            raise UnknownConnection(f"no backfill connection with id {connection_id!r}")
        return record

    def _maybe_connection(self, connection_id: str) -> dict[str, Any] | None:
        if not connection_id:
            return None
        record = self.store.get(connection_id)
        if record is None or record["collection"] != CONNECTIONS:
            return None
        return record

    def _read_run(self, run_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None or record["collection"] != RUNS:
            raise RunNotFound(f"no backfill run with id {run_id!r}")
        if room_id is not None and record.get("room_id") not in (None, room_id):
            raise RunNotFound(f"no backfill run with id {run_id!r} in room {room_id}")
        return record

    def _replica_index(
        self, room_id: str, connection_id: str, keys: set[str]
    ) -> dict[str, dict[str, Any]]:
        """The room's stored rows for these external ids, resolved in one pass.

        Walks the room's replica in ``list``-order batches and stops as soon as
        every key is found, so the cost is one plain scan per thousand rows
        rather than one correlated-index scan per row. A room with more than a
        thousand replica rows for one connection is walked in several batches,
        which is the price of a store that has no bulk-id lookup on the public
        interface; it is stated here rather than hidden, because a reviewer
        reading `_land` should know the loop is real and why it is not a
        per-row query.
        """
        found: dict[str, dict[str, Any]] = {}
        if not keys:
            return found
        remaining = set(keys)
        offset = 0
        batch = 1000
        while remaining:
            rows = self.store.list(
                REPLICA, room_id=room_id or None, order_by="id", descending=False,
                limit=batch, offset=offset,
            )
            if not rows:
                break
            for record in rows:
                if record["data"].get("connection_id") != connection_id:
                    continue
                key = str(record["data"].get("external_id") or "")
                if key in remaining:
                    found[key] = record
                    remaining.discard(key)
                    if not remaining:
                        return found
            offset += len(rows)
            if len(rows) < batch:
                break
        return found

    def _cursor_record(self, room_id: str, connection_id: str) -> dict[str, Any] | None:
        for record in self.store.find(CURSORS_STORE, {"connection_id": connection_id}, limit=50):
            if record.get("room_id") == room_id:
                return record
        return None

    def _patch(
        self, record: Mapping[str, Any], patch: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        return self.store.update(str(record["id"]), patch, actor=actor, source=source)

    def log(
        self,
        record: Mapping[str, Any],
        event: str,
        detail: str,
        *,
        actor: str | None,
        source: str,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one line to the per-run log the research's step 6 asks for."""
        run_id = str(record["id"])
        # Every line carries its own number, because a transcript is read in
        # order and a timestamp cannot supply that.
        #
        # `created_at` is millisecond-precision, so the four lines the opening
        # wizard writes can all land in the same millisecond and tie. The tie
        # used to be broken by `id` - a uuid4, so the order of tied lines came
        # out arbitrary. That is what put `run_created` third in the opening
        # transcript on CI while the identical test passed locally, where the
        # four lines happened to land in four different milliseconds and the
        # primary key was never consulted.
        #
        # The number is the run's own line count, so it needs no extra state on
        # the run record and stays correct for a run resumed days later.
        seq = self.store.count_where(EVENTS, {"run_id": run_id}) + 1
        return self.store.create(
            EVENTS,
            {
                "run_id": run_id,
                "seq": seq,
                "event": event,
                "detail": detail,
                "at": self._at(),
                **(dict(data) if data else {}),
            },
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )

    def _plan(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The vendor-facing view of a run: what to ask for, and where to resume from."""
        data = record["data"]
        obj = data.get("object") or {}
        return {
            "room_id": str(record.get("room_id") or ""),
            "run_id": str(record["id"]),
            "vendor": data.get("vendor"),
            "connection_id": data.get("connection_id"),
            "direction": data.get("direction"),
            "scope": data.get("scope") or {},
            "object_name": obj.get("object_name"),
            "object_type_id": obj.get("object_type_id"),
            "properties": tuple(obj.get("properties") or ()),
            "field_map": data.get("field_map") or {},
            "page_size": int(data.get("page_size") or 1),
            "poll_interval_seconds": int(data.get("poll_interval_seconds") or 0),
            "ready_after": int(data.get("ready_after") or 1),
            "attempt": int((data.get("job") or {}).get("attempt") or 0),
        }

    def _not_due(self, data: Mapping[str, Any]) -> bool:
        nxt = data.get("next_poll_at")
        if not nxt:
            return False
        return self.now() < _parse(nxt)


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #


def progress(counters: Mapping[str, Any], state: str) -> dict[str, Any]:
    """The percentage the research's step 6 asks to be shown.

    Two rules, both of them about not lying. A run with no total is
    ``indeterminate`` and says so rather than reporting a number that would go
    backwards; and a run is *never* given an ETA, because the research quotes
    Salesforce saying it "doesn't guarantee a service level agreement" and an ETA
    on an asynchronous job is a promise the vendor has explicitly declined to
    make.
    """
    total = counters.get("rows_total")
    seen = int(counters.get("rows_seen") or 0)
    if state == "complete":
        return {
            "percent": 100.0,
            "indeterminate": False,
            "basis": "complete",
            "detail": f"{seen} row(s) read; the range is fully covered",
        }
    if total in (None, 0):
        return {
            "percent": None,
            "indeterminate": True,
            "basis": "total_not_reported",
            "detail": "the vendor has not reported a total, so there is no denominator to be a "
            "percentage of; rows read so far are still counted",
            "no_sla": NO_SLA_QUOTE,
        }
    return {
        "percent": round(min(100.0, 100.0 * seen / int(total)), 1),
        "indeterminate": False,
        "basis": "rows_seen_over_reported_total",
        "detail": f"{seen} of {int(total)} row(s) read",
        "no_sla": NO_SLA_QUOTE,
    }


def _reconsider(
    record: Mapping[str, Any], adapter: vendors.VendorAdapter, total: int | None
) -> tuple[str | None, dict[str, Any]]:
    """Whether the vendor's own record count contradicts the strategy chosen.

    Only when the caller said nothing about the volume. If the admin stated a
    volume and the room followed the researched threshold with it, second-guessing
    the admin would be the room inventing a fact nobody gave it.
    """
    data = record["data"]
    reason = data.get("strategy_reason") or {}
    if reason.get("rule") != "undetermined_volume" or total is None:
        return None, {}
    threshold = bulk_threshold()
    wanted = "async_job" if int(total) > threshold else "paged_read"
    current = str(data.get("strategy"))
    if wanted == current or not adapter.supports(wanted):
        return None, {}
    return wanted, {
        "threshold": threshold,
        "reported_total": int(total),
        "chosen_before": current,
        "chosen_after": wanted,
        "detail": (
            f"no volume was supplied, so the run opened as {current}; the vendor then reported "
            f"{int(total)} record(s), which is {'more' if int(total) > threshold else 'not more'} "
            f"than {threshold} and asks for {wanted}. Nothing has been written yet, so the job is "
            "reopened rather than left on the wrong side of the researched rule."
        ),
    }


# --------------------------------------------------------------------------- #
# Payload normalisation
# --------------------------------------------------------------------------- #


def _connection_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a connection declaration.

    An unknown vendor is refused rather than stored. The research's
    extensibility note makes "add a vendor" a supported direction, but it also
    says what adding one consists of - implementing ``create job`` and
    ``read page`` - and a connection naming a system with no adapter cannot be
    started against anyway. The way to add one is a module that registers an
    adapter, and the refusal says so.
    """
    if not isinstance(payload, Mapping):
        raise PlanError("a connection must be a JSON object")
    vendor = str(payload.get("vendor") or "").strip().lower()
    if not vendor:
        raise PlanError("vendor is required; it is the key the adapter registry resolves")
    if vendor not in VENDORS:
        raise UnsupportedVendor(
            f"{vendor!r} is not one of the vendors this workflow researched "
            f"({', '.join(sorted(VENDORS))}). A third party adds one by implementing only 'create "
            "job' and 'read page' - see dsr.crm_backfill.vendors.VendorAdapter."
        )
    name = str(payload.get("name") or vendor)
    if not name.strip():
        raise PlanError("a connection needs a name; the vendor's own name is enough, but empty is not")
    return {
        "name": name,
        "vendor": vendor,
        "auth": str(payload.get("auth") or "oauth"),
        "granted_scopes": [str(scope) for scope in (payload.get("granted_scopes") or [])],
        "standard_objects": [str(item) for item in (payload.get("standard_objects") or [])],
        "replica_key_field": str(
            payload.get("replica_key_field") or transform.DEFAULT_KEY_FIELD
        ),
        "object_name": payload.get("object_name"),
        "daily_limit": _optional_int(payload.get("daily_limit"), "daily_limit"),
        "quota_timezone_offset_hours": _optional_int(
            payload.get("quota_timezone_offset_hours"), "quota_timezone_offset_hours"
        ),
        # The research says the Organization column "controls this duration and
        # can be changed", so a connection may declare its own window. Absent, the
        # researched seven days stands.
        "change_tracking_expiry_days": _optional_int(
            payload.get("change_tracking_expiry_days"), "change_tracking_expiry_days"
        ),
        "quota": {"window_started_at": None, "calls": 0},
    }


def _object(payload: Mapping[str, Any]) -> tuple[str | None, str | None, tuple[str, ...]]:
    """The object and the properties, in the research's own spelling.

    HubSpot's rule is checked by the adapter's preflight rather than here,
    because whether a name is standard is a property of the connection and not of
    the run, and because a vendor with no such rule should not be held to it.
    """
    object_name = payload.get("object_name") or payload.get("objectName")
    object_type_id = payload.get("object_type_id") or payload.get("objectTypeId")
    properties = payload.get("properties") or ()
    if isinstance(properties, str):
        properties = [properties]
    if not isinstance(properties, (list, tuple)):
        raise PlanError("properties must be a list of property names")
    return (
        str(object_name).strip() if object_name else None,
        str(object_type_id).strip() if object_type_id else None,
        tuple(str(name) for name in properties),
    )


def _expiry_days(connection: Mapping[str, Any]) -> int:
    raw = (connection.get("data") or {}).get("change_tracking_expiry_days")
    if raw is None:
        return cursors.default_expiry_days()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return cursors.default_expiry_days()


def _optional_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PlanError(f"{field} must be a whole number, got {value!r}") from None


def _summarise_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A short, safe rendering of a row that could not be keyed.

    Enough to find it in the vendor's own export, and not the whole payload: a
    rejected row goes into a log somebody reads, and a log is the wrong place for
    a record that may hold a buyer's personal data.
    """
    return {
        str(key): value
        for key, value in list(row.items())[:6]
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _parse(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
