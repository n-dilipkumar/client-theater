"""WF-050: reconcile gaps after a dropped change stream.

A Change Data Capture stream can lose a change. The vendor says so: "Gap events are
generated when change events can't be generated. They inform subscribers about errors
or operations done outside of Salesforce application servers." This feature is the
repair the vendor's own guide prescribes, expressed over one room's records.

Fourteen routes on ``/api/wf-050``. The two that do the work:

* ``POST /rooms/{room_id}/gap-events`` is step 1. A gap marks its record dirty as of
  the gap's own commit timestamp. An overflow cannot be repaired per record, because
  it carries "no record data and no record ID", so it unsubscribes the room and
  stores the Replay ID that the reconciliation starts from.
* ``POST /rooms/{room_id}/reconcile`` is steps 3 and 5. It makes the full re-read,
  overwrites the replica row, derives the deleted set by one of the research's two
  methods, and clears the dirty flag.

``POST /rooms/{room_id}/change-events`` is the rule that makes the repair safe: a
change for a dirty record is dropped, and the route says which of the four reasons
dropped it.

The domain rules live in ``dsr.crm_integration``. This module is the HTTP seam and
the demo data, and it owns nothing else.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.crm_integration import gap_vocabulary as vocab
from dsr.crm_integration.gap_errors import GapReconcileError
from dsr.crm_integration.gap_sources import StoredCrm
from dsr.crm_integration.reconcile_engine import REPLICA, ReconcileEngine
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-050-reconcile-gaps-after-a-dropped-change-st",
    "ticket": "WF-050",
    "name": "Reconcile gaps after a dropped change stream",
    "description": (
        "Repair a room's CRM replica after a gap or an overflow event, by a full "
        "re-read and a delete diff."
    ),
    "nav": [{"id": "gaps", "label": "Gaps"}],
}

router = APIRouter(prefix="/api/wf-050", tags=["WF-050"])

#: The vendor the demo reader speaks for, and the entity it holds. Both are the
#: research's own spellings.
DEMO_VENDOR = "salesforce"
DEMO_ENTITY = "Opportunity"

#: The record field the research names as the one to compare against a change event.
LAST_MODIFIED = vocab.LAST_MODIFIED_FIELD


def get_engine(store: RecordStore = StoreDep) -> ReconcileEngine:
    """The engine for the room's records.

    Built per request so one room's engine cannot be handed to another room's route.
    The reader is the room's own copy of the vendor's tables; a room with a real
    vendor connection overrides this dependency with its own reader and nothing
    above this line changes.
    """
    return ReconcileEngine(store, reader=StoredCrm(store, vendor=DEMO_VENDOR))


#: The dependency every route below takes, so a room with a real vendor connection
#: overrides one place rather than fourteen.
EngineDep = Depends(get_engine)


def _error(request: Request, exc: GapReconcileError) -> JSONResponse:
    """One handler for the whole hierarchy.

    ``code`` and ``status`` ride on the exception rather than being decided here,
    because asking to repair a clean record and asking to resume from a dead token
    are both this feature's errors and only one of them is a malformed request.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {GapReconcileError: _error}


# --------------------------------------------------------------------------- #
# What this workflow accepts
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The gap types, cursors, numbers, and states")
def vocabulary(engine: ReconcileEngine = EngineDep) -> dict[str, Any]:
    """Everything this API will accept, served as data.

    A client renders its pickers from this rather than from a list compiled into a
    page, so a value added in one place reaches every client at once.
    """
    return engine.vocabulary()


@router.get("/inferences", summary="Every judgement call, with its sourced sentences")
def inferences(engine: ReconcileEngine = EngineDep) -> dict[str, Any]:
    """The register of what the research fixes and what this build decided."""
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Step 1: the subscriber reports what the stream lost
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/gap-events", summary="Report a gap or an overflow event")
def report_gap_event(
    room_id: str,
    body: dict[str, Any],
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """Record the header, and do the first half of the repair it asks for."""
    return engine.report_gap_event(
        room_id,
        body,
        actor="subscriber",
        source=f"POST {router.prefix}/rooms/{{room_id}}/gap-events",
    )


@router.get("/rooms/{room_id}/gap-events", summary="List the gap and overflow events")
def list_gap_events(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """The event ledger, newest first."""
    events = engine.gap_events(room_id, limit=limit)
    return {"room_id": room_id, "count": len(events), "events": events}


@router.get("/rooms/{room_id}/gap-events/{event_id}", summary="Read one gap or overflow event")
def read_gap_event(
    room_id: str, event_id: str, engine: ReconcileEngine = EngineDep
) -> dict[str, Any]:
    """One event, with the record ids it named or the replay id it carried."""
    return engine.gap_event(room_id, event_id)


# --------------------------------------------------------------------------- #
# Step 2: a change arrives while a record may be dirty
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/change-events", summary="Apply one change event, or drop it")
def change_event(
    room_id: str,
    body: dict[str, Any],
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """Apply the change, or drop it and say which rule dropped it.

    "If you receive change events for new changes for the same record before the data
    has been reconciled, don't process them." A drop is the prescribed behaviour, not
    a caller error, so this answers 200 with ``applied: false``.
    """
    return engine.change_event(
        room_id,
        body,
        actor="subscriber",
        source=f"POST {router.prefix}/rooms/{{room_id}}/change-events",
    )


@router.get("/rooms/{room_id}/dirty", summary="The data-health view of dirty records")
def dirty(
    room_id: str,
    state: str = Query(default="dirty"),
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """Every record this room still owes a full re-read for, oldest gap first."""
    records = engine.dirty_records(room_id, state=state)
    return {
        "room_id": room_id,
        "state": state,
        "count": len(records),
        "records": records,
    }


# --------------------------------------------------------------------------- #
# Steps 3 and 5: the re-read, the delete diff, the flag clear
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/reconcile", summary="Re-read and repair a record or a whole entity")
def reconcile(
    room_id: str,
    body: dict[str, Any],
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """Repair one dirty record, or one whole entity after an overflow."""
    return engine.reconcile(
        room_id, body, actor="api", source=f"POST {router.prefix}/rooms/{{room_id}}/reconcile"
    )


@router.get("/rooms/{room_id}/runs", summary="List the reconciliation runs")
def list_runs(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """The runs, newest first."""
    runs = engine.runs(room_id, limit=limit)
    return {"room_id": room_id, "count": len(runs), "runs": runs}


@router.get("/rooms/{room_id}/runs/{run_id}", summary="Read one reconciliation run")
def read_run(room_id: str, run_id: str, engine: ReconcileEngine = EngineDep) -> dict[str, Any]:
    """One run, with its numbered sync log."""
    return engine.run(room_id, run_id)


@router.get("/rooms/{room_id}/runs/{run_id}/log", summary="Read one run's sync log in order")
def read_run_log(room_id: str, run_id: str, engine: ReconcileEngine = EngineDep) -> dict[str, Any]:
    """The sync log, in the order it happened.

    "Room ... records a reconciliation event in the sync log for audit."
    """
    lines = engine.run_log(room_id, run_id)
    return {"room_id": room_id, "run_id": run_id, "count": len(lines), "lines": lines}


# --------------------------------------------------------------------------- #
# The two cursors
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/cursors", summary="Both resumable positions, with their verdicts")
def cursors(room_id: str, engine: ReconcileEngine = EngineDep) -> dict[str, Any]:
    """The Replay ID store and the delta-link store, each with whether it is resumable.

    Two named cursors rather than one, because the research's own gaps section says
    the two "were not reconciled in a single source" and are "a different shape".
    """
    rows = engine.cursors(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "cursors": rows,
        "note": (
            "a Salesforce Replay ID is scoped to one entity type and does not expire; "
            "a Dataverse delta link is scoped to one org and stops being resumable "
            "after seven days"
        ),
    }


@router.get("/rooms/{room_id}/replica", summary="The reconciled rows this workflow owns")
def replica(
    room_id: str,
    entity: str = Query(default=""),
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """The replica rows WF-050 repaired, namespaced to WF-050.

    Another feature owns the streaming replica. This one repairs its own view and
    writes into no collection it does not own; the reasoning is recorded in the
    inference register.
    """
    rows = engine.replica(room_id, entity=entity)
    return {
        "room_id": room_id,
        "count": len(rows),
        "rows": rows,
        "ownership": (
            "crm_gap_replica. Another feature owns the streaming replica and this "
            "workflow repairs its own view rather than writing into it."
        ),
    }


# --------------------------------------------------------------------------- #
# Step 6: resubscribe, and the health view
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/subscribe", summary="Resubscribe after an overflow")
def subscribe(
    room_id: str,
    body: dict[str, Any] | None = None,
    engine: ReconcileEngine = EngineDep,
) -> dict[str, Any]:
    """Record the resubscription and close the overflow run it finishes."""
    return engine.subscribe(
        room_id, body, actor="api", source=f"POST {router.prefix}/rooms/{{room_id}}/subscribe"
    )


@router.get("/rooms/{room_id}/health", summary="One payload for the data-health view")
def health(room_id: str, engine: ReconcileEngine = EngineDep) -> dict[str, Any]:
    """Dirty count, open runs, and both cursor verdicts in one place."""
    return engine.health(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: Mapping[str, Any]) -> str:
    """Demo rows for a reviewer, in the states this workflow repairs from.

    The interesting states are the ones a healthy room never reaches: a record that
    is still dirty, a deleted gap whose record the vendor no longer holds, and an
    overflow that has unsubscribed the room and stored a Replay ID but has not been
    reconciled yet.

    Every string here is ASCII. A rightwards arrow in one recovered feature broke
    the whole seeder on a Windows console, and the seeder prints this.
    """
    rooms = _rooms(context)
    if not rooms:
        return "no rooms to scope them to; create a room first and reseed"

    rng: random.Random = context.get("rng") or random.Random("wf-050")
    now: datetime = context.get("now") or datetime.now(timezone.utc)
    store = RecordStore(db)

    events = 0
    dirty = 0
    tombstones = 0
    overflows = 0
    for index, (room_id, _account) in enumerate(rooms[:2]):
        clock = _Clock(now)
        engine = ReconcileEngine(store, reader=StoredCrm(store, DEMO_VENDOR), clock=clock)
        events += _seed_room(engine, str(room_id), index, rng)
        dirty += len(engine.dirty_records(str(room_id)))
        overflows += sum(
            1
            for row in engine.gap_events(str(room_id))
            if row["kind"] == "overflow" and row["subscription_state"] == "unsubscribed"
        )
        tombstones += sum(1 for row in engine.replica(str(room_id)) if row["deleted"])

    return (
        f"{len(rooms)} room(s): {events} gap and overflow events, "
        f"{dirty} record(s) still dirty, {tombstones} delete tombstone(s), "
        f"{overflows} overflow event(s) awaiting a reconcile"
    )


class _Clock:
    """A clock the seed moves, so the demo's timestamps are ordered and reproducible.

    Injected rather than mocked, because the seven-day cursor rule reads the room's
    own clock, and a seed that stamped every row with one instant would make the
    log's order unreadable.
    """

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._tick = 0

    def __call__(self) -> datetime:
        self._tick += 1
        return self._now + timedelta(minutes=self._tick)


def _seed_room(engine: ReconcileEngine, room_id: str, index: int, rng: random.Random) -> int:
    """One room's demo state. Returns the number of gap and overflow events written.

    Written entirely through the engine's own methods, so a demo row is a row the
    API would have written and a reviewer can reproduce it by calling the routes.
    """
    actor, source = "seed", "seed"

    # The room's copy of the vendor's tables. This is what a reconciliation reads.
    engine.put_source(
        room_id,
        entity=DEMO_ENTITY,
        record_id="006A000001",
        payload={"Name": "Northwind rollout", "Amount": 48000, LAST_MODIFIED: _stamp(engine)},
        actor=actor,
        source=source,
    )
    engine.put_source(
        room_id,
        entity=DEMO_ENTITY,
        record_id="006A000002",
        payload={"Name": "Contoso renewal", "Amount": 21500, LAST_MODIFIED: _stamp(engine)},
        actor=actor,
        source=source,
    )
    engine.put_source(
        room_id,
        entity=DEMO_ENTITY,
        record_id="006A000003",
        payload={"Name": "Fabrikam pilot", "Amount": 9800},
        is_deleted=True,
        actor=actor,
        source=source,
    )
    engine.put_source(
        room_id,
        entity=DEMO_ENTITY,
        record_id="006A000004",
        payload={"Name": "Tailspin expansion", "Amount": 31400, LAST_MODIFIED: _stamp(engine)},
        actor=actor,
        source=source,
    )

    # The room's replica before the gap, so a delete diff has something to diff.
    for record_id in ("006A000001", "006A000002", "006A000003", "006A000004"):
        engine.store.create(
            REPLICA,
            {
                "entity": DEMO_ENTITY,
                "record_id": record_id,
                "deleted": False,
                "payload": {"Name": record_id},
                "last_modified_date": None,
                "reconciled_at": None,
                "run_id": None,
                "event_id": None,
                "reason": "seeded",
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    written = 0

    # A gap that is repaired: the record is re-read and the flag is cleared.
    repaired = engine.report_gap_event(
        room_id,
        _gap("GAP_UPDATE", DEMO_ENTITY, ["006A000001"], f"seed-repair-{room_id}", engine),
        actor=actor,
        source=source,
    )
    written += 1
    engine.reconcile(
        room_id,
        {"event_id": repaired["event"]["id"], "record_id": "006A000001"},
        actor=actor,
        source=source,
    )

    # A gap whose record the vendor no longer holds: the repair writes a tombstone.
    purged = engine.report_gap_event(
        room_id,
        _gap("GAP_DELETE", DEMO_ENTITY, ["006A000003"], f"seed-purge-{room_id}", engine),
        actor=actor,
        source=source,
    )
    written += 1
    engine.purge_source(
        room_id,
        entity=DEMO_ENTITY,
        record_id="006A000003",
        actor=actor,
        source=source,
    )
    engine.reconcile(
        room_id,
        {"event_id": purged["event"]["id"], "record_id": "006A000003"},
        actor=actor,
        source=source,
    )

    # A gap left open on purpose: the data-health view needs a row to show, and a
    # demo where everything is repaired shows nothing to review.
    engine.report_gap_event(
        room_id,
        _gap("GAP_CREATE", DEMO_ENTITY, ["006A000009"], f"seed-open-{room_id}", engine),
        actor=actor,
        source=source,
    )
    written += 1

    # An overflow: unsubscribes the room and stores the Replay ID, unreconciled.
    overflow = engine.report_gap_event(
        room_id,
        {
            "changeType": "GAP_OVERFLOW",
            "transactionKey": f"seed-overflow-{room_id}",
            "commitTimestamp": _stamp(engine),
            "entity": DEMO_ENTITY,
            "changeCount": vocab.OVERFLOW_CHANGE_THRESHOLD + rng.randint(1, 999),
            "replayId": f"seed-replay-{index}",
        },
        actor=actor,
        source=source,
    )
    written += 1
    assert overflow["replay_id_stored"], "the demo overflow must store its replay id"
    return written


def _gap(
    change_type: str, entity: str, record_ids: list[str], key: str, engine: ReconcileEngine
) -> dict[str, Any]:
    return {
        "changeType": change_type,
        "transactionKey": key,
        "commitTimestamp": _stamp(engine),
        "entity": entity,
        "recordIds": record_ids,
    }


def _stamp(engine: ReconcileEngine) -> str:
    return engine.clock().astimezone(timezone.utc).isoformat()


def _rooms(context: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The rooms the seeder scoped this run to, accepting ids or pairs."""
    rooms: list[tuple[str, str]] = []
    for entry in context.get("room_ids") or []:
        if isinstance(entry, (tuple, list)) and entry:
            rooms.append((str(entry[0]), str(entry[1]) if len(entry) > 1 else ""))
        elif entry:
            rooms.append((str(entry), ""))
    return rooms
