"""WF-045: backfill historical records on a schedule with a resumable cursor.

The researched workflow, in full. An admin opens **Sync -> Backfill** and picks a
date range or full history; the room asks the CRM for an asynchronous extract job
when the volume is large and a delta or paged read when it is moderate; the job
id or delta token is stored as a **cursor**; a poller on a fixed interval reads
batches and writes rows in pages; a crash restarts from the stored cursor with no
duplicates and no gaps; and progress is a percentage with a per-run log.

The domain logic is in :mod:`dsr.crm_backfill`, which this module does not own
and which no other feature could have written into its own path. What lives here
is the three things a workflow has to take out of shared files: the HTTP surface,
the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-045``, and room scoping is real.** The research
describes backfilling *a room's* replica, so every run, cursor, replica row and
summary is served under ``/rooms/{room_id}/...``. Connections are the exception:
the research's own cursor record is keyed on ``connectionId``, which makes a
connection an account-level object rather than a room's, and putting a room in
that key would make the same account twice-declared.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."``, built from ``router.prefix`` so the audit row and the
route table cannot drift. A hardcoded string inside a domain method is a defect,
and the same class of bug has shipped in this codebase before: a feature's audit
log kept naming a path the app had stopped serving. ``source`` is a *required*
keyword on every writing method of :class:`~dsr.crm_backfill.engine.BackfillEngine`,
so omitting it is a ``TypeError`` at the call site rather than an untraceable row
in production.

**One handler for the whole error hierarchy.** ``BackfillError`` is the base of
every refusal in :mod:`dsr.crm_backfill`, and each carries its own ``status`` and
``code``, so one handler answers 400 for a malformed range and 409 for a cursor
the vendor has aged out without being told which. It is a domain type, so
registering it globally cannot intercept anything unrelated elsewhere in the
product. ``RecordNotFound`` is deliberately *not* claimed: the core app already
maps it to 404, and two handlers for one type is a collision the host refuses.

**Two routes for one poll.** ``/poll`` is the scheduler's route: it respects the
run's fixed interval and answers ``not_due`` rather than asking the vendor again.
``/resume`` is the recovery route, and it forces the poll, because the researched
recovery story is a room that crashed and a room that has just restarted has not
been respecting anybody's interval. The same page cycle runs behind both, so the
two cannot disagree about what a run has done.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm_backfill import BackfillEngine, BackfillError, SimulatedHistory
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-045-backfill-historical-records-on-a-sched",
    "ticket": "WF-045",
    "name": "Backfill historical records on a schedule with a resumable cursor",
    "description": (
        "Read a room's CRM history into its replica on a schedule. Large volumes go through an "
        "asynchronous extract job, moderate ones through a delta or paged read, and both keep a "
        "resumable cursor so a crash restarts with no duplicates and no gaps. The cursor ages out on "
        "the vendor's own schedule, and a run that cannot be resumed says so instead of quietly "
        "reading the range again."
    ),
    "nav": [{"id": "backfill-history", "label": "Backfill history"}],
}

router = APIRouter(prefix="/api/wf-045", tags=["wf045"])


def get_engine(store: RecordStore = StoreDep) -> BackfillEngine:
    """A :class:`BackfillEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and the vendor registry, and an ``app.state`` entry is
    exactly the edit to the shared ``dsr/api.py`` that the feature host exists to
    make unnecessary. Building it here also leaves the vendor registry a plain
    constructor argument, which is what lets a test drive the whole workflow with
    rows of its own and a clock it controls.
    """
    return BackfillEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _backfill_error(request: Request, exc: BackfillError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``BackfillError`` is the base of every
    refusal in :mod:`dsr.crm_backfill` - a range that runs backwards, a
    connection that does not exist, a scope nobody granted, a cursor the vendor
    has stopped answering for - and all of them are the caller's to fix. The
    status rides on the exception rather than being decided here, because a
    malformed range and an aged-out token are both this package's errors and only
    one of them is a conflict with state that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {BackfillError: _backfill_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: BackfillEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The strategies and their meanings, the directions, the scope options, the run
    states and which of them are terminal, the per-run log's event kinds, the
    cursor kinds, and the three researched numbers with the sentence that fixes
    each one. A client renders its pickers from this rather than from a list
    compiled into the page, so a value added here reaches every client at once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: BackfillEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research fixes the volume threshold, the page size, the change-tracking
    window, the scope grant, the object-naming rule and the absence of an SLA. It
    does not say what a room does when two of those rules disagree, what
    "no duplicates, no gaps" means for a row it cannot key, or how much history
    "full history" covers. Those edges are served as data rather than left for a
    reader to reconstruct from a diff, and the researched half comes back beside
    the inferred half so a reviewer can see where the line falls.
    """
    return engine.inferences()


@router.get("/vendors")
def vendors(engine: BackfillEngine = EngineDep) -> dict[str, Any]:
    """The registered vendor adapters and what each one implements.

    The research's extensibility note says a third party "can add a new vendor by
    implementing only 'create job' and 'read page'". This is where a reader sees
    which vendors exist, which strategies each has, and which of them can receive
    a reverse backfill - so the note is a property of the code and not only of
    the documentation.
    """
    return engine.catalog()


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_connections(
    room_id: str | None = Query(default=None),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """Declared CRM connections, each with its strategies and today's allowance.

    Account-level rather than room-scoped, because the researched cursor record is
    keyed on ``connectionId``: a connection is a thing an account has, not a
    thing a room has.
    """
    listed = engine.connections(room_id=room_id)
    return {"count": len(listed), "connections": listed}


@router.post("/connections", status_code=201)
def create_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a CRM connection: which vendor, and what its token can reach.

    The fields the vendors' own rules need live here rather than on the run,
    because they are properties of the account and not of one backfill - a
    ``crm.export`` grant, which names count as standard objects, the daily call
    limit, the time zone whose midnight that limit resets on, and the
    change-tracking window the Organization column controls.
    """
    return engine.create_connection(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connections"
    )


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, engine: BackfillEngine = EngineDep) -> dict[str, Any]:
    """One connection, with what is left of its daily allowance."""
    return engine.connection(connection_id)


# --------------------------------------------------------------------------- #
# The backfill runs, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/backfills")
def list_backfills(
    room_id: str,
    state: str | None = Query(default=None),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=50, ge=1, le=500),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """This room's backfill runs, newest first.

    Every filter is a JSON path in the run's own payload, resolved through the
    dynamic index, so a field a team added later is queryable without a change to
    this route.
    """
    listed = engine.runs(room_id=room_id, state=state, where=where, limit=limit)
    return {"room_id": room_id, "count": len(listed), "backfills": listed}


@router.post("/rooms/{room_id}/backfills", status_code=201)
def start_backfill(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """Open a backfill. The wizard's **Start**.

    Takes the range or the full-history option, the connection, an optional field
    map, an optional volume estimate and an optional poll interval, and does the
    three researched steps in one call: choose the strategy from the volume, ask
    the CRM for a job, and store the job id or delta token as a cursor. Each
    decision is written to the run's log as it is taken, so the run is a
    transcript rather than a summary.

    A stored cursor for the connection is adopted rather than replaced - that is
    the researched behaviour - unless the body says ``from_scratch``, which is
    only allowed alongside the full-history scope.

    A run whose connection cannot do what was asked (no ``crm.export`` grant, a
    custom object named by its label) is created and returned with its findings
    and no job: the other decisions are worth keeping, and a job opened without
    the grant would fail at the vendor after the fact.
    """
    return engine.start(room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/backfills")


@router.get("/rooms/{room_id}/backfills/{run_id}")
def read_backfill(
    room_id: str, run_id: str, engine: BackfillEngine = EngineDep
) -> dict[str, Any]:
    """One run: its state, its progress percentage, its cursor and its counts."""
    return engine.run(room_id, run_id)


@router.get("/rooms/{room_id}/backfills/{run_id}/log")
def read_log(
    room_id: str,
    run_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """The per-run log, oldest first.

    The research's step 6 asks for "a per-run log" beside the progress figure,
    and this is it: every state change, every page write, every cursor move, in
    the order they happened. A log an operator reads to answer "what happened to
    this backfill" is a story, and a story told backwards is not one.
    """
    engine.run(room_id, run_id)
    listed = engine.events(run_id, limit=limit)
    return {
        "room_id": room_id,
        "run_id": run_id,
        "count": len(listed),
        "events": [{"id": event["id"], "room_id": event.get("room_id"), **event["data"]} for event in listed],
    }


@router.post("/rooms/{room_id}/backfills/{run_id}/poll")
def poll_backfill(
    room_id: str,
    run_id: str,
    actor: str | None = Query(default=None),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """One step of the poller the research describes.

    "Dataverse's change-tracking token and Salesforce's job queue both require
    polling, so the room runs a poller on a fixed interval." Nothing here starts
    a thread: a scheduler calls this route, and a call made before the run's
    interval has elapsed is answered with ``reason: not_due`` and no vendor call
    at all - which matters, because the research documents a *daily* limit on
    exactly this vendor.

    The response carries the run and what this step did: a page written, a job
    not ready yet, a quota wall reached, or a vendor-reported failure. Every one
    of those is a fact about the run rather than an exception, because the
    researched flow ends with a log precisely so somebody can read what happened.
    """
    return engine.poll(room_id, run_id, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/backfills/{{run_id}}/poll")


@router.post("/rooms/{room_id}/backfills/{run_id}/resume")
def resume_backfill(
    room_id: str,
    run_id: str,
    actor: str | None = Query(default=None),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """Continue a run from the cursor it stored.

    The researched recovery step, made callable: "If the job or the room crashes
    mid-run, the room restarts from the stored cursor - no duplicates, no gaps."
    It forces a poll and counts the resume, so a run that has been restarted four
    times says so in its own totals rather than in somebody's memory.

    A cursor the vendor has stopped answering for is refused with 409 and the run
    is marked ``stalled``. Reading the range again under the same run id would
    make the research's promise untrue, so the error names the backfill to open
    instead.
    """
    return engine.resume(room_id, run_id, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/backfills/{{run_id}}/resume")


@router.post("/rooms/{room_id}/backfills/{run_id}/cancel")
def cancel_backfill(
    room_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """Stop a run where it is.

    The rows already landed stay, and the stored cursor stays, so a corrected
    backfill against the same connection continues rather than re-reading. A run
    that has already finished cannot be cancelled, because saying it had been
    would say something untrue about what happened.
    """
    reason = payload.get("reason")
    return engine.cancel(
        room_id,
        run_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/backfills/{{run_id}}/cancel",
        reason=str(reason) if reason else None,
    )


# --------------------------------------------------------------------------- #
# Cursors and the room replica
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/cursors")
def list_cursors(room_id: str, engine: BackfillEngine = EngineDep) -> dict[str, Any]:
    """Every cursor this room holds, each with its expiry verdict.

    The research's standard record - ``{vendor, connectionId, cursor, updatedAt}``
    - plus when the vendor will stop answering for it. A Dataverse token that has
    aged past ``ExpireChangeTrackingInDays`` says so here, which is the earliest
    place an operator can be told: before the resume that would have failed.
    """
    listed = engine.cursors(room_id=room_id)
    return {"room_id": room_id, "count": len(listed), "cursors": listed}


@router.get("/rooms/{room_id}/replica")
def list_replica(
    room_id: str,
    connection_id: str | None = Query(default=None),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: BackfillEngine = EngineDep,
) -> dict[str, Any]:
    """The room's replica rows, filtered on any JSON path in their payload.

    A team that added a field to its CRM object and mapped it in can query it
    here without a change to this route, which is the whole point of a
    schema-flexible store.
    """
    listed = engine.replica(
        room_id=room_id, connection_id=connection_id, where=where, limit=limit
    )
    return {
        "room_id": room_id,
        "collection": "crm_replica",
        "count": len(listed),
        "rows": listed,
    }


@router.get("/rooms/{room_id}/backfill-summary")
def backfill_summary(room_id: str, engine: BackfillEngine = EngineDep) -> dict[str, Any]:
    """Counts for this room's backfills, and the runs that are stalled.

    Counted over this room's own runs, so a room's header says what happened in
    that room. ``stalled`` is listed separately because a stalled run is the one
    state that needs a human to open a different backfill.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# Seeded through the real engine over a scripted history, so the demo cannot show
# a shape this workflow would not produce, and seeding never opens a socket. The
# runs are then *driven* - polled until they stop moving - so the page shows a
# finished run's numbers rather than a run sitting at zero per cent.
#
# Every number in the return string is measured from the runs the engine produced
# rather than typed out here. The first draft of this section typed them, and the
# two versions disagreed: a run described as "complete" was still moving, because
# all three vendors shared one history and a HubSpot export of CONTACT was
# answering with opportunities. A seeder that reports what it did is a different
# thing from a seeder that reports what it meant to do.
#
# Deliberately mixed, because a demo of only green teaches a reviewer nothing:
#
# * a completed Salesforce run above the researched 2,000-record boundary, so the
#   async-job branch is a row and the percentage has a real denominator;
# * a completed Salesforce run below it, in a second room so no stored job id
#   short-circuits the volume rule, so the paged-read branch is a row too;
# * a Dataverse run left partway through, so the percentage is a real partial
#   figure and the cursor is a DataToken with a real age on it, and whose last
#   two rows carry no identifier so rows_rejected is non-zero;
# * a HubSpot run refused at preflight, because that connection's token was never
#   granted ``crm.export`` - the research's Super Admin rule as a row;
# * a completed HubSpot run on the connection that *was* granted, so both
#   branches of the preflight are rows;
# * a run holding a DataToken nine days old, marked ``stalled`` - the state where
#   a restart cannot be made safe, which no amount of green would teach.

#: The connections the demo declares, with the fields each vendor's own rules
#: need. Two of them are HubSpot, one granted and one not, because the scope rule
#: is only visible as a contrast. ``object_name`` also scopes what the scripted
#: history will answer with, because an export of one object answering with
#: another is a demo nobody could read.
DEMO_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "Northwind — Salesforce production",
        "vendor": "salesforce",
        "auth": "oauth",
        "granted_scopes": ["api", "refresh_token"],
        "object_name": "opportunity",
        "replica_key_field": "Id",
    },
    {
        "name": "Northwind — Dataverse",
        "vendor": "dataverse",
        "auth": "oauth",
        "granted_scopes": [],
        "object_name": "account",
        "change_tracking_expiry_days": 7,
    },
    {
        "name": "Contoso — HubSpot (crm.export never granted)",
        "vendor": "hubspot",
        "auth": "oauth",
        "granted_scopes": ["crm.objects.read"],
        "standard_objects": ["CONTACT", "DEAL"],
        "object_name": "CONTACT",
        "daily_limit": 50_000,
        "quota_timezone_offset_hours": -5,
    },
    {
        "name": "Fabrikam — HubSpot (Super Admin granted crm.export)",
        "vendor": "hubspot",
        "auth": "oauth",
        "granted_scopes": ["crm.objects.read", "crm.export"],
        "standard_objects": ["CONTACT", "DEAL"],
        "object_name": "CONTACT",
        "daily_limit": 50_000,
        "quota_timezone_offset_hours": -5,
    },
)

#: The field map the demo uses, so the replica's column names are visibly a
#: decision rather than a copy of the CRM's. ``Stage__c`` is a Salesforce custom
#: field, which is the case the map exists for. Applied to every run, not just
#: the Salesforce ones: a map is a property of the room's replica, and a run that
#: quietly wrote columns under the vendor's spelling would leave two shapes in
#: the same collection for a reader to reconcile.
DEMO_FIELD_MAP: dict[str, str] = {
    "Name": "title",
    "Stage__c": "stage",
    "Amount": "amount",
}

#: The date bands the four batches occupy. The two Salesforce runs have to see
#: genuinely different volumes - one over the researched 2,000-record threshold
#: and one under it - and only the recent band may fall inside the narrow range.
HISTORY_DAYS = 900
RECENT_DAYS = 45

#: Above the researched 2,000-record boundary, by design: this batch is what
#: makes the async-job branch a row rather than a claim.
BULK_BATCH = 2_050
#: Under it, and recent, so the narrow range reaches nothing else.
RECENT_BATCH = 60
ACCOUNT_BATCH = 300
CONTACT_BATCH = 40

#: How many rows of the account batch carry no identifier. Two, because one
#: would look like a typo, and because the researched failure this feature has
#: to survive - a row that cannot be keyed - is only visible with more than one.
DEMO_UNKEYED_ROWS = 2

#: The demo's own quote, so the seeder's return string can name it and a reviewer
#: knows the progress figures come with it.
DEMO_NO_SLA = "Salesforce doesn't guarantee a service level agreement."


def _history(
    rng: random.Random,
    *,
    count: int,
    days: int,
    title: str,
    object_name: str,
    prefix: str,
    now: datetime,
    with_key: bool = True,
) -> list[dict[str, Any]]:
    """A deterministic batch of CRM history, spread over the last ``days`` days.

    Built rather than typed out because the batch that matters is over two
    thousand records, and a hand-written list that size would be unreadable.
    ``object`` is the source's routing tag, not record data - the adapters strip
    it - and it is what lets one history serve three vendors whose accounts hold
    different things.
    """
    rows: list[dict[str, Any]] = []
    for index in range(count):
        happened = now - timedelta(
            days=days * (index + 1) / (count + 1), minutes=rng.randrange(0, 240)
        )
        row: dict[str, Any] = {
            "object": object_name,
            "Name": f"{title} {index + 1:04d}",
            "Stage__c": ["Prospecting", "Qualification", "Proposal", "Negotiation"][index % 4],
            "Amount": rng.randrange(4_000, 240_000),
            "occurred_at": happened.isoformat(timespec="seconds"),
        }
        if with_key or index < count - DEMO_UNKEYED_ROWS:
            row["id"] = f"{prefix}-{index:05d}"
        rows.append(row)
    return rows


def _drive(
    engine: BackfillEngine, room_id: str, run: Mapping[str, Any], *, source: str, limit: int = 40
) -> dict[str, Any]:
    """Poll a run until it stops moving, the way a scheduler would.

    Bounded, because a loop with no bound in a seeder is a hang waiting for a
    vendor bug. The bound is far above the page counts the demo's own page sizes
    produce, so reaching it would mean something is genuinely wrong - and the
    seeder's return string is built from what the run's own state says, so a run
    that did stop early is reported as still moving rather than as complete.
    """
    current = dict(run)
    for _ in range(limit):
        if current["data"]["state"] != "running":
            return current
        current = engine.poll(room_id, str(current["id"]), source=source, force=True)["run"]
    return current


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Four connections, six runs, and the states the research says matter.

    The runs are produced by running the real :class:`BackfillEngine` over a
    scripted history, so the demo's log, counters, cursors and replica rows are
    what this workflow actually writes rather than rows composed by hand.
    """
    from dsr.crm_backfill.vendors import default_registry

    store = RecordStore(db)
    rng: random.Random = context.get("rng") or random.Random("wf045")
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    source = "seed"

    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]

    # One scripted history behind all three vendors, tagged by object so a
    # connection only ever reads its own account's things.
    history = SimulatedHistory(
        _history(
            rng, count=BULK_BATCH, days=HISTORY_DAYS, title="Opportunity",
            object_name="opportunity", prefix="006", now=base,
        )
        + _history(
            rng, count=RECENT_BATCH, days=RECENT_DAYS, title="Opportunity",
            object_name="opportunity", prefix="recent", now=base,
        )
        + _history(
            rng, count=ACCOUNT_BATCH, days=600, title="Account",
            object_name="account", prefix="acc", now=base, with_key=False,
        )
        + _history(
            rng, count=CONTACT_BATCH, days=400, title="Contact",
            object_name="CONTACT", prefix="con", now=base,
        )
    )
    engine = BackfillEngine(store, registry=default_registry(history), clock=lambda: base)

    connections = [engine.create_connection(spec, actor="dana", source=source) for spec in DEMO_CONNECTIONS]
    salesforce, dataverse, hubspot_blocked, hubspot_ok = connections

    if not rooms:
        return (
            f"{len(DEMO_CONNECTIONS)} connections, 0 backfill runs "
            "(no rooms to scope a backfill to)"
        )

    room_id = str(rooms[0][0])
    other_room = str(rooms[1][0]) if len(rooms) > 1 else room_id
    wide = {
        "kind": "range",
        "from": (base - timedelta(days=HISTORY_DAYS)).isoformat(),
        "to": base.isoformat(),
    }
    recent = {
        "kind": "range",
        "from": (base - timedelta(days=RECENT_DAYS)).isoformat(),
        "to": base.isoformat(),
    }

    # 1. Salesforce, above the researched 2,000-record boundary: an async job,
    #    a real denominator for the percentage, and rows in the replica.
    bulk = _drive(
        engine,
        room_id,
        engine.start(
            room_id,
            {
                "connection_id": salesforce["id"],
                "scope": wide,
                "estimated_records": BULK_BATCH,
                "field_map": DEMO_FIELD_MAP,
                "page_size": 1_000,
                "poll_interval_seconds": 0,
                "ready_after": 1,
            },
            actor="dana",
            source=source,
        ),
        source=source,
    )

    # 2. Salesforce again, below the boundary and in the second room. The room
    #    matters: a stored job id in the first room would short-circuit the
    #    volume rule and the paged-read branch would never be a row.
    paged = _drive(
        engine,
        other_room,
        engine.start(
            other_room,
            {
                "connection_id": salesforce["id"],
                "scope": recent,
                "estimated_records": RECENT_BATCH,
                "field_map": DEMO_FIELD_MAP,
                "page_size": 25,
                "poll_interval_seconds": 0,
                "ready_after": 1,
            },
            actor="sam",
            source=source,
        ),
        source=source,
    )

    # 3. Dataverse: a delta read whose cursor is a DataToken. Left partway on
    #    purpose - one page has landed, so the percentage is a real partial
    #    figure and the run is visibly still moving. The last two rows of the
    #    account batch carry no identifier, so rows_rejected is non-zero too.
    delta = engine.start(
        room_id,
        {
            "connection_id": dataverse["id"],
            "scope": {"kind": "full_history", "from": (base - timedelta(days=600)).isoformat()},
            "field_map": DEMO_FIELD_MAP,
            "page_size": 100,
            "poll_interval_seconds": 0,
            "ready_after": 1,
        },
        actor="dana",
        source=source,
    )

    # 4. HubSpot without the grant: refused before a job exists, which is the
    #    research's Super Admin rule as a row rather than as a claim.
    blocked = engine.start(
        room_id,
        {
            "connection_id": hubspot_blocked["id"],
            "scope": wide,
            "object_name": "CONTACT",
            "estimated_records": CONTACT_BATCH,
            "poll_interval_seconds": 0,
        },
        actor="dana",
        source=source,
    )

    # 5. HubSpot with the grant, in the second room, driven to completion so the
    #    export-status path and the download-URL path are both in the log.
    export = _drive(
        engine,
        other_room,
        engine.start(
            other_room,
            {
                "connection_id": hubspot_ok["id"],
                "scope": wide,
                "object_name": "CONTACT",
                "field_map": DEMO_FIELD_MAP,
                "estimated_records": CONTACT_BATCH,
                "page_size": 20,
                "poll_interval_seconds": 0,
                "ready_after": 1,
            },
            actor="sam",
            source=source,
        ),
        source=source,
    )

    # 6. A run holding a DataToken past the window, marked ``stalled``. Written
    #    through the engine's own pieces rather than composed, because the only
    #    honest way to reach that state is to move the clock, and the demo's
    #    clock is the seeder's ``now``.
    _seed_stalled_run(engine, dataverse, room_id, base, source)

    return _summarise(engine, room_id, other_room, (bulk, paged, delta, blocked, export))


def _state_of(run: Mapping[str, Any]) -> str:
    """How a run finished, in the words the return string uses.

    Reads the run's own state rather than a local variable, so a run that was
    meant to finish and did not is described as still moving.
    """
    return str(run["data"]["state"])


def _summarise(
    engine: BackfillEngine,
    room_id: str,
    other_room: str,
    runs: tuple[Mapping[str, Any], ...],
) -> str:
    """Describe the demo from what the engine actually did.

    Every figure here is read back off a run. That is the point: the first draft
    typed its numbers, and two of them turned out to be wrong about the same run
    in the same sentence.
    """
    bulk, paged, delta, blocked, export = runs
    first_room = engine.runs(room_id=room_id, limit=50)
    written = sum(int(run["counters"].get("rows_created") or 0) for run in first_room)
    rejected = sum(int(run["counters"].get("rows_rejected") or 0) for run in first_room)
    other_written = sum(
        int(run["counters"].get("rows_written") or 0) for run in engine.runs(room_id=other_room, limit=50)
    )
    findings = len(blocked["data"].get("findings") or [])

    def clause(run: Mapping[str, Any], label: str) -> str:
        counters = run["counters"]
        return (
            f"{label} ({_state_of(run)}, {counters.get('rows_written') or 0} written"
            f"{', ' + str(counters.get('rows_unchanged')) + ' already current' if counters.get('rows_unchanged') else ''})"
        )

    return (
        f"{len(DEMO_CONNECTIONS)} connections (3 vendors; 1 HubSpot connection without the "
        f"crm.export grant), 6 backfill runs across 2 rooms, {written + other_written} replica rows "
        f"({written} in the first room, {other_written} in the second): "
        f"{clause(bulk, 'Salesforce async job over ' + str(BULK_BATCH) + ' records')}, "
        f"{clause(paged, 'Salesforce paged read over ' + str(RECENT_BATCH))}, "
        f"{clause(delta, 'Dataverse delta read, partway with a DataToken cursor and ' + str(rejected) + ' row(s) rejected for having no key')}, "
        f"1 HubSpot export refused at preflight ({findings} finding(s), no job created), "
        f"{clause(export, 'HubSpot export over ' + str(CONTACT_BATCH) + ' contacts')}, "
        f"1 run stalled on a DataToken older than 7 days. "
        f'Progress is a percentage and never an ETA: "{DEMO_NO_SLA}"'
    )


def _seed_stalled_run(
    engine: BackfillEngine,
    connection: Mapping[str, Any],
    room_id: str,
    base: datetime,
    source: str,
) -> str:
    """A run holding a DataToken older than the window, marked ``stalled``.

    Written through the engine's own pieces rather than by composing a record, so
    the row it produces is the shape a real stall produces - including the log
    line, which is the thing an operator actually reads. Returns the run's id.
    """
    from dsr.crm_backfill import cursors
    from dsr.crm_backfill.engine import CURSORS_STORE, RUNS, new_counters

    aged = (base - timedelta(days=9)).isoformat()
    detail = (
        f"the data_token recorded at {aged} is older than the 7 day window dataverse will still "
        "answer for, so a resume would be refused. Reading the range again under this run would "
        "write every row twice, so the run is stalled: open a new full-history backfill."
    )
    record = engine.store.create(
        RUNS,
        {
            "run_key": "",
            "direction": "pull",
            "strategy": "delta_read",
            "strategy_reason": {
                "rule": "volume",
                "threshold": 2000,
                "estimated_records": ACCOUNT_BATCH,
                "detail": (
                    f"{ACCOUNT_BATCH} record(s) is not more than 2000, which asks for a paged read; "
                    "the run is stored as a delta read because a DataToken is the only cursor this "
                    "vendor ages out, and a stalled DataToken is the state worth having in the demo"
                ),
            },
            "vendor": "dataverse",
            "connection_id": str(connection["id"]),
            "object": {"object_name": "account", "object_type_id": None, "properties": []},
            "scope": {
                "kind": "full_history",
                "from": aged,
                "to": base.isoformat(),
                "unbounded_below": False,
                "days": 9.0,
            },
            "field_map": {},
            "page_size": 100,
            "poll_interval_seconds": 300,
            "ready_after": 1,
            "state": "stalled",
            "job": {
                "id": "200",
                "state": "running",
                "cursor_kind": "data_token",
                "total": ACCOUNT_BATCH,
                "processed": 200,
            },
            "cursor": cursors.build(
                vendor="dataverse",
                connection_id=str(connection["id"]),
                cursor="200",
                updated_at=aged,
                kind="data_token",
                pages_advanced=2,
                rows_written=200,
            ),
            "counters": {
                **new_counters(),
                "pages": 2,
                "polls": 2,
                "rows_seen": 200,
                "rows_written": 200,
                "rows_created": 200,
                "rows_total": ACCOUNT_BATCH,
            },
            "quota": {},
            "findings": [],
            "adopted_cursor": False,
            "failure": {"reason": "cursor_expired", "detail": detail},
            "next_poll_at": None,
            "last_advanced_at": aged,
            "completed_at": None,
            "started_at": aged,
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    record = engine.store.update(
        str(record["id"]), {"run_key": record["id"]}, actor="dana", source=source
    )
    engine.store.create(
        CURSORS_STORE,
        dict(record["data"]["cursor"] | {"run_id": record["id"]}),
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.log(record, "run_stalled", detail, actor="dana", source=source)
    return str(record["id"])
