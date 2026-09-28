"""WF-061: send conditional pre- and post-meeting reminders and SMS nudges.

A researched workflow, not a port: there is no source branch. The research
document is the specification, and what it specifies is Chili Piper's *Reminders
and Messages* surface plus the Cal.com workflow model the same logic is said to
port onto.

What the research specifies
---------------------------

An administrator opens a Meeting Type's *Reminders and Messages*, adds a
reminder, and chooses a **Reminder Type** (Email or SMS); the delivery
configuration (``Send Email To`` / ``Send Email From`` / ``Send Replies To`` /
``Send SMS From``); the **firing condition** (*Before the Meeting*, *Before the
Meeting - if the Primary Guest did not respond*, or *After the Meeting*) with a
minutes/hours/days/weeks offset; a composed subject and body using dynamic tags;
and the two advanced gates - *Send only if meeting starts on* and *Send if the
meeting was booked more than selected timeframe*. Later, *Meetings Activity*
shows a per-reminder status.

Reminders are "reusable assets attachable to many Meeting Types", and the
research names two different ways to stop one being used - ``Remove from Meeting
Type`` versus ``Delete`` - which is why this module has two routes that do two
different things to two different rows.

Decisions in here a reviewer would otherwise have to reverse-engineer
-----------------------------------------------------------------------

**Room-scoped paths are room-scoped.** Step 7 reads a *meeting*, and a meeting in
this product hangs off a room, so the routes that plan, deliver and audit are all
``/rooms/{room_id}/...``. What is genuinely not about a room stays unscoped: the
vocabularies, the reminder assets (a reusable asset attached to many meeting
types cannot belong to one room), the meeting types themselves, and the
organisation's messaging setup - which the research locates on an org-level
Command Center page.

**``source`` is built from ``router.prefix`` and passed down.** The audit row
must name the route that actually served the write, so every write route builds
its source string here and hands it to a method that requires it. The defect
this prevents - an audit log recording a path the app no longer serves - has
shipped in this codebase before, so the suite asserts that every source
recorded matches a route the host actually mounted.

**A skip writes a row; a refusal does not.** The research gives five skip
reasons, and each of them is a *delivery outcome* - a row an administrator
reads in *Meetings Activity*. A missing Twilio account or a missing custom
domain is not that: it is a refusal to save the configuration, answered 400, so
the problem is found by the administrator who caused it rather than by a customer
who did not get a text.

**The Cal projection is a read.** It returns the exact DTO a
``POST /v2/workflows`` would carry, with the ``cal-api-version`` header, and
opens no socket. See the ``no-outbound-send`` inference.

Only the three things a feature is allowed to add live in this module: the HTTP
surface, the mapping from domain errors to responses, and the demo data. The
behaviour is in :mod:`dsr.meeting_reminders`, where it can be tested without a
request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr import meeting_reminders
from dsr.deps import StoreDep
from dsr.meeting_reminders import ReminderEngine
from dsr.meeting_reminders import cal as cal_mod
from dsr.meeting_reminders import tags as tag_mod
from dsr.meeting_reminders import vocabulary as vocab
from dsr.meeting_reminders.errors import ConfigurationRefused, ReminderError
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-061-send-conditional-pre-and-post-meeting-",
    "ticket": "WF-061",
    "name": "Send conditional pre- and post-meeting reminders and SMS nudges",
    "description": (
        "Declare a reusable reminder - email or SMS, before or after the meeting, optionally only "
        "if the primary guest has not responded - attach it to many meeting types, and let it fire "
        "on a schedule with a recorded reason for every message that did not go out."
    ),
    "nav": [{"id": "meeting-reminders", "label": "Meeting reminders"}],
}

router = APIRouter(prefix="/api/wf-061", tags=["wf-061"])


def get_reminders(store: RecordStore = StoreDep) -> ReminderEngine:
    """A :class:`ReminderEngine` over the process-wide audited store.

    Per request, for the same reason WF-041 builds its ``DedupeEngine`` per
    request: the engine holds nothing beyond the store and a clock, so building
    it here leaves both overridable in a test instead of hanging a long-lived
    object off ``app.state`` - which is a shared file this feature may not edit.
    """
    return ReminderEngine(store)


ReminderDep = Depends(get_reminders)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #
#
# Both types are this workflow's own, raised by :mod:`dsr.meeting_reminders` and
# by nothing else in the product. That is what makes it safe to map them here:
# the host refuses two features mapping the same type, and a handler for
# ``ValueError`` - which ``ReminderError`` subclasses - would intercept
# exceptions from the whole product.
#
# ``RecordNotFound`` is deliberately *not* claimed. The core app already maps it
# to 404, and two handlers for one type is a collision the host refuses.


def _reminder_error(request: Request, exc: ReminderError) -> JSONResponse:
    # 400: the request was well formed, the reminder configuration inside it is
    # not one we accept.
    return JSONResponse(status_code=400, content={"error": "invalid_reminder", "detail": str(exc)})


def _configuration_refused(request: Request, exc: ConfigurationRefused) -> JSONResponse:
    # 428 rather than 400, deliberately: the request is perfectly valid and the
    # organisation has not finished a setup step the research names as a
    # precondition. 428 is "the server requires the request to be conditional",
    # which is the closest standard code, and the detail says which step. The
    # alternative - a 400 - reads as the administrator having got the request
    # wrong, which they have not.
    return JSONResponse(
        status_code=428,
        content={"error": "configuration_required", "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {
    ReminderError: _reminder_error,
    ConfigurationRefused: _configuration_refused,
}


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually served.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the build brief is that a write's audit row must name the route that served
    it. Writing the string by hand is how the same defect shipped once already.

    Path parameters are interpolated rather than left as ``{room_id}``, so an
    audit row names the one booking it served and can be grepped for it. The
    route it is compared against is the one the host mounted, template and all.
    """
    return f"{verb} {router.prefix}{suffix}"


def _room_or_404(engine: ReminderEngine, room_id: str) -> dict[str, Any]:
    room = engine.store.get(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    return room


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term, in the shape a client builds its pickers from.

    Served as data so a client renders from the server's vocabulary rather than a
    list compiled into the frontend, and so a term added in one place reaches
    every client at once. The validator and the UI cannot disagree about what
    the product accepts, because neither of them has a list of its own.
    """
    return vocab.published_vocabulary()


@router.get("/tags", summary="The dynamic tags a message can use")
def dynamic_tags() -> dict[str, Any]:
    """Every Chili Piper tag and Cal token, with a rendered example each.

    The composer needs two things from the server: the list, and what each one
    will actually produce for *this* seller, so the examples come from a real
    booking shape rather than a placeholder nobody believes.
    """
    return tag_mod.token_catalog()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The researched half is served beside it, because the point of the endpoint
    is showing the reader where the line falls, which means showing what is on
    each side of it.

    A read with no side effect, so it needs no store.
    """
    return meeting_reminders.describe()


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Deliveries by status, reason and channel, and whether SMS is switched on.

    The counts are computed over exactly the rows the same filters would return,
    so a room-scoped total above an unscoped list cannot be misread as a
    product-wide one.
    """
    return reminders.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# The organisation's messaging setup
# --------------------------------------------------------------------------- #
#
# The research locates both of these on the same org-level page: "A Chili Piper
# Admin must connect Twilio to your company's Command Center Integrations page",
# and the no-reply sender is "a No-reply address on a custom domain". One record,
# two settings, because they are one setup step in the product being modelled.


@router.get("/messaging", summary="The organisation's messaging setup")
def read_messaging(reminders: ReminderEngine = ReminderDep) -> dict[str, Any]:
    """The no-reply domain, the sending numbers, and the Twilio connection's state.

    Absent setup is an empty setup rather than a 404: "not configured yet" is a
    normal state, and every read of a reminder's sender goes through here.
    """
    settings = reminders.org_settings()
    return {
        "org": reminders.org(),
        "settings": settings,
        "sms_ready": reminders.twilio_ready(),
        "reply_forwarding_ready": reminders.reply_forwarding_ready(),
        "quotes": {
            "twilio": vocab.TWILIO_QUOTE,
            "reply_forwarding": vocab.TWILIO_REPLY_FORWARDING_QUOTE,
            "guest_form_phone": vocab.GUEST_FORM_PHONE_QUOTE,
        },
    }


@router.patch("/messaging", summary="Configure the organisation's messaging")
def save_messaging(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Upsert the no-reply domain, the numbers, and the Twilio connection.

    A **partial** update, which is why the verb is ``PATCH`` and not ``PUT``. The
    stored setup is merged rather than replaced, because an administrator
    connecting their Twilio account should not have to resend their custom
    sending domain to do it - and a ``PUT`` that quietly merged would make the
    endpoint's own contract a lie to anyone reading it.

    ``connected`` gates enabling any SMS reminder, and a number behind it gates
    sending from one. ``own_account`` additionally gates forwarding a guest's
    reply by email, which the research flags separately with a warning - a
    connection can send and not forward, and conflating them would silently drop
    guest replies.
    """
    record = reminders.save_org(payload, actor=actor, source=_source("PATCH", "/messaging"))
    return {
        "org": record,
        "sms_ready": reminders.twilio_ready(),
        "reply_forwarding_ready": reminders.reply_forwarding_ready(),
    }


# --------------------------------------------------------------------------- #
# Reminder assets
# --------------------------------------------------------------------------- #


@router.get("/reminders", summary="List reminder assets")
def list_reminders(
    channel: str | None = Query(default=None),
    condition: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """The reusable reminder assets, newest first.

    Unscoped on purpose: "reminders are reusable assets attachable to many
    Meeting Types", so a reminder that belonged to one room could not be reused
    across two. Its *deliveries* are what carry the room.
    """
    records = reminders.list_reminders(channel=channel, condition=condition, limit=limit)
    return {"count": len(records), "reminders": records}


@router.post("/reminders", status_code=201, summary="Create a reminder")
def create_reminder(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Steps 2 to 6: the whole reminder, validated, in one call.

    Channel, delivery configuration, firing condition with its offset, the
    composed subject and body, and the advanced gates.

    Refused - not skipped - when a researched organisational precondition is
    missing: an SMS reminder with no connected Twilio account, or a no-reply
    sender with no custom domain. Both are 428s, and both are found here rather
    than by a customer who did not get a text.
    """
    return reminders.create_reminder(payload, actor=actor, source=_source("POST", "/reminders"))


@router.get("/reminders/{reminder_id}", summary="Read one reminder")
def read_reminder(reminder_id: str, reminders: ReminderEngine = ReminderDep) -> dict[str, Any]:
    """One reminder, with the meeting types it is attached to."""
    record = reminders.require_reminder(reminder_id)
    return {
        **record,
        "attached_meeting_types": reminders.attachment_ids_for(reminder_id),
        "cal_template": cal_mod.cal_template(record["data"]),
    }


@router.patch("/reminders/{reminder_id}", summary="Patch a reminder")
def update_reminder(
    reminder_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Patch a reminder, re-validated against the merged result.

    So a patch cannot leave a reminder whose channel is SMS with no Twilio
    account behind it, or whose no-reply sender has no domain.
    """
    return reminders.update_reminder(
        reminder_id, payload, actor=actor, source=_source("PATCH", f"/reminders/{reminder_id}")
    )


@router.delete("/reminders/{reminder_id}", summary="Delete a reminder")
def delete_reminder(
    reminder_id: str,
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> Response:
    """**Delete** the reusable asset. Soft. 204.

    The researched counterpart to ``Remove from Meeting Type``: this one removes
    the reminder itself, while the reminder's deliveries stay auditable, because
    the history of what a reminder did outlives the reminder.
    """
    if reminders.get_reminder(reminder_id) is None:
        raise HTTPException(status_code=404, detail=f"reminder {reminder_id} not found")
    reminders.delete_reminder(reminder_id, actor=actor, source=_source("DELETE", f"/reminders/{reminder_id}"))
    return Response(status_code=204)


@router.get("/reminders/{reminder_id}/cal-workflow", summary="This reminder as a Cal.com workflow")
def cal_workflow(
    reminder_id: str,
    booking_id: str | None = Query(default=None, description="Fold the meeting duration into an after-meeting offset"),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """``user_flow`` step 8: the same logic as a Cal.com Workflow.

    Returns the DTO a ``POST /v2/workflows`` would carry, beside the
    ``cal-api-version: 2024-08-13`` header that call requires, and the trigger,
    filter and action steps the research's enums allow.

    Supplying a ``booking_id`` folds that meeting's duration into an
    after-meeting offset, because this product anchors a follow-up on the meeting
    *end* while Cal's ``afterEvent`` fires relative to the event - the two only
    agree once the duration is accounted for.
    """
    record = reminders.require_reminder(reminder_id)
    booking_record = reminders.get_booking(booking_id) if booking_id else None
    if booking_id and booking_record is None:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} not found")
    return {
        "reminder_id": reminder_id,
        "workflow": cal_mod.workflow(
            record["data"],
            meeting_type_ids=reminders.attachment_ids_for(reminder_id),
            # The booking's *payload*, not its envelope: the duration this folds
            # in is a field the guest form collected, and it lives in `data`.
            booking=booking_record["data"] if booking_record else None,
        ),
        "notes": {
            "outbound": False,
            "weeks_offset": "Cal has no week unit; a weeks offset is projected as whole days",
            "response_gate": "Cal has no response-conditional pre-meeting trigger, so it becomes a filter step",
        },
    }


@router.post("/cal-workflows/validate", summary="Check a Cal workflow against the published enums")
def validate_cal_workflow(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Validate an inbound Cal workflow.

    A workflow this product cannot run is **not** an error: it is a workflow for
    Cal.com, and Cal.com is where it belongs. So the answer lists what would not
    survive and separates the blocking problems from the ones this product simply
    does not act on.
    """
    return cal_mod.validate_workflow(payload)


# --------------------------------------------------------------------------- #
# Meeting types and their attachments
# --------------------------------------------------------------------------- #


@router.get("/meeting-types", summary="List meeting types")
def list_meeting_types(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Meeting types, with the reminder count and whether the form needs a phone."""
    records = reminders.list_meeting_types(room_id=room_id, limit=limit)
    return {"count": len(records), "meeting_types": [reminders.meeting_type_view(record) for record in records]}


@router.post("/meeting-types", status_code=201, summary="Declare a meeting type")
def create_meeting_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """A Meeting Type: what a booking is made against and reminders attach to."""
    return reminders.create_meeting_type(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/meeting-types")
    )


@router.get("/meeting-types/{meeting_type_id}", summary="Read one meeting type")
def read_meeting_type(meeting_type_id: str, reminders: ReminderEngine = ReminderDep) -> dict[str, Any]:
    """One meeting type, its attachments, and the phone requirement it implies."""
    record = reminders.require_meeting_type(meeting_type_id)
    return {
        **reminders.meeting_type_view(record),
        "reminders": [entry["data"] for entry in reminders.attached_reminders(meeting_type_id)],
    }


@router.post("/meeting-types/{meeting_type_id}/reminders", status_code=201, summary="Attach a reminder")
def attach_reminder(
    meeting_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """``user_flow`` step 1: add a reminder to a meeting type.

    Idempotent - re-attaching returns the existing attachment rather than
    sending the same reminder twice.
    """
    reminder_id = str(payload.get("reminder_id") or payload.get("reminderId") or "")
    if not reminder_id:
        raise HTTPException(status_code=400, detail="reminder_id is required")
    return reminders.attach(
        meeting_type_id,
        reminder_id,
        actor=actor,
        source=_source("POST", f"/meeting-types/{meeting_type_id}/reminders"),
    )


@router.delete(
    "/meeting-types/{meeting_type_id}/reminders/{reminder_id}",
    summary="Remove from Meeting Type",
)
def detach_reminder(
    meeting_type_id: str,
    reminder_id: str,
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> Response:
    """**Remove from Meeting Type**, and only that. 204.

    The reminder asset survives, and so does every other meeting type it is
    attached to - which is the whole reason this route is not the delete route.
    """
    try:
        reminders.detach(
            meeting_type_id,
            reminder_id,
            actor=actor,
            source=_source("DELETE", f"/meeting-types/{meeting_type_id}/reminders/{reminder_id}"),
        )
    except ReminderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/bookings", summary="A room's bookings")
def room_bookings(
    room_id: str,
    meeting_type_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """The bookings a room's reminders can fire against."""
    _room_or_404(reminders, room_id)
    records = reminders.list_bookings(room_id=room_id, meeting_type_id=meeting_type_id, limit=limit)
    return {"room_id": room_id, "count": len(records), "bookings": records}


@router.post("/rooms/{room_id}/bookings", status_code=201, summary="Record a booking")
def create_booking(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Record a booked meeting against a room.

    Refused with 428 when the meeting type has an SMS reminder attached and the
    booking carries no guest phone: Cal says the phone field "becomes required
    when SMS reminders are enabled for the event type", which is a statement about
    the booking *form*, so it belongs here rather than at delivery time.
    """
    return reminders.create_booking(
        room_id, payload, actor=actor, source=_source("POST", f"/rooms/{room_id}/bookings")
    )


@router.get("/rooms/{room_id}/bookings/{booking_id}", summary="One booking")
def read_booking(room_id: str, booking_id: str, reminders: ReminderEngine = ReminderDep) -> dict[str, Any]:
    """One booking, with its per-reminder delivery rows.

    That is ``user_flow`` step 7 read as a single response: *Meetings Activity*,
    open a meeting, per-reminder status.
    """
    _room_or_404(reminders, room_id)
    record = reminders.require_booking(booking_id)
    if record.get("room_id") != room_id:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} is not in room {room_id}")
    return {
        **record,
        "deliveries": reminders.deliveries(booking_id=booking_id, limit=500),
    }


@router.post("/rooms/{room_id}/bookings/{booking_id}/plan", summary="Plan the reminders onto a booking")
def plan_booking(
    room_id: str,
    booking_id: str,
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Attach every enabled reminder to this booking, and record the schedule.

    This is where "Reminder schedule time in the past" is decided. A "2 hours
    before" reminder attached to a meeting starting in thirty minutes has no
    moment at which it could have been sent, and the row says so now rather than
    sitting in the list looking scheduled.
    """
    _room_or_404(reminders, room_id)
    record = reminders.require_booking(booking_id)
    if record.get("room_id") != room_id:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} is not in room {room_id}")
    return reminders.plan(
        record, actor=actor, source=_source("POST", f"/rooms/{room_id}/bookings/{booking_id}/plan")
    )


@router.post("/rooms/{room_id}/bookings/{booking_id}/deliver", status_code=201, summary="Run the workflow")
def deliver_booking(
    room_id: str,
    booking_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """The whole workflow for one booking: plan, decide, record.

    Writes one row per attached reminder, each carrying its status, the reason
    if it was skipped, the resolved recipients, and the composed message. Passing
    ``reminder_id`` runs one reminder only.
    """
    return reminders.deliver(
        room_id,
        booking_id,
        reminder_id=payload.get("reminder_id"),
        actor=actor,
        source=_source("POST", f"/rooms/{room_id}/bookings/{booking_id}/deliver"),
    )


@router.post("/rooms/{room_id}/bookings/{booking_id}/preview", summary="Preview without writing")
def preview_booking(
    room_id: str,
    booking_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Render and decide for a real booking, and write **nothing at all**.

    The researched Preview pane. It calls the same evaluation the delivery does,
    so the answer here is the answer there, and it is safe to call repeatedly
    while a seller is still composing.
    """
    _room_or_404(reminders, room_id)
    record = reminders.require_booking(booking_id)
    if record.get("room_id") != room_id:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} is not in room {room_id}")
    reminder_id = str(payload.get("reminder_id") or payload.get("reminderId") or "")
    reminder = reminders.require_reminder(reminder_id).get("data") if reminder_id else None
    if reminder is None:
        meeting_type_id = str((record["data"] or {}).get("meetingTypeId") or "")
        attached = reminders.attached_reminders(meeting_type_id)
        if not attached:
            raise HTTPException(status_code=404, detail=f"booking {booking_id} has no reminders attached")
        reminder = attached[0]["data"]
    return {
        "booking_id": booking_id,
        "room_id": room_id,
        **reminders.preview(reminder, record["data"], now=reminders.clock()),
    }


# --------------------------------------------------------------------------- #
# Meetings Activity
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/deliveries", summary="A room's reminder activity")
def room_deliveries(
    room_id: str,
    status: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Every delivery taken for a room, newest first.

    This is the researched *Meetings Activity* list: per reminder, a status, a
    reason if it was skipped, the recipients, and the message that went out.
    """
    _room_or_404(reminders, room_id)
    records = reminders.deliveries(
        room_id=room_id, status=status, reason=reason, channel=channel, limit=limit
    )
    return {"room_id": room_id, "count": len(records), "deliveries": records}


@router.get("/deliveries", summary="Every delivery, product-wide")
def all_deliveries(
    status: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """Deliveries across every room. The unscoped read beside the room's own."""
    records = reminders.deliveries(status=status, reason=reason, channel=channel, limit=limit)
    return {"count": len(records), "deliveries": records}


@router.get("/deliveries/{delivery_id}", summary="One delivery in full")
def read_delivery(delivery_id: str, reminders: ReminderEngine = ReminderDep) -> dict[str, Any]:
    """One delivery, with its check trail, composed message and forwarded replies."""
    record = reminders.get_delivery(delivery_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"delivery {delivery_id} not found")
    return {**record, "replies": reminders.replies(delivery_id=delivery_id, limit=200)}


@router.post("/deliveries/{delivery_id}/sms-replies", status_code=201, summary="Record an inbound SMS reply")
def record_sms_reply(
    delivery_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """"When a guest replies to an SMS reminder, Chili Piper forwards the text to
    your team by email."

    Records the reply and the addresses ``Send Replies To`` resolved to. Refused
    with 428 without an organisation-**owned** Twilio account, because the
    research flags exactly that requirement separately: a connection that is not
    owned can send a text and cannot forward a reply, and pretending otherwise
    would lose a guest's answer.
    """
    record = reminders.record_reply(
        delivery_id,
        payload,
        actor=actor,
        source=_source("POST", f"/deliveries/{delivery_id}/sms-replies"),
    )
    return {"reply": record, "forwarded_to": record["data"].get("forwardedTo")}


# --------------------------------------------------------------------------- #
# The scheduler
# --------------------------------------------------------------------------- #


@router.post("/fire", summary="Fire every due reminder")
def fire(
    room_id: str | None = Query(default=None),
    reminders: ReminderEngine = ReminderDep,
) -> dict[str, Any]:
    """The automation, on demand: fire everything whose moment has arrived.

    A deployment would put this on a timer. It is a route rather than a
    background task because this product has no scheduler - and because a
    reviewer can call it and watch a reminder go out, which is the only way to
    check the researched behaviour is real.

    A delivery already sent or skipped is not re-decided. Re-running a window
    has to give the same answer, or the audit row would be rewritten by a second
    pass.
    """
    return reminders.fire(room_id=room_id, source=_source("POST", "/fire"))



# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The brief is explicit that a demo of the happy path alone teaches a reviewer
# nothing, and here that is sharper than usual: the researched statuses are
# `Scheduled` / `Sent` / `Skipped` with five documented skip reasons, so *all
# eight* are the demo. A seed that only sent reminders would leave five of them
# unshown and four of the six researched statuses unshown too.
#
# Everything below is produced by running the real engine, so the demo cannot
# show a status the workflow would not produce, and seeding opens no socket.

DEMO_MESSAGING: dict[str, Any] = {
    # The org-level setup the research makes a precondition: a connected Twilio
    # account, and a no-reply address on the organisation's own domain.
    "noreply_domain": "no-reply.contoso.example",
    "number": "+15550100",
    "localNumber": "+35315550100",
    vocab.CONNECTION_CONNECTED: True,
    # ``own_account`` is a second flag, not a stronger first one, because the
    # research flags reply forwarding separately with a warning glyph. Set here
    # so the forwarded-reply half of the data flow is visible in the demo.
    vocab.CONNECTION_OWN_ACCOUNT: True,
}

#: One reminder per researched condition, plus the message options
#: ``features_tools`` names. Each exists to make one rule visible.
DEMO_REMINDERS: tuple[dict[str, Any], ...] = (
    {
        "name": "24 hours before - primary guest only",
        "channel": vocab.EMAIL,
        "condition": vocab.BEFORE,
        "offset": {"value": 24, "unit": vocab.HOURS},
        "emailTo": vocab.PRIMARY_GUEST,
        "emailFrom": vocab.NO_REPLY,
        "repliesTo": vocab.REPLY_TO_HOST,
        "subject": "Your evaluation with us on {CP.Meeting.StartTime}",
        "body": (
            "Hi {CP.Guest.FirstName},\n\n"
            "A quick reminder about {CP.Meeting.Name} on {CP.Meeting.Date}.\n\n"
            "Need a different time? {CP.Meeting.RescheduleUrl}\n"
            "Cannot make it? {CP.Meeting.CancelUrl}"
        ),
        # includeCalendarEvent: the researched .ics in the email.
        "includeCalendarEvent": True,
        "conditions": {"match": vocab.MATCH_ALL, "rules": []},
    },
    {
        # The conditional pre-meeting reminder: the researched rule this whole
        # ticket is named for.
        "name": "2 hours before - only if the primary guest has not responded",
        "channel": vocab.EMAIL,
        "condition": vocab.BEFORE_IF_NO_RESPONSE,
        "offset": {"value": 2, "unit": vocab.HOURS},
        "emailTo": vocab.PRIMARY_GUEST,
        "emailFrom": vocab.HOST_ADDRESS,
        "repliesTo": vocab.REPLY_TO_HOST,
        "subject": "Can we confirm {CP.Meeting.Name}?",
        "body": (
            "Hi {CP.Guest.FirstName},\n\n"
            "We have not had a reply to the invite for {CP.Meeting.Name} on {CP.Meeting.Date}.\n\n"
            "{CP.Meeting.RescheduleUrl}"
        ),
        "conditions": {"match": vocab.MATCH_ALL, "rules": []},
    },
    {
        # The two step-6 advanced gates in one group: business hours only, and
        # only for meetings booked at least a week ahead - the research's own
        # worked example of the lead-time option.
        "name": "Weekdays only, booked a week ahead",
        "channel": vocab.EMAIL,
        "condition": vocab.BEFORE,
        "offset": {"value": 2, "unit": vocab.DAYS},
        "emailTo": vocab.ALL_GUESTS,
        "emailFrom": vocab.BOOKER_ADDRESS,
        "repliesTo": vocab.REPLY_TO_ASSIGNEES,
        "subject": "Agenda for {CP.Meeting.Name}",
        "body": "Hi {CP.Guest.FirstName}, the agenda for {CP.Meeting.Name} is below.",
        "skipNoShowAttendees": True,
        "conditions": {
            "match": vocab.MATCH_ALL,
            "rules": [
                {
                    "kind": vocab.RULE_WEEKDAY,
                    "weekdays": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                },
                {"kind": vocab.RULE_LEAD_TIME, "value": 1, "unit": vocab.WEEKS},
            ],
        },
    },
    {
        # The post-meeting follow-up, anchored on the meeting's *end* because the
        # booking's duration is a researched field and this is the only rule that
        # needs it.
        "name": "Follow-up an hour after",
        "channel": vocab.EMAIL,
        "condition": vocab.AFTER,
        "offset": {"value": 1, "unit": vocab.HOURS},
        "emailTo": vocab.ALL_GUESTS,
        "emailFrom": vocab.NO_REPLY,
        "repliesTo": vocab.REPLY_TO_BOOKER,
        "subject": "Thanks for your time - next steps",
        "body": "Hi {CP.Guest.FirstName}, thank you for meeting us. {CP.Meeting.RescheduleUrl}",
        "autoTranslateEnabled": True,
        vocab.SOURCE_LOCALE: "en",
        "conditions": {"match": vocab.MATCH_ALL, "rules": []},
    },
    {
        # The SMS nudge, and the phone-requiring consequence Cal documents. With
        # this attached, the meeting type's booking form must collect a phone.
        "name": "Text an hour before",
        "channel": vocab.SMS,
        "condition": vocab.BEFORE,
        "offset": {"value": 1, "unit": vocab.HOURS},
        "smsFrom": vocab.LOCAL_AREA_NUMBER,
        "repliesTo": vocab.REPLY_TO_HOST,
        "subject": "Reminder",
        "body": (
            "Reminder: {CP.Meeting.Name} starts at {CP.Meeting.Time} your time. "
            "Reply to reach the host."
        ),
        "conditions": {"match": vocab.MATCH_ALL, "rules": []},
    },
    {
        # Off, deliberately. A disabled reminder is never planned, so it shows up
        # as *absent* rather than as a planned-and-skipped row - a distinction a
        # reviewer can only check by looking for the thing that is not there.
        "name": "Disabled - never fires",
        "channel": vocab.EMAIL,
        "condition": vocab.BEFORE,
        "offset": {"value": 30, "unit": vocab.MINUTES},
        "emailTo": vocab.PRIMARY_GUEST,
        "emailFrom": vocab.HOST_ADDRESS,
        "enabled": False,
    },
)


def _guest(
    first: str, last: str, email: str, phone: str = "", status: str = "", **extra: Any
) -> dict[str, Any]:
    """One guest as the guest form would collect them."""
    body: dict[str, Any] = {"firstName": first, "name": f"{first} {last}", "email": email}
    if phone:
        body["phone"] = phone
    if status:
        body["responseStatus"] = status
    body.update(extra)
    return body


def _host(first: str = "Dana", last: str = "Okoro", email: str = "dana@contoso.example") -> dict[str, Any]:
    return {"firstName": first, "name": f"{first} {last}", "email": email}


def _booker(first: str, last: str, email: str) -> dict[str, Any]:
    return {"firstName": first, "name": f"{first} {last}", "email": email}


def _next_weekday(now, days_ahead: int, weekday: int, hour: int = 14, minute: int = 0):
    """A start ``days_ahead`` days out, forced onto a named weekday.

    Forced because the weekday gate is evaluated against the *meeting's* start in
    its own local time, so demonstrating it needs a booking that genuinely lands
    on a Saturday. Computed in UTC with the booking's offset set to UTC to match,
    rather than guessed - a demo whose weekend case lands on a Friday teaches the
    opposite of the rule.
    """
    from datetime import timedelta, timezone as _tz

    start = (now + timedelta(days=days_ahead)).astimezone(_tz.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    # date.weekday() is Monday-zero, which is the same order as vocab.WEEKDAYS.
    shift = (weekday - start.weekday()) % 7
    return start + timedelta(days=shift)


#: The bookings the demo delivers against.
#:
#: Every row is here for one specific reason, and the *arrangement* is the
#: interesting part: the same reminder has to give different answers for
#: different bookings, or nothing is demonstrated. ``fire_at`` is what makes each
#: one land differently.
DEMO_BOOKINGS: tuple[dict[str, Any], ...] = (
    {
        # Everything reachable, everybody addressable, guest accepted. The four
        # reminders that can fire on this booking all do, and the conditional one
        # is the only refusal - because the guest has responded.
        "label": "sent: addressable guests on a meeting four hours out",
        "why": "the unconditional email, the all-guests email, the follow-up and the SMS all go out",
        "start": "hours:4",
        "booked_days_ago": 20,
        "booking": {
            "title": "Northwind — Enterprise Evaluation",
            "durationMinutes": 45,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "location": "Zoom",
            "meetingUrl": "https://meet.example/northwind-eval",
            "rescheduleUrl": "https://meet.example/northwind-eval/reschedule",
            "cancelUrl": "https://meet.example/northwind-eval/cancel",
            "primaryGuest": _guest("Priya", "Raman", "priya.raman@northwind.example", "+15550100", "accepted"),
            "guests": [
                _guest("Priya", "Raman", "priya.raman@northwind.example", "+15550100", "accepted"),
                _guest("Marcus", "Webb", "marcus.webb@northwind.example", "+15550101", "accepted"),
            ],
            "host": _host(),
            "booker": _booker("Wen", "Li", "wen.li@contoso.example"),
            "assignees": [{"name": "Alba Ries", "email": "alba.ries@contoso.example"}],
        },
    },
    {
        # needsAction and a phone. The conditional pre-meeting reminder fires -
        # the researched rule this ticket is named for - and the SMS goes out, so
        # there is a delivery for a guest to reply to.
        "label": "sent: the primary guest has not answered the invite",
        "why": "the conditional pre-meeting reminder applies, and the SMS has a number to go to",
        "start": "hours:4",
        "booked_days_ago": 20,
        "booking": {
            "title": "Contoso — Security Review",
            "durationMinutes": 60,
            "timezone": "Asia/Tokyo",
            "timezoneOffsetMinutes": 540,
            "location": "Teams",
            "meetingUrl": "https://meet.example/contoso-security",
            "rescheduleUrl": "https://meet.example/contoso-security/reschedule",
            "cancelUrl": "https://meet.example/contoso-security/cancel",
            "primaryGuest": _guest("Rui", "Silva", "rui.silva@contoso.example", "+15550104", vocab.RESPONSE_NEEDS_ACTION),
            "guests": [_guest("Rui", "Silva", "rui.silva@contoso.example", "+15550104", vocab.RESPONSE_NEEDS_ACTION)],
            "host": _host("Sam", "Adeyemi", "sam@contoso.example"),
            "booker": _booker("Dana", "Okoro", "dana@contoso.example"),
        },
    },
    {
        # Declined, not accepted. The researched parenthetical counts Declined as
        # responding, so a guest who said no must not be chased - and this is the
        # one booking where that shows as a refusal rather than a coincidence.
        "label": "skipped: the primary guest declined the invite",
        "why": "the research counts Accepted and Declined alike, so the 'did not respond' nudge is not sent",
        "start": "hours:4",
        "booked_days_ago": 20,
        "booking": {
            "title": "Fabrikam — Procurement Walkthrough",
            "durationMinutes": 30,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "meetingUrl": "https://meet.example/fabrikam-procurement",
            "rescheduleUrl": "https://meet.example/fabrikam-procurement/reschedule",
            "cancelUrl": "https://meet.example/fabrikam-procurement/cancel",
            "primaryGuest": _guest(
                "Marcus", "Webb", "marcus.webb@solowebb.example", "+15550101", vocab.RESPONSE_DECLINED
            ),
            "guests": [
                _guest("Marcus", "Webb", "marcus.webb@solowebb.example", "+15550101", vocab.RESPONSE_DECLINED)
            ],
            "host": _host("Sam", "Adeyemi", "sam@contoso.example"),
            "booker": _booker("Dana", "Okoro", "dana@contoso.example"),
        },
    },
    {
        # Four days out, booked two days ago. A weekday, so the weekday gate
        # passes; four days' notice, so the "a week in advance" gate fails. The
        # arithmetic is deliberately tight - a meeting ten days out booked two
        # days ago would *pass* a one-week lead time, and a demo case labelled
        # "booked too late" that quietly satisfies the rule is worse than no
        # demo case at all.
        "label": "skipped: booked only two days before a meeting four days out",
        "why": "the gate says send *only* if booked a week in advance, and four days is not a week",
        "start": "days:4",
        "booked_days_ago": 2,
        "booking": {
            "title": "Alba Ries — Commercial review",
            "durationMinutes": 45,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "meetingUrl": "https://meet.example/ries-commercial",
            "rescheduleUrl": "https://meet.example/ries-commercial/reschedule",
            "cancelUrl": "https://meet.example/ries-commercial/cancel",
            "primaryGuest": _guest("Alba", "Ries", "alba.ries@fabrikam.example", "+15550102", "accepted"),
            "guests": [_guest("Alba", "Ries", "alba.ries@fabrikam.example", "+15550102", "accepted")],
            "host": _host(),
            "booker": _booker("Wen", "Li", "wen.li@contoso.example"),
        },
    },
    {
        # The same reminder, a Saturday instead - so the *other* researched gate
        # is the one that refuses, and a reviewer can see the two failing for
        # different reasons rather than one standing in for both.
        "label": "skipped: the meeting starts on a Saturday",
        "why": "'send only if meeting starts on' with the weekdays ticked, and this is a weekend",
        "start": "weekday:saturday",
        "days_ahead": 12,
        "booked_days_ago": 30,
        "booking": {
            "title": "Rui Silva — Platform walkthrough",
            "durationMinutes": 60,
            "timezone": "UTC",
            "timezoneOffsetMinutes": 0,
            "meetingUrl": "https://meet.example/silva-platform",
            "rescheduleUrl": "https://meet.example/silva-platform/reschedule",
            "cancelUrl": "https://meet.example/silva-platform/cancel",
            "primaryGuest": _guest("Rui", "Silva", "rui.silva@silva-consulting.example", "+15550105", "accepted"),
            "guests": [
                _guest("Rui", "Silva", "rui.silva@silva-consulting.example", "+15550105", "accepted")
            ],
            "host": _host(),
            "booker": _booker("Wen", "Li", "wen.li@contoso.example"),
        },
    },
    {
        # No address on anybody. The email path is the researched
        # "Recipient not found", while the SMS path - which reads the phone and
        # not the email - is unaffected. Two reasons from one booking.
        "label": "skipped: no guest has an email address to send to",
        "why": "'Send Email To' resolved nobody, while the SMS reminder still had a number",
        "start": "days:14",
        "booked_days_ago": 30,
        "booking": {
            "title": "Newco — Intro call",
            "durationMinutes": 30,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "meetingUrl": "https://meet.example/newco-intro",
            "rescheduleUrl": "https://meet.example/newco-intro/reschedule",
            "cancelUrl": "https://meet.example/newco-intro/cancel",
            # A phone but no email: reachable by SMS, unreachable by email.
            "primaryGuest": {"firstName": "Wen", "name": "Wen Li", "phone": "+15550103"},
            "guests": [{"firstName": "Wen", "name": "Wen Li", "phone": "+15550103"}],
            "host": _host(),
            "booker": _booker("Sam", "Adeyemi", "sam@contoso.example"),
        },
    },
    {
        # No phone at all. The booking has to be seeded out of the phone-required
        # rule to exist at all, which is exactly the deployment situation it
        # models: a booking made before the SMS reminder was attached. The SMS
        # then takes the researched "Phone not found".
        "label": "skipped: an SMS reminder with no phone on the guest form",
        "why": "the phone is only required from the moment an SMS reminder is enabled, and this booking predates that",
        "start": "days:15",
        "booked_days_ago": 30,
        "skip_the_phone_rule": True,
        "booking": {
            "title": "Wen Li — Renewal discussion",
            "durationMinutes": 30,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "meetingUrl": "https://meet.example/li-renewal",
            "rescheduleUrl": "https://meet.example/li-renewal/reschedule",
            "cancelUrl": "https://meet.example/li-renewal/cancel",
            "primaryGuest": _guest("Wen", "Li", "wen.li@newco.example", "", "accepted"),
            "guests": [_guest("Wen", "Li", "wen.li@newco.example", "", "accepted")],
            "host": _host(),
            "booker": _booker("Sam", "Adeyemi", "sam@contoso.example"),
        },
    },
    {
        # Already under way. A 24-hour-before reminder attached to a meeting that
        # started an hour ago has no moment at which it could have fired, so
        # planning records the researched "Reminder schedule time in the past".
        "label": "skipped at planning: the reminder's moment had already passed",
        "why": "a 24-hour reminder on a meeting that started an hour ago can never have been on time",
        "start": "hours:-1",
        "booked_days_ago": 30,
        "booking": {
            "title": "Northwind — Technical deep dive",
            "durationMinutes": 60,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "meetingUrl": "https://meet.example/northwind-deep-dive",
            "rescheduleUrl": "https://meet.example/northwind-deep-dive/reschedule",
            "cancelUrl": "https://meet.example/northwind-deep-dive/cancel",
            "primaryGuest": _guest("Priya", "Raman", "priya.raman@northwind.example", "+15550100", "accepted"),
            "guests": [_guest("Priya", "Raman", "priya.raman@northwind.example", "+15550100", "accepted")],
            "host": _host(),
            "booker": _booker("Wen", "Li", "wen.li@contoso.example"),
        },
    },
    {
        # Far enough out that nothing is due yet. This is the researched
        # `Scheduled` status, and it is the one state that only appears if the
        # seed plans *before* it fires - a run that planned and fired at the same
        # instant would have nothing left to show as pending.
        "label": "scheduled: a meeting next quarter, nothing due yet",
        "why": "the researched `Scheduled` status, which is otherwise invisible in a fired run",
        "start": "days:60",
        "booked_days_ago": 30,
        "booking": {
            "title": "Fabrikam — Annual review",
            "durationMinutes": 60,
            "timezone": "Europe/Dublin",
            "timezoneOffsetMinutes": 60,
            "meetingUrl": "https://meet.example/fabrikam-annual",
            "rescheduleUrl": "https://meet.example/fabrikam-annual/reschedule",
            "cancelUrl": "https://meet.example/fabrikam-annual/cancel",
            "primaryGuest": _guest("Alba", "Ries", "alba.ries@fabrikam.example", "+15550102", vocab.RESPONSE_NEEDS_ACTION),
            "guests": [
                _guest("Alba", "Ries", "alba.ries@fabrikam.example", "+15550102", vocab.RESPONSE_NEEDS_ACTION)
            ],
            "host": _host(),
            "booker": _booker("Wen", "Li", "wen.li@contoso.example"),
        },
    },
)

#: When the demo's scheduler runs, relative to the seed's clock. Late enough that
#: every near reminder is due, far short of the next-quarter booking, which is
#: what leaves a `Scheduled` row on the activity feed.
DEMO_FIRE_DAYS_AHEAD = 20


def _demo_start(now, spec: str, case: Mapping[str, Any]):
    """Resolve a demo booking's start from a short, readable spec.

    Written as specs rather than as precomputed timestamps so the seed is
    re-runnable on any day: a demo that pinned absolute dates would put every
    reminder in the past the first time the clock moved past them, and the
    interesting states would silently disappear.
    """
    from datetime import timedelta

    if spec.startswith("hours:"):
        return now + timedelta(hours=float(spec.split(":", 1)[1]))
    if spec.startswith("days:"):
        return now + timedelta(days=float(spec.split(":", 1)[1]))
    if spec.startswith("weekday:"):
        # vocab.WEEKDAYS is Monday-first, which is date.weekday()'s order too.
        return _next_weekday(
            now, int(case["days_ahead"]), vocab.WEEKDAY_INDEX[spec.split(":", 1)[1]]
        )
    raise ReminderError(f"unknown demo start spec {spec!r}")


def seed(db, context: dict[str, Any]) -> str:
    """Seed the messaging setup, six reminders, a meeting type, and nine bookings.

    Three passes, and the order matters. The setup and the reminders have to exist
    before the meeting type's attachments, and the meeting type has to exist
    before any booking. Then the run is split into planning and firing, because
    that is the researched shape - "Reminder schedule time in the past" is a
    planning question - and because planning first is what leaves the
    next-quarter booking visibly `Scheduled` instead of nothing at all.

    Returns a description naming the statuses and reasons that landed, so a
    reviewer can see at a glance that the demo reaches all three statuses and all
    five skip reasons rather than only the ones that worked.
    """
    from datetime import timedelta

    from dsr.store import RecordStore

    store = RecordStore(db)
    now: Any = context["now"]
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        return "0 deliveries (no rooms to attach bookings to)"

    engine = ReminderEngine(store, clock=lambda: now)
    source = "seed"

    engine.save_org(DEMO_MESSAGING, actor="dana", source=source)

    meeting_type = engine.create_meeting_type(
        {
            "name": "Enterprise Evaluation",
            "durationMinutes": 45,
            "location": "Zoom",
            # Cal's own spelling, so a team importing the meeting type finds the
            # field where the API reference puts it.
            "eventTypeName": "Enterprise Evaluation",
        },
        room_id=rooms[0][0],
        actor="dana",
        source=source,
    )

    for spec in DEMO_REMINDERS:
        record = engine.create_reminder(spec, actor="dana", source=source)
        engine.attach(meeting_type["id"], record["id"], actor="dana", source=source)

    booking_count = 0
    for index, case in enumerate(DEMO_BOOKINGS):
        room_id = rooms[index % len(rooms)][0]
        payload = {
            **case["booking"],
            "start": _demo_start(now, str(case["start"]), case).isoformat(),
            "bookedAt": (now - timedelta(days=float(case["booked_days_ago"]))).isoformat(),
            "meetingTypeId": meeting_type["id"],
        }
        engine.create_booking(
            room_id,
            payload,
            actor="dana",
            source=source,
            # One booking on purpose models a booking made *before* the SMS
            # reminder was enabled, which is the only way the researched
            # "Phone not found" is reachable at all.
            enforce_phone=not case.get("skip_the_phone_rule", False),
        )
        booking_count += 1

    # Pass one: plan. This is where a moment that has already gone by is recorded
    # as the researched "schedule time in the past" rather than being left to
    # look scheduled.
    for record in engine.list_bookings(limit=1000):
        engine.plan(record, actor="dana", source=source)

    # Pass two: fire, twenty days on. Every near reminder is due by then and the
    # next-quarter booking is not, so both a `Sent` and a `Scheduled` row survive
    # onto the activity feed.
    fired = engine.fire(now=now + timedelta(days=DEMO_FIRE_DAYS_AHEAD), source=source)

    # One forwarded reply, so the data flow's last clause - "inbound SMS replies
    # forwarded by email to selected roles" - is visible rather than described.
    forwarded = 0
    for record in engine.deliveries(status=vocab.SENT, channel=vocab.SMS, limit=50):
        try:
            engine.record_reply(
                record["id"],
                {"from": "+15550104", "body": "Can we push tomorrow's call to the afternoon?"},
                actor="dana",
                source=source,
            )
            forwarded = 1
            break
        except ReminderError:
            continue

    rows = engine.deliveries(limit=1000)
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in rows:
        data = row["data"]
        status = str(data.get("status"))
        by_status[status] = by_status.get(status, 0) + 1
        if data.get("reason"):
            by_reason[str(data["reason"])] = by_reason.get(str(data["reason"]), 0) + 1

    missing = sorted(set(vocab.SKIP_REASONS) - set(by_reason))
    summary = (
        f"{len(DEMO_REMINDERS)} reminders, 1 meeting type, {booking_count} bookings, "
        f"{len(rows)} deliveries ({fired['sent']} sent by the run, {forwarded} forwarded SMS reply), "
        f"statuses: " + ", ".join(f"{count} {name}" for name, count in sorted(by_status.items()))
    )
    if by_reason:
        summary += ", reasons: " + ", ".join(f"{count} {name}" for name, count in sorted(by_reason.items()))
    if missing:
        # Said out loud rather than left for a reviewer to notice. A demo that
        # cannot reach a documented reason is a demo that is hiding a rule.
        summary += f", NOT reached: {', '.join(missing)}"
    return summary
