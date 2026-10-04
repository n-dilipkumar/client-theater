"""WF-046: throttle and retry under a vendor's API rate limits.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-046.md``, which is
the specification, and from ``orchestration/decisions/WF-046-RESOLUTION.md``,
which settles where the rate limiting lives: **once**, in :mod:`dsr.throttle`,
with ``dsr.partial_failures`` and ``dsr.integ_monitor`` delegating to it rather
than each keeping a copy of the 429 classification and the backoff.

The researched workflow, in five steps:

1. the room's connector keeps a per-connection token bucket sized from the
   vendor's published limits;
2. every response is inspected for the vendor's quota headers and written to the
   room's quota meter;
3. on a throttling response the connector applies exponential backoff with
   jitter, honours ``Retry-After`` where present, and defers the batch;
4. for HubSpot's high-volume sync locks (``423``) the room inserts a delay of at
   least 2 seconds between requests;
5. admin sees the remaining budget and the throttle log in the connection's
   **Quota** page, and can pause or resume a connection by hand.

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the demo
data. The domain lives in :mod:`dsr.throttle`.

Why the prefix is ``/api/wf-046``
--------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is room-scoped
and a connection is account-level, which is what WF-034 and WF-045 both settled:
the vendor's own cursor record is keyed on ``connectionId``, so a connection is a
thing an account has rather than a thing a room has.

``source=`` comes from the route
--------------------------------
Every write below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
required keyword on every domain method that writes, so it cannot silently
regress. A test asserts that every ``source`` recorded in the audit log names a
route the host actually mounted - including the queue worker's ``drain``, which
writes the batch, the bucket and the meter behind one call.

Error mapping
-------------
Four handlers, one per distinct answer, and every type is this feature's own.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
404, and two handlers for one type is a collision the host refuses. The split is
400 for a payload or a policy this layer cannot read, 404 for no such connection,
room or batch, 409 for a retry that would change its idempotency keys, and 422 for
a policy that would produce a bucket which cannot refill.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore, parse_where
from dsr.throttle import (
    backoff as throttle_backoff,
    classify as throttle_classify,
    inferences as throttle_inferences,
    policies as throttle_policies,
    vocabulary as throttle_vocabulary,
)
from dsr.throttle.engine import ThrottleEngine
from dsr.throttle.errors import (
    InvalidPayload,
    ThrottleError,
    UnknownBatch,
    UnknownConnection,
    UnknownPolicy,
    UnknownRoom,
)

FEATURE = {
    "id": "wf-046-throttle-and-retry-under-vendor-api-ra",
    "ticket": "WF-046",
    "name": "Throttle and retry under a vendor's API rate limits",
    "description": (
        "Keep a per-connection token bucket sized from the vendor's published limits, read the "
        "quota headers on every response, and on a throttling answer wait - with jitter, and with "
        "Retry-After where the vendor sends it - then retry the batch under the same idempotency "
        "keys so a rate-limited vendor never duplicates or loses a CRM row. A connection can be "
        "paused by hand, and the throttle log says what every deferral was waiting for."
    ),
    "nav": [{"id": "throttle-quota", "label": "Quota and throttling"}],
}

router = APIRouter(prefix="/api/wf-046", tags=["WF-046"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _throttle_error(request: Request, exc: ThrottleError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400."""
    return JSONResponse(status_code=400, content={"error": "throttle_refused", "detail": str(exc)})


def _unknown_connection(request: Request, exc: UnknownConnection) -> JSONResponse:
    """No such connection, or one that belongs to another room."""
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_connection", "detail": str(exc), "id": exc.args[0]},
    )


def _unknown_batch(request: Request, exc: UnknownBatch) -> JSONResponse:
    """No such batch, or one on another room."""
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_batch", "detail": str(exc), "id": exc.args[0]},
    )


def _unknown_room(request: Request, exc: UnknownRoom) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_room", "detail": str(exc), "id": exc.args[0]},
    )


def _invalid_policy(request: Request, exc: UnknownPolicy) -> JSONResponse:
    """A policy that would produce a bucket which cannot refill. 422.

    422 rather than 400 because the request is well-formed JSON and the *policy*
    is what is wrong: a rate with no window is a rate the room would accept and
    then never refill, and a reviewer needs to see that the shape is the problem
    rather than the request.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_policy",
            "detail": str(exc),
            "fields": list(throttle_policies.PATCHABLE),
        },
    )


EXCEPTION_HANDLERS = {
    ThrottleError: _throttle_error,
    UnknownConnection: _unknown_connection,
    UnknownBatch: _unknown_batch,
    UnknownRoom: _unknown_room,
    UnknownPolicy: _invalid_policy,
}


def get_engine(store: RecordStore = StoreDep) -> ThrottleEngine:
    """A :class:`ThrottleEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds the store
    handle, a clock and the policy registry, and an ``app.state`` entry is exactly
    the edit to the shared ``dsr/api.py`` that the feature host exists to make
    unnecessary.
    """
    return ThrottleEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The five researched steps, the batch states, the throttle signals, the ladder.

    Every picker on the page renders from this rather than from a list compiled
    into the page, so a state or a signal added here reaches every client at once.
    """
    return throttle_vocabulary.describe()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research fixes the 429, the 423, the two-second floor, the ``Retry-After``
    header and the idempotency promise. It names no backoff base, no ceiling, no
    jitter seed and no cap on the header, and it says the Dataverse and
    Salesforce numbers do not exist in the source set. Each of those is an entry
    here, with the choice and the cost of the alternative.
    """
    return throttle_inferences.describe()


@router.get("/policies")
def policies() -> dict[str, Any]:
    """Every registered rate-limit policy, and which can refuse a call pre-emptively.

    The researched extensibility note made concrete: "adding a vendor means
    filling in a policy object, not writing a new backoff algorithm". A policy
    with no numbers is the honest reading of a vendor whose limits this build
    could not source, and it is shown as ``sourced: false`` rather than hidden.
    """
    described = {vendor: throttle_policies.describe(vendor) for vendor in throttle_policies.VENDORS}
    return {
        "count": len(described),
        "vendors": described,
        "preemptive": sorted(name for name, row in described.items() if row["preemptive"]),
        "reactive_only": sorted(name for name, row in described.items() if not row["preemptive"]),
        "fields": list(throttle_policies.PATCHABLE),
    }


@router.get("/signals")
def signals() -> dict[str, Any]:
    """The throttle table itself, one row per vendor answer, with its quote.

    Served so a reviewer can read the classification without opening a Python
    file, and so the page can say *why* a status is a throttle rather than only
    that it is one.
    """
    return {
        "count": len(throttle_classify.SIGNALS),
        "kinds": list(throttle_classify.THROTTLE_KINDS),
        "signals": throttle_classify.catalogue(),
    }


@router.get("/backoff")
def backoff_ladder(engine: ThrottleEngine = EngineDep) -> dict[str, Any]:
    """The wait ladder and its provenance, including every rung."""
    return throttle_backoff.describe()


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_connections(
    room_id: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Declared connectors, each with its live bucket and whether it is paused.

    Account-level rather than room-scoped in the same way as the batches: the
    research's own wording is "per-connection", so a connection is a thing an
    account has. ``room_id`` narrows the list rather than being required.
    """
    listed = engine.connections(room_id=room_id)
    return {
        "count": len(listed),
        "connections": listed,
        "paused": sum(1 for row in listed if row["paused"]),
        "preemptive": sum(1 for row in listed if row["effective"]["preemptive"]),
    }


@router.post("/connections", status_code=201)
def create_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a connector the throttle keeps a bucket for.

    Takes the researched step 1: the vendor, and the policy fields this account
    actually has numbers for. A vendor with no registered policy is accepted and
    recorded ``sourced: false`` - see the ``a-vendor-without-a-policy-still-runs``
    inference - because refusing it would make the researched extensibility note
    untrue.
    """
    return engine.create_connection(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connections"
    )


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, engine: ThrottleEngine = EngineDep) -> dict[str, Any]:
    """One connection: its effective policy, its bucket and its pause state."""
    return engine.connection(connection_id)


@router.patch("/connections/{connection_id}")
def patch_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Change a connection's rate-limit numbers.

    Merged over the stored policy rather than replacing it, so patching the daily
    cap does not reset the burst back to the vendor default. An unrecognised field
    is refused rather than ignored: an operator who typed ``daily_limit`` and got
    no error would believe they had raised a cap they had not.
    """
    return engine.patch_connection(
        connection_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connections/{connection_id}",
    )


@router.post("/connections/{connection_id}/pause")
def pause_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Stop sending on this connection, by hand. The researched step 5 control.

    A pause is a refusal to send, not a refusal to answer: the connection still
    reads, so whoever pressed the button can see the state they created, including
    what is left in the bucket.
    """
    return engine.set_paused(
        connection_id,
        True,
        reason=str(payload.get("reason") or ""),
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/pause",
    )


@router.post("/connections/{connection_id}/resume")
def resume_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Send again. The tokens the connection had left are carried over.

    Resuming does **not** refill the bucket. A pause is usually a response to a
    throttle, and handing a resumed connection a full bucket would put it straight
    back into the limit it was just pulled out of.
    """
    return engine.set_paused(
        connection_id,
        False,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/resume",
    )


# --------------------------------------------------------------------------- #
# Batches, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/batches")
def list_batches(
    room_id: str,
    state: str | None = Query(default=None),
    connection_id: str | None = Query(default=None),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=50, ge=1, le=500),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """This room's batches, newest first, filterable on any JSON path in them.

    Every filter resolves a dotted path inside the stored payload, so a field a
    team added to a batch later is queryable without a change to this route. A
    ``where`` this layer cannot parse is refused with 400 rather than raising past
    the error handlers: ``dsr.store.parse_where`` raises a plain ``ValueError``,
    which nothing in this feature maps, and an unmapped error is a 500 on a
    filter a caller typed.
    """
    try:
        filters = parse_where(where)
    except ValueError as exc:
        raise InvalidPayload(f"{exc}") from exc
    listed = engine.batches(
        room_id=room_id,
        state=state,
        connection_id=connection_id,
        where=filters,
        limit=limit,
    )
    counts: dict[str, int] = {}
    for row in listed:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"room_id": room_id, "count": len(listed), "counts": counts, "batches": listed}


@router.post("/rooms/{room_id}/batches", status_code=201)
def submit_batch(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Step 1: ask the bucket whether this batch may go out now.

    Answers ``proceed`` or ``defer``, and never raises: a throttle is a scheduling
    answer, not an error. The decision, the wait, the per-row idempotency keys and
    the reason are all written on the batch, so the queue can pick it up later
    without anybody re-deriving what happened.

    ``rows`` are keyed on ``external_id``. A row with none is refused, because an
    idempotency key derived from nothing is the same key for every row in the
    batch and one row's success would silence the rest.
    """
    return engine.submit(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/batches"
    )


@router.get("/rooms/{room_id}/batches/{batch_id}")
def read_batch(room_id: str, batch_id: str, engine: ThrottleEngine = EngineDep) -> dict[str, Any]:
    """One batch, with the throttle log that is the thing a reviewer actually reads."""
    return engine.batch(room_id, batch_id)


@router.post("/rooms/{room_id}/batches/{batch_id}/observe")
def observe_batch(
    room_id: str,
    batch_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 2 and 3: hand the vendor's answer in, and get the decision out.

    One call carries the whole response - status, error code, headers, whether the
    rows were accepted - because the three things that happen to it have to happen
    in this order:

    1. the quota headers are read and written to the room's meter, because they
       are the vendor's own count of what is left;
    2. the tokens are spent, because a refused call still came off the limit;
    3. the signal is classified, and a throttle defers the batch under the wait
       the vendor or the ladder asks for, keeping the keys it was first given.
    """
    return engine.observe(
        room_id,
        batch_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/batches/{batch_id}/observe",
    )


@router.post("/rooms/{room_id}/batches/{batch_id}/retry")
def retry_batch(
    room_id: str,
    batch_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """Send it again, under the keys it was first given.

    This is the researched promise, so it is worth being exact: the keys are read
    from the batch and **not** regenerated, so a row the vendor already wrote is
    updated rather than duplicated. Supplying different keys is refused, because
    "retry with the same idempotency key" and "retry with a new key" are opposites.

    A manual retry is not capped by the attempt bound. The bound governs the
    automatic drain; refusing a person's explicit action because a counter ran out
    would make the researched waiting state a dead end. ``force`` sends before the
    scheduled time, for the same reason.
    """
    return engine.retry(
        room_id,
        batch_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/batches/{batch_id}/retry",
    )


@router.post("/rooms/{room_id}/drain")
def drain_queue(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """The queue worker's route: retry everything that is due.

    The research says the throttle "runs inside the queue worker", and this
    repository already has one from WF-045. This is what it calls. Only what is
    due is retried: a batch whose wait has not elapsed is reported as
    ``scheduled`` and left alone, because sending one early is the immediate retry
    into a rate limit that produces a second refusal.
    """
    return engine.drain(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/drain"
    )


@router.get("/rooms/{room_id}/quota")
def read_quota(room_id: str, engine: ThrottleEngine = EngineDep) -> dict[str, Any]:
    """Step 2 read back: what each connection last reported, and what is left.

    Every number here came from the vendor's own headers or from the room's own
    bucket. A half the vendor did not send is ``known: false`` rather than zero,
    because HubSpot's own documentation says the daily headers are absent on OAuth
    responses and reporting a zero budget there would refuse every call forever.
    """
    return engine.quota(room_id)


@router.get("/rooms/{room_id}/throttle-log")
def read_log(
    room_id: str,
    batch_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: ThrottleEngine = EngineDep,
) -> dict[str, Any]:
    """The throttle log, oldest first.

    A log an operator reads to answer "why is this batch sitting there" is a
    story, and the story is in each entry's ``detail``: the vendor's own sentence
    or the room's own arithmetic, never a bare status code.
    """
    entries = engine.events(room_id=room_id, batch_id=batch_id, limit=limit)
    return {"room_id": room_id, "count": len(entries), "events": entries}


@router.get("/rooms/{room_id}/summary")
def read_summary(room_id: str, engine: ThrottleEngine = EngineDep) -> dict[str, Any]:
    """Counts for the page header, over this room's own batches."""
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# Seeded through the real engine over a scripted set of vendor answers, so the demo
# cannot show a shape this workflow would not produce, and seeding never opens a
# socket. Every vendor response is a literal taken from the vendor documentation
# quoted in the research, so the throttle log in the demo reads as the vendor's
# own words.
#
# Deliberately mixed, because a demo of only green teaches a reviewer nothing:
#
# * **HubSpot, healthy** - a batch the bucket allowed and the vendor accepted, so
#   the page shows a real completion with real keys;
# * **HubSpot, throttled** - a 429 with the documented ``RATE_LIMIT`` body and the
#   rate-limit headers, so the quota meter has both halves and the batch is
#   deferred with a wait taken from the vendor's own ``Retry-After``;
# * **HubSpot, locked** - a 423, so the researched two-second floor is a row
#   rather than a claim, and it is the floor rather than the ladder that decides
#   the wait;
# * **Salesforce, request limit exceeded** - a 403 whose code is
#   ``REQUEST_LIMIT_EXCEEDED``, which is the researched case where a permission
#   status is really a throttle, carrying the ``Sforce-Limit-Info`` header;
# * **Salesforce, over the attempt bound** - a batch that has been throttled five
#   times and is now ``needs_action``, which is the state where the automatic
#   queue has stopped and a person has to decide;
# * **Dataverse, no numbers** - a connection whose policy declares no burst and no
#   daily cap, so the bucket reports ``unknown_capacity`` and the room waits on the
#   vendor's own refusal. The research could not source a Dataverse number and
#   none is invented to fill the gap.

#: The vendor answers the demo plays back, one entry per batch. Every status,
#: header and body here is copied from the vendor documentation the research
#: quotes, so the demo's log reads as the vendor's own words rather than as this
#: build's invention.
DEMO_RESPONSES: dict[str, dict[str, Any]] = {
    "healthy": {
        "status": 201,
        "accepted": True,
        "headers": {
            "X-HubSpot-RateLimit-Max": "100",
            "X-HubSpot-RateLimit-Remaining": "97",
            "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            "X-HubSpot-RateLimit-Daily": "250000",
            "X-HubSpot-RateLimit-Daily-Remaining": "249812",
        },
    },
    "throttled": {
        "status": 429,
        "code": "RATE_LIMIT",
        "accepted": False,
        "headers": {
            "X-HubSpot-RateLimit-Max": "100",
            "X-HubSpot-RateLimit-Remaining": "0",
            "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            "X-HubSpot-RateLimit-Daily": "250000",
            "X-HubSpot-RateLimit-Daily-Remaining": "249001",
            "Retry-After": "45",
        },
        "note": (
            "HubSpot: 'You have reached your daily limit', errorType RATE_LIMIT, policyName DAILY."
        ),
    },
    "locked": {
        "status": 423,
        "accepted": False,
        "headers": {"Retry-After": "1"},
        "note": (
            "HubSpot 423 Locked: locks last 2 seconds, so the vendor's own floor is longer than the "
            "Retry-After it sent. The floor wins, which is the researched decision."
        ),
    },
    "request_limit": {
        "status": 403,
        "code": "REQUEST_LIMIT_EXCEEDED",
        "accepted": False,
        "headers": {"Sforce-Limit-Info": "api-usage=10018/100000; api-bursts=1/750"},
        "note": (
            "Salesforce 403 with REQUEST_LIMIT_EXCEEDED: a permission status that is really the "
            "daily cap, and the usage header the room reads its budget from."
        ),
    },
    "exhausted": {
        "status": 429,
        "accepted": False,
        "headers": {"Sforce-Limit-Info": "api-usage=10018/100000", "Retry-After": "60"},
        "note": "the sixth refusal on one batch: the bound is reached and a person decides.",
    },
    "dataverse_blind": {
        "status": 429,
        "accepted": False,
        "headers": {},
        "note": (
            "Dataverse 429 Too Many Requests. The room declares no number for this vendor because "
            "the research's Service Protection page was not found at a readable URL."
        ),
    },
}

#: The connections the demo declares. Each vendor appears once, so the page shows
#: the three policies side by side - one that can refuse a call before it is
#: sent, and two that cannot.
DEMO_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {
        "label": "Northwind Traders - HubSpot (110 requests / 10s, OAuth)",
        "vendor": "hubspot",
        "policy": {"burst": 110, "sustained": 110, "window_seconds": 10, "daily": 250_000},
    },
    {
        "label": "Contoso Health - Salesforce (daily cap by name, no published number)",
        "vendor": "salesforce",
    },
    {
        "label": "Fabrikam Logistics - Dataverse (429 documented, no sourced limit)",
        "vendor": "dataverse",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Six batches across three connections, and the states the research distinguishes.

    Every row is produced by running the real :class:`ThrottleEngine` over the real
    vendor answers in :data:`DEMO_RESPONSES`, so the demo's decisions, waits, keys
    and quota readings are what this workflow actually writes. Seeding never opens
    a socket: the connector hands in the answer.
    """
    store = RecordStore(db)
    now: datetime = context.get("now") or datetime.now(timezone.utc)
    source = "seed"
    actor = "dana"

    rooms: list[str] = [
        str(room_id)
        for room_id, _account in (context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        return "0 throttle connections (no demo rooms to scope them to)"
    room_id = rooms[0]

    engine = ThrottleEngine(store, clock=lambda: now)
    connections = [
        engine.create_connection(spec, room_id=room_id, actor=actor, source=source)
        for spec in DEMO_CONNECTIONS
    ]
    hubspot, salesforce, dataverse = connections

    def rows(count: int, prefix: str) -> list[dict[str, Any]]:
        return [
            {"external_id": f"{prefix}-{index:04d}", "email": f"{prefix}.{index}@example.test"}
            for index in range(count)
        ]

    # 1. HubSpot, healthy: allowed by the bucket, accepted by the vendor.
    healthy = engine.submit(
        room_id,
        {
            "connection_id": hubspot["id"],
            "object_name": "contacts",
            "rows": rows(3, "northwind"),
            "calls": 1,
        },
        actor=actor,
        source=source,
    )
    engine.observe(room_id, healthy["id"], DEMO_RESPONSES["healthy"], actor=actor, source=source)

    # 2. HubSpot, throttled: the 429 the vendor documents, with both halves of the
    #    rate-limit headers and a Retry-After the room honours.
    throttled = engine.submit(
        room_id,
        {
            "connection_id": hubspot["id"],
            "object_name": "deals",
            "rows": rows(4, "contoso"),
            "calls": 1,
        },
        actor=actor,
        source=source,
    )
    engine.observe(
        room_id, throttled["id"], DEMO_RESPONSES["throttled"], actor=actor, source=source
    )

    # 3. HubSpot, locked: the 423, where the researched two-second floor beats the
    #    Retry-After the vendor sent alongside it.
    locked = engine.submit(
        room_id,
        {
            "connection_id": hubspot["id"],
            "object_name": "companies",
            "rows": rows(2, "fabrikam"),
            "calls": 1,
        },
        actor=actor,
        source=source,
    )
    engine.observe(room_id, locked["id"], DEMO_RESPONSES["locked"], actor=actor, source=source)

    # 4. Salesforce, request limit exceeded: the 403 that is really a throttle, and
    #    the usage header the connection reads its budget from.
    salesforce_batch = engine.submit(
        room_id,
        {
            "connection_id": salesforce["id"],
            "object_name": "Engagement__c",
            "rows": rows(3, "adventure"),
        },
        actor=actor,
        source=source,
    )
    engine.observe(
        room_id, salesforce_batch["id"], DEMO_RESPONSES["request_limit"], actor=actor, source=source
    )

    # 5. Salesforce, past the attempt bound: five refusals, then the queue stops
    #    asking and the batch waits for a person.
    exhausted = engine.submit(
        room_id,
        {
            "connection_id": salesforce["id"],
            "object_name": "Opportunity",
            "rows": rows(2, "northwind-opp"),
        },
        actor=actor,
        source=source,
    )
    record = store.get(exhausted["id"])
    data = dict(record["data"])
    data["attempt"] = throttle_backoff.MAX_ATTEMPTS
    data["state"] = "deferred"
    store.update(exhausted["id"], data, actor=actor, source=source)
    engine.observe(
        room_id, exhausted["id"], DEMO_RESPONSES["exhausted"], actor=actor, source=source
    )

    # 6. Dataverse: a connection with no sourced numbers, so the bucket cannot
    #    refuse a call and the room waits on the vendor's own 429.
    blind = engine.submit(
        room_id,
        {
            "connection_id": dataverse["id"],
            "object_name": "engagements",
            "rows": rows(2, "fabrikam-eng"),
        },
        actor=actor,
        source=source,
    )
    engine.observe(
        room_id, blind["id"], DEMO_RESPONSES["dataverse_blind"], actor=actor, source=source
    )

    # 7. A connection paused by hand, so step 5's control is a row a reviewer can
    #    see rather than a control nothing in the demo has ever pressed.
    engine.set_paused(
        dataverse["id"],
        True,
        reason="paused for the demo: the vendor is refusing and the account owner is looking at it",
        actor=actor,
        source=source,
    )

    summary = engine.summary(room_id=room_id)
    counts = summary["counts"]
    kinds = ", ".join(summary["kinds"]) or "none"
    deferred = engine.batches(room_id=room_id, state="deferred")
    locks = sum(1 for row in deferred if (row.get("signal") or {}).get("kind") == "lock")
    deferred_count = counts.get("deferred", 0)
    return (
        f"{len(DEMO_CONNECTIONS)} connections (1 that refuses a call before it is sent, 2 that wait "
        f"on the vendor's own refusal), {summary['batches']} batches: "
        f"{counts.get('complete', 0)} complete, {deferred_count} deferred, "
        f"{counts.get('needs_action', 0)} waiting for a person. The deferrals cover these vendor "
        f"signals ({kinds}), and {locks} of them are held at the researched 2 second HubSpot lock "
        f"floor rather than the ladder. 1 connection is paused by hand."
    )
