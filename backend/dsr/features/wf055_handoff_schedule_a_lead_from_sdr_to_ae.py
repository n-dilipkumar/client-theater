"""WF-055: hand a lead off from an SDR scheduler to an AE.

The researched workflow, in full. An admin builds a **Handoff Router** in a
workspace, defining routing paths (e.g. region to an AE pod, product line to an
AE); an SDR opens the Handoff scheduler and enters the guest's email, or the CRM
record id; the router is evaluated and **one or more routing paths** come back,
each with its own ``pathId`` and ``startTimes``; and the SDR picks a path and a
slot, and the meeting is booked with the AE as Assignee and the SDR as Booker.

The domain logic is in :mod:`dsr.handoff_scheduler`, which this module does not own.
What lives here is the three things a workflow has to take out of shared files: the
HTTP surface, the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-055``, and the router is the reusable asset.** The
researched init call is scoped to ``workspace/{workspaceId}``, so the workspace is
in the path, and a Handoff Router is declared inside one. The two calls that
produce one meeting are room-scoped, because a handoff is taken for a room's lead.

**The researched calls keep their own shape.** ``init-simple`` answers with a
``routingId`` plus ``routers[].pathResults[].startTimes``, and
``schedule-simple`` takes ``routingId``, ``routerId``, ``pathId`` and a
``booker/{userId}`` in its path with ``{startTime}`` in its body. All four
references are carried in the route rather than in the body, because the researched
path carries all four and a booking that could be made against a path the routing
never offered would lose the property that makes the flow safe.

**``source=`` comes from the route.** Every write below passes a string built from
``router.prefix``, so the audit row and the route table cannot drift. A hardcoded
string inside a domain method is a defect, and the same class of bug has shipped in
this codebase before: a feature's audit log kept naming a path the app had stopped
serving. ``source`` is a *required* keyword on every writing method of
:class:`~dsr.handoff_scheduler.engine.HandoffSchedulerEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.**
:class:`~dsr.handoff_scheduler.errors.HandoffError` is the base of every refusal in
the package, and each subclass carries its own ``status`` and ``code``, so one
handler answers 400 for a malformed request and 409 for an AE who has taken the
slot the routing offered. ``RecordNotFound`` is deliberately *not* claimed: the core
app already maps it to 404, and two handlers for one type is a collision the host
refuses.

**The derivation is served, not buried.** The research never says which calendar
operation combines the assignee and a Required invitee on one path, so it was
derived, recorded in :mod:`dsr.handoff_scheduler.availability`, put to Jev (audit
``jev-20261004T065905-27100-45006``, which selected
``intersection_with_gate_recheck`` at confidence 1.00), and served at
``GET /api/wf-055/inferences`` so a reviewer can disagree with it by name.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.handoff_scheduler import (
    HandoffError,
    HandoffSchedulerEngine,
    inferences as handoff_inferences,
    vocabulary as handoff_vocab,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-055-handoff-schedule-a-lead-from-sdr-to-ae",
    "ticket": "WF-055",
    "name": "Hand a lead off from an SDR scheduler to an AE",
    "description": (
        "An admin builds a Handoff Router in a workspace, defining routing paths such as region to "
        "an AE pod or product line to an AE. An SDR opens the Handoff scheduler with the guest's "
        "email or a CRM record id, and the router answers with one or more routing paths, each "
        "with its own pathId and startTimes. The SDR picks a path and a slot, and the meeting is "
        "booked with the AE as Assignee and the SDR as Booker. A Required invitee's availability "
        "narrows the path; a not-required invitee is invited and their calendar is not read."
    ),
    "nav": [{"id": "handoff-scheduler", "label": "Handoff scheduler"}],
}

router = APIRouter(prefix="/api/wf-055", tags=["wf055"])


def get_engine(store: RecordStore = StoreDep) -> HandoffSchedulerEngine:
    """A :class:`HandoffSchedulerEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a clock, and an ``app.state`` entry is exactly the edit to
    the shared ``dsr/api.py`` that the feature host exists to make unnecessary.
    Building it here also leaves the clock a plain constructor argument, which is
    what lets a test drive the whole workflow with rows of its own and a clock it
    controls.
    """
    return HandoffSchedulerEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _handoff_error(request: Request, exc: HandoffError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``HandoffError`` is the base of every
    refusal in :mod:`dsr.handoff_scheduler` - a request naming neither a guest
    email nor a CRM record id, a router whose path has no AE, a slot the path never
    offered, a routing already booked, an AE who took the slot in between - and all
    of them are the caller's to fix. The status rides on the exception rather than
    being decided here, because a malformed request and a conflict with state that
    has already moved are different problems and answering both 400 would hide the
    second.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {HandoffError: _handoff_error}


def _require_room(engine: HandoffSchedulerEngine, room_id: str) -> None:
    """404 when the room the handoff is being taken for does not exist.

    A handoff is scoped to a room's lead, so a room that is not there is nothing to
    act on rather than a malformed request. Checked here because the engine has no
    opinion about rooms: it does not own them.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the two request shapes, the two meeting roles, the
    researched API field names, the ``Required`` toggle, and the researched calls.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a key added in one place reaches every client
    at once.
    """
    return handoff_vocab.published_vocabulary()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research for WF-055 is precise about the two request shapes, the four fields
    the schedule call takes, the two role names, the Additional Invitees and the
    ``Required`` toggle. It is silent about which calendar operation combines the
    people on one path, about what a router that matches nothing answers, about
    which CRM fields an integrator may pass, and about whether this plugin owns
    reassignment. Those gaps are product behaviour rather than comments, so they are
    collected here for a reviewer to disagree with by name. A read with no side
    effect, so it needs no store.
    """
    return handoff_inferences.describe()


@router.get("/catalog", summary="The workspaces and routers the handoff reads")
def catalog(
    room_id: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Everything a reviewer needs to understand which paths a lead reaches.

    Each path reports its gate set, which is the set whose calendars narrow it. That
    set is a product surface rather than an internal detail, because the researched
    ``Required`` toggle changes what a path can offer and a reader needs to see whose
    calendar is deliberately *not* read.
    """
    return engine.catalog(room_id=room_id)


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Workspaces, routers, routings and meetings, by outcome and by state.

    The summary is computed over exactly the rows the same filters would return, so
    a room-scoped total above an unscoped list cannot be misread as a product-wide
    one.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


@router.get("/workspaces", summary="List workspaces")
def list_workspaces(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """The declared workspaces with their users annotated by what each may do.

    A user who cannot be assigned is listed with the reason beside it. "this lead
    cannot reach Sam" is the thing an SDR needs to see, and a user who silently
    vanishes from the list is the hardest version of that bug to diagnose.
    """
    records = engine.workspaces(room_id=room_id, limit=limit)
    return {"count": len(records), "workspaces": [engine.workspace_view(r) for r in records]}


@router.post("/workspaces", status_code=201, summary="Declare a workspace and its users")
def create_workspace(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a workspace. One SDR/AE pod, with its user records.

    Validated in full before the row is created, so a user with no id, a duplicated
    user id, an unrecognised role, or an unreadable busy block cannot leave a
    half-configured workspace behind that later looks usable.
    """
    return engine.create_workspace(payload, actor=actor, source=f"POST {router.prefix}/workspaces")


@router.get("/workspaces/{workspace_id}", summary="Read one workspace")
def read_workspace(workspace_id: str, engine: HandoffSchedulerEngine = EngineDep) -> dict[str, Any]:
    """One workspace, its users, and each user's booking and assignment rights."""
    return engine.workspace_view(engine.require_workspace(workspace_id))


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #


@router.get("/routers", summary="List Handoff Routers")
def list_routers(
    room_id: str | None = Query(default=None),
    workspace_ref: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """The declared routers with each path's gate set spelled out.

    ``gating_user_ids`` are the people whose calendars narrow the path.
    ``ignored_user_ids`` are the invitees whose availability is deliberately not
    read, which is the researched consequence of leaving the ``Required`` toggle
    alone.
    """
    records = engine.routers(room_id=room_id, workspace_ref=workspace_ref, limit=limit)
    return {"count": len(records), "routers": [engine.router_view(r) for r in records]}


@router.post("/routers", status_code=201, summary="Declare a Handoff Router and its paths")
def create_router(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a Handoff Router.

    "Admin builds a **Handoff Router** in the workspace, defining routing paths
    (e.g. region -> AE pod, product line -> AE)."

    Every path is validated against the live workspace, so a path whose assignee is
    not on the pod, or holds no assignee role, or has no connected calendar, is
    refused at declaration rather than at booking time. A path with no ``match``
    block matches every request, which is how an admin writes the catch-all that
    takes any lead the specific paths did not claim.
    """
    return engine.create_router(payload, actor=actor, source=f"POST {router.prefix}/routers")


@router.get("/routers/{router_id}", summary="Read one Handoff Router")
def read_router(router_id: str, engine: HandoffSchedulerEngine = EngineDep) -> dict[str, Any]:
    """One router, its paths, and each path's gate set."""
    return engine.router_view(engine.require_router(router_id))


# --------------------------------------------------------------------------- #
# The researched calls
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/workspaces/{workspace_id}/check",
    summary="Which paths would this lead reach?",
)
def check(
    room_id: str,
    workspace_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Run steps 2 and 3 for a lead and report what *would* happen.

    Writes nothing at all: no routing, no meeting, no calendar event. It is the
    read-only half of ``init-simple``, for an SDR who wants to see which paths a
    lead reaches before committing.

    The answer is identical to what ``init-simple`` would produce, because both call
    the same evaluation; only the consequences differ.
    """
    _require_room(engine, room_id)
    return engine.check(workspace_id, payload)


@router.post(
    "/rooms/{room_id}/workspaces/{workspace_id}/init-simple",
    status_code=201,
    summary="Evaluate the router and answer with its routing paths",
)
def init_simple(
    room_id: str,
    workspace_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 2 and 3: evaluate the router and answer with the per-path ``startTimes``.

    The researched init call sends either
    ``{type: "GuestEmailRequest", guestEmail, interval}`` or
    ``{type: "CrmRequest", id, interval}``, plus optional ``routerId`` and
    ``crmExplicits``, and answers with a ``routingId`` plus
    ``routers[].pathResults[].startTimes``.

    Two outcomes are possible and both are successes. When at least one matched path
    has a free slot the outcome is ``paths_offered``. When every matched path is
    booked out for the window the outcome is ``no_availability``, and the matched
    paths still come back with their empty ``startTimes`` - a busy week is a
    legitimate answer rather than an error, and the SDR's next move is to look at
    another path. A router that matched no path at all is the one refusal, and it
    names every path it measured and why each one did not match.
    """
    _require_room(engine, room_id)
    return engine.init_simple(
        workspace_id, payload, room_id=room_id, actor=actor, source=_source_init()
    )


def _source_init() -> str:
    """The audit source for the researched init call.

    Built from ``router.prefix`` at call time rather than at import time, so a route
    that moved cannot leave a hardcoded string behind.
    """
    return f"POST {router.prefix}/rooms/{{room_id}}/workspaces/{{workspace_id}}/init-simple"


def _source_schedule() -> str:
    """The audit source for the researched schedule call."""
    return (
        f"POST {router.prefix}/rooms/{{room_id}}/routing/{{routing_id}}/router/{{router_id}}"
        "/path/{path_id}/booker/{booker_id}/schedule-simple"
    )


@router.post(
    "/rooms/{room_id}/routing/{routing_id}/router/{router_id}/path/{path_id}"
    "/booker/{booker_id}/schedule-simple",
    status_code=201,
    summary="Book one slot on one of the paths this routing offered",
)
def schedule_simple(
    room_id: str,
    routing_id: str,
    router_id: str,
    path_id: str,
    booker_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Step 4: book the slot the SDR picked.

    The researched schedule payload is ``{startTime}`` and all four references travel
    in the path. Five refusals guard it: a routing that has already been booked, a
    router that was not one of the routers this routing consulted, a path the router
    does not declare, a booker who is not the booker the routing was opened for, and
    a ``startTime`` the path never offered or whose gate set has since taken it.

    That last one is re-checked against the live workspace rather than trusted from
    the stored ``startTimes``, because the routing was opened earlier and the AE may
    have taken another meeting since. It is answered 409 rather than 400, because it
    is state that moved rather than a request that was written wrongly.

    The meeting and the routing's transition land in one transaction, because a
    meeting that exists beside an open routing is the state that would let one slot
    be taken twice.
    """
    _require_room(engine, room_id)
    return engine.schedule_simple(
        routing_id,
        router_id,
        path_id,
        booker_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=_source_schedule(),
    )


# --------------------------------------------------------------------------- #
# Routings
# --------------------------------------------------------------------------- #


@router.get("/routings", summary="List routings")
def list_routings(
    room_id: str | None = Query(default=None),
    workspace_ref: str | None = Query(default=None),
    state: str | None = Query(default=None, description="open | booked"),
    outcome: str | None = Query(default=None, description="paths_offered | no_availability"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Every routing, newest first, with the ``startTimes`` each path offered.

    The stored ``startTimes`` are what the SDR was shown and are never rewritten
    from a calendar that has since moved, so a routing stays a record of the
    decision rather than a view of today's diary.
    """
    records = engine.routings(
        room_id=room_id, workspace_ref=workspace_ref, state=state, outcome=outcome, limit=limit
    )
    return {
        "count": len(records),
        "routings": [engine.routing_view(record) for record in records],
    }


@router.get("/routings/{routing_id}", summary="Read one routing")
def read_routing(routing_id: str, engine: HandoffSchedulerEngine = EngineDep) -> dict[str, Any]:
    """One routing, its request, and every path it offered with its start times."""
    return engine.routing_view(engine.require_routing(routing_id))


# --------------------------------------------------------------------------- #
# Meetings
# --------------------------------------------------------------------------- #


@router.get("/meetings", summary="List booked meetings")
def list_meetings(
    room_id: str | None = Query(default=None),
    assignee_ref: str | None = Query(default=None, description="the AE the lead went to"),
    booker_ref: str | None = Query(default=None, description="the SDR who opened the scheduler"),
    state: str | None = Query(default=None, description="confirmed | cancelled"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Every booked meeting, newest first, with the booker and the assignee.

    A meeting names the workspace, the router and the path it came from rather than
    copying them, which is what lets a later reassignment reopen that same routing
    context, as the research requires.
    """
    records = engine.meetings(
        room_id=room_id,
        assignee_ref=assignee_ref,
        booker_ref=booker_ref,
        state=state,
        limit=limit,
    )
    return {"count": len(records), "meetings": records}


@router.get("/meetings/{meeting_id}", summary="Read one booked meeting")
def read_meeting(meeting_id: str, engine: HandoffSchedulerEngine = EngineDep) -> dict[str, Any]:
    """One meeting, or 404 if it does not exist."""
    meeting = engine.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail=f"meeting {meeting_id} not found")
    return meeting


@router.post("/meetings/{meeting_id}/cancel", summary="Cancel a booked meeting")
def cancel_meeting(
    meeting_id: str,
    actor: str | None = Query(default=None),
    engine: HandoffSchedulerEngine = EngineDep,
) -> dict[str, Any]:
    """Cancel a meeting.

    The routing that produced it stays ``booked``, because its slot was taken and
    re-opening it would let the same slot be booked twice. A reassignment is a
    different transition and is owned by WF-063, not by this feature.
    """
    return engine.cancel_meeting(
        meeting_id, actor=actor, source=f"POST {router.prefix}/meetings/{{meeting_id}}/cancel"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The two pods the demo runs. The researched extensibility note is that
#: "workspaces partition SDR/AE pods so a third party can run one router per pod",
#: and this is that: two pods, one workspace each, one router each.
DEMO_WORKSPACES: tuple[dict[str, Any], ...] = (
    {
        "name": "EMEA commercial pod",
        "note": "Two SDR paths by region and product line, plus a catch-all",
        "users": [
            {
                "user_id": "sdr-nadia",
                "name": "Nadia A. Farouk",
                "email": "nadia@soluspring.example",
                "roles": ["booker"],
            },
            {
                "user_id": "ae-rui",
                "name": "Rui Silva",
                "email": "rui@soluspring.example",
                "roles": ["assignee"],
            },
            {
                "user_id": "ae-priya",
                "name": "Priya Raman",
                "email": "priya@soluspring.example",
                "roles": ["assignee"],
            },
            {
                "user_id": "ae-ade",
                "name": "Ade Balogun",
                "email": "ade@soluspring.example",
                "roles": ["assignee"],
            },
            {
                # An SE who is an invitee on both EMEA paths. On one path they are
                # Required and their calendar narrows it. On the other they are not,
                # and their calendar is never read. The same person, the two
                # behaviours, is the clearest demo of the researched toggle.
                "user_id": "se-sam",
                "name": "Sam Okonjo",
                "email": "sam@soluspring.example",
                "roles": ["assignee"],
            },
        ],
    },
    {
        "name": "North America enterprise pod",
        "note": "One path whose Required manager has no connected calendar",
        "users": [
            {
                "user_id": "sdr-dan",
                "name": "Dan Whitfield",
                "email": "dan@soluspring.example",
                "roles": ["booker"],
            },
            {
                "user_id": "ae-kwame",
                "name": "Kwame Mensah",
                "email": "kwame@soluspring.example",
                "roles": ["assignee"],
            },
            {
                # No connected calendar. Required on the only path in this pod, so
                # that path can offer no times at all. This is the demo's honest
                # state: the fix is a calendar connection, not a different AE.
                "user_id": "mgr-lena",
                "name": "Lena Fischer",
                "email": "lena@soluspring.example",
                "calendar_connected": False,
            },
        ],
    },
)

#: The routers the demo declares. ``match`` blocks are the researched examples:
#: region to an AE pod and product line to an AE. The last EMEA path has no match
#: block, which makes it the catch-all for any lead the two specific paths did not
#: claim.
DEMO_ROUTERS: tuple[dict[str, Any], ...] = (
    {
        "name": "EMEA discovery handoff",
        "workspace": "EMEA commercial pod",
        "paths": [
            {
                "path_id": "emea-standard",
                "name": "EMEA, any product line",
                "assignee_ref": "ae-rui",
                "match": {"region": "emea"},
                "invitees": [{"user_ref": "se-sam", "required": False}],
            },
            {
                "path_id": "emea-platform",
                "name": "EMEA platform, SE required",
                "assignee_ref": "ae-priya",
                "match": {"region": "emea", "product_line": "platform"},
                "invitees": [{"user_ref": "se-sam", "required": True}],
            },
            {
                "path_id": "any-lead",
                "name": "Catch-all, EMEA pod",
                "assignee_ref": "ae-ade",
            },
        ],
    },
    {
        "name": "North America enterprise handoff",
        "workspace": "North America enterprise pod",
        "paths": [
            {
                "path_id": "na-enterprise",
                "name": "North America, manager required",
                "assignee_ref": "ae-kwame",
                "match": {"region": "na"},
                "invitees": [{"user_ref": "mgr-lena", "required": True}],
            },
        ],
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed two pods, two routers, three routings and two meetings.

    The routings and the meetings come from running the real
    :class:`HandoffSchedulerEngine` over the real rules, so the demo cannot show a
    shape the workflow would not produce.

    Deliberately mixed. A demo of only clean handoffs would teach nothing about the
    states this workflow exists for:

    * one routing whose only path has a Required invitee with no connected calendar,
      so the path offers no times at all and the outcome is ``no_availability``;
    * one request whose ``crmExplicits`` carried a key that shadows a researched
      field, so the response reports the shadowed key rather than losing it;
    * one meeting cancelled, so the page shows a released slot as well as a live one.

    All three are run here and expected, so the demo asserts the behaviour rather
    than a reader having to take the seed's word for it.

    Every character of the returned string is ASCII, because the seeder prints it on
    a Windows console whose codec is cp1252. A single RIGHTWARDS ARROW in one
    recovered feature broke the entire seeder.
    """
    from datetime import timedelta

    store = RecordStore(db)
    engine = HandoffSchedulerEngine(store, clock=lambda: context["now"])
    source = "seed"
    room_id = (context.get("room_ids") or [(None, "demo")])[0][0]
    now = context["now"]

    start = now + timedelta(days=1)
    start += timedelta(days=(7 - start.weekday()) % 7)
    start = start.replace(hour=9, minute=0, second=0, microsecond=0)
    interval = {
        "start": start.isoformat(),
        "end": (start + timedelta(days=5)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
        "max_days": 14,
    }

    def busy(day: int, hour: int, hours: int = 2) -> dict[str, str]:
        low = start + timedelta(days=day, hours=hour)
        return {"start": low.isoformat(), "end": (low + timedelta(hours=hours)).isoformat()}

    # The SE is Required on one EMEA path, so a busy SE narrows that path below a
    # full day. The other AEs are free across the window apart from one commitment
    # each, so the demo shows a narrowed path and a wide one side by side.
    emea_spec = _demo_workspace("EMEA commercial pod")
    workspaces = {
        "EMEA commercial pod": engine.create_workspace(
            {
                **emea_spec,
                "users": [
                    {**user, "busy": _busy_for(user["user_id"], busy)}
                    for user in emea_spec["users"]
                ],
                "room_id": room_id,
            },
            actor="dana",
            source=source,
        ),
        "North America enterprise pod": engine.create_workspace(
            {
                **_demo_workspace("North America enterprise pod"),
                "room_id": room_id,
            },
            actor="dana",
            source=source,
        ),
    }

    # The router records are created and not kept. Every step below reaches its
    # router through the routing it opened, which is the route a reader takes too,
    # so a second handle here would be one more thing to keep in step.
    for spec in DEMO_ROUTERS:
        engine.create_router(
            {
                "name": spec["name"],
                "workspace_ref": workspaces[spec["workspace"]]["id"],
                "paths": spec["paths"],
                "interval": interval,
                "room_id": room_id,
            },
            actor="dana",
            source=source,
        )

    emea = workspaces["EMEA commercial pod"]
    north_america = workspaces["North America enterprise pod"]

    outcomes: list[str] = []
    bookings = 0
    cancelled = 0
    shadowed = 0
    refusals: list[str] = []
    empty_paths = 0

    def open_routing(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal empty_paths
        try:
            opened = engine.init_simple(
                workspace_id, payload, room_id=room_id, actor="dana", source=source
            )
        except HandoffError as exc:
            refusals.append(exc.code)
            return None
        outcomes.append(str(opened["outcome"]))
        for path in opened["paths"]:
            if not path["slot_count"]:
                empty_paths += 1
        return opened

    def book(opened: dict[str, Any] | None, path_id: str) -> None:
        nonlocal bookings
        if opened is None:
            return
        for path in opened["paths"]:
            if path["path_id"] != path_id or not path["start_times"]:
                continue
            try:
                engine.schedule_simple(
                    str(opened["routing_id"]),
                    str(path["router_ref"]),
                    path_id,
                    str(opened["booker_ref"]),
                    {"startTime": path["start_times"][0]},
                    room_id=room_id,
                    actor="dana",
                    source=source,
                )
            except HandoffError as exc:
                refusals.append(exc.code)
                return
            bookings += 1
            return

    # 1. A guest email request that matches two specific EMEA paths and the
    #    catch-all. The first is booked, so the page has a live handoff.
    first = open_routing(
        str(emea["id"]),
        {
            "type": "GuestEmailRequest",
            "guestEmail": "m.oyelaran@northwind.example",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "EMEA", "product_line": "platform"},
            "interval": interval,
        },
    )
    book(first, "emea-standard")

    # 2. The same shape, plus a crmExplicits key that shadows the researched
    #    guest_email. The researched field wins and the shadowed key is reported.
    second = open_routing(
        str(emea["id"]),
        {
            "type": "GuestEmailRequest",
            "guestEmail": "d.rao@contoso.example",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {
                "region": "emea",
                "product_line": "platform",
                "guest_email": "wrong.address@example.test",
            },
            "interval": interval,
        },
    )
    shadowed = len((second or {}).get("shadowed_explicit_keys") or [])
    book(second, "emea-platform")
    if second is not None:
        for meeting in engine.meetings(room_id=room_id, limit=50):
            if meeting["data"].get("routing_ref") == second["routing_id"]:
                engine.cancel_meeting(str(meeting["id"]), actor="dana", source=source)
                cancelled += 1
                break

    # 3. A CRM record request whose only path has a Required manager with no
    #    connected calendar. The path matches, so it is returned, and it offers no
    #    times at all. That is the researched behaviour of the Required toggle with
    #    an unreadable calendar behind it, and it is the demo's honest refusal of a
    #    kind that is not an error.
    open_routing(
        str(north_america["id"]),
        {
            "type": "CrmRequest",
            "id": "00Q5s00000AbCdE",
            "booker_ref": "sdr-dan",
            "crmExplicits": {"region": "na"},
            "interval": interval,
        },
    )

    # 4. The one refusal this workflow raises: a router that matched no path. The
    #    North America pod declares one path, keyed on region na, and this request
    #    is keyed on region emea, so nothing matches. The refusal names the path and
    #    why it did not match, and no routing row is written. It is run against this
    #    pod rather than the EMEA one because the EMEA router declares a catch-all
    #    path, and a catch-all matches every request by design.
    try:
        engine.init_simple(
            str(north_america["id"]),
            {
                "type": "GuestEmailRequest",
                "guestEmail": "stranger@newco.example",
                "booker_ref": "sdr-dan",
                "crmExplicits": {"region": "emea"},
                "interval": interval,
            },
            room_id=room_id,
            actor="dana",
            source=source,
        )
        refusals.append("evaluated_without_refusal")
    except HandoffError as exc:
        refusals.append(exc.code)

    tally: dict[str, int] = {}
    for outcome in outcomes:
        tally[outcome] = tally.get(outcome, 0) + 1
    return (
        f"{len(DEMO_WORKSPACES)} workspaces, 8 users, {len(DEMO_ROUTERS)} routers, "
        f"{sum(len(spec['paths']) for spec in DEMO_ROUTERS)} paths, {len(outcomes)} routings, "
        f"{bookings} meetings ({cancelled} cancelled); outcomes: "
        + ", ".join(f"{count} {name}" for name, count in sorted(tally.items()))
        + f"; {empty_paths} path(s) with no free time"
        + (f"; {shadowed} shadowed crmExplicits key(s)" if shadowed else "")
        + (f"; refused: {', '.join(sorted(set(refusals)))}" if refusals else "")
    )


def _demo_workspace(name: str) -> dict[str, Any]:
    """One declared workspace by name, with no busy blocks attached."""
    for spec in DEMO_WORKSPACES:
        if spec["name"] == name:
            return dict(spec)
    raise KeyError(name)


def _busy_for(user_id: str, busy) -> list[dict[str, str]]:
    """The busy blocks one demo user starts the seed with.

    The SE is the one that matters: they are Required on ``emea-platform``, so their
    two booked hours narrow that path's ``startTimes`` below a full day. Everyone
    else starts clear apart from one afternoon, so the demo shows a narrowed path and
    a wide one side by side rather than two identical full diaries.
    """
    if user_id == "se-sam":
        return [busy(0, 10), busy(2, 13)]
    if user_id == "ae-ade":
        return [busy(1, 14)]
    if user_id == "ae-kwame":
        return [busy(3, 15)]
    return []
