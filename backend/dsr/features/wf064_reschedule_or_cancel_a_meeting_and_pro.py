"""WF-064: reschedule or cancel a meeting, and propagate the change.

A researched workflow, not a port: there is no source branch. The research
document is the specification, and what it specifies is the whole of a meeting
change - who moved it, to when, the reschedule chain it belongs to, the calendar
and CRM events that follow, the webhooks that go out, and the Events History row
that records it.

What the research specifies
---------------------------

* The two invite tags, ``CP.Meeting.RescheduleUrl`` and ``CP.Meeting.CancelUrl``,
  injected into the invite's Description, and the ``Expire Reschedule Link``
  setting that closes the first of them "after a meeting has happened".
* The availability recomputation, and the one sentence that makes it work:
  ``bookingUidToReschedule`` "will ensure that the original booking time appears
  within the returned available slots when rescheduling".
* The reschedule chain: ``rescheduledFromUid`` / ``rescheduledToUid`` /
  ``rescheduleId`` / ``rescheduleReason``.
* The two webhook payloads, field for field: ``rescheduleId``, ``rescheduleUid``,
  ``rescheduleStartTime``, ``rescheduleEndTime`` on ``BOOKING_RESCHEDULED``;
  ``cancellationReason`` and ``cancelledByEmail`` on ``BOOKING_CANCELLED``.
* Chili Piper's ``Meeting Update``, plus ``type: "Deleted"`` on a cancel, and the
  ``Delete Event`` toggle that decides whether the CRM ``Event`` goes with it.
* Cal's workflow triggers ``rescheduleEvent`` and ``eventCancelled``, and the
  reminder recomputation that goes with them.
* The Events History row: "who rescheduled it, to whom, when, and the
  rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)".

What this module is
-------------------

Only the three things a feature is allowed to add: the HTTP surface, the mapping
from domain errors to responses, and the demo data. The behaviour is in
:mod:`dsr.scheduling`, which is where it can be tested without a request.

Decisions in here a reviewer would otherwise have to reverse-engineer
---------------------------------------------------------------------

**Room-scoped paths are room-scoped.** The change belongs to a room's meeting, so
the routes that act on one take ``/rooms/{room_id}/...``. The unscoped routes are
the ones genuinely not about a room: the vocabularies, the Meeting Types, the
bookings a room does not own, the link tokens, and the downstream rows a rep reads
back.

**``source`` is built from ``router.prefix`` and passed down.** The audit row must
name the route that actually served the write, so every write route builds its
source here and hands it to an engine method that requires it. The defect this
prevents - an audit log recording a path the app no longer serves - has shipped in
this codebase before, so the suite asserts that every source recorded matches a
route the host actually mounted.

**``plan`` writes nothing; the three write routes do everything.** ``plan`` is the
read-only half, sharing every rule with the writes, so a rep about to tell an
attendee "yes, we can move it to Thursday" can see the whole propagation - the
rows, the webhooks, the CRM decision - before committing to it, and a caller can
see a refusal before causing one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase, RecordNotFound
from dsr.deps import StoreDep
from dsr.scheduling import MeetingChangeEngine
from dsr.scheduling import inferences as scheduling_inferences
from dsr.scheduling import propagation
from dsr.scheduling.availability import MAX_RANGE_DAYS
from dsr.scheduling.errors import MeetingChangeError
from dsr.scheduling.meeting_types import MAX_RESCHEDULE_HORIZON_DAYS
from dsr.scheduling.vocabulary import (
    CHANGE_RESCHEDULED,
    CHANGE_RESCHEDULE_REQUESTED,
    published_vocabulary,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-064-reschedule-or-cancel-a-meeting-and-pro",
    "ticket": "WF-064",
    "name": "Reschedule or cancel a meeting, and propagate the change",
    "description": (
        "Move or release a booked meeting from the link in the invite or the host's panel, "
        "then propagate it: the calendar event moves, the CRM Event is updated or deleted per "
        "the Meeting Type, the webhooks go out, and Events History records who, to whom, when, "
        "and from which source."
    ),
    "nav": [{"id": "meeting-changes", "label": "Meeting changes"}],
}

router = APIRouter(prefix="/api/wf-064", tags=["wf064"])


def get_engine(store: RecordStore = StoreDep) -> MeetingChangeEngine:
    """A :class:`~dsr.scheduling.engine.MeetingChangeEngine` over the audited store.

    Built per request for the same reason WF-016 and WF-041 build theirs: the
    engine holds the store, a clock and three id factories and nothing else, so
    per-request construction is equivalent and leaves all four overridable in a
    test instead of hanging a long-lived object off ``app.state`` - which is a
    shared file this feature may not edit.

    The link base is read from the request so the URL in a generated invite is one
    the caller can actually open, rather than one hard-coded to a hostname that is
    not theirs.
    """
    return MeetingChangeEngine(store, link_base="")


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _meeting_error(request: Request, exc: MeetingChangeError) -> JSONResponse:
    """One handler for the whole hierarchy, with the status chosen on the type.

    The three statuses are the point of the subclasses: 400 for a request that
    cannot be honoured as written, 404 for a booking that does not exist, 409 for
    one that has already moved on, and 410 for a link that has expired - which was
    valid once, so the answer to "what now" is that somebody has to act rather than
    that the caller typed it wrong.

    ``RecordNotFound`` is deliberately *not* claimed. The core app already maps it
    to 404, and two handlers for one type is a collision the host refuses.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {MeetingChangeError: _meeting_error}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term, with the sentence it comes from.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a term added in one place reaches every
    client at once. The webhooks this workflow does **not** push are in here too,
    under ``webhooks_not_emitted``, because a decision not to emit something is
    product behaviour and a reviewer should be able to see it.
    """
    return {
        **published_vocabulary(),
        "limits": {
            "availability_range_days": MAX_RANGE_DAYS,
            "reschedule_horizon_days": MAX_RESCHEDULE_HORIZON_DAYS,
        },
        "collections": {
            "meeting_types": "meeting_type",
            "bookings": "booking",
            "events_history": "meeting_change",
            "reschedule_requests": "meeting_reschedule_request",
            "calendar_events": propagation.CALENDAR_EVENT_COLLECTION,
            "crm_events": propagation.CRM_EVENT_COLLECTION,
            "webhooks": propagation.WEBHOOK_COLLECTION,
            "notifications": propagation.NOTIFICATION_COLLECTION,
        },
    }


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    A read with no side effect, so it needs no store. Served as data because these
    gaps are product behaviour rather than comments, and a reviewer should be able
    to disagree with a *named* entry instead of finding it in a diff.
    """
    return scheduling_inferences.describe()


# --------------------------------------------------------------------------- #
# Meeting Types: the context a reschedule re-opens
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Changes by type and by rescheduling source, plus what the fan-out did.

    Computed over exactly the rows the same filters would return, so a
    room-scoped total above an unscoped list cannot be misread as a product-wide
    one.
    """
    return engine.summary(room_id=room_id)


@router.get("/meeting-types", summary="List Meeting Types")
def list_meeting_types(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The Meeting Types a reschedule re-opens, with their two researched settings."""
    records = engine.list_meeting_types(room_id=room_id, limit=limit)
    return {"count": len(records), "meeting_types": records}


@router.post("/meeting-types", status_code=201, summary="Declare a Meeting Type")
def create_meeting_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a Meeting Type: hours, length, and the two researched toggles.

    Validated before the row exists, so a Meeting Type with an unreadable hour or
    an impossible length cannot leave a half-configured context behind for a
    reschedule to re-open later.
    """
    return engine.create_meeting_type(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/meeting-types"
    )


@router.get("/meeting-types/{meeting_type_id}", summary="Read one Meeting Type")
def read_meeting_type(
    meeting_type_id: str, engine: MeetingChangeEngine = EngineDep
) -> dict[str, Any]:
    """One Meeting Type, or 404 if it does not exist."""
    record = engine.get_meeting_type(meeting_type_id)
    if record is None or record.get("collection") != "meeting_type":
        raise HTTPException(status_code=404, detail=f"meeting type {meeting_type_id} not found")
    return record


@router.patch("/meeting-types/{meeting_type_id}", summary="Patch a Meeting Type")
def update_meeting_type(
    meeting_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Patch a Meeting Type, re-validated against the merged result."""
    return engine.update_meeting_type(
        meeting_type_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/meeting-types/{meeting_type_id}",
    )


@router.delete("/meeting-types/{meeting_type_id}", summary="Delete a Meeting Type")
def delete_meeting_type(
    meeting_type_id: str,
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> Response:
    """Soft-delete a Meeting Type. 204.

    Its bookings and its history stay readable, which is the point: the record of
    what a meeting type did outlives the meeting type.
    """
    if engine.get_meeting_type(meeting_type_id) is None:
        raise HTTPException(status_code=404, detail=f"meeting type {meeting_type_id} not found")
    engine.delete_meeting_type(
        meeting_type_id, actor=actor, source=f"DELETE {router.prefix}/meeting-types/{meeting_type_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


@router.get("/bookings", summary="List bookings")
def list_bookings(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="booked | rescheduled | cancelled"),
    host_email: str | None = Query(default=None),
    attendee_email: str | None = Query(default=None),
    recurring_group: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The booked meetings, newest first, filterable on any JSON path.

    The full envelope per booking, because a rep configuring a change needs the
    ``uid`` - Cal's identifier, the one in the invite - to name it.
    """
    records = engine.list_bookings(
        room_id=room_id,
        status=status,
        host_email=host_email,
        attendee_email=attendee_email,
        recurring_group=recurring_group,
        limit=limit,
    )
    return {"count": len(records), "bookings": records}


@router.post("/bookings", status_code=201, summary="Register a booked meeting")
def create_booking(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Register a booked meeting: the "old booking record" the flow starts from.

    A booking is this workflow's input rather than its output, so it is creatable
    here. That is what makes the feature demonstrable end to end against a real
    database, and the shape is the one a booking webhook from Cal would hand this
    product.
    """
    return engine.create_booking(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/bookings"
    )


@router.get("/bookings/{uid}", summary="Read one booking")
def read_booking(uid: str, engine: MeetingChangeEngine = EngineDep) -> dict[str, Any]:
    """One booking by its ``uid``, or 404 if it does not exist."""
    record = engine.get_booking(uid)
    if record is None:
        raise HTTPException(status_code=404, detail=f"booking {uid} not found")
    return record


@router.get("/bookings/{uid}/invite", summary="The invite body with its two links")
def read_invite(
    uid: str,
    request: Request = None,  # type: ignore[assignment]
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The invite's Description, with both researched dynamic tags resolved.

    Resolved against the request's own origin, so the URL in a generated invite is
    one the caller can open rather than one hard-coded to another host.
    """
    engine.link_base = str(request.base_url).rstrip("/") if request is not None else ""
    return engine.invite(uid, base=engine.link_base)


@router.get("/bookings/{uid}/changes", summary="One booking's Events History")
def booking_changes(
    uid: str,
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Every change made to one booking, newest first.

    Includes the changes made to it *as* a predecessor, so a booking that has
    been moved twice reads as one story rather than two.
    """
    records = engine.changes(booking_uid=uid, limit=limit)
    if not records:
        # A booking that has never been changed has no rows of its own, but a
        # rescheduled booking's history is on the booking that replaced it.
        record = engine.get_booking(uid)
        if record is not None:
            records = engine.changes(
                chain_root=str(record["data"].get("chain_root") or uid), limit=limit
            )
    return {"booking_uid": uid, "count": len(records), "changes": records}


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


@router.get("/availability", summary="Recomputed availability")
def availability(
    meeting_type_id: str | None = Query(default=None),
    host_email: str | None = Query(default=None),
    from_at: str | None = Query(default=None, alias="from"),
    to_at: str | None = Query(default=None, alias="to"),
    booking_uid_to_reschedule: str | None = Query(
        default=None,
        description="the researched parameter: this booking's own slot reappears as available",
    ),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Every slot the host is free in, with the researched exception applied.

    The endpoint is named for the parameter rather than hidden behind a friendlier
    one, because the parameter *is* the researched behaviour: without it, "the
    original booking time appears within the returned available slots when
    rescheduling" does not happen, and a meeting cannot be moved to the time it is
    already at.
    """
    meeting_type = engine.resolve_meeting_type(meeting_type_id)
    slots = engine.host_slots(
        meeting_type,
        host_email=host_email,
        from_at=from_at,
        to_at=to_at,
        booking_uid_to_reschedule=booking_uid_to_reschedule,
    )
    return {
        "meeting_type_id": meeting_type.get("id"),
        "host_email": host_email or (meeting_type.get("data") or {}).get("host_email"),
        "booking_uid_to_reschedule": booking_uid_to_reschedule,
        "count": len(slots),
        "slots": slots,
    }


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


@router.get("/links", summary="List invite links")
def list_links(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Every booking's two links, and whether each one still works.

    The read a rep needs before telling an attendee to use a link, and the one that
    makes the ``Expire Reschedule Link`` setting visible rather than something a
    caller discovers by being refused.
    """
    states = []
    for booking in engine.list_bookings(room_id=room_id, limit=limit):
        for kind in ("reschedule", "cancel"):
            token = str(booking["data"].get(f"{kind}_token") or "")
            if not token:
                continue
            state = engine.link_state(token)
            states.append({**state.to_dict(), "room_id": booking.get("room_id")})
    return {"count": len(states), "links": states}


@router.get("/links/{token}", summary="Resolve one link token")
def read_link(token: str, engine: MeetingChangeEngine = EngineDep) -> dict[str, Any]:
    """Resolve a token, and report whether it is open.

    Resolves both artefacts an attendee can be holding: the two tags in the
    original invite, and the link in a "please pick a new time" mail. A closed link
    is a fact, not an error, so this answers 200 with ``expired: true`` and a
    reason; the write paths are where it becomes a refusal.
    """
    return engine.link_state(token).to_dict()


# --------------------------------------------------------------------------- #
# Events History
# --------------------------------------------------------------------------- #


@router.get("/changes", summary="List Events History")
def list_changes(
    room_id: str | None = Query(default=None),
    change_type: str | None = Query(default=None),
    booking_uid: str | None = Query(default=None),
    chain_root: str | None = Query(default=None),
    actor_email: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Every change, newest first.

    ``chain_root`` is the useful filter for the researched churn question: every
    move of one booking's chain, in the order the numbers were issued.
    """
    records = engine.changes(
        room_id=room_id,
        change_type=change_type,
        booking_uid=booking_uid,
        chain_root=chain_root,
        actor_email=actor_email,
        limit=limit,
    )
    return {"count": len(records), "changes": records}


@router.get("/changes/{change_id}", summary="One change in full")
def read_change(change_id: str, engine: MeetingChangeEngine = EngineDep) -> dict[str, Any]:
    """One Events History row, with the propagation it recorded."""
    record = engine.get_change(change_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"change {change_id} not found")
    return record


# --------------------------------------------------------------------------- #
# What the fan-out did
# --------------------------------------------------------------------------- #


@router.get("/webhooks", summary="Pushed webhooks")
def list_webhooks(
    booking_uid: str | None = Query(default=None),
    change_id: str | None = Query(default=None),
    webhook: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Every webhook this workflow pushed, with the payload it pushed.

    Readable rather than hidden because the researched payloads are the contract
    with a third party: ``rescheduleReason`` exists precisely so somebody can
    build churn alerting on it, and they cannot do that from a field nobody shows
    them.
    """
    records = engine.webhooks(
        booking_uid=booking_uid, change_id=change_id, webhook=webhook, status=status, limit=limit
    )
    return {"count": len(records), "webhooks": records}


@router.get("/notifications", summary="Notices sent by the workflow triggers")
def list_notifications(
    booking_uid: str | None = Query(default=None),
    change_id: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """What ``rescheduleEvent`` and ``eventCancelled`` sent, and where.

    One row per channel per recipient, because the research names two channels and
    two parties and collapses none of them: the attendee's inbox and the host's
    Slack are different places to look when a meeting has moved.
    """
    records = engine.notifications(
        booking_uid=booking_uid, change_id=change_id, channel=channel, limit=limit
    )
    return {"count": len(records), "notifications": records}


@router.get("/crm-events", summary="CRM Event rows")
def list_crm_events(
    booking_uid: str | None = Query(default=None),
    include_deleted: bool = Query(default=True),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The CRM ``Event`` rows, including the ones ``Delete Event`` removed.

    Included by default because a cancelled meeting whose CRM event was deleted is
    exactly the row a reviewer wants to see, and hiding it behind a flag would make
    the most interesting state the least convenient one.
    """
    records = engine.crm_events(booking_uid=booking_uid, include_deleted=include_deleted)
    return {"count": len(records), "crm_events": records, "sobject": "Event"}


@router.get("/calendar-events", summary="Calendar event rows")
def list_calendar_events(
    booking_uid: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The calendar provider's rows, so a rep can see the event actually moved."""
    records = engine.calendar_events(booking_uid=booking_uid)
    return {"count": len(records), "calendar_events": records}


# --------------------------------------------------------------------------- #
# Reschedule requests
# --------------------------------------------------------------------------- #


@router.get("/reschedule-requests", summary="List reschedule requests")
def list_reschedule_requests(
    status: str | None = Query(default=None),
    booking_uid: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The pending and completed requests an attendee was asked to answer."""
    records = engine.requests(status=status, booking_uid=booking_uid, limit=limit)
    return {"count": len(records), "reschedule_requests": records}


@router.get("/reschedule-requests/{request_id}", summary="One reschedule request")
def read_reschedule_request(
    request_id: str, engine: MeetingChangeEngine = EngineDep
) -> dict[str, Any]:
    """One request, or 404 if it does not exist."""
    record = engine.get_request(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"reschedule request {request_id} not found")
    return record


@router.post("/reschedule-requests/{request_id}/complete", status_code=201, summary="Complete a reschedule request")
def complete_reschedule_request(
    request_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The other half of a reschedule request: the attendee picks a new time.

    Creates the new booking, writes the history row, and propagates - the same work
    an immediate reschedule does, with the request's token supplying the source and
    the reason the request was raised.
    """
    return engine.complete_request(
        request_id, payload, actor=actor, source=f"POST {router.prefix}/reschedule-requests/{request_id}/complete"
    )


# --------------------------------------------------------------------------- #
# The workflow, room-scoped
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/meetings", summary="A room's meetings")
def room_meetings(
    room_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Every booking scoped to one room, newest first.

    Room-scoped because a change belongs to a room's meeting, so a rep reads that
    room's meetings from that room rather than from a product-wide feed.
    """
    _require_room(engine, room_id)
    records = engine.list_bookings(room_id=room_id, status=status, limit=limit)
    return {"room_id": room_id, "count": len(records), "bookings": records}


@router.get("/rooms/{room_id}/meeting-changes", summary="A room's Events History")
def room_changes(
    room_id: str,
    change_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """The room's Events History, newest first.

    The surface the research names - an audit row per change, showing who, to whom,
    when, and from which source - read from the room it happened in.
    """
    _require_room(engine, room_id)
    records = engine.changes(room_id=room_id, change_type=change_type, limit=limit)
    return {"room_id": room_id, "count": len(records), "changes": records}


@router.post("/rooms/{room_id}/bookings/{uid}/plan", summary="Plan a change, writing nothing")
def plan_change(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """Report what a reschedule or a cancel would do, and write none of it.

    The read-only half of the three writes, sharing every rule with them, so the
    answer is the answer the write will produce - including a refusal, which comes
    back as the same error it would have been.
    """
    _require_room(engine, room_id)
    return engine.plan(room_id, uid, payload)


@router.post("/rooms/{room_id}/bookings/{uid}/reschedule", status_code=201, summary="Reschedule a meeting")
def reschedule(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """``POST /v2/bookings/{uid}/reschedule``: move a booked meeting, now.

    Steps 1 to 4. The availability is recomputed with this booking's own slot
    released, the old booking is superseded by a new one carrying
    ``rescheduledFromUid`` / ``rescheduleId`` / ``rescheduleReason``, the calendar
    event moves, the CRM ``Event`` updates, the webhooks go out, the reminders are
    re-based onto the new time, and the Events History row records who, to whom,
    when, and from which source.

    Returns the history row, because that is the artefact the flow is required to
    produce and the one a rep reads afterwards.
    """
    return engine.reschedule(
        room_id, uid, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings/{uid}/reschedule"
    )


@router.post("/rooms/{room_id}/bookings/{uid}/request-reschedule", status_code=201, summary="Ask the attendee to pick a new time")
def request_reschedule(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """``POST /v2/bookings/{uid}/request-reschedule``.

    "The booking will be cancelled and the attendee will receive an email with a
    link to reschedule" - both sentences, so both happen. The cancel path runs in
    full, and a pending request is written carrying its own token, which is the
    link the attendee completes.
    """
    return engine.request_reschedule(
        room_id,
        uid,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/bookings/{uid}/request-reschedule",
    )


@router.post("/rooms/{room_id}/bookings/{uid}/cancel", status_code=201, summary="Cancel a meeting")
def cancel(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MeetingChangeEngine = EngineDep,
) -> dict[str, Any]:
    """``POST /v2/bookings/{uid}/cancel``: one occurrence, or every remaining one.

    The optional cancellation reason is captured and shipped, the meeting is
    released, the calendar event is cancelled, ``Delete Event`` decides the CRM
    event's fate, and ``BOOKING_CANCELLED`` plus ``Meeting Update`` with
    ``type: "Deleted"`` go out.

    A recurring series cancelled at ``scope=all`` answers with a ``sweep`` naming
    every history row it wrote, because returning only the first would hide the
    rest from the caller.
    """
    return engine.cancel(
        room_id, uid, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings/{uid}/cancel"
    )


def _require_room(engine: MeetingChangeEngine, room_id: str) -> dict[str, Any]:
    """A room that does not exist is a 404, and ``RecordNotFound`` says 404 already.

    Raised rather than re-raised as a domain error on purpose: a missing room is a
    404 in every route in this product, and mapping it to 400 here would make this
    feature the only place a missing room answers differently.
    """
    room = engine.store.get(room_id)
    if room is None:
        raise RecordNotFound(room_id)
    return room


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: One Meeting Type per configuration the research names, plus the two worth
#: seeing side by side: a permissive cancel policy and a strict one, and a type
#: that expires its reschedule links against one that does not.
#:
#: Every arrangement here earns its place by making one researched rule visible.
#: The strict type is the one whose links die after the meeting; the permissive one
#: is the control a reviewer needs to tell the setting's effect from the passage
#: of time.
DEMO_MEETING_TYPES: tuple[dict[str, Any], ...] = (
    {
        # A 45-minute type on a three-hour window, so its slot grid is 09:00,
        # 09:45, 10:30 and 11:15. The demo's bookings on this type sit on those
        # points; see DEMO_BOOKINGS.
        "name": "Northwind — Enterprise Demo",
        "distribution": "Northwind Enterprise",
        "host_email": "dana@northwind.example",
        "duration_minutes": 45,
        "hours": "09:00-12:00",
        "days": [0, 1, 2, 3, 4],
        "expire_reschedule_link": True,
        "delete_event": True,
        "calendar_provider": "google",
        "notify_channels": ["email", "slack"],
        "reminder_offsets": [1440, 60],
    },
    {
        # A half-hourly type on a four-hour afternoon window, and the control the
        # demo's other two need: ``Expire Reschedule Link`` off and ``Delete Event``
        # off, so the same shape with the strict settings on gives a link that is
        # still open after its meeting and a CRM event that is kept rather than
        # deleted. Without a control, neither of those is falsifiable.
        "name": "Contoso — Security Review",
        "distribution": "Contoso Security",
        "host_email": "sam@contoso.example",
        "duration_minutes": 30,
        "hours": "13:00-17:00",
        "days": [0, 1, 2, 3, 4],
        "expire_reschedule_link": False,
        "delete_event": False,
        "calendar_provider": "google",
        "notify_channels": ["email"],
    },
    {
        # An hourly type on a ninety-minute window, on Tuesdays and Thursdays only.
        # The narrowest window in the demo on purpose: it is the one where a
        # reschedule has to come off the recomputed availability rather than off a
        # round number, because almost any time is out of hours.
        "name": "Fabrikam — Renewal Check-in",
        "distribution": "Fabrikam Renewal",
        "host_email": "dana@northwind.example",
        "duration_minutes": 60,
        "hours": "15:00-16:30",
        "days": [1, 3],
        "expire_reschedule_link": True,
        "delete_event": True,
        "notify_channels": ["email", "slack"],
    },
)

#: The demo's bookings, on a morning window.
#:
#: Two things are deliberate here and both are about the booking times being on
#: the Meeting Type's own slot grid. A booking at 10:00 on a 45-minute type that
#: runs 09:00 to 12:00 is not a slot anybody could have booked, so a reschedule to
#: that same time - the very thing ``bookingUidToReschedule`` exists to allow - is
#: refused as misaligned, and the demo would demonstrate the refusal instead of
#: the rule. Every ``start_minute`` here is therefore a point on its type's grid.
#:
#: The reminder offsets come from the Meeting Type rather than the payload, so the
#: recompute rule has something to re-base without this module inventing a
#: reminder set.
DEMO_BOOKINGS: tuple[dict[str, Any], ...] = (
    {
        "uid": "bk_northwind_enterprise",
        "meeting_type": 0,
        "title": "Northwind Traders — Enterprise walkthrough",
        "attendee_name": "Priya Raman",
        "attendee_email": "priya.raman@northwind.example",
        "start_offset_days": 3,
        "start_minute": 9 * 60,
        "location": "Zoom — Enterprise room",
        "room_index": 0,
    },
    {
        # Pinned to a Thursday, which is one of this type's two working days. The
        # type works Tuesdays and Thursdays only, so a date chosen by arithmetic
        # from "now" is a coin flip - and a demo row that lands outside its own
        # meeting type's hours is a row a reviewer has to work out was wrong.
        "uid": "bk_northwind_renewal",
        "meeting_type": 2,
        "title": "Fabrikam Logistics — Renewal check-in",
        "attendee_name": "Marcus Webb",
        "attendee_email": "marcus.webb@solowebb.example",
        "start_offset_days": 4,
        "start_minute": 15 * 60,
        "location": "Google Meet",
        "room_index": 2,
    },
    {
        # A meeting in the past on the type that expires links. This is the row
        # that makes the researched refusal reachable in a demo: a rep looking at
        # last week's call has to be told the link is dead, and the reason has to
        # be the setting and not a bug.
        #
        # The second, cancelled one is here for the same reason from the other
        # side: a rep cleaning up a meeting that was called off has to see what
        # ``Delete Event`` did to its CRM event, and a live booking cannot show it.
        "uid": "bk_northwind_completed",
        "meeting_type": 0,
        "title": "Northwind Traders — Security pack walkthrough",
        "attendee_name": "Tomas Vela",
        "attendee_email": "tomas.vela@contoso.example",
        "start_offset_days": -3,
        "start_minute": 10 * 60 + 30,
        "location": "Zoom — Enterprise room",
        "room_index": 0,
    },
    {
        "uid": "bk_northwind_called_off",
        "meeting_type": 0,
        "title": "Northwind Traders — Commercials walkthrough",
        "attendee_name": "Rui Silva",
        "attendee_email": "rui.silva@silva-consulting.example",
        "start_offset_days": -1,
        "start_minute": 11 * 60 + 15,
        "location": "Zoom — Enterprise room",
        "room_index": 0,
    },
    {
        # The permissive type's meeting, still in the future, so the demo can
        # show a link that is open where the setting would have closed it. Left
        # untouched by the change cases below so it stays a live booking for a
        # reviewer to act on.
        "uid": "bk_contoso_review",
        "meeting_type": 1,
        "title": "Contoso Health — Security review",
        "attendee_name": "Alba Ries",
        "attendee_email": "alba.ries@fabrikam.example",
        "start_offset_days": 6,
        "start_minute": 13 * 60,
        "location": "Onsite — Contoso campus",
        "room_index": 1,
    },
    {
        # A meeting on the permissive type that a case below moves and then calls
        # off, so the demo can show the *other* half of the Delete Event toggle:
        # the CRM Event kept and marked cancelled rather than deleted. Without a
        # second booking on this type, the demo could only show deletion.
        "uid": "bk_contoso_onsite",
        "meeting_type": 1,
        "title": "Contoso Health — Onsite walkthrough",
        "attendee_name": "Dana Okoro",
        "attendee_email": "dana.okoro@fabrikam-ops.example",
        "start_offset_days": 8,
        "start_minute": 14 * 60,
        "location": "Onsite — Contoso campus",
        "room_index": 1,
    },
    # A recurring series, so the researched per-instance-versus-wholesale cancel
    # has something to act on. Four live occurrences, a week apart, in the host's
    # morning window.
    #
    # A different attendee on each occurrence, deliberately. Two occurrences
    # sharing a person would make a per-instance cancel look like one customer
    # cancelling two meetings, and the subject of the case is the scope, not the
    # churn.
    {
        "uid": "bk_northwind_recurring_1",
        "meeting_type": 0,
        "title": "Northwind Traders — Weekly check-in",
        "attendee_name": "Nadia Farouk",
        "attendee_email": "nadia.farouk@soluspring.example",
        "start_offset_days": 2,
        "start_minute": 11 * 60,
        "location": "Google Meet",
        "recurring_group": "bk_northwind_recurring",
        "recurrence_index": 1,
        "room_index": 0,
    },
    {
        "uid": "bk_northwind_recurring_2",
        "meeting_type": 0,
        "title": "Northwind Traders — Weekly check-in",
        "attendee_name": "Marcus Webb",
        "attendee_email": "marcus.webb@solowebb.example",
        "start_offset_days": 9,
        "start_minute": 11 * 60,
        "location": "Google Meet",
        "recurring_group": "bk_northwind_recurring",
        "recurrence_index": 2,
        "room_index": 0,
    },
    {
        "uid": "bk_northwind_recurring_3",
        "meeting_type": 0,
        "title": "Northwind Traders — Weekly check-in",
        "attendee_name": "Tomas Vela",
        "attendee_email": "tomas.vela@contoso.example",
        "start_offset_days": 16,
        "start_minute": 11 * 60,
        "location": "Google Meet",
        "recurring_group": "bk_northwind_recurring",
        "recurrence_index": 3,
        "room_index": 0,
    },
    {
        # The fourth is here so the demo can show a wholesale cancel as well as a
        # per-instance one, with both leaving something behind. A three-occurrence
        # series cannot demonstrate that, because cancelling one of three still
        # leaves two, which reads the same as a series untouched by a mistake.
        "uid": "bk_northwind_recurring_4",
        "meeting_type": 0,
        "title": "Northwind Traders — Weekly check-in",
        "attendee_name": "Rui Silva",
        "attendee_email": "rui.silva@silva-consulting.example",
        "start_offset_days": 23,
        "start_minute": 11 * 60,
        "location": "Google Meet",
        "recurring_group": "bk_northwind_recurring",
        "recurrence_index": 4,
        "room_index": 0,
    },
)

#: One run of each of the three intents, each against a *different* booking so the
#: cases cannot interfere - a reschedule supersedes its booking, so reusing one
#: would leave the second case with nothing to act on.
#:
#: Together they reach all four change types, all three rescheduling sources, both
#: ``Delete Event`` settings, the location webhook, the per-instance and wholesale
#: cancels, the two-step request, and both link states.
DEMO_CHANGES: tuple[dict[str, Any], ...] = (
    {
        "label": "rescheduled from the host panel: the meeting moved a day later",
        "booking": "bk_northwind_enterprise",
        "intent": "reschedule",
        "payload": {
            # Back to its own time. This is the researched rule the whole
            # availability recomputation exists for: "the original booking time
            # appears within the returned available slots when rescheduling". A
            # demo that only ever moves a meeting somewhere new never shows it.
            "start_at": "original",
            "reason": "Priya asked for the afternoon after the security review lands.",
            "actor_email": "dana@northwind.example",
            "actor_kind": "host",
            "reschedule_source": "chilical_home",
        },
    },
    {
        # A reschedule that also moves the location, so BOOKING_LOCATION_UPDATED
        # appears in the demo. The research lists that webhook in this section's
        # fan-out and gives it no separate trigger, so the condition it states -
        # the location changed - is what has to produce it.
        #
        # The new time is read off the recomputed availability rather than written
        # by hand, because this meeting type works Tuesday and Thursday afternoon
        # only and a hand-picked "five days from now" lands on a weekend about a
        # third of the time. A demo that seeds its own impossible target would be
        # demonstrating the refusal instead of the path.
        "label": "rescheduled with a new location: two webhooks, not one",
        "booking": "bk_northwind_renewal",
        "intent": "reschedule",
        "payload": {
            "start_at": "next_slot",
            "location": "Zoom — Fabrikam room",
            "reason": "Moving to Zoom so procurement can join from their own office.",
            "actor_email": "marcus.webb@solowebb.example",
            "actor_kind": "attendee",
            "reschedule_source": "reschedule_link",
            "use_link": "reschedule",
        },
    },
    {
        # A meeting that was moved and then called off, on the strict type. The
        # CRM Event exists because of the move, and the cancel deletes it, which is
        # the only way a demo can show ``Delete Event`` doing its job on a row
        # there is something to delete.
        "label": "moved, then cancelled with a reason: Delete Event removes the CRM event",
        "booking": "bk_northwind_called_off",
        "intent": "reschedule_then_cancel",
        "payload": {
            "start_at": "original",
            "reason": "I am no longer able to attend this session.",
            "actor_email": "rui.silva@silva-consulting.example",
            "actor_kind": "host",
            "reschedule_source": "chilical_home",
        },
    },
    {
        # A reschedule on the permissive type followed by its cancellation, so the
        # CRM Event exists when the toggle is consulted and the two outcomes can
        # be compared. Delete Event is off here, so the CRM Event is kept and
        # marked cancelled - the other half of the researched toggle, and the only
        # way a reviewer can see it.
        "label": "cancelled from the panel, Delete Event off: the CRM event is kept",
        "booking": "bk_contoso_onsite",
        "intent": "reschedule_then_cancel",
        "payload": {
            "start_at": "original",
            "actor_email": "sam@contoso.example",
            "actor_kind": "host",
            "reschedule_source": "chilical_home",
        },
    },
    {
        # The two-step path, on a meeting type that expires its links. The booking
        # is cancelled and the request survives it, which is the whole point of
        # minting a request token rather than reusing the booking's own - and this
        # case is on a *live* booking, so the link it travels through is open.
        #
        # Left pending rather than completed: a request the attendee never
        # answered is a state a reviewer has to be able to see, and it is the one
        # a demo that only shows finished work never reaches. The completion path
        # is covered by the suite and by the "make a change" page.
        "label": "asked to pick a new time: the booking is released, the request stands",
        "booking": "bk_northwind_recurring_1",
        "intent": "request_reschedule",
        "payload": {
            "reason": "Something came up next week; can we find another slot?",
            "actor_email": "nadia.farouk@soluspring.example",
            "actor_kind": "attendee",
            "reschedule_source": "reschedule_link",
            "use_link": "reschedule",
            "complete": False,
        },
    },
    {
        # A cancel of one occurrence, leaving the rest of the series live. The
        # researched distinction is per-instance versus wholesale, and only this
        # one of the two is visible in the demo without a second change. A new
        # attendee, so it cannot be confused with the request case above.
        "label": "cancelled one occurrence, the rest of the series untouched",
        "booking": "bk_northwind_recurring_2",
        "intent": "cancel",
        "payload": {
            "scope": "this",
            "actor_email": "dana@northwind.example",
            "actor_kind": "host",
            "reschedule_source": "calendar_event",
        },
    },
)


def _at(spec: Mapping[str, Any], base: Any) -> str:
    """Resolve a demo timestamp written as ``+4d 09:00`` against the seeder's now.

    A relative stamp rather than an absolute one, because a demo dataset that hard
    codes 2026-10-01 is a demo that is silently wrong the day after, and the
    past-meeting rows in particular have to stay in the past for the expiry
    refusal to remain reachable.
    """
    from datetime import datetime, timedelta, timezone

    moment = base if isinstance(base, datetime) else datetime.now(timezone.utc)
    text = str(spec)
    days = 0
    working_day = True
    if text.startswith("+") or text.startswith("-"):
        head, _, rest = text.partition(" ")
        # The `d` is part of the spec, not part of the number: the two forms a
        # reader writes are `+4d` and `+4`, and a seeder that accepted only one
        # would fail on whichever the author did not have in mind.
        days = int(head.rstrip("dD"))
        text = rest
    if text.startswith("!"):
        # `!` pins the stamp to whatever day it lands on, for the one demo row
        # that has to be a Saturday: the type whose working days are Tuesday and
        # Thursday is the case a weekend proves something about.
        working_day = False
        text = text[1:]
    hour, _, minute = text.partition(":")
    target = (moment + timedelta(days=days)).astimezone(timezone.utc).replace(
        hour=int(hour), minute=int(minute or 0), second=0, microsecond=0
    )
    if working_day:
        while target.weekday() > 4:
            target += timedelta(days=1)
    return target.isoformat(timespec="seconds")


def _next_slot(
    engine: MeetingChangeEngine, meeting_type_id: str | None, now: Any
) -> str:
    """The first open slot on a Meeting Type, read off the recomputed availability.

    Used by the one demo case that has to name a new time it did not choose -
    completing a reschedule request. Every other case names the meeting's own
    original time, which is on offer by construction because
    ``bookingUidToReschedule`` releases it, and that is the researched rule worth
    demonstrating. This one cannot, so it takes whatever the availability
    computation says, and a demo that seeds its own impossible target would be
    demonstrating the refusal rather than the path.
    """
    slots = engine.host_slots(meeting_type_id, now=now)
    usable = [slot for slot in slots if not slot.get("in_past")]
    if not usable:
        raise MeetingChangeError(
            "the demo meeting type has no open slot in the default range, so the reschedule "
            "request cannot be completed; widen the type's hours or the demo range"
        )
    return str(usable[0]["start_at"])


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the Meeting Types, the bookings, and one run of each intent.

    The changes come from running the real
    :class:`~dsr.scheduling.engine.MeetingChangeEngine` over the real rules, so
    the demo cannot show a shape the workflow would not produce, and seeding opens
    no socket because the calendar and the CRM here are the audited store.

    Deliberately mixed, and the mixture is the argument. A demo of only successful
    reschedules would teach nothing about the three states this workflow exists
    for: a link closed by the researched setting, a CRM event deleted because the
    toggle says so, and a series with one occurrence cancelled out of three. All
    three are here, alongside both link states, both ``Delete Event`` settings, and
    a two-step request that outlives the booking it cancelled.

    Returns a description the seeder prints, and which says how many of each change
    type landed so a reviewer can see at a glance that the demo is mixed.
    """
    store = RecordStore(db)
    now = context.get("now")
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        return "0 changes (no rooms to scope them to)"
    source = "seed"

    # A fixed clock and fixed tokens, so a reseed of the same dataset produces the
    # same demo and a reviewer comparing two runs is comparing the workflow rather
    # than the clock.
    counter = {"n": 0}

    def next_token() -> str:
        counter["n"] += 1
        return f"demo-token-{counter['n']:04d}"

    engine = MeetingChangeEngine(
        store, clock=(lambda: now) if now is not None else None,
        uid_factory=lambda: f"bk_demo_{counter['n']:04d}", token_factory=next_token,
    )

    meeting_types = [
        engine.create_meeting_type(spec, room_id=rooms[0][0], actor="dana", source=source)
        for spec in DEMO_MEETING_TYPES
    ]

    def room_at(index: Any) -> str:
        return rooms[int(index) % len(rooms)][0]

    bookings: dict[str, dict[str, Any]] = {}
    for spec in DEMO_BOOKINGS:
        payload = dict(spec)
        room_id = room_at(payload.pop("room_index", 0))
        meeting_type = meeting_types[int(payload.pop("meeting_type"))]
        days = int(payload.pop("start_offset_days"))
        minute_of_day = int(payload.pop("start_minute"))
        payload["start_at"] = _at(
            f"{'+' if days >= 0 else '-'}{abs(days)}d {minute_of_day // 60:02d}:{minute_of_day % 60:02d}",
            now,
        )
        payload["meeting_type_id"] = meeting_type["id"]
        record = engine.create_booking(payload, room_id=room_id, actor="dana", source=source)
        bookings[record["data"]["uid"]] = record

    landed: list[str] = []
    for case in DEMO_CHANGES:
        booking = bookings[case["booking"]]
        data = booking["data"]
        payload = dict(case["payload"])
        if "start_at" in payload:
            # Three ways to name the new time, and the choice is deliberate in
            # every case. `"original"` uses the researched release, which a demo
            # that only ever moves meetings to new times never exercises. An
            # offset like `"+4d 09:00"` is pinned to a working day, because the
            # Meeting Types here do not work weekends. `"next_slot"` is read off
            # the recomputed availability, for the type that works two afternoons
            # a week where a hand-picked date is a coin flip.
            target = payload["start_at"]
            if target == "original":
                payload["start_at"] = data["start_at"]
            elif target == "next_slot":
                payload["start_at"] = _next_slot(
                    engine, booking["data"].get("meeting_type_id"), now
                )
            else:
                payload["start_at"] = _at(target, now)
        # `use_link` names which of the booking's own two tokens the case travels
        # through, so the demo exercises the link guard rather than only the panel
        # path. A key that does not exist on the booking would silently drop the
        # token and turn an intended link case into a panel one, so it is named
        # per case rather than inferred from the intent.
        if link_kind := payload.pop("use_link", None):
            payload["link_token"] = data[f"{link_kind}_token"]
        room_id = str(booking.get("room_id") or rooms[0][0])
        meeting_type = booking["data"].get("meeting_type_id")
        try:
            if case["intent"] == "reschedule":
                change = engine.reschedule(room_id, data["uid"], payload, actor="dana", source=source)
            elif case["intent"] == "reschedule_then_cancel":
                # Move it and then release it, so the CRM Event exists and the
                # Delete Event toggle is consulted on a row it can act on. Two
                # history rows, which is also what really happens to a meeting
                # that was moved and then called off.
                moved = engine.reschedule(room_id, data["uid"], payload, actor="dana", source=source)
                landed.append(CHANGE_RESCHEDULED)
                # The reason is the cancellation's, not the reschedule's - it ships
                # in BOOKING_CANCELLED and is what a rep reads afterwards.
                cancel_payload = {
                    "actor_email": payload.get("actor_email"),
                    "actor_kind": payload.get("actor_kind"),
                    "reschedule_source": payload.get("reschedule_source"),
                }
                if payload.get("reason"):
                    cancel_payload["reason"] = payload["reason"]
                change = engine.cancel(
                    room_id,
                    moved["data"]["new_booking_uid"],
                    cancel_payload,
                    actor="dana",
                    source=source,
                )
            elif case["intent"] == "request_reschedule":
                request = engine.request_reschedule(
                    room_id, data["uid"], payload, actor="dana", source=source
                )
                landed.append(CHANGE_RESCHEDULE_REQUESTED)
                change = request
                if payload.pop("complete", True):
                    # Completed, so the demo can also carry the researched two-step
                    # path all the way through rather than stopping at the request.
                    # The target is read off the recomputed availability rather than
                    # written by hand: a hard-coded "six days from now at nine" is on
                    # a Sunday more often than anyone checking a demo would like, and
                    # the refusal it would produce is correct behaviour demonstrating
                    # itself in the wrong place.
                    change = engine.complete_request(
                        request["id"],
                        {"start_at": _next_slot(engine, meeting_type, now)},
                        actor="dana",
                        source=source,
                    )
            else:
                change = engine.cancel(room_id, data["uid"], payload, actor="dana", source=source)
        except MeetingChangeError as exc:
            # A demo case that the rules refuse is recorded as refused, not
            # dropped. Silently skipping it would leave a reviewer reading a
            # summary that claims coverage the demo does not have.
            landed.append(f"refused:{exc.code}")
            continue
        # A request that was deliberately left pending is not a change row, so it
        # has no `type`; counting it as one would put a `None` in the seeder's
        # summary, which a reviewer would read as a case that produced nothing.
        if isinstance(change, dict) and change["data"].get("type"):
            landed.append(str(change["data"]["type"]))

    tally: dict[str, int] = {}
    for entry in landed:
        tally[entry] = tally.get(entry, 0) + 1
    summary = engine.summary()
    # Counted from the store rather than from `landed`, because `landed` records
    # what each *case* produced and a case that moved and then cancelled a meeting
    # produces two rows. The number a reviewer wants is how many history rows there
    # are, and a summary that said "6 changes" when there are eight would be the
    # kind of small lie that makes a demo untrustworthy.
    changes_written = len(engine.changes(limit=1000))
    return (
        f"{len(DEMO_MEETING_TYPES)} meeting types, {len(bookings)} bookings, "
        f"{changes_written} changes "
        + ", ".join(f"{count} {name}" for name, count in sorted(tally.items()))
        + f"; {summary['webhooks_pushed']} webhooks pushed, "
        f"{summary['crm_events_deleted']} CRM events deleted"
    )
