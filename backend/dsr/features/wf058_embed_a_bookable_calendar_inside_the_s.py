"""WF-058: embed a bookable calendar inside the sales room / app.

A researched workflow, not a port: there is no source branch. The research document
is the specification, and what it specifies is the Cal.com embed surface - the four
slot selectors, the five-minute hold, the three booking kinds, the metadata limits
and the ``BOOKING_CREATED`` automation - rendered so a prospect books without
leaving the room.

The five sources it rests on, and what each contributes:

* ``slots/get-available-time-slots-for-an-event-type`` - the four ways of naming
  what to ask about (``eventTypeId``, ``eventTypeSlug``+``username``,
  ``usernames``, ``teamSlug``), the ``start``/``end``/``timeZone`` parameters, and
  the ``cal-api-version: 2024-09-04`` header. Also the dynamic case: "Checking slots
  by usernames is used mainly for dynamic events where there is no specific event but
  we just want to know when 2 or more people are available."
* ``slots/reserve-a-slot`` - ``POST /v2/slots/reservations`` returning
  ``reservationUid``, ``reservationDuration`` and ``reservationUntil``, the five
  minute default, the Get/Update/Delete trio on one reservation, and the
  automation: "no user action needed for the hold to expire".
* ``bookings/create-a-booking`` - the create payload (``attendee``, ``start``,
  the event type reference, ``bookingFieldsResponses``, ``metadata``), the
  ``cal-api-version: 2026-02-25`` header, the three kinds, ``recurrenceCount`` max
  32, ``instant`` for team events only, and ``bookingUidToReschedule``.
* ``atoms/introduction`` - the components a room may render, the
  ``CalOAuthProvider`` the step-1 OAuth client exists for, the custom booking flow
  ("intercept a booking to introduce your custom flow and then submit the booking")
  and the custom slot selection flow (``handleSlotReservation``), and the
  maintenance-mode note that says the next generation is copy-and-paste on API v2.
* ``llms.txt`` - the index the other four sit under, and the reason the embed in
  this product is rendered here rather than imported.

What this module is
-------------------

Only the three things a feature is allowed to add: the HTTP surface, the mapping
from domain errors to responses, and the demo data. The behaviour is in
:mod:`dsr.inroom_scheduling`, where it can be tested without a request.

Decisions in here a reviewer would otherwise have to reverse-engineer
---------------------------------------------------------------------

**Room-scoped paths are room-scoped.** Step 5 is "The prospect books entirely
in-room; no Chili-Piper-like external page is shown", so every route that a
prospect's browser calls takes ``/rooms/{room_id}/...``. The unscoped routes are
the ones genuinely not about a room: the vocabulary, the OAuth clients, the event
types, the calendar connects and the routing forms.

**``source`` is built from ``router.prefix`` and passed down.** The audit row must
name the route that actually served the write, so every write route builds its
source string here and hands it to an engine method that requires it. The defect
this prevents - an audit log recording a path the app no longer serves - has
shipped in this codebase before, so the suite asserts that every source recorded
matches a route the host actually mounted.

**``routed-slots`` is a GET and takes no ``source``.** The research is explicit
that the routing read "will not actually save the response", so the route has no
body to write and the engine method it calls takes no actor and no source. That
absence is the enforcement, and a test asserts the record count is unchanged after
driving it.

**A refusal writes nothing.** A booking for a slot somebody else holds, a
recurrence above 32, an instant booking on a personal event, a read-only booking
field answered differently: none of them create a booking, a hold, a room
annotation or a webhook. A 400 that left a row behind would be counted as a write
by anything reading the store, which is the opposite of what a refusal means.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.inroom_scheduling import (
    BOOKING_CREATED,
    DEFAULT_RESERVATION_DURATION_MINUTES,
    ROOM_FIELD,
    SchedulingEngine,
    SchedulingError,
    UnknownEventType,
    published_vocabulary,
)
from dsr.inroom_scheduling import inferences as scheduling_inferences
from dsr.inroom_scheduling.routing import OPERATORS
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-058-embed-a-bookable-calendar-inside-the-s",
    "ticket": "WF-058",
    "name": "Embed a bookable calendar inside the sales room",
    "description": (
        "Install a bookable calendar in a room: an OAuth client and an embed, a slot grid, an "
        "optional slot hold, and a booking the prospect submits entirely in-room."
    ),
    "nav": [{"id": "bookable-calendar", "label": "Bookable calendar"}],
}

router = APIRouter(prefix="/api/wf-058", tags=["wf-058"])


def get_engine(store: RecordStore = StoreDep) -> SchedulingEngine:
    """A :class:`SchedulingEngine` over the process-wide audited store.

    Per request, for the same reason the other features build their engines per
    request: the engine holds nothing beyond the store and a clock, and building it
    here leaves both overridable in a test instead of hanging a long-lived object
    off ``app.state`` - which is a shared file this feature may not edit.
    """
    return SchedulingEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _scheduling_error(request: Request, exc: SchedulingError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    One handler for the whole hierarchy, including the subclasses. Every refusal in
    :mod:`dsr.inroom_scheduling` is the caller's to fix - whether it is an ambiguous
    slot selector, a recurrence above the documented 32, a metadata payload over one
    of the documented limits, or a hold the clock has retired - and each carries the
    field that broke so the client can point at it.

    ``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
    404, and two handlers for one type is a collision the host refuses.
    """
    body: dict[str, Any] = {"error": "scheduling_error", "detail": str(exc)}
    for field in ("reason", "limit", "maximum", "value", "key", "uid", "until", "expires_at", "kind", "event_type"):
        value = getattr(exc, field, None)
        if value is not None:
            body[field] = value
    alternatives = getattr(exc, "alternatives", None)
    if alternatives:
        body["alternatives"] = list(alternatives)[:3]
    return JSONResponse(status_code=400, content=body)


EXCEPTION_HANDLERS = {SchedulingError: _scheduling_error}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the two API versions, the four slot selectors, the
    documented limits, the embed components, the calendar and conference providers,
    the booking-field vocabulary, the routing operators, and the researched quotes.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a term added in one place reaches every
    client at once.
    """
    return {
        **published_vocabulary(),
        "routing_operators": dict(OPERATORS),
        "booking_created": BOOKING_CREATED,
        "room_field": ROOM_FIELD,
        "default_hold_minutes": DEFAULT_RESERVATION_DURATION_MINUTES,
    }


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    WF-058 is a build rather than a port, so most of the workflow is this build's
    decisions rather than quoted behaviour. Those decisions are product behaviour,
    not comments, so they are collected here where a reviewer can disagree with a
    *named* entry instead of finding it in a diff.

    A read with no side effect, so it needs no store.
    """
    return scheduling_inferences.describe()


# --------------------------------------------------------------------------- #
# Counts for the page header
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Holds by state, bookings by kind and status, and what is installed.

    Computed over exactly the rows the same filters would return, so a room-scoped
    total above an unscoped list cannot be misread as a product-wide one.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Step 1: the OAuth client
# --------------------------------------------------------------------------- #


@router.get("/oauth-clients", summary="List OAuth clients")
def list_oauth_clients(
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Every client, with its live/granted state and never its token value.

    The full envelope per client, because an administrator reconnecting one needs
    the record id to patch it and the expiry to know whether it is worth patching.
    """
    records = engine.list_clients(limit=limit)
    return {
        "count": len(records),
        "clients": [
            {**record, "token_state": engine.client_token_state(record)} for record in records
        ],
    }


@router.post("/oauth-clients", status_code=201, summary="Stand up an OAuth client")
def create_oauth_client(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Step 1: the client the app "acts on behalf of a scheduling user" with.

    A credential-shaped field is refused by name rather than stored, so an
    accidental paste into ``client_secret`` is reported instead of becoming a live
    secret in a table the audit log and the schema explorer can both read.
    """
    return engine.create_client(payload, actor=actor, source=f"POST {router.prefix}/oauth-clients")


@router.get("/oauth-clients/{client_id}", summary="Read one OAuth client")
def read_oauth_client(client_id: str, engine: SchedulingEngine = EngineDep) -> dict[str, Any]:
    record = engine.get_client(client_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"oauth client {client_id} not found")
    return {**record, "token_state": engine.client_token_state(record)}


@router.patch("/oauth-clients/{client_id}", summary="Patch an OAuth client")
def update_oauth_client(
    client_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Patch a client's name, redirect URI or scopes, re-validating the result.

    A patch cannot write a token: the grant is the only thing that sets one, and a
    patch that could would be a second, unguarded door to the same field.
    """
    return engine.update_client(
        client_id, payload, actor=actor, source=f"PATCH {router.prefix}/oauth-clients/{client_id}"
    )


@router.delete("/oauth-clients/{client_id}", summary="Delete an OAuth client")
def delete_oauth_client(
    client_id: str,
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> Response:
    """Soft-delete a client. Its grants and the bookings they served stay auditable."""
    if engine.get_client(client_id) is None:
        raise HTTPException(status_code=404, detail=f"oauth client {client_id} not found")
    engine.delete_client(
        client_id, actor=actor, source=f"DELETE {router.prefix}/oauth-clients/{client_id}"
    )
    return Response(status_code=204)


@router.post("/oauth-clients/{client_id}/grant", summary="Record that a token was granted")
def grant_oauth_token(
    client_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Record a grant: the subject, the scopes and the expiry. Never the value.

    The data flow begins at "OAuth access token", so a token in hand is the expected
    input. What is stored is that one exists, who it acts for, what it may do and
    when it lapses - which is all the product needs to answer "can this room book?".
    """
    record = engine.grant(
        client_id, payload, actor=actor, source=f"POST {router.prefix}/oauth-clients/{client_id}/grant"
    )
    return {**record, "token_state": engine.client_token_state(record)}


# --------------------------------------------------------------------------- #
# Step 2: event types, calendar connects, routing forms
# --------------------------------------------------------------------------- #


@router.get("/event-types", summary="List event types")
def list_event_types(
    kind: str | None = Query(default=None, description="personal | team | routing | seated"),
    team_slug: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """The event types a room can book, each with its hosts and booking fields.

    The full envelope per event type, because a builder configuring an embed needs
    the record id to point at it and the resolved working hours to know what the grid
    will offer.
    """
    records = engine.list_event_types(kind=kind, team_slug=team_slug, limit=limit)
    return {"count": len(records), "event_types": records}


@router.post("/event-types", status_code=201, summary="Create an event type")
def create_event_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Create an event type: a kind, a slug, a host's working hours and a length.

    ``booking_fields`` carries the researched prefill and read-only rules, and
    ``seats`` is required for a seated event and refused on any other kind, so a
    seat count cannot cap bookings nobody meant to cap.
    """
    return engine.create_event_type(
        payload, actor=actor, source=f"POST {router.prefix}/event-types"
    )


@router.get("/event-types/{event_type_id}", summary="Read one event type")
def read_event_type(event_type_id: str, engine: SchedulingEngine = EngineDep) -> dict[str, Any]:
    record = engine.get_event_type(event_type_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"event type {event_type_id} not found")
    return record


@router.patch("/event-types/{event_type_id}", summary="Patch an event type")
def update_event_type(
    event_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Patch an event type, re-running the create validation on the merged result.

    Re-validated so a patch cannot leave an event type whose host has no working
    hours, or whose read-only field has lost the prefilled value it depends on.
    """
    return engine.update_event_type(
        event_type_id, payload, actor=actor, source=f"PATCH {router.prefix}/event-types/{event_type_id}"
    )


@router.delete("/event-types/{event_type_id}", summary="Delete an event type")
def delete_event_type(
    event_type_id: str,
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> Response:
    """Soft-delete an event type. Its bookings stay auditable."""
    if engine.get_event_type(event_type_id) is None:
        raise HTTPException(status_code=404, detail=f"event type {event_type_id} not found")
    engine.delete_event_type(
        event_type_id, actor=actor, source=f"DELETE {router.prefix}/event-types/{event_type_id}"
    )
    return Response(status_code=204)


@router.get("/calendars", summary="Which calendars are connected")
def list_calendars(
    host: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """The Google/Outlook/Apple connects, and which providers are still to connect.

    ``missing`` is published beside ``connected`` because a page showing three
    enabled connect buttons for three calendars that are already connected looks
    broken when a rep clicks one.
    """
    return {
        "connections": engine.list_calendars(host=host),
        **engine.calendars(),
    }


@router.post("/calendars", summary="Connect a calendar")
def connect_calendar(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Record a calendar connect, idempotent per provider and host.

    The researched buttons are part of the embed, not of the booking path, and
    nothing in the researched data flow depends on a connected calendar - so this is
    configuration, and the payload says so rather than claiming an OAuth exchange
    this product cannot make.
    """
    return engine.connect_calendar(payload, actor=actor, source=f"POST {router.prefix}/calendars")


@router.delete("/calendars/{calendar_id}", summary="Disconnect a calendar")
def disconnect_calendar(
    calendar_id: str,
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> Response:
    """Disconnect a calendar. 204."""
    record = engine.store.get(calendar_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"calendar connection {calendar_id} not found")
    engine.disconnect_calendar(
        calendar_id, actor=actor, source=f"DELETE {router.prefix}/calendars/{calendar_id}"
    )
    return Response(status_code=204)


@router.get("/routing-forms", summary="List routing forms")
def list_routing_forms(
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Every form, with its ordered rules and the catch-all each one must end in."""
    records = engine.list_forms(limit=limit)
    return {"count": len(records), "routing_forms": records}


@router.post("/routing-forms", status_code=201, summary="Create a routing form")
def create_routing_form(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Create a form whose rules route to event types and which ends in a catch-all.

    ``fallbackEventTypeId`` is required. A response matching no rule still has to
    reach a host, or the prospect is left on a form that appears broken - and every
    event type named, including the fallback, is checked to exist before the row is
    written, so a form cannot fail on a prospect's first answer.
    """
    return engine.create_form(payload, actor=actor, source=f"POST {router.prefix}/routing-forms")


@router.get("/routing-forms/{form_id}", summary="Read one routing form")
def read_routing_form(form_id: str, engine: SchedulingEngine = EngineDep) -> dict[str, Any]:
    record = engine.get_form(form_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"routing form {form_id} not found")
    return record


@router.patch("/routing-forms/{form_id}", summary="Patch a routing form")
def update_routing_form(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Patch a form's rules or its catch-all, re-validating both."""
    return engine.update_form(
        form_id, payload, actor=actor, source=f"PATCH {router.prefix}/routing-forms/{form_id}"
    )


@router.delete("/routing-forms/{form_id}", summary="Delete a routing form")
def delete_routing_form(
    form_id: str,
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> Response:
    """Soft-delete a form. 204."""
    if engine.get_form(form_id) is None:
        raise HTTPException(status_code=404, detail=f"routing form {form_id} not found")
    engine.delete_form(
        form_id, actor=actor, source=f"DELETE {router.prefix}/routing-forms/{form_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The embed, per room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/embed", summary="The room's embed")
def read_embed(room_id: str, engine: SchedulingEngine = EngineDep) -> dict[str, Any]:
    """The embed, the event type it books, and whether the token can book.

    A room with no embed is a 404 rather than an empty object: "no embed installed"
    and "an embed with nothing on it" are different states, and only the first is
    worth a page.
    """
    record = engine.embed_record(room_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} has no embed installed")
    return engine.embed_response(room_id)


@router.put("/rooms/{room_id}/embed", summary="Install or replace the room's embed")
def save_embed(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 1 and 2 together: which components the room renders, and what it books.

    Validated before anything is written - the client, the event type, the routing
    form, the components and the custom properties - so a room cannot end up with an
    embed pointing at something that does not exist. An embed naming only a routing
    form takes the form's catch-all as its event type, which is the researched
    custom flow rather than a convenience.
    """
    return engine.save_embed(
        room_id, payload, actor=actor, source=f"PUT {router.prefix}/rooms/{room_id}/embed"
    )


# --------------------------------------------------------------------------- #
# The slot grid, per room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/slots", summary="GET /v2/slots, for this room")
def room_slots(
    room_id: str,
    eventTypeId: str | None = Query(default=None, description="query an event type by id"),
    slug: str | None = Query(default=None, description="eventTypeSlug"),
    username: str | None = Query(default=None),
    teamSlug: str | None = Query(default=None),
    usernames: str | None = Query(default=None, description="usernames=alice,bob"),
    start: str | None = Query(default=None, description="ISO-8601"),
    end: str | None = Query(default=None, description="ISO-8601"),
    timeZone: str | None = Query(default=None, description="IANA zone for the labels"),
    bookingUidToReschedule: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """The Booker's grid: every candidate slot, and why each is or is not bookable.

    A query names exactly one of the four researched selectors. ``usernames`` is the
    dynamic case and needs two or more names, because the researched feature is
    finding "when 2 or more people are available" - one name is not that feature,
    it is the personal case spelled oddly.

    The grid is generated from working hours and then marked, never generated as
    only the free ones, so "09:00 is not available because Priya already has a
    booking" and "09:00 is not available because the last seat has gone" are
    different answers. ``bookingUidToReschedule`` excludes that booking's own slot
    from busy time, which is the only documented purpose of the field.
    """
    params = {
        "eventTypeId": eventTypeId,
        "slug": slug,
        "username": username,
        "teamSlug": teamSlug,
        "usernames": usernames,
        "start": start,
        "end": end,
        "timeZone": timeZone,
        "bookingUidToReschedule": bookingUidToReschedule,
    }
    return engine.slots(room_id, params)


@router.get("/rooms/{room_id}/routed-slots", summary="GET /v2/routing-forms/slots")
def room_routed_slots(
    room_id: str,
    responses: str = Query(
        default="{}", description='the answers, as JSON: {"topic":"security"}'
    ),
    formId: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    timeZone: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Route an answer to an event type, and return that event type's slots.

    A GET, deliberately: "It will not actually save the response just return the
    routed event type and slots when it can be booked." A prospect answering a
    qualification question is answering it to be shown a calendar, and a stored
    answer they never submitted is a record of something they did not do. The
    answers arrive as a JSON query parameter rather than a request body, so the
    route stays a plain read with nothing to write.

    The fall-through is explicit in the response. ``reason`` is ``rule_matched``,
    ``fallback`` or ``rule_declined``, so "nothing matched and this is the catch-all"
    never looks like "a rule sent you here".
    """
    try:
        answers = json.loads(responses or "{}")
    except json.JSONDecodeError as exc:
        raise SchedulingError(f"responses is not valid JSON: {exc}") from exc
    if not isinstance(answers, dict):
        raise SchedulingError("responses must be a JSON object of question to answer")
    return engine.routed_slots(
        room_id,
        {"formId": formId, "start": start, "end": end, "timeZone": timeZone},
        answers,
    )


# --------------------------------------------------------------------------- #
# Slot holds, per room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/holds", summary="List a room's holds")
def list_holds(
    room_id: str,
    state: str | None = Query(default=None, description="held | consumed | released | expired"),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Every hold on a room, each read at the current moment.

    A hold whose row still says ``held`` after its ``reservationUntil`` is reported
    as ``expired`` with ``stored_state: held`` beside it. That divergence is the
    researched automation made visible: "no user action needed for the hold to
    expire", and no job ran to make it so.
    """
    holds = engine.holds(room_id, state=state)
    return {"room_id": room_id, "count": len(holds), "holds": holds}


@router.post("/rooms/{room_id}/holds", status_code=201, summary="POST /v2/slots/reservations")
def reserve_slot(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Hold a slot, and answer with the three researched fields.

    ``reservationDuration`` defaults to the documented five minutes, and the
    response carries ``reservationUid``, ``reservationDuration`` and
    ``reservationUntil`` under exactly those names. The slot stops being offered to
    anybody else until the hold lapses, and lapsing needs nothing from anybody.

    Needs a live token, because a hold is a commitment of the scheduling user's
    calendar and the token is what the app acts with.
    """
    return engine.reserve(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/holds"
    )


@router.get("/rooms/{room_id}/holds/{uid}", summary="GET a reserved slot")
def read_hold(
    room_id: str, uid: str, engine: SchedulingEngine = EngineDep
) -> dict[str, Any]:
    """One hold, read at the current moment.

    Scoped to the room: a hold belongs to the room that took it, so another room's
    uid is a 404 here rather than somebody else's hold.
    """
    try:
        return engine.hold(room_id, uid)
    except UnknownEventType as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/rooms/{room_id}/holds/{uid}", summary="PATCH a reserved slot")
def extend_hold(
    room_id: str,
    uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Change a hold's duration, measured from now.

    The new duration is measured from this moment rather than from the original
    grant, because the point of extending a hold is to buy more time from now - and
    it is why ``reservationUntil`` moves while ``reserved_at`` does not.

    Extending a hold the clock has already retired is a 400 naming the expiry, not
    a silent resurrection: the slot has been offered to somebody else in the
    meantime, and pretending otherwise books a double.
    """
    try:
        return engine.extend_hold(
            room_id, uid, payload, actor=actor, source=f"PATCH {router.prefix}/rooms/{room_id}/holds/{uid}"
        )
    except UnknownEventType as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/rooms/{room_id}/holds/{uid}", summary="DELETE a reserved slot")
def release_hold(
    room_id: str,
    uid: str,
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Give the slot back. 200 with the hold's new state, so the page can confirm it.

    Releasing an already-released hold is a 400 rather than a second success: a
    second release would move the recorded release time and make the audit trail
    say the slot was given back later than it was.
    """
    try:
        return engine.release_hold(
            room_id, uid, actor=actor, source=f"DELETE {router.prefix}/rooms/{room_id}/holds/{uid}"
        )
    except UnknownEventType as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Bookings, per room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/bookings", summary="List a room's bookings")
def list_bookings(
    room_id: str,
    status: str | None = Query(default=None, description="confirmed | cancelled"),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Every booking this room has taken, newest first.

    Each carries the researched payload field names - ``eventTypeId``,
    ``bookingFieldsResponses``, ``metadata`` - so a record read here is recognisable
    to anything that already speaks the create-booking shape.
    """
    bookings = engine.bookings(room_id, status=status)
    return {"room_id": room_id, "count": len(bookings), "bookings": bookings}


@router.post("/rooms/{room_id}/bookings", status_code=201, summary="POST /v2/bookings")
def create_booking(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """The whole flow: intercept, ask, submit - entirely in-room.

    This is the researched custom booking flow: "intercept a booking to introduce
    your custom flow and then submit the booking". Whatever the page asked on the
    way here - a qualification question, a slot hold - arrives as
    ``bookingFieldsResponses`` and ``reservationUid``, and this route does the
    researched work of validating and writing it.

    Three refusals a caller will meet, all of them the research's own rules:
    ``instant: true`` on anything but a team event; ``recurrenceCount`` above the
    documented 32, which is refused rather than truncated; and metadata over any of
    the three documented limits, which is refused naming the key that broke it. A
    refusal writes nothing at all.
    """
    return engine.book(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings"
    )


@router.get("/rooms/{room_id}/bookings/{uid}", summary="Read one booking")
def read_booking(
    room_id: str, uid: str, engine: SchedulingEngine = EngineDep
) -> dict[str, Any]:
    """One booking, with its video link when the event type has a conference."""
    try:
        return engine.booking(room_id, uid)
    except UnknownEventType as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/rooms/{room_id}/bookings/{uid}", summary="Cancel a booking")
def cancel_booking(
    room_id: str,
    uid: str,
    reason: str = Query(default="cancelled by the room"),
    actor: str | None = Query(default=None),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """Cancel a booking. The slot returns to the grid and the record says why.

    A second cancellation is a 400 rather than a success, because absorbing it
    would overwrite the first cancellation's reason and time.
    """
    try:
        return engine.cancel_booking(
            room_id,
            uid,
            reason,
            actor=actor,
            source=f"DELETE {router.prefix}/rooms/{room_id}/bookings/{uid}",
        )
    except UnknownEventType as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/booking-events", summary="The BOOKING_CREATED events")
def list_booking_events(
    room_id: str,
    event: str | None = Query(default=None, description="BOOKING_CREATED | BOOKING_CANCELLED"),
    engine: SchedulingEngine = EngineDep,
) -> dict[str, Any]:
    """The automation the research names, as a record rather than a dispatch.

    "On success, ``BOOKING_CREATED`` webhook fires; downstream automations can
    chain (see #16)." The event is recorded so another workflow in this product can
    read it, and the accompanying delivery row says ``pending`` - this build claims
    the event, not a transport to somebody's endpoint.

    A reschedule is deliberately absent from this list: it moves a booking rather
    than creating one, so re-firing the event would notify the prospect twice about
    the same meeting.
    """
    return {
        "room_id": room_id,
        "events": engine.booking_events(room_id, event=event),
        "deliveries": engine.webhooks(room_id),
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The event types the demo installs: one of each researched kind, and the two
#: configurations worth seeing side by side.
#:
#: * a **personal** 30-minute event on a 30-minute grid, so a booked slot blocks the
#:   next one too - the buffer nobody configured;
#: * a **team** event, which is the only kind an instant booking can use, so the demo
#:   has one;
#: * a **seated** event with four seats, so "no seats left" is a reason a slot is
#:   unavailable that is neither busy nor held;
#: * a **routing** event the catch-all routes to, which is what makes the
#:   fall-through in the demo end at a real host.
DEMO_EVENT_TYPES: tuple[dict[str, Any], ...] = (
    {
        "eventTypeId": "evt_personal_30",
        "slug": "intro",
        "kind": "personal",
        "title": "30 minute intro",
        "host": "priya",
        "length_minutes": 30,
        "slot_interval_minutes": 30,
        "minimum_notice_minutes": 60,
        "time_zone": "UTC",
        "days": [1, 2, 3, 4, 5],
        "working_hours_start": "09:00",
        "working_hours_end": "17:00",
        "location": "google_meet",
        "booking_fields": [
            {
                "name": "team_size",
                "label": "How many people will be on the call?",
                "type": "select",
                "required": True,
                "options": ["1", "2-5", "6-20", "20+"],
            },
            {
                "name": "what_to_cover",
                "label": "What should we cover?",
                "type": "textarea",
                "required": False,
            },
            {
                # Read-only and prefilled, which is the researched pairing: a field
                # the embed answers and the prospect cannot change.
                "name": "room_account",
                "label": "Account",
                "type": "text",
                "read_only": True,
                "prefilled": "Northwind Traders",
            },
        ],
    },
    {
        "eventTypeId": "evt_team_15",
        "slug": "war-room",
        "kind": "team",
        "title": "Team war room",
        "teamSlug": "revenue-team",
        "host": "revenue-team",
        "length_minutes": 15,
        "hosts": [
            {
                "username": "priya",
                "time_zone": "UTC",
                "days": [1, 2, 3, 4, 5],
                "start": "09:00",
                "end": "13:00",
                "slot_interval_minutes": 15,
                "minimum_notice_minutes": 30,
            },
            {
                "username": "marcus",
                "time_zone": "UTC",
                "days": [1, 2, 3, 4],
                "start": "11:00",
                "end": "17:00",
                "slot_interval_minutes": 30,
                "minimum_notice_minutes": 0,
            },
        ],
        "location": "zoom",
        "instant_bookable": True,
    },
    {
        "eventTypeId": "evt_seated_45",
        "slug": "deep-dive",
        "kind": "seated",
        "title": "45 minute deep dive",
        "host": "alba",
        "length_minutes": 45,
        "seats": 4,
        "slot_interval_minutes": 45,
        "minimum_notice_minutes": 0,
        "time_zone": "UTC",
        "days": [2, 4],
        "working_hours_start": "10:00",
        "working_hours_end": "12:00",
        "location": "ms_teams",
        "booking_fields": [
            {
                "name": "topic",
                "label": "What should we deep dive on?",
                "type": "text",
                "required": True,
                "prefilled": "migration plan",
            }
        ],
    },
    {
        "eventTypeId": "evt_routing_general",
        "slug": "general",
        "kind": "routing",
        "title": "General enquiry",
        "host": "dana",
        "length_minutes": 30,
        "slot_interval_minutes": 30,
        "minimum_notice_minutes": 0,
        "time_zone": "UTC",
        "days": [1, 2, 3, 4, 5],
        "working_hours_start": "10:00",
        "working_hours_end": "12:00",
        "location": "phone",
    },
)


#: The routing form the demo installs. Three rules and a catch-all, so a reviewer
#: can see all three routing outcomes: a match, the catch-all, and a rule that
#: matched and deliberately declined.
DEMO_ROUTING_FORM: dict[str, Any] = {
    "name": "What kind of conversation is this?",
    "fields": ["topic", "company_size"],
    "rules": [
        {
            "field": "topic",
            "operator": "equals",
            "value": "security",
            "eventTypeId": "evt_seated_45",
            "label": "A security review",
        },
        {
            "field": "topic",
            "operator": "equals",
            "value": "pricing",
            "eventTypeId": "evt_personal_30",
            "label": "A pricing conversation",
        },
        {
            # Deliberately declines the fallback, which is the fourth routing
            # outcome and the one that is hardest to tell apart from a bug. A
            # request that reaches it is answered, deliberately, with no calendar.
            "field": "topic",
            "operator": "equals",
            "value": "unsubscribe",
            "eventTypeId": "",
            "fallback": True,
            "label": "Not a sales conversation",
        },
    ],
    "fallbackEventTypeId": "evt_routing_general",
}

#: The calendars the demo connects: two providers, and one provider deliberately
#: left unconnected so the page shows a button that is still available.
DEMO_CALENDARS: tuple[dict[str, Any], ...] = (
    {"provider": "google", "host": "priya", "label": "Google (priya)"},
    {"provider": "outlook", "host": "alba", "label": "Outlook (alba)"},
)


def _first_room(context: dict[str, Any]) -> str | None:
    rooms = list(context.get("room_ids") or [])
    return rooms[0][0] if rooms else None


def _room_at(context: dict[str, Any], index: int) -> str | None:
    rooms = list(context.get("room_ids") or [])
    return rooms[index % len(rooms)][0] if rooms else None


def _future_day(now: Any, wanted: int, *, margin_hours: int = 48) -> Any:
    """A UTC midnight at least ``margin_hours`` away, on ISO weekday ``wanted``.

    The margin is what makes the demo independent of the hour the seed happens to
    run at. Without it, a seed that runs on a Tuesday afternoon would pick *this*
    Tuesday, whose 10:00 has already passed, and the booking would be refused for
    being in the past - a demo failing for a reason that has nothing to do with the
    workflow.

    Returned as a midnight so a caller can build several windows on the same day
    without each one walking the calendar again.
    """
    from datetime import timedelta, timezone

    base = (now.astimezone(timezone.utc) + timedelta(hours=margin_hours)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for offset in range(0, 15):
        candidate = base + timedelta(days=offset)
        if candidate.isoweekday() == wanted:
            return candidate
    return base  # pragma: no cover - 15 days covers every weekday


def _window(day: Any, from_hour: int, to_hour: int) -> tuple[str, str]:
    """An ISO ``[start, end)`` window on a day returned by :func:`_future_day`."""
    from datetime import timedelta

    opens = day.replace(hour=from_hour)
    closes = day.replace(hour=to_hour)
    return (
        opens.isoformat().replace("+00:00", "Z"),
        (closes + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    )


def _first_free(
    engine: SchedulingEngine, room_id: str, event_type_id: str, window: tuple[str, str]
) -> str:
    """The first bookable slot in a window, read off the grid.

    Deliberately the same two calls a browser makes - ask for the grid, take the
    first slot marked available - rather than the seed computing a time and
    hoping. A 45-minute grid does not contain 10:00, a slot inside a host's
    minimum notice is not offered, and a slot somebody already holds is marked
    unavailable: guessing would have to reimplement all three rules to be right,
    and would silently stop demonstrating them.
    """
    grid = engine.slots(
        room_id,
        {"eventTypeId": event_type_id, "start": window[0], "end": window[1]},
    )
    for slot in grid["slots"]:
        if slot["available"]:
            return str(slot["start"])
    raise SchedulingError(
        f"the seeded demo needs a free slot on {event_type_id} between {window[0]} and {window[1]}, "
        "and the grid offers none. The event type's working hours or the room's hold are the likely "
        "cause; a demo that cannot find a slot is a demo that cannot be reviewed."
    )


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the clients, event types, calendars, a routing form, two embeds and five bookings.

    Everything is created by running the real engine over the real rules, and every
    time comes off the real slot grid, so the demo cannot show a shape the workflow
    would not produce - and seeding opens no socket, because the calendar here is the
    audited store.

    Deliberately mixed. A demo of only successful bookings would teach nothing about
    the four things this workflow exists for: a hold that has to lapse, a booking
    refused for a documented limit, a reschedule that moves rather than duplicates,
    and a seated event filling up. All four are here, alongside a live hold somebody
    is in the middle of using and a second client with no grant.

    Returns a description the seeder prints, and which says how many of each state
    landed so a reviewer can see at a glance that the demo is mixed.
    """
    room_id = _first_room(context)
    if room_id is None:
        return "nothing seeded (no rooms to scope an embed to)"

    now = context["now"]
    engine = SchedulingEngine(RecordStore(db), clock=lambda: now)
    source = "seed"

    # -- step 1: the OAuth client and its grant --------------------------- #
    client = engine.create_client(
        {
            "name": "Northwind sales room",
            "client_id": "dsr-northwind",
            "redirect_uri": "https://rooms.example/wf-058/callback",
            "scopes": ["BOOKING", "EVENT_TYPE", "SCHEDULE", "CALENDAR_READ"],
        },
        actor="dana",
        source=source,
    )
    engine.grant(
        client["id"], {"subject": "priya"}, actor="dana", source=source
    )
    # A second client with no grant, so the page shows the difference between a
    # client that can book and one that cannot, and so the "several clients, which
    # one?" refusal has something to refuse.
    ungranted = engine.create_client(
        {
            "name": "Contoso (no grant yet)",
            "client_id": "dsr-contoso",
            "redirect_uri": "https://rooms.example/wf-058/callback-2",
            "scopes": ["BOOKING"],
        },
        actor="dana",
        source=source,
    )

    # -- step 2: event types, calendars, a routing form ------------------- #
    created = {
        spec["eventTypeId"]: engine.create_event_type(spec, actor="dana", source=source)
        for spec in DEMO_EVENT_TYPES
    }
    if len(created) != len(DEMO_EVENT_TYPES):
        return "DEMO SHAPE BROKEN: two event types share an eventTypeId"
    for spec in DEMO_CALENDARS:
        engine.connect_calendar(spec, actor="dana", source=source)
    form = engine.create_form(DEMO_ROUTING_FORM, actor="dana", source=source)

    engine.save_embed(
        room_id,
        {
            "clientId": client["id"],
            "eventTypeId": "evt_personal_30",
            "routingFormId": form["id"],
            "components": ["booker", "availability", "calendar_connect"],
            "css": {"--dsr-cal-accent": "#4f9cf9", "--dsr-cal-radius": "10px"},
            "time_zone": "UTC",
            "horizon_days": 21,
        },
        actor="dana",
        source=source,
    )

    # -- the outcomes, in the order they happen --------------------------- #
    tuesday = _future_day(now, 2)
    wednesday = _future_day(now, 3)
    thursday = _future_day(now, 4)

    morning = _window(tuesday, 9, 12)
    afternoon = _window(tuesday, 13, 15)
    evening = _window(tuesday, 15, 17)

    # A standard booking, with the read-only booking field left at its prefilled
    # value and a metadata value that is a number rather than a string.
    first = engine.book(
        room_id,
        {
            "start": _first_free(engine, room_id, "evt_personal_30", morning),
            "attendee": {
                "name": "Ines Duarte",
                "email": "ines.duarte@northwind.example",
                "timeZone": "UTC",
            },
            "bookingFieldsResponses": {"team_size": "6-20", "what_to_cover": "migration"},
            "metadata": {"deal_stage": "evaluation", "seats": 4},
        },
        actor="dana",
        source=source,
    )

    # A hold somebody is in the middle of using, with the researched *custom*
    # duration rather than the five-minute default.
    #
    # "you can also specify custom duration for how long the slot should be
    # reserved for (defaults to 5 minutes)" - and a longer hold is what a prospect
    # filling in a qualification form actually needs, because the hold exists to
    # cover the time they spend on the next screen. Forty-five minutes also means
    # this hold is still live when the demo's six-minutes-later write happens, so
    # the page shows a live hold and an expired one side by side rather than
    # expiring both.
    hold = engine.reserve(
        room_id,
        {
            "start": _first_free(engine, room_id, "evt_personal_30", afternoon),
            "attendee": {"name": "Tomas Vela", "email": "tomas.vela@contoso.example"},
            "reservationDuration": 45,
        },
        actor="dana",
        source=source,
    )

    # A hold that lapses, and the write that notices.
    #
    # "no user action needed for the hold to expire - a reservation auto-expires
    # after reservationDuration" is a statement about time, not about a job. The demo
    # shows both halves: the hold is taken at ``now`` with the researched five-minute
    # default, and six minutes later somebody takes *another* hold - the researched
    # custom slot-selection flow being used by someone else in the meantime - and
    # that write is what notices the first hold has lapsed and records that it has.
    # No sweep ran. The clock is the whole story.
    from datetime import timedelta

    lapsed = engine.reserve(
        room_id,
        {"start": _first_free(engine, room_id, "evt_personal_30", _window(wednesday, 9, 12))},
        actor="dana",
        source=source,
    )
    later = SchedulingEngine(RecordStore(db), clock=lambda: now + timedelta(minutes=6))
    later.reserve(
        room_id,
        {"start": _first_free(later, room_id, "evt_personal_30", evening)},
        actor="dana",
        source=source,
    )
    expired_view = later.hold(room_id, lapsed["reservationUid"])

    # An instant booking, which only a team event can take. No start, so the soonest
    # bookable slot is the answer - which is what "instant" has to mean.
    instant = engine.book(
        room_id,
        {
            "eventTypeId": "evt_team_15",
            "instant": True,
            "attendee": {"name": "Rui Silva", "email": "rui.silva@newco.example", "timeZone": "UTC"},
            "metadata": {"deal_stage": "discovery"},
        },
        actor="dana",
        source=source,
    )

    # A recurring booking, described as a series on one record.
    recurring = engine.book(
        room_id,
        {
            "eventTypeId": "evt_team_15",
            "start": _first_free(engine, room_id, "evt_team_15", _window(thursday, 10, 13)),
            "recurrenceCount": 6,
            "attendee": {"name": "Alba Ries", "email": "alba.ries@fabrikam.example", "timeZone": "UTC"},
        },
        actor="dana",
        source=source,
    )

    # A reschedule onto the slot the booking already holds.
    #
    # This is the case ``bookingUidToReschedule`` exists for. Without the exclusion
    # the booking's own slot is in its own busy set, so a prospect who re-submits
    # the time they already have is told somebody else took it - by themselves. The
    # record is moved in place rather than duplicated, and ``BOOKING_CREATED`` is not
    # re-fired.
    moved = engine.book(
        room_id,
        {
            "start": first["start"],
            "bookingUidToReschedule": first["uid"],
            "attendee": {
                "name": "Ines Duarte",
                "email": "ines.duarte@northwind.example",
                "timeZone": "UTC",
            },
            "bookingFieldsResponses": {"team_size": "6-20"},
            "metadata": {"rescheduled_by": "the prospect"},
        },
        actor="dana",
        source=source,
    )

    # A refusal, run here and expected to raise, so the demo asserts the documented
    # ceiling rather than a reader having to take its word for it.
    refused = False
    try:
        engine.book(
            room_id,
            {
                "eventTypeId": "evt_team_15",
                "start": _first_free(engine, room_id, "evt_team_15", _window(thursday, 10, 13)),
                "recurrenceCount": 40,
                "attendee": {"name": "Dana Okoro", "email": "dana.okoro@fabrikam.example"},
            },
            actor="dana",
            source=source,
        )
    except SchedulingError:
        refused = True

    # A second room on the seated event, so the page shows a second installation and
    # a slot that becomes unavailable for want of a seat rather than for want of a
    # free host. Four bookings fill a four-seat event; the fifth would be refused.
    other = _room_at(context, 1)
    seated_taken = 0
    if other and other != room_id:
        engine.save_embed(
            other,
            {
                "clientId": client["id"],
                "eventTypeId": "evt_seated_45",
                "components": ["booker", "event_type", "calendar_connect"],
                "time_zone": "UTC",
            },
            actor="dana",
            source=source,
        )
        seated_window = _window(tuesday, 10, 12)
        seated_slot = _first_free(engine, other, "evt_seated_45", seated_window)
        for index in range(4):
            engine.book(
                other,
                {
                    "eventTypeId": "evt_seated_45",
                    "start": seated_slot,
                    "attendee": {
                        "name": f"Seated attendee {index + 1}",
                        "email": f"seated{index + 1}@fabrikam.example",
                        "timeZone": "UTC",
                    },
                    "bookingFieldsResponses": {"topic": "seats"},
                },
                actor="dana",
                source=source,
            )
        seated_taken = 4

    # Self-checks, so a demo that stopped demonstrating what it claims is a loud seed
    # line rather than a quietly wrong page. Every value the seed created is used
    # here, which is the point: a shape nobody asserts is a shape nobody reads.
    shapes = {
        "the first booking is standard": first["kind"] == "standard",
        "the read-only booking field kept its prefilled value": (
            first["booking"]["bookingFieldsResponses"]["room_account"] == "Northwind Traders"
        ),
        "the room context reached the booking metadata": (
            first["booking"]["metadata"].get("dsr_room_id") == room_id
        ),
        "the instant booking has a video link": bool(instant["video"]),
        "the recurring booking carries its 6 occurrences": len(
            recurring["booking"]["recurrence"]["occurrences"]
        )
        == 6,
        "the reschedule moved one record rather than adding one": bool(moved["rescheduled"])
        and moved["moved_from"] == moved["start"],
        "the lapsed hold is stored expired, noticed by a write and no sweep": (
            expired_view["state"] == "expired" and expired_view["stored_state"] == "expired"
        ),
        "recurrenceCount over 32 was refused": refused,
        "the seated slot took exactly 4 bookings": seated_taken == 4,
        "the live hold protects a slot nobody else has taken": bool(hold["reservationUid"])
        and hold["reservationDuration"] == 45,
        "the ungranted client cannot book": not engine.client_token_state(
            engine.require_client(ungranted["id"])
        )["live"],
    }
    wrong = [name for name, ok in shapes.items() if not ok]

    summary = engine.summary()
    return (
        f"2 clients (1 ungranted), {len(DEMO_EVENT_TYPES)} event types, "
        f"{len(DEMO_CALENDARS)} calendars, 1 routing form, 2 embeds; "
        f"{summary['bookings']} bookings ({summary['instant_bookings']} instant, "
        f"{summary['recurring_bookings']} recurring, 1 rescheduled in place onto the slot it already "
        f"held, which only the bookingUidToReschedule exclusion permits); "
        f"{summary['holds']} holds ({summary['holds_live']} live, {summary['holds_expired']} expired "
        f"with nobody acting); {seated_taken} of 4 seats taken on one seated slot"
        + ("" if not wrong else "; DEMO SHAPE BROKEN: " + ", ".join(wrong))
    )
