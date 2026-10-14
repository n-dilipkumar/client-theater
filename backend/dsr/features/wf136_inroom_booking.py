"""WF-136: in-deal scheduling and next-step booking.

The seller proposes the next step at the end of a room session; the buyer picks
a slot from the seller's live availability *inside the room*; the meeting is
created, the join link is provisioned, the recording is attached, the MAP task
flips to done and the room's "next steps" panel updates.

The research is explicit that the custom work is the room integration only -
"No OSS project does booking *inside* a deal room" - so the rules live in
:mod:`dsr.inroom_booking`, where they can be tested without a request, and the
slot grid, the booking, the join link and the MAP task are borrowed from the
three workflows that already own them (WF-058, WF-059, WF-124). This module is
the three things a feature is allowed to add:

* the route table, under a prefix this feature owns,
* the mapping from this workflow's error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app,
* the demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

A note on the audit ``source``
------------------------------

Every write route builds its source from :data:`router` and hands it down, so an
audit row names the route that actually served the write. The value is the route
*template* (``POST /api/wf-136/proposals/{proposal_id}/accept``), not the
substituted id, because the feature registry publishes templates and a test can
then assert every source names a route the app really serves. A hand-written
literal would agree with a typo; a substituted id would match nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.inroom_booking import (
    InRoomBookingEngine,
    InRoomBookingError,
    inferences as booking_inferences,
    published_vocabulary,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-136-inroom-booking",
    "ticket": "WF-136",
    "name": "In-deal scheduling and next-step booking",
    "description": (
        "Book the next step from inside the deal room: the seller proposes, the "
        "buyer picks a slot from live availability, the meeting and its join link "
        "are created, the recording is attached, the MAP task is completed and the "
        "room's next-steps panel updates."
    ),
    "nav": [{"id": "wf-136-next-steps", "label": "Next steps"}],
}

#: Ticket-derived and unique, per ``docs/FEATURE-CONTRACT.md``: every path below
#: sits under it and no other feature claims it.
router = APIRouter(prefix="/api/wf-136", tags=["WF-136"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal, and deliberately the
    template rather than the substituted id: the feature registry publishes
    templates, so this is the shape a test can compare against.
    """
    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> InRoomBookingEngine:
    """An :class:`InRoomBookingEngine` over the process-wide audited store.

    Per request, for the same reason the other features build their engines per
    request: the engine holds nothing beyond the store and a clock, and building
    it here leaves both overridable in a test instead of hanging a long-lived
    object off ``app.state`` - which is a shared file this feature may not edit.
    """
    return InRoomBookingEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _booking_error(request: Request, exc: InRoomBookingError) -> JSONResponse:
    """One handler for the whole hierarchy, with the status on the exception.

    Every refusal in :mod:`dsr.inroom_booking` is the caller's to act on, and the
    status was decided where the refusal was declared rather than here, so a new
    refusal cannot ship with a status nobody chose. ``reason`` is a stable token
    the UI branches on, so the page never parses an English sentence.

    The three borrowed workflows' error types are deliberately *not* mapped:
    ``dsr.inroom_scheduling``, ``dsr.conference_links`` and
    ``dsr.mutual_action.plan`` are already mapped by WF-058, WF-059 and WF-124,
    and the host refuses a second handler for one type. This engine translates
    those at the boundary instead.
    """
    body: dict[str, Any] = {
        "error": exc.code,
        "reason": exc.reason,
        "detail": str(exc),
    }
    errors = getattr(exc, "errors", None)
    if errors:
        body["errors"] = dict(errors)
    return JSONResponse(status_code=exc.http_status, content=body)


EXCEPTION_HANDLERS = {InRoomBookingError: _booking_error}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the five proposal states, the two meeting states,
    the collections this workflow writes, the idle threshold and the proposal
    expiry, and the researched quotes each comes from.

    Served as data so a client renders its pickers from the same source the
    engine enforces against, and so an unresolved inference travels to the page
    rather than being hidden behind a number.
    """
    return published_vocabulary()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    ``unresolved`` names the entries Jev did not clear, so a reader can see how
    much of this build rests on a decision nobody verified without reading a diff.
    """
    return booking_inferences.describe()


# --------------------------------------------------------------------------- #
# Counts for the page header
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Proposals
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/proposals", summary="A room's next-step proposals")
def list_proposals(
    room_id: str,
    state: str | None = Query(default=None, description="filter by effective state"),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    proposals = engine.list_proposals(room_id, state=state)
    return {"room_id": room_id, "count": len(proposals), "proposals": proposals}


@router.post("/rooms/{room_id}/proposals", status_code=201, summary="Propose a next step")
def create_proposal(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """The seller's next step. Refused if the room has no bookable calendar, if
    the body names no summary, or if it points at a MAP task the plan does not
    hold. A refusal writes nothing."""
    return engine.create_proposal(
        room_id,
        payload,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/proposals"),
    )


@router.post("/rooms/{room_id}/auto-propose", summary="Auto-propose a slot when the room is idle")
def auto_propose(
    room_id: str,
    actor: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """The researched automation: "Auto-propose a slot when a room is idle".

    Refuses to propose twice while a proposal is awaiting an answer, and does
    nothing at all while the room is active.
    """
    return engine.auto_propose(
        room_id,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/auto-propose"),
    )


@router.post("/rooms/{room_id}/expire-lapsed", summary="Expire proposals whose clock ran out")
def expire_lapsed(
    room_id: str,
    actor: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """The one route that persists a lapse. Reads derive the same state."""
    return engine.expire_lapsed(
        room_id,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/expire-lapsed"),
    )


@router.get("/proposals/{proposal_id}", summary="Read one proposal")
def read_proposal(
    proposal_id: str,
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    return engine.read_proposal(proposal_id)


@router.get("/proposals/{proposal_id}/slots", summary="The seller's live availability, in-room")
def proposal_slots(
    proposal_id: str,
    eventTypeId: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    timeZone: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """A read that saves nothing, the same as WF-058's ``/routed-slots``."""
    params: dict[str, Any] = {}
    for key, value in (
        ("eventTypeId", eventTypeId),
        ("start", start),
        ("end", end),
        ("timeZone", timeZone),
    ):
        if value not in (None, ""):
            params[key] = value
    return engine.slots(proposal_id, params)


@router.post("/proposals/{proposal_id}/accept", summary="One-click accept: book the slot")
def accept_proposal(
    proposal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """Book the chosen slot, provision the link, flip the MAP task, update the
    room. Nothing is written before the booking succeeds."""
    return engine.accept(
        proposal_id,
        payload,
        actor=actor,
        source=_source("POST", "/proposals/{proposal_id}/accept"),
    )


@router.post("/proposals/{proposal_id}/decline", summary="Decline a proposed next step")
def decline_proposal(
    proposal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    return engine.decline(
        proposal_id,
        payload,
        actor=actor,
        source=_source("POST", "/proposals/{proposal_id}/decline"),
    )


# --------------------------------------------------------------------------- #
# Meetings
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/meetings", summary="A room's booked next steps")
def list_meetings(
    room_id: str,
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    meetings = engine.list_meetings(room_id)
    return {"room_id": room_id, "count": len(meetings), "meetings": meetings}


@router.get("/meetings/{meeting_id}", summary="Read one meeting")
def read_meeting(
    meeting_id: str,
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    return engine.read_meeting(meeting_id)


@router.post("/meetings/{meeting_id}/recording", summary="Attach a recording by URL")
def attach_recording(
    meeting_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """The automation is "auto-record and attach". This attaches the artefact and
    claims nothing about producing it; the timeline row says Watch recording."""
    return engine.attach_recording(
        meeting_id,
        payload,
        actor=actor,
        source=_source("POST", "/meetings/{meeting_id}/recording"),
    )


# --------------------------------------------------------------------------- #
# The next-steps panel
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/next-steps", summary="The room's next-steps panel")
def next_steps(
    room_id: str,
    engine: InRoomBookingEngine = EngineDep,
) -> dict[str, Any]:
    """Merges this workflow's live proposals with WF-124's open tasks, freshly on
    every read. Nothing is stored, so the panel cannot go stale."""
    return engine.next_steps(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed a bookable room, two next steps, one booked meeting and its recording.

    Everything is created by running the real engines over the real rules, so the
    demo cannot show a shape the workflow would not produce, and the slot the
    booked meeting lands on comes off the real grid. The prerequisites are the
    three merged dependencies: WF-058's embed and live token, WF-059's default
    Location, and WF-124's plan with two open tasks.

    The room is deliberately not the one WF-058 seeds, so this feature's demo is
    visible on its own page without competing with the calendar demo for one
    room. The returned string is ASCII only, because a Windows console encodes it
    as cp1252 and a non-ASCII glyph there takes the whole seeder down.
    """
    from dsr.conference_links.engine import ProvisioningEngine
    from dsr.inroom_scheduling import SchedulingEngine
    from dsr.mutual_action.plan import PlanEngine

    rooms = list(context.get("room_ids") or [])
    if not rooms:
        return "nothing seeded (no rooms to scope a next step to)"
    room_id = rooms[-1][0]
    now = context["now"]
    store = RecordStore(db)
    scheduling = SchedulingEngine(store, clock=lambda: now)
    conferencing = ProvisioningEngine(store, now=lambda: now)
    plans = PlanEngine(store, now=lambda: now)
    engine = InRoomBookingEngine(
        store,
        now=lambda: now,
        scheduling=scheduling,
        conferencing=conferencing,
        plans=plans,
    )
    source = "seed"

    # -- WF-058: a client with a live BOOKING token, an event type, an embed -- #
    client = scheduling.create_client(
        {
            "name": "WF-136 next-step room",
            "client_id": "dsr-wf136-nextstep",
            "redirect_uri": "https://rooms.example/wf-136/callback",
            "scopes": ["BOOKING", "EVENT_TYPE"],
        },
        actor="dana",
        source=source,
    )
    scheduling.grant(client["id"], {"subject": "priya"}, actor="dana", source=source)
    scheduling.create_event_type(
        {
            "eventTypeId": "evt_wf136_next_step",
            "slug": "next-step",
            "kind": "personal",
            "title": "Next step",
            "host": "priya",
            "length_minutes": 30,
            "slot_interval_minutes": 30,
            "minimum_notice_minutes": 0,
            "time_zone": "UTC",
            "days": [1, 2, 3, 4, 5, 6, 7],
            "working_hours_start": "08:00",
            "working_hours_end": "18:00",
            "location": "google_meet",
        },
        actor="dana",
        source=source,
    )
    scheduling.save_embed(
        room_id,
        {
            "clientId": client["id"],
            "eventTypeId": "evt_wf136_next_step",
            "time_zone": "UTC",
            "horizon_days": 14,
            "require_hold": False,
        },
        actor="dana",
        source=source,
    )

    # -- WF-059: a default Location that mints no conference, so seeding opens
    # no socket; the meeting is still linked, to a static room link.
    if conferencing.default_location() is None:
        conferencing.create_location(
            {
                "kind": "conference-details",
                "name": "Next-step meeting room",
                "conference_details": "https://meet.example/wf-136-next-step",
                "is_default": True,
            },
            actor="dana",
            source=source,
        )

    # -- WF-124: a plan with two open tasks the next steps can complete ------- #
    plan = plans.create_plan(
        room_id,
        {
            "name": "Northwind next steps",
            "tasks": [
                {"title": "Run the technical deep dive", "owner": "Priya", "owner_side": "seller"},
                {"title": "Complete the security review", "owner": "Ines", "owner_side": "buyer"},
            ],
        },
        actor="dana",
        source=source,
    )
    tasks = plans.tasks_of(plan["id"])

    # -- one next step awaiting an answer, one already booked ----------------- #
    engine.create_proposal(
        room_id,
        {
            "summary": "Book the technical deep dive",
            "task_id": tasks[0]["id"],
            "duration_minutes": 30,
        },
        actor="dana",
        source=source,
    )
    grid = scheduling.slots(room_id, {"eventTypeId": "evt_wf136_next_step"})
    slot = next((row for row in grid["slots"] if row["available"]), None)
    if slot is None:
        return "WF-136: room seeded but no free slot, so no booked meeting"
    booked = engine.create_proposal(
        room_id,
        {"summary": "Book the security review", "task_id": tasks[1]["id"]},
        actor="dana",
        source=source,
    )
    outcome = engine.accept(
        booked["id"],
        {
            "start": slot["start"],
            "attendee": {"name": "Ines Duarte", "email": "ines@northwind.example"},
        },
        actor="ines",
        source=source,
    )
    engine.attach_recording(
        outcome["meeting"]["id"],
        {"url": "https://record.example/wf-136-security-review"},
        actor="priya",
        source=source,
    )
    return (
        "WF-136: 1 bookable room, 2 next steps (1 booked, 1 awaiting), "
        "1 meeting linked, 1 recording attached, 2 MAP tasks"
    )
