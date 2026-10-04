"""WF-054: distribute bookings across a team by round robin.

The researched workflow, in full. An admin creates a Team and a **Round Robin**
distribution - Strict, which rotates by equal turns, or Flexible, which is
weighted by availability - with per-member weights and credits; a prospect opens
the team link, or a backend calls the researched init call; the distribution is
evaluated and a single combined availability window is returned; the prospect
books, the chosen member is credited, and the distribution advances; and if a rep
no-shows, an admin marks the prospect No-Show so credits can be credited back.

The domain logic is in :mod:`dsr.round_robin`, which this module does not own.
What lives here is the three things a workflow has to take out of shared files:
the HTTP surface, the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf054``, and the distribution is the reusable asset.** The
researched distribution is "reusable assets independent of the router" and the
same context is "reused later for reassignment", so a distribution is a
workspace asset rather than a row inside a booking. The two calls that produce
one booking are room-scoped, because a booking is taken for a room's prospect:
``/rooms/{room_id}/check`` and ``/rooms/{room_id}/init-simple``, with the second
call booking against the ``routingId`` the first returned.

**``source=`` comes from the route.** Every write below passes a string built
from ``router.prefix``, so the audit row and the route table cannot drift. A
hardcoded string inside a domain method is a defect, and the same class of bug
has shipped in this codebase before: a feature's audit log kept naming a path
the app had stopped serving. ``source`` is a *required* keyword on every writing
method of :class:`~dsr.round_robin.engine.RoundRobinEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.**
:class:`~dsr.round_robin.errors.RoundRobinError` is the base of every refusal in
the package, and each subclass carries its own ``status`` and ``code``, so one
handler answers 400 for a malformed payload and 409 for a team with nobody
assignable - which are not the same kind of problem. ``RecordNotFound`` is
deliberately *not* claimed: the core app already maps it to 404, and two handlers
for one type is a collision the host refuses.

**The two researched calls stay two calls.** ``init-simple`` answers with
``startTimes`` and a ``routingId``; ``schedule-simple`` takes a ``startTime``
that was in that list. Merging them would lose the property that makes the flow
safe, which is that the distribution is evaluated once and the slot is taken from
what it offered.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.round_robin import (
    RoundRobinEngine,
    RoundRobinError,
    inferences as round_robin_inferences,
    vocabulary as round_robin_vocab,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-054-round-robin-booking",
    "ticket": "WF-054",
    "name": "Distribute bookings across a team by round robin",
    "description": (
        "An admin builds a Team and a Round Robin distribution, either Strict, which rotates by "
        "equal turns, or Flexible, which is weighted by availability. The distribution is "
        "evaluated into one combined availability window, the prospect books, the chosen member "
        "is credited, the distribution advances, and a no-show credits the member back. An "
        "unlicensed member is excluded from assignment rather than warned about."
    ),
    "nav": [{"id": "round-robin-booking", "label": "Round robin booking"}],
}

router = APIRouter(prefix="/api/wf054", tags=["wf054"])


def get_engine(store: RecordStore = StoreDep) -> RoundRobinEngine:
    """A :class:`RoundRobinEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a clock, and an ``app.state`` entry is exactly the edit
    to the shared ``dsr/api.py`` that the feature host exists to make
    unnecessary. Building it here also leaves the clock a plain constructor
    argument, which is what lets a test drive the whole workflow with rows of its
    own and a clock it controls.
    """
    return RoundRobinEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _round_robin_error(request: Request, exc: RoundRobinError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``RoundRobinError`` is the base of every
    refusal in :mod:`dsr.round_robin` - a link type this workflow does not route,
    a missing ``guestEmail``, a slot that was never offered, a team with nobody
    licensed, a route already booked - and all of them are the caller's to fix.
    The status rides on the exception rather than being decided here, because a
    malformed request and a conflict with state that already exists are different
    problems and answering both 400 would hide the second.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {RoundRobinError: _round_robin_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the five link types, the two round robin modes, the
    license gate, the credit ledger's two directions, and the researched calls.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a key added in one place reaches every
    client at once.
    """
    return round_robin_vocab.published_vocabulary()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research for WF-054 is precise about the two modes, the license gate, the
    credit ledger's two directions, and the Distribution as a reusable asset. It
    is silent about which of union or intersection applies to which mode, the
    tie-breaks, the credit amount, and several smaller things. Those gaps are
    product behaviour rather than comments, so they are collected here for a
    reviewer to disagree with by name. A read with no side effect, so it needs no
    store.
    """
    return round_robin_inferences.describe()


@router.get("/catalog", summary="The teams and distributions the rotation reads")
def catalog(
    room_id: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Everything a reviewer needs to understand why a prospect was routed where.

    Team membership is a product surface rather than an internal detail, because
    the licensing rule is a hard gate: a reader needs to see which members are on
    which team and why a member cannot be reached, not only who was chosen.
    """
    return engine.catalog(room_id=room_id)


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Teams, distributions, routes, bookings and no-shows, by mode and outcome.

    The summary is computed over exactly the rows the same filters would return,
    so a room-scoped total above an unscoped list cannot be misread as a
    product-wide one.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #


@router.get("/teams", summary="List round robin teams")
def list_teams(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """The declared teams with their members annotated by eligibility.

    An unlicensed or unconnected member is listed with the reason beside it.
    "this prospect cannot reach Sam" is the thing an admin needs to see, and a
    member who silently vanishes from the list is the hardest version of that
    bug to diagnose.
    """
    records = engine.teams(room_id=room_id, limit=limit)
    return {"count": len(records), "teams": [engine.team_view(record) for record in records]}


@router.post("/teams", status_code=201, summary="Declare a team and its members")
def create_team(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a team. "Admin creates a Team".

    Validated in full before the row is created, so a member with no id, a
    duplicated member id, or an unreadable busy block cannot leave a
    half-configured team behind that later looks usable.
    """
    return engine.create_team(payload, actor=actor, source=f"POST {router.prefix}/teams")


@router.get("/teams/{team_id}", summary="Read one team")
def read_team(team_id: str, engine: RoundRobinEngine = EngineDep) -> dict[str, Any]:
    """One team, its members, and each member's eligibility reason."""
    return engine.team_view(engine.require_team(team_id))


# --------------------------------------------------------------------------- #
# Distributions
# --------------------------------------------------------------------------- #


@router.get("/distributions", summary="List round robin distributions")
def list_distributions(
    room_id: str | None = Query(default=None),
    mode: str | None = Query(default=None, description="strict | flexible"),
    team_ref: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """The declared distributions with their credit ledger read against the team.

    The ledger is stored keyed by member id, so it is rendered against the team's
    member list. A ledger entry for a member who has left the team would otherwise
    be invisible, and that is exactly the entry an administrator is looking for
    when asking whether a rotation is fair.
    """
    records = engine.distributions(room_id=room_id, mode=mode, team_ref=team_ref, limit=limit)
    return {
        "count": len(records),
        "distributions": [engine.distribution_view(record) for record in records],
    }


@router.post("/distributions", status_code=201, summary="Declare a round robin distribution")
def create_distribution(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a Round Robin distribution.

    "Admin creates a Team and a **Round Robin** distribution (Strict or Flexible)
    with per-member weights and credits. Link type is ``RoundRobin``."

    Only ``RoundRobin`` is accepted: the other four researched link types route
    by something this workflow does not consult, and declaring one here would make
    the link's own type a lie. The distribution is validated against its team
    rather than against a snapshot, so a team that gains a member before the
    distribution is used is visible to it.
    """
    return engine.create_distribution(
        payload, actor=actor, source=f"POST {router.prefix}/distributions"
    )


@router.get("/distributions/{distribution_id}", summary="Read one distribution")
def read_distribution(distribution_id: str, engine: RoundRobinEngine = EngineDep) -> dict[str, Any]:
    """One distribution, its ledger, and each member's weight and eligibility."""
    return engine.distribution_view(engine.require_distribution(distribution_id))


# --------------------------------------------------------------------------- #
# The researched API calls
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/check", summary="Who would this prospect reach?")
def check(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Run steps 2 to 4 for a prospect and report what *would* happen.

    Writes nothing at all - not a routing session, not a credit, not the
    distribution's cursor. It is the read-only half of ``init-simple``, for a form
    that wants to say "this reaches Sam" before the prospect commits.

    The answer is identical to what ``init-simple`` would produce, because both
    call the same selection and the same calendar arithmetic; only the
    consequences differ.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    distribution_id = str(payload.get("distribution_id") or "")
    if not distribution_id:
        raise HTTPException(
            status_code=400,
            detail="distribution_id is required; which distribution did the prospect land on?",
        )
    return engine.check(distribution_id, payload)


@router.post(
    "/rooms/{room_id}/init-simple",
    status_code=201,
    summary="Evaluate the distribution and offer the combined window",
)
def init_simple(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 2 to 4: evaluate the distribution and answer with the offered slots.

    The researched init call sends ``{link: {type: "RoundRobin", linkId}, interval}``
    and answers with a ``routingId`` plus the available ``startTimes``.

    Two outcomes are possible and both are successes. When at least one licensed
    member is free, a routing session is written holding the offered slots, the
    chosen member and the credit it will consume. When nobody is free in the
    window, the answer is outcome ``eligible`` with no slots and no session -
    that is the researched Not Scheduled path, and it is a legitimate answer for
    a busy week rather than an error.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    distribution_id = str(payload.get("distribution_id") or "")
    if not distribution_id:
        raise HTTPException(
            status_code=400,
            detail="distribution_id is required; which distribution did the prospect land on?",
        )
    return engine.init_simple(
        distribution_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/init-simple",
    )


@router.post(
    "/rooms/{room_id}/schedule-simple", status_code=201, summary="Book one of the offered slots"
)
def schedule_simple(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Step 4: book a slot this room's prospect was offered.

    The researched schedule-simple payload is ``{startTime, guestEmail}`` and it
    names the ``routingId`` in its path. Three refusals guard it, and a fourth is
    the re-check the union forces: a session that has already been booked, a
    ``startTime`` the session never offered, a ``guestEmail`` that is not the one
    the session was opened for, and a chosen member who took another booking in
    between. The last one advances the distribution to the next eligible member
    rather than refusing, because a busy rep is not the prospect's error.

    The booking, the consumed credit, the session's transition and the
    distribution's advance land in one transaction, because a booking that exists
    beside an open session is the state that would let one slot be taken twice.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    route_id = str(payload.get("routing_id") or payload.get("routingId") or "")
    if not route_id:
        raise HTTPException(
            status_code=400,
            detail="routing_id is required; the researched schedule-simple path carries the routeId",
        )
    return engine.book(
        route_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/schedule-simple",
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/routes", summary="List routing sessions")
def list_routes(
    room_id: str | None = Query(default=None),
    distribution_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="open | booked"),
    member_id: str | None = Query(default=None, description="the chosen team member"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Every routing session, newest first, with the slots it offered.

    ``state`` separates the sessions still waiting for a booking from the ones
    whose slot is gone, which is the distinction a rep reads first.
    """
    records = engine.routes(
        room_id=room_id,
        distribution_id=distribution_id,
        state=state,
        member_id_filter=member_id,
        limit=limit,
    )
    return {"count": len(records), "routes": records}


@router.get("/routes/{route_id}", summary="Read one routing session")
def read_route(route_id: str, engine: RoundRobinEngine = EngineDep) -> dict[str, Any]:
    """One routing session, its offered slots, and whether it is still open."""
    return engine.require_route(route_id)


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


@router.get("/bookings", summary="List bookings")
def list_bookings(
    room_id: str | None = Query(default=None),
    member_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="confirmed | cancelled | no_show"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Every booking, newest first, with the member it was taken on.

    A booking names the distribution it came from rather than copying its ledger,
    which is what lets a later reassignment reopen that same Distribution, as the
    research requires.
    """
    records = engine.bookings(
        room_id=room_id, member_id_filter=member_id, status=status, limit=limit
    )
    return {"count": len(records), "bookings": records}


@router.get("/bookings/{booking_id}", summary="Read one booking")
def read_booking(booking_id: str, engine: RoundRobinEngine = EngineDep) -> dict[str, Any]:
    """One booking, or 404 if it does not exist."""
    booking = engine.get_booking(booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} not found")
    return booking


@router.post("/bookings/{booking_id}/cancel", summary="Cancel a booking and release its slot")
def cancel_booking(
    booking_id: str,
    actor: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Cancel a booking, releasing its slot. It returns no credit.

    The research describes a credit-back for a no-show, not for a cancellation. A
    rep who was booked and then cancelled did not fail to attend anything, so
    treating the two alike would invent a rule the research does not state.
    """
    return engine.cancel_booking(
        booking_id, actor=actor, source=f"POST {router.prefix}/bookings/{{booking_id}}/cancel"
    )


# --------------------------------------------------------------------------- #
# Step 5: the no-show and its credit-back
# --------------------------------------------------------------------------- #


@router.get("/no-shows", summary="List recorded no-shows")
def list_no_shows(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Every no-show an admin has recorded, newest first.

    A record rather than a flag on the booking, because a rep's credit history is
    what an administrator reads when asking whether a rotation is fair.
    """
    records = engine.no_shows(room_id=room_id, limit=limit)
    return {"count": len(records), "no_shows": records}


@router.post(
    "/bookings/{booking_id}/no-show",
    status_code=201,
    summary="Mark the prospect No-Show and credit the member back",
)
def mark_no_show(
    booking_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """Step 5: mark the prospect No-Show so credits can be credited back.

    "If a rep no-shows, an admin marks the prospect No-Show in Meetings Activity
    so credits can be credited back." Admin-triggered, but the distribution's
    ``credit_back_on_no_show`` flag makes the return a standing rule, so a
    distribution with the flag off refuses rather than quietly marking the booking
    without returning the credit: an admin who pressed the button expects one or
    the other.
    """
    return engine.mark_no_show(
        booking_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/bookings/{{booking_id}}/no-show",
    )


@router.get("/credits", summary="Every credit that moved, in either direction")
def list_credit_movements(
    distribution_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: RoundRobinEngine = EngineDep,
) -> dict[str, Any]:
    """The credit ledger as a list of movements, newest first.

    Both directions, because the ledger is only legible as a history: a member
    with a low balance may have taken fewer bookings or had more no-shows credited
    back, and those are different problems with the same number.
    """
    records = engine.credit_movements(distribution_id=distribution_id, limit=limit)
    return {"count": len(records), "movements": records}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The reps the demo rotates through. The two states worth showing are the
#: unlicensed member, who is excluded from assignment by the researched hard gate,
#: and the member whose calendar is not connected, who is reported separately
#: because the fix is a calendar connection rather than a licence purchase. A
#: demo of only clean teams would teach nothing about the gate this workflow
#: exists to enforce.
DEMO_TEAMS: tuple[dict[str, Any], ...] = (
    {
        "name": "Enterprise round robin",
        "note": "Strict rotation over four licensed reps",
        "members": [
            {
                "member_id": "rr-nadia",
                "name": "Nadia A. Farouk",
                "email": "nadia@soluspring.example",
            },
            {"member_id": "rr-rui", "name": "Rui Silva", "email": "rui@soluspring.example"},
            {"member_id": "rr-priya", "name": "Priya Raman", "email": "priya@soluspring.example"},
            # Unlicensed: excluded from assignment, and the demo's honest refusal.
            {
                "member_id": "rr-tomas",
                "name": "Tomas Iglesias",
                "email": "tomas@soluspring.example",
                "licensed": False,
            },
        ],
    },
    {
        "name": "Commercial weighted round robin",
        "note": "Flexible rotation weighted by availability",
        "members": [
            {"member_id": "rr-ines", "name": "Ines Baptista", "email": "ines@soluspring.example"},
            {"member_id": "rr-kwame", "name": "Kwame Mensah", "email": "kwame@soluspring.example"},
            # Connected calendar flag off: reported separately from the license gate.
            {
                "member_id": "rr-lena",
                "name": "Lena Fischer",
                "email": "lena@soluspring.example",
                "calendar_connected": False,
            },
        ],
    },
)


def _demo_interval(now) -> dict[str, Any]:
    """A window that starts on the next working morning and runs a week.

    Built from the seeder's clock so the demo's slots are in the future whenever
    the seed runs, rather than being a fixed week that goes stale the first time
    somebody seeds after it.
    """
    from datetime import timedelta

    start = now + timedelta(days=1)
    start += timedelta(days=(7 - start.weekday()) % 7)
    start = start.replace(hour=9, minute=0, second=0, microsecond=0)
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(days=5)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
        "max_days": 14,
    }


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed two teams, two distributions, routes, bookings and a no-show.

    The routes and bookings come from running the real
    :class:`RoundRobinEngine` over the real rules, so the demo cannot show a
    shape the workflow would not produce.

    Deliberately mixed. A demo of only clean rotations would teach nothing about
    the three states this workflow exists for: an unlicensed member excluded from
    assignment, a member with no connected calendar, and a no-show that credits a
    credit back. All three are run here and expected, so the demo asserts the
    behaviour rather than a reader having to take the seed's word for it.

    Every character of the returned string is ASCII, because the seeder prints it
    on a Windows console whose codec is cp1252. A single RIGHTWARDS ARROW in one
    recovered feature broke the entire seeder.
    """
    store = RecordStore(db)
    engine = RoundRobinEngine(store, clock=lambda: context["now"])
    source = "seed"
    interval = _demo_interval(context["now"])

    teams: dict[str, str] = {}
    for spec in DEMO_TEAMS:
        created = engine.create_team(spec, actor="dana", source=source)
        teams[str(created["data"]["name"])] = created["id"]

    strict = engine.create_distribution(
        {
            "name": "Enterprise strict rotation",
            "mode": "strict",
            "team_ref": teams["Enterprise round robin"],
            "interval": interval,
            "credit_back_on_no_show": True,
        },
        actor="dana",
        source=source,
    )
    flexible = engine.create_distribution(
        {
            "name": "Commercial flexible weighting",
            "mode": "flexible",
            "team_ref": teams["Commercial weighted round robin"],
            "interval": interval,
            "members": [
                {"member_id": "rr-ines", "weight": 2.0},
                {"member_id": "rr-kwame", "weight": 1.0},
            ],
        },
        actor="dana",
        source=source,
    )

    outcomes: list[str] = []
    bookings = 0
    refusals: list[str] = []
    no_shows = 0

    # Three bookings on the strict rotation, so the ledger shows the rotation
    # advancing across three different members.
    for guest in ("m.oyelaran@northwind.example", "d.rao@contoso.example", "ops@fabrikam.example"):
        try:
            opened = engine.init_simple(
                strict["id"],
                {"guestEmail": guest, "interval": interval},
                actor="dana",
                source=source,
            )
            outcomes.append(str(opened["outcome"]))
            if opened["routing_id"] and opened["start_times"]:
                taken = engine.book(
                    str(opened["routing_id"]),
                    {"startTime": opened["start_times"][0], "guestEmail": guest},
                    actor="dana",
                    source=source,
                )
                bookings += 1
                # Mark the first booking No-Show so the credit-back is demonstrated.
                if bookings == 1:
                    engine.mark_no_show(
                        str(taken["booking_id"]),
                        {"note": "Rep did not join the call"},
                        actor="dana",
                        source=source,
                    )
                    no_shows += 1
        except RoundRobinError as exc:
            refusals.append(exc.code)

    # One flexible evaluation, left open so the page has a live routing session.
    try:
        opened = engine.init_simple(
            flexible["id"],
            {"guestEmail": "lead@adventure.example", "interval": interval},
            actor="dana",
            source=source,
        )
        outcomes.append(str(opened["outcome"]))
    except RoundRobinError as exc:
        refusals.append(exc.code)

    # The honest refusal: a distribution over a team with nobody licensed. It is
    # built and evaluated here, and it is the one configuration that is legal to
    # declare and impossible to route to.
    try:
        unlicensed = engine.create_team(
            {
                "name": "Unlicensed only",
                "members": [{"member_id": "rr-ghost", "name": "Ghost Account", "licensed": False}],
            },
            actor="dana",
            source=source,
        )
        blocked = engine.create_distribution(
            {
                "name": "Nobody can be assigned",
                "mode": "strict",
                "team_ref": unlicensed["id"],
                "interval": interval,
            },
            actor="dana",
            source=source,
        )
        engine.init_simple(
            blocked["id"], {"guestEmail": "stranger@newco.example"}, actor="dana", source=source
        )
        refusals.append("evaluated_without_refusal")
    except RoundRobinError as exc:
        refusals.append(exc.code)

    tally: dict[str, int] = {}
    for outcome in outcomes:
        tally[outcome] = tally.get(outcome, 0) + 1
    return (
        f"{len(DEMO_TEAMS)} teams plus 1 unlicensed-only team, 2 distributions, "
        f"{len(outcomes)} evaluations, {bookings} bookings, {no_shows} no-show; outcomes: "
        + ", ".join(f"{count} {name}" for name, count in sorted(tally.items()))
        + (f"; refused: {', '.join(sorted(set(refusals)))}" if refusals else "")
    )
