"""WF-057: find a time that works for a multi-person panel.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-057.md``, which is
the specification. The researched decisions are the product: the two provider
endpoints, the least-privileged scopes, the ``Prefer: outlook.timezone`` header,
the ``calendarExpansionMax`` (50) and ``groupExpansionMax`` (100) capacity knobs,
the ``returnSuggestionReasons`` toggle, and above all the sentence the whole
ranking rests on - **free=100%, unknown=49%, busy=0%**, averaged and then sorted
**high→low, then chronologically**, each suggestion carrying a
``suggestionReason``.

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the demo
data. The domain lives in :mod:`dsr.panel_time`.

Why the prefix is ``/api/wf-057``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is room-scoped
- ``/rooms/{room_id}/panels``, ``/rooms/{room_id}/searches``,
``/rooms/{room_id}/bookings`` - and the host's loader would report a
``(method, path)`` clash as a failed feature rather than shadowing it.

Calendars are deliberately **not** room-scoped. The research's step 1 places the
find-a-time surface "in a sales room, a CRM record, or a scheduling page", and one
person's calendar is the same calendar in all three.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain method that writes, so it cannot silently
regress. A test asserts that every source recorded in the audit log names a
route the host actually mounted.

An empty shortlist is a ``200`` and a row
-----------------------------------------
The most important decision on this surface. The research's automations line says
``emptySuggestionsReason`` "is documented as the signal to re-call with adjusted
parameters", so a caller who cannot read that signal cannot act on the one thing
the documentation tells them to do. A ``4xx`` here would throw away the shortlist,
the reason, *and* the adjustment that would have fixed it. So
``POST /panels/{panel_id}/find`` returns ``200`` with
``emptySuggestionsReason`` and ``suggested_adjustments``, writes the row, and
``POST /searches/{search_id}/retune`` is the documented second call.

Error mapping
-------------
Four handlers, one per distinct HTTP answer, all on this feature's own types.
``RecordNotFound`` and ``AuditError`` are deliberately not claimed: the core app
already maps them correctly, and two handlers for one type is a collision the host
refuses. The split between ``400`` and ``422`` is deliberate - "this panel is
wrong" and "this slot cannot be booked in the state it is in now" are different
things for a client, and ``apiRequest`` in the frontend carries the status so a
page can tell them apart.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.panel_time import (
    BOOKING_COLLECTION,
    CALENDAR_COLLECTION,
    COLLECTIONS,
    INFERENCES,
    KINDS,
    LOCATION_TYPES,
    PANEL_COLLECTION,
    PROVIDERS,
    RANKERS,
    RETUNE_ADJUSTMENTS,
    SEARCH_COLLECTION,
    SUGGESTION_REASON_ALL_FREE,
    CalendarShapeError,
    ConstraintError,
    LimitExceeded,
    NotFound,
    PanelShapeError,
    PanelTimeError,
    PanelTimeNotConfigured,
    SlotFinder,
    SlotUnavailable,
    describe_inferences,
    describe_reasons,
    describe_vocabulary,
    tzdb_available,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-057-find-a-time-that-works-for-a-multi-per",
    "ticket": "WF-057",
    "name": "Find a time that works for a multi-person panel",
    "description": (
        "Read several calendars' free/busy, score every candidate slot with the researched "
        "per-attendee availability (free=100%, unknown=49%, busy=0%), and return them ranked "
        "high-to-low then chronologically with a confidence percentage and a "
        "suggestionReason. Expands whole distribution lists inside the documented "
        "calendarExpansionMax (50) and groupExpansionMax (100) knobs, reports "
        "emptySuggestionsReason with the documented re-call when nothing fits, and creates "
        "the event on the organizer's calendar - with a fresh conference if asked - when a "
        "slot is picked."
    ),
    "nav": [{"id": "find-a-time", "label": "Find a time"}],
}

router = APIRouter(prefix="/api/wf-057", tags=["wf057"])


def get_finder(store: RecordStore = StoreDep) -> SlotFinder:
    """A :class:`SlotFinder` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a provider factory, and ``app.state`` is where it would
    otherwise have to be built in the shared app's lifespan. Building it here also
    leaves the provider a seam a test can override, so the suite can find and book
    without a calendar tenant.
    """
    return SlotFinder(store)


FinderDep = Depends(get_finder)

#: The ``source`` the preview route passes. A preview writes nothing, so no audit
#: row names it - the constant exists only so the required keyword is satisfied
#: with a value that says what the route is rather than a plausible-sounding
#: placeholder. If a write ever *does* flow through it, this string is what the
#: audit log will say, which is why the test on audit sources fails loudly rather
#: than letting a preview-shaped source slip in beside a real route's.
PREVIEW_SOURCE = f"POST {router.prefix}/rooms/{{room_id}}/panels/{{panel_id}}/preview"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _panel_time_error(request: Request, exc: PanelTimeError) -> JSONResponse:
    """A request this layer will not honour. 400.

    The base of every refusal in :mod:`dsr.panel_time`: a calendar with no email, a
    panel with no time constraint, a ``meetingDuration`` that is not a positive ISO
    8601 duration, a cap above its documented maximum. All of them are the
    caller's to fix, and the message always names the rule it broke.
    """
    return JSONResponse(status_code=400, content={"error": "panel_time_error", "detail": str(exc)})


def _not_configured(request: Request, exc: PanelTimeNotConfigured) -> JSONResponse:
    """Well formed, but this installation cannot answer it yet. 428.

    Distinct from 400 so a client can say "finish the setup" rather than "you got
    the request wrong" - the frontend's ``apiRequest`` carries the status for
    exactly this, and a panel with no calendars registered is a real state a rep
    will meet.
    """
    return JSONResponse(status_code=428, content={"error": "not_configured", "detail": str(exc)})


def _slot_unavailable(request: Request, exc: SlotUnavailable) -> JSONResponse:
    """The request is well formed and the *state* refuses it. 422.

    Not a 400: nothing about the request is wrong. The slot has been taken, the
    organizer is now busy, or the slot is not one the search returned. A client
    that reported this as a bad request would tell a rep to fix their input when
    the thing to do is run the search again.
    """
    return JSONResponse(status_code=422, content={"error": "slot_unavailable", "detail": str(exc)})


def _not_found(request: Request, exc: NotFound) -> JSONResponse:
    """A room, panel, calendar, search or booking id that does not resolve. 404.

    The resource name travels in the body rather than only in the prose, so a
    client can branch on it instead of pattern-matching a message.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": str(exc),
            "resource": exc.resource,
            "id": exc.record_id,
            "room_id": exc.room_id,
        },
    )


#: The four handlers, on four types. The three ``PanelTimeError`` subclasses that
#: are *not* listed here - ``CalendarShapeError``, ``PanelShapeError``,
#: ``ConstraintError`` and ``LimitExceeded`` - all map to ``400`` through
#: ``_panel_time_error``, which FastAPI resolves through their shared base. Four
#: handlers rather than eight, and each one is one distinct HTTP answer.
EXCEPTION_HANDLERS = {
    PanelTimeError: _panel_time_error,
    PanelTimeNotConfigured: _not_configured,
    SlotUnavailable: _slot_unavailable,
    NotFound: _not_found,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The sourced contract: the endpoints, scopes, header, weights, limits,
    capacities, the toggle, ``emptySuggestionsReason``, and the user flow.

    Served as data so a client renders its pickers from the same source the
    validator enforces against. Includes the adjacent surfaces this build
    deliberately does not implement, so an omission reads as a decision rather
    than an oversight, and the research's own six stated gaps, so a reader knows
    which figures are quoted and which shapes are not.
    """
    payload = describe_vocabulary()
    payload["collections"] = list(COLLECTIONS)
    payload["calendar_kinds"] = list(KINDS)
    payload["time_zone"] = {"tzdb_available": tzdb_available()}
    return payload


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about the wire and silent about almost everything
    around it. The parts that are therefore judgement calls - the panel record
    shape, the 30-minute grid, what ``activityDomain: work`` excludes, whether an
    unreadable calendar counts toward the average, the ``emptySuggestionsReason``
    vocabulary, whether a booking re-reads availability - are collected in
    :mod:`dsr.panel_time.inferences` and served here, next to the sourced facts
    they are measured against.

    A read with no side effect, so it needs no store. Also carries the reason
    vocabulary and the re-call adjustments, because a reason and an inference are
    the same kind of statement about the same gap.
    """
    payload = describe_inferences()
    payload["reasons"] = describe_reasons()
    return payload


# --------------------------------------------------------------------------- #
# Calendars: whose availability is read
# --------------------------------------------------------------------------- #


@router.get("/calendars")
def list_calendars(
    kind: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    readable: bool | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Registered calendars: people, rooms, and distribution lists to expand.

    Deliberately not room-scoped - see the module docstring. Filterable by kind,
    provider and readability, each through the dynamic index, so a new kind or a
    new provider needs no change here.
    """
    calendars = finder.list_calendars(kind=kind, provider=provider, readable=readable)
    by_kind: dict[str, int] = {}
    for calendar in calendars:
        by_kind[calendar["kind"]] = by_kind.get(calendar["kind"], 0) + 1
    return {
        "count": len(calendars),
        "by_kind": by_kind,
        "unreadable": len([c for c in calendars if not c["readable"]]),
        "filter": {"kind": kind, "provider": provider, "readable": readable},
        "calendars": calendars,
    }


@router.post("/calendars", status_code=201)
def create_calendar(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Register a calendar: an address, what it is, and what is already on it.

    ``busy`` is the availability a local provider answers from, and
    ``readable: false`` (or an ``unavailable_reason``) is the researched
    *unknown* case - how a rep meets a distribution list nobody has published,
    which scores the researched 49% and must not silently count as free.
    """
    return finder.create_calendar(payload, actor=actor, source=f"POST {router.prefix}/calendars")


@router.get("/calendars/{calendar_id}")
def read_calendar(calendar_id: str, finder: SlotFinder = FinderDep) -> dict[str, Any]:
    return finder.get_calendar(calendar_id)


@router.patch("/calendars/{calendar_id}")
def update_calendar(
    calendar_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Patch a calendar: add busy blocks, publish it, or take it out of rotation."""
    return finder.update_calendar(
        calendar_id, payload, actor=actor, source=f"PATCH {router.prefix}/calendars/{calendar_id}"
    )


@router.delete("/calendars/{calendar_id}", status_code=204)
def delete_calendar(
    calendar_id: str,
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> Response:
    """Soft-delete a calendar. Its searches and bookings stay, and keep its id.

    A soft delete rather than a hard one so a booking's ``organizer_id`` still
    resolves to something and a reader can see who the organizer was at the time -
    which is the only version of it that matters.
    """
    finder.delete_calendar(
        calendar_id, actor=actor, source=f"DELETE {router.prefix}/calendars/{calendar_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Panels: the saved find-a-time request
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/panels")
def list_panels(
    room_id: str,
    provider: str | None = Query(default=None),
    calendar_provider: str | None = Query(default=None),
    room_only: bool | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """The panels declared for this room, newest first.

    Filterable by provider and by ``room_only`` - the last one is the useful case
    for a scheduling page that shares one panel across rooms - through the dynamic
    index, so a new key is filterable the day a deployment writes it.
    """
    panels = finder.list_panels(room_id, provider=provider, room_only=room_only)
    return {
        "room_id": room_id,
        "count": len(panels),
        "filter": {
            "provider": provider,
            "calendar_provider": calendar_provider,
            "room_only": room_only,
        },
        "panels": panels,
    }


@router.post("/rooms/{room_id}/panels", status_code=201)
def create_panel(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Declare a panel: the participants, the date range, and the researched knobs.

    This is the researched flow's step 1 and step 2 made persistent - "picks a set
    of participants + a date range" and "the attendees' email addresses and
    location constraints (room / 'suggest a location')" - so the same set can be
    asked for again next week. Everything beyond those two steps is a researched
    parameter: ``meetingDuration``, ``minAttendeePercentage``,
    ``activityDomain``, the time slots, and the ``returnSuggestionReasons`` toggle.
    """
    return finder.create_panel(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/panels"
    )


@router.get("/rooms/{room_id}/panels/{panel_id}")
def read_panel(room_id: str, panel_id: str, finder: SlotFinder = FinderDep) -> dict[str, Any]:
    return finder.get_panel(room_id, panel_id)


@router.patch("/rooms/{room_id}/panels/{panel_id}")
def update_panel(
    room_id: str,
    panel_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Patch a panel: widen the range, raise the bar, add a house rule, change the
    ranker.

    Every key is ordinary JSON and no key is required, so a team adding a field to
    a panel does it by shipping a payload rather than by a migration.
    """
    return finder.update_panel(
        room_id,
        panel_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{room_id}/panels/{panel_id}",
    )


@router.delete("/rooms/{room_id}/panels/{panel_id}", status_code=204)
def delete_panel(
    room_id: str,
    panel_id: str,
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> Response:
    """Soft-delete a panel. Its searches and bookings stay, and keep its id."""
    finder.delete_panel(
        room_id, panel_id, actor=actor, source=f"DELETE {router.prefix}/rooms/{room_id}/panels/{panel_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The researched call
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/panels/{panel_id}/preview")
def preview_panel(
    room_id: str,
    panel_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """The researched **find a time** surface, without writing a row.

    Writes nothing and accepts ``meeting_duration``, ``slot_interval``,
    ``min_attendee_percentage``, ``return_suggestion_reasons``, ``ranker`` and a
    whole ``time_constraint``, so a rep can try the other settings and see the
    shortlist *and the exact request* change before committing to one. Every field
    here is computed by the same code the real call runs, so the preview cannot
    drift from it.

    ``POST .../find`` is the same computation plus one row.
    """
    return finder.find(
        room_id,
        panel_id,
        # A preview writes nothing, so there is no write for an audit row to name -
        # and a synthetic source would put a row in the log that no request served.
        source=PREVIEW_SOURCE,
        dry_run=True,
        overrides=_overrides(payload),
    )


@router.post("/rooms/{room_id}/panels/{panel_id}/find")
def find_slots(
    room_id: str,
    panel_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """The researched find-a-time call, and the researched user flow, step by step.

    Ranked candidate slots come back, each with a confidence percentage and a
    ``suggestionReason`` - step 4 of the flow. **An empty result is a ``200`` with
    an ``emptySuggestionsReason`` and the documented re-call adjustments, not a
    ``4xx``**, because the research says that property is the signal to re-call
    with adjusted parameters, and a client that cannot read it cannot act on the
    one thing the documentation tells it to do.

    Writes one ``panel_search`` row: a rep running a find-a-time *is* a find-a-time
    call, and the room's log should show it - including the ones that came back
    with nothing, which are the rows the next call is built from.
    """
    result = finder.find(
        room_id,
        panel_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/panels/{panel_id}/find",
        overrides=_overrides(payload),
    )
    result["user_flow"] = describe_vocabulary()["user_flow"]
    result["ok"] = True
    return result


@router.get("/rooms/{room_id}/searches")
def list_searches(
    room_id: str,
    panel_id: str | None = Query(default=None),
    empty: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Every find-a-time call made for this room, newest first.

    An empty result is a row, not a gap: the failure is the thing a rep has to
    read, and the researched re-call means the row is the input to the next call.
    ``empty=true`` filters on the presence of ``emptySuggestionsReason`` - the
    researched property - so a room can be listed showing only the searches that
    came back with nothing.
    """
    searches = finder.list_searches(room_id, panel_id=panel_id, empty=empty, limit=limit)
    summary = {
        "searches": len(searches),
        "empty": len([s for s in searches if s.get("empty_suggestions_reason")]),
        "suggested": sum(s.get("suggestion_count") or 0 for s in searches),
        "retunes": len([s for s in searches if s.get("parent_search_id")]),
    }
    return {
        "room_id": room_id,
        "count": len(searches),
        "summary": summary,
        "filter": {"panel_id": panel_id, "empty": empty, "limit": limit},
        "searches": searches,
    }


@router.get("/rooms/{room_id}/searches/{search_id}")
def read_search(room_id: str, search_id: str, finder: SlotFinder = FinderDep) -> dict[str, Any]:
    """One find-a-time call in full: the request that would go on the wire, the
    ranked shortlist, the candidates that were refused and why, the warnings, and
    the ``emptySuggestionsReason`` with its adjustments."""
    return finder.get_search(room_id, search_id)


# --------------------------------------------------------------------------- #
# The researched re-call
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/searches/{search_id}/adjustments")
def read_adjustments(room_id: str, search_id: str, finder: SlotFinder = FinderDep) -> dict[str, Any]:
    """What to change, in the order worth trying, for this search's empty reason.

    The research's automations line, served as data so the UI can render a picker
    with each option's *reason* rather than a list of identifiers. Reading this
    costs nothing and writes nothing; ``POST .../retune`` is the call it precedes.
    """
    search = finder.get_search(room_id, search_id)
    return {
        "search_id": search_id,
        "empty_suggestions_reason": search.get("empty_suggestions_reason"),
        "suggested": search.get("suggested_adjustments") or [],
        "all_adjustments": search.get("retune_adjustments") or [],
        "retunable_parameters": search.get("retunable_parameters") or [],
        "applied": search.get("adjustment"),
        "parent_search_id": search.get("parent_search_id"),
    }


@router.post("/rooms/{room_id}/searches/{search_id}/retune")
def retune_search(
    room_id: str,
    search_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """The researched automation, as a route.

    "Based on this value, you can better adjust the parameters and call
    findMeetingTimes again." So this takes the search that came back empty, applies
    **one** named adjustment to the parameters that produced it, runs the call
    again, and writes a *new* row linked to the first. The original is untouched:
    the parameters that produced the empty result are what a reviewer needs, and an
    update would overwrite them.

    Omit ``adjustment`` to take the first one the reason suggests, which is
    ``widen_window`` for every reason except the two where widening cannot help.
    """
    return finder.retune(
        room_id,
        search_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/searches/{search_id}/retune",
    )


# --------------------------------------------------------------------------- #
# The researched commit
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/searches/{search_id}/book")
def book_slot(
    room_id: str,
    search_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Create the event on the organizer's calendar for a slot from this search.

    Step 5 of the researched flow: "On pick, the app creates the event on the
    organizer's calendar (optionally creating a fresh conference)."

    Three refusals, and the status distinguishes them from a bad request: the
    slot is not one this search returned; the organizer's calendar is now busy, so
    the event would land on top of another one; or the slot no longer clears the
    panel's ``minAttendeePercentage``. Availability is re-read for all three,
    because the research's own note says the suggestions are "fine-tuned from time
    to time" and its gap list says the free/busy read is a *pull* with no
    invalidation.

    ``create_conference`` defaults to true, which is what the research's
    "(optionally creating a fresh conference)" makes the common case.
    """
    return finder.book(
        room_id,
        search_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/searches/{search_id}/book",
    )


@router.get("/rooms/{room_id}/bookings")
def list_bookings(
    room_id: str,
    panel_id: str | None = Query(default=None),
    finder: SlotFinder = FinderDep,
) -> dict[str, Any]:
    """Every panel booked for this room, newest first.

    Each row carries the researched commit request and the availability the
    re-read found, so a reader can see what was checked at booking time and not
    only what the search said.
    """
    bookings = finder.list_bookings(room_id, panel_id=panel_id)
    with_conference = len([b for b in bookings if b.get("conference")])
    return {
        "room_id": room_id,
        "count": len(bookings),
        "with_conference": with_conference,
        "filter": {"panel_id": panel_id},
        "bookings": bookings,
    }


@router.get("/rooms/{room_id}/bookings/{booking_id}")
def read_booking(room_id: str, booking_id: str, finder: SlotFinder = FinderDep) -> dict[str, Any]:
    """One booking in full: the event, the conference, the commit request, and the
    availability the re-read found."""
    return finder.get_booking(room_id, booking_id)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _overrides(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The per-call knobs a preview or a find may move, and nothing else.

    Read as a whitelist rather than a passthrough, so a client cannot smuggle a
    ``provider`` or an ``access_token`` in through the override channel. Everything
    here is a *searched parameter*, which is exactly what the research's re-call
    advice says to adjust.
    """
    allowed = (
        "meeting_duration",
        "slot_interval",
        "min_attendee_percentage",
        "return_suggestion_reasons",
        "ranker",
        "unknown_penalty",
        "house_rules",
        "time_constraint",
        "location_constraint",
        "time_zone",
        "max_suggestions",
        "group_expansion_max",
        "calendar_expansion_max",
        "widen_days",
    )
    return {key: payload[key] for key in allowed if key in payload}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The calendars the demo registers, chosen so the *states* the research says
#: matter are all reachable. A demo whose attendees are all free and readable
#: teaches a reviewer nothing about a 49%.
#:
#: * **Six people** across three rooms and two accounts, so a panel has a real
#:   mix of busy and free calendars on the same morning.
#: * **One room resource** - the researched "book into a room resource" as a third
#:   calendar kind, whose busy blocks the search honours.
#: * **One distribution list** whose members are all registered, so group
#:   expansion has something to expand.
#: * **One distribution list** with a member nobody has registered, so
#:   ``groups_unexpanded`` is populated rather than always empty.
#: * **One calendar that has not published**, marked unreadable - the researched
#:   *unknown* case, and the only way to see a 49% in a demo.
DEMO_CALENDARS: tuple[Mapping[str, Any], ...] = (
    {
        "email": "dana@northwind.example",
        "name": "Dana - Account Executive",
        "kind": "person",
        "provider": "google",
        "time_zone": "Europe/London",
        "busy": [
            {"start": "2026-10-05T09:00:00Z", "end": "2026-10-05T10:00:00Z"},
            {"start": "2026-10-05T14:00:00Z", "end": "2026-10-05T15:00:00Z"},
        ],
    },
    {
        "email": "sam@northwind.example",
        "name": "Sam - Solutions Architect",
        "kind": "person",
        "provider": "google",
        "time_zone": "Europe/London",
        "busy": [
            {"start": "2026-10-05T10:00:00Z", "end": "2026-10-05T12:00:00Z"},
            {"start": "2026-10-06T09:00:00Z", "end": "2026-10-06T10:30:00Z"},
        ],
    },
    {
        "email": "priya@northwind.example",
        "name": "Priya - Security",
        "kind": "person",
        "provider": "google",
        "time_zone": "Europe/London",
        "busy": [{"start": "2026-10-05T09:00:00Z", "end": "2026-10-05T17:00:00Z"}],
    },
    {
        "email": "a.buyer@northwind.example",
        "name": "Buyer - Procurement",
        "kind": "person",
        "provider": "google",
        "time_zone": "Europe/London",
        "busy": [{"start": "2026-10-06T11:00:00Z", "end": "2026-10-06T12:00:00Z"}],
    },
    {
        "email": "b.buyer@northwind.example",
        "name": "Buyer - Engineering lead",
        "kind": "person",
        "provider": "graph",
        "time_zone": "Europe/London",
        "busy": [{"start": "2026-10-05T08:00:00Z", "end": "2026-10-05T08:30:00Z"}],
    },
    {
        "email": "c.buyer@northwind.example",
        "name": "Buyer - Has not published a calendar",
        "kind": "person",
        "provider": "graph",
        "time_zone": "Europe/London",
        "readable": False,
        "unavailable_reason": (
            "this contact has not shared a calendar, so their availability is unknown "
            "and scores the researched 49%"
        ),
    },
    {
        "email": "ops@contoso.example",
        "name": "Contoso - operations",
        "kind": "person",
        "provider": "google",
        "time_zone": "Europe/London",
        "busy": [{"start": "2026-10-05T09:00:00Z", "end": "2026-10-05T10:00:00Z"}],
    },
    {
        "email": "procurement@contoso.example",
        "name": "Contoso - Procurement",
        "kind": "person",
        "provider": "graph",
        "time_zone": "Europe/London",
        "busy": [{"start": "2026-10-05T09:30:00Z", "end": "2026-10-05T10:30:00Z"}],
    },
    {
        "email": "engagement-room-london@northwind.example",
        "name": "Engagement room - London",
        "kind": "room",
        "provider": "graph",
        "time_zone": "Europe/London",
        "busy": [{"start": "2026-10-05T09:00:00Z", "end": "2026-10-05T10:00:00Z"}],
    },
    {
        "email": "northwind-sales@northwind.example",
        "name": "Northwind - all sales (distribution list)",
        "kind": "group",
        "provider": "google",
        "members": [
            "dana@northwind.example",
            "sam@northwind.example",
            "priya@northwind.example",
        ],
    },
    {
        "email": "contoso-approvers@contoso.example",
        "name": "Contoso - approvers (one address unregistered)",
        "kind": "group",
        "provider": "graph",
        "members": [
            "ops@contoso.example",
            "procurement@contoso.example",
            "cfo@contoso.example",
        ],
    },
)


def _slot(day: int, hour: int, minute: int = 0) -> str:
    """A UTC instant on the demo's Monday, for the demo's windows."""
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00Z"


#: The panels the demo declares, each chosen to reach a different researched
#: state. Between them they produce a clean ranking, a threshold refusal, an
#: ``emptySuggestionsReason`` with the documented re-call, house rules that remove
#: every candidate, a group expansion, an unreadable calendar at 49%, and a
#: committed booking with a fresh conference.
DEMO_PANELS: tuple[Mapping[str, Any], ...] = (
    {
        "room": 0,
        "name": "Northwind - Q4 panel, the happy path",
        "note": (
            "Four people, one of whom has not published a calendar. The best slot is not "
            "the earliest one, which is the researched sort doing its job: high→low first, "
            "chronological only to break the tie."
        ),
        "spec": {
            "provider": "local",
            "calendar_provider": "google",
            "calendars": [
                "dana@northwind.example",
                "sam@northwind.example",
                "a.buyer@northwind.example",
                "c.buyer@northwind.example",
            ],
            "organizer": "dana@northwind.example",
            "time_constraint": {
                "activityDomain": "work",
                "timeSlots": [
                    {"start": _slot(5, 9), "end": _slot(5, 17)},
                    {"start": _slot(6, 9), "end": _slot(6, 17)},
                ],
            },
            "meeting_duration": "PT1H",
            "slot_interval": "PT30M",
            "min_attendee_percentage": 50,
            "location_constraint": {"type": "suggest", "display_name": "Teams"},
        },
    },
    {
        "room": 0,
        "name": "Northwind - security panel with house rules",
        "note": (
            "Priya is booked solid on the Monday, so the whole first day scores zero. The "
            "house rules are the researched examples - no early starts, and no back-to-back "
            "against the organizer's diary - and each refusal names the rule that refused it."
        ),
        "spec": {
            "provider": "local",
            "calendar_provider": "google",
            "calendars": [
                "dana@northwind.example",
                "sam@northwind.example",
                "priya@northwind.example",
            ],
            "organizer": "dana@northwind.example",
            "time_constraint": {
                "activityDomain": "work",
                "timeSlots": [{"start": _slot(5, 9), "end": _slot(5, 17)}],
            },
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
            "min_attendee_percentage": 100,
            "house_rules": {
                "earliest_start": "10:00",
                "no_back_to_back": True,
            },
        },
    },
    {
        "room": 1,
        "name": "Contoso - approval panel from a distribution list",
        "note": (
            "The panel invites one address - a distribution list - and group expansion "
            "replaces it with its two registered members. The third member is not a "
            "registered calendar, so the response says which one is missing rather than "
            "quietly inviting two of three."
        ),
        "spec": {
            "provider": "local",
            "calendar_provider": "graph",
            "calendars": [
                "contoso-approvers@contoso.example",
                "engagement-room-london@northwind.example",
            ],
            "organizer": "ops@contoso.example",
            "time_constraint": {
                "activityDomain": "work",
                "timeSlots": [{"start": _slot(5, 9), "end": _slot(5, 13)}],
            },
            "meeting_duration": "PT45M",
            "slot_interval": "PT45M",
            "min_attendee_percentage": 0,
            "location_constraint": {"type": "room", "room_id": "engagement-room-london@northwind.example"},
        },
    },
    {
        "room": 1,
        "name": "Contoso - a panel with nowhere to go",
        "note": (
            "The whole window is one short morning and the attendees are booked solid, so "
            "nothing clears the threshold. This is the researched ``emptySuggestionsReason`` "
            "path, and the demo runs the documented re-call on it - the search that came back "
            "empty stays readable, and the retune that fixed it is a second row beside it."
        ),
        "spec": {
            "provider": "local",
            "calendar_provider": "google",
            "calendars": [
                "priya@northwind.example",
                "ops@contoso.example",
            ],
            "organizer": "ops@contoso.example",
            "time_constraint": {
                "activityDomain": "work",
                "timeSlots": [{"start": _slot(5, 11), "end": _slot(5, 12)}],
            },
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
            "min_attendee_percentage": 80,
        },
    },
    {
        "room": 2,
        "name": "Adventure Works - the whole sales distribution list",
        "note": (
            "One group address that expands to three people, one of whom is busy all day. "
            "This is the researched 'group expansion for whole distribution lists' at its "
            "largest, and the researched 50-calendar cap is on the request that would go on "
            "the wire."
        ),
        "spec": {
            "provider": "local",
            "calendar_provider": "google",
            "calendars": ["northwind-sales@northwind.example"],
            "organizer": "sam@northwind.example",
            "time_constraint": {
                "activityDomain": "unrestricted",
                "timeSlots": [{"start": _slot(5, 9), "end": _slot(5, 18)}],
            },
            "meeting_duration": "PT30M",
            "slot_interval": "PT30M",
            "min_attendee_percentage": 0,
        },
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Eleven calendars, five panels, six searches, one retune, and two bookings.

    Every search in the demo is produced by running the real
    :class:`~dsr.panel_time.engine.SlotFinder` over the real local directory, so
    the demo cannot show a shape the workflow would not produce, and seeding never
    opens a socket.

    It is deliberately mixed, because a demo showing only green teaches a
    reviewer nothing. Between them the five panels produce:

    * a **ranked shortlist** whose best slot is not the earliest one, and where one
      attendee has not published a calendar and scores the researched 49%;
    * a **threshold refusal** - ``minAttendeePercentage: 100`` against an attendee
      who is booked solid - with every candidate that missed the bar still
      reported and its percentage shown;
    * a **group expansion** in both directions: a list that expands to its
      registered members, and one whose missing member is named;
    * an **``emptySuggestionsReason``** search, and beside it the **documented
      re-call** that fixed it - two rows, so the parameters that produced the empty
      result are still readable;
    * **house-rule refusals** naming the rule that refused each candidate,
      including the researched "no back-to-back" against the organizer's own
      diary;
    * a **booking with a fresh conference** and one **without**, so step 5's
      "optionally creating a fresh conference" is visible both ways;
    * and a booking whose slot was **re-validated at commit time**, with the
      availability the re-read found on the row.

    Returns a short description of what was added, which the seeder prints.
    """
    store = RecordStore(db)
    finder = SlotFinder(store)
    actor = "dana"
    source = "seed"

    calendars = {
        str(spec["email"]): finder.create_calendar(spec, actor=actor, source=source)
        for spec in DEMO_CALENDARS
    }

    rooms: list[str] = [
        str(room_id)
        for room_id, _account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        # No demo rooms to attach to. The calendars are still worth having, and
        # the seeder prints what was skipped. Filtered by existence rather than
        # trusted, because a seeder that aborts a whole feature over one stale
        # room id leaves a page nobody can review.
        return f"{len(calendars)} calendars, 0 panels (no rooms to scope them to)"

    panels: dict[str, dict[str, Any]] = {}
    for spec in DEMO_PANELS:
        room_id = rooms[int(spec["room"]) % len(rooms)]
        panel = finder.create_panel(
            room_id, spec["spec"] | {"name": spec["name"]}, actor=actor, source=source
        )
        panels[str(spec["name"])] = {**panel, "room_id": room_id}

    def search_for(name: str) -> dict[str, Any] | None:
        panel = panels.get(name)
        if panel is None:
            return None
        return finder.find(
            panel["room_id"], panel["id"], actor=actor, source=source
        )

    found = 0
    retuned = 0
    empty = 0
    ranked = 0
    for name in panels:
        result = search_for(name)
        if result is None:
            continue
        found += 1
        if result.get("suggestions"):
            ranked += 1
        if result.get("empty_suggestions_reason"):
            empty += 1

    # The researched re-call, run on the search that came back with nothing. This
    # is the whole point of the emptySuggestionsReason automation: the same panel,
    # the same calendars, one parameter moved, and the panel that could not be
    # scheduled now can. A retune nobody can see working is an automation nobody
    # trusts.
    stuck = panels.get("Contoso - a panel with nowhere to go")
    if stuck is not None:
        result = finder.find(stuck["room_id"], stuck["id"], actor=actor, source=source)
        if result.get("empty_suggestions_reason"):
            fixed = finder.retune(
                stuck["room_id"],
                result["id"],
                {"adjustment": "widen_window", "days": 3},
                actor=actor,
                source=source,
            )
            if fixed.get("suggestions"):
                retuned += 1

    # The researched commit, both ways round the "optionally creating a fresh
    # conference" - one booking with a fresh conference and one without.
    booked = 0
    for name, with_conference in (
        ("Northwind - Q4 panel, the happy path", True),
        ("Contoso - approval panel from a distribution list", False),
    ):
        panel = panels.get(name)
        if panel is None:
            continue
        result = finder.find(panel["room_id"], panel["id"], actor=actor, source=source)
        suggestion = (result.get("suggestions") or [None])[0]
        if suggestion is None:
            continue
        finder.book(
            panel["room_id"],
            result["id"],
            {
                "start": suggestion["start"],
                "create_conference": with_conference,
                "summary": name,
            },
            actor=actor,
            source=source,
        )
        booked += 1

    return (
        f"{len(calendars)} calendars (1 room, 2 distribution lists, 1 unpublished), "
        f"{len(panels)} panels, {found} searches of which {ranked} ranked a shortlist and "
        f"{empty} returned an emptySuggestionsReason, {retuned} documented re-call(s) that "
        f"fixed one, {booked} bookings (1 with a fresh conference, 1 without), every "
        "availability re-validated at commit time"
    )
