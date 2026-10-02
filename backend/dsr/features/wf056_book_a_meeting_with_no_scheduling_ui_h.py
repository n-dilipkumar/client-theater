"""WF-056: book a meeting with no scheduling UI (headless / AI agent).

Built from ``docs/research/digital-sales-room-workflows/wf/WF-056.md``, which is
the specification, and from section 6 of
``docs/research/raw/scheduling-meetings.md`` that it points at.

What the research specifies
---------------------------

A *headless* booking, which means a lead never touches a Chili Piper UI. The
caller - "a backend process, a custom frontend, an AI assistant" - makes exactly
two calls:

1. **Discover or route.** Returns "a session with a list of available time slots
   and an identifier for the session (``routeId``)".
2. **Book.** "A second call passes the ``routeId`` and a chosen ``startTime`` to
   commit the meeting", and the response carries a ``meetingId``.

Between them sit four rules the research states in as many words, and every one
of them is the kind of rule an implementation gets wrong by omission:

* "**Sessions are single-use.** On a schedule failure, do not retry the schedule
  call with the same ``routeId`` - start again from the discover or route step."
  The *failure* half is the trap: an implementation that only spends a session on
  success lets a caller retry forever against slots that are now stale.
* "**Slot times are UTC.** The ``startTime`` in responses is ISO-8601 UTC; pass
  it back verbatim on the book call." Both halves are enforced, and a value with
  no UTC designator is refused rather than guessed at.
* The two-step session "has a server-side TTL (``timeoutInMS`` for Concierge,
  per-router-path; server-side TTL for links/handoff)".
* "Calendar invites are sent immediately", and bookings "immediately emit the
  ``For New Meeting`` webhook".

And one credential rule: "Admin generates a scoped API token in ``Command Center
> Credentials`` (``Generate Token``), choosing the ``Schedule`` permission for
  the relevant section (Concierge / Scheduling-links / Handoff) plus ``Read``
where listing assets is needed. Token is shown once. Admins only."

This module
-----------

Only the three things the contract allows a feature to add: the HTTP surface,
the mapping from domain errors to responses, and the demo data. The behaviour is
in :mod:`dsr.headless_booking`, where it can be tested without a request.

Why the routes are shaped this way
----------------------------------

**The two researched calls get the two researched routes.** Call #1 and call #2
are the product, so they are ``POST .../sessions`` and ``POST
.../sessions/{route_id}/book`` under the room, and a caller following the vendor
documentation finds them where it expects. The routes below them - assets,
credentials, calendar blocks - are the *configuration* the two calls need, and
they are the unresearched half this build had to design.

**``source=`` comes from the route, always.** Every write route builds its source
string from ``router.prefix`` and hands it to a domain method that *requires* it,
so a domain function cannot hardcode a path. This exact bug has shipped in this
codebase - a feature's audit log kept recording a route the app had stopped
serving - and a test asserts that every source recorded names a route the host
actually mounted.

**Error mapping is three handlers for three answers.** A 400 for something the
caller wrote wrong, a 403 for something the *credential* may not do, and a 404
for an id that does not resolve. ``RecordNotFound`` and ``AuditError`` are
deliberately not claimed: the core app maps both, and the host refuses a second
handler for a type it already handles.

**A booking is one transaction.** Meeting, invites, webhook event, CRM writeback
record and the session's move to ``booked`` commit together. The research says a
commit produces all of them, and a crash between them would leave a meeting with
no invite - a half-state the data flow does not describe.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.headless_booking import (
    assets as assets_mod,
    inferences as headless_inferences,
    vocabulary as vocab,
)
from dsr.headless_booking.engine import (
    ASSET_COLLECTION,
    CALENDAR_COLLECTION,
    CALL_COLLECTION,
    CREDENTIAL_COLLECTION,
    INVITE_COLLECTION,
    MEETING_COLLECTION,
    SESSION_COLLECTION,
    WEBHOOK_COLLECTION,
    HeadlessBooking,
)
from dsr.headless_booking.errors import HeadlessBookingError, NotFound, PermissionDenied
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-056-book-a-meeting-with-no-scheduling-ui-h",
    "ticket": "WF-056",
    "name": "Book a meeting with no scheduling UI (headless / AI agent)",
    "description": (
        "Book a meeting from your own code, with no scheduling UI in front of the buyer. A "
        "scoped, admin-only, shown-once token; call #1 discovers or routes and returns a "
        "single-use routeId with a list of UTC start times; call #2 passes routeId and a "
        "startTime back verbatim and returns a meetingId, with the calendar invites and the "
        "'For New Meeting' webhook emitted in the same transaction. Sessions are single-use, so "
        "a schedule failure is answered with a fresh discover rather than a retry - over Concierge, "
        "scheduling links, and handoff routers."
    ),
    "nav": [{"id": "headless-booking", "label": "Headless booking"}],
}

router = APIRouter(prefix="/api/wf-056", tags=["wf-056"])


def get_headless(store: RecordStore = StoreDep) -> HeadlessBooking:
    """A :class:`HeadlessBooking` over the process-wide audited store.

    Per request, for the same reason the other features build their engine per
    request: the engine holds only the store handle and a clock, and building it
    here leaves the clock overridable in a test - which the TTL rules need, since
    a session-expiry test that depended on real time would sleep or flake.
    """
    return HeadlessBooking(store)


HeadlessDep = Depends(get_headless)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _booking_error(request: Request, exc: HeadlessBookingError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    Also the handler for the two subclasses' *base* type, so an uncaught
    refusal in any of the eight domain modules answers with the researched
    reason rather than a 500. The researched vocabulary's failure names go in
    the body so a client can branch on ``reason`` instead of pattern-matching
    prose - which is what makes "start again from the discover or route step"
    something a caller can act on programmatically.
    """
    return JSONResponse(
        status_code=400,
        content={
            "error": "headless_booking_error",
            "detail": str(exc),
            "reason": getattr(exc, "reason", ""),
        },
    )


def _permission_denied(request: Request, exc: PermissionDenied) -> JSONResponse:
    """Well formed, but this identity may not make it. 403.

    Separate from 400 because the remedy is different: a 400 says fix your
    request, a 403 says get a token that can do this. The research makes the
    distinction by scoping ``Schedule`` per section - the same payload against a
    different section's token is a different answer, not a malformed one.
    """
    return JSONResponse(
        status_code=403,
        content={
            "error": "permission_denied",
            "detail": str(exc),
            "required_permission": exc.required,
            "section": exc.section,
        },
    )


def _not_found(request: Request, exc: NotFound) -> JSONResponse:
    """An id that does not resolve. 404.

    The resource name travels in the body rather than only in the prose, so a
    client can branch on it. Note that a *spent* session is not a 404: a
    session that was used is still readable, and saying "start again" is the
    researched answer rather than "no such thing".
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


EXCEPTION_HANDLERS = {
    HeadlessBookingError: _booking_error,
    PermissionDenied: _permission_denied,
    NotFound: _not_found,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the three sections, the quoted endpoint paths, the
    MCP tools, the discovery tools, the permissions, the roles, the four session
    rules, and both failure vocabularies.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so the terms a caller *sends* and the terms
    it *branches on* cannot drift apart. The ``ttl`` block is explicitly flagged
    ``sourced: false`` - the research establishes that a TTL exists and which
    surface sets it, and publishes no duration.
    """
    payload = vocab.published_vocabulary()
    payload["collections"] = [
        ASSET_COLLECTION,
        CREDENTIAL_COLLECTION,
        CALENDAR_COLLECTION,
        SESSION_COLLECTION,
        MEETING_COLLECTION,
        INVITE_COLLECTION,
        WEBHOOK_COLLECTION,
        CALL_COLLECTION,
    ]
    payload["assets"] = assets_mod.published_assets()
    return payload


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    A read with no side effect, so it needs no store. The registry sits beside
    the sourced half - the four session rules, the two calls, the immediate
    commit - so a reader can see where the line falls rather than being told
    there is one.
    """
    return headless_inferences.describe()


@router.get("/rules", summary="The four researched session rules on their own")
def rules() -> dict[str, Any]:
    """The sourced half alone, for a caller that wants the rules and not the
    decisions this build made about them.

    The distinction is the point of the endpoint. ``/inferences`` is where a
    reviewer disagrees with this build; this is where they check what the
    research actually said, so a disagreement is aimed at the right thing.
    """
    return {
        "rules": [dict(entry) for entry in vocab.SESSION_RULES],
        "calls": [dict(entry) for entry in vocab.CALLS],
        "on_book": vocab.ON_BOOK,
        "webhook": {"name": vocab.WEBHOOK_NAME, "event": vocab.WEBHOOK_EVENT},
        "quotes": {
            "single_use": headless_inferences.SINGLE_USE_QUOTE,
            "utc_slots": headless_inferences.UTC_QUOTE,
            "token": headless_inferences.TOKEN_QUOTE,
        },
    }


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Sessions by state, meetings by section, and calls by outcome.

    ``invites_sent`` and ``webhooks_delivered`` are zero and are kept separate
    from the ``_recorded`` counters, because this product records the commit's
    documented consequences and transmits none of them. Collapsing the two into
    one number would invite a reader to believe something left the process.
    """
    return headless.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Credentials: user-flow step 1
# --------------------------------------------------------------------------- #


@router.get("/credentials", summary="List scoped tokens")
def list_credentials(
    room_id: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """The generated tokens, without their secrets.

    A read never returns the token: only a prefix and the last four characters,
    because the token *is* the credential and the underlying record is readable
    by anyone who can call the records API.
    """
    rows = headless.list_credentials(room_id=room_id)
    return {"count": len(rows), "credentials": rows}


@router.post("/credentials", status_code=201, summary="Generate a scoped token")
def create_credential(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """``Command Center > Credentials`` → ``Generate Token``.

    "Only users with the **Admin** role can generate API tokens in Command
    Center. Workspace Managers do not have access to the credentials page." So
    ``role`` is required, and a ``workspace_manager`` is refused with that
    sentence rather than with a generic "you are not an admin" - the first
    tells them the page is unreachable for their role, the second does not.

    **This is the only response that ever contains the token.** What is stored is
    a SHA-256 digest, so nothing - this feature, the core records API, or anyone
    holding the database file - can show it again.
    """
    return headless.create_credential(
        payload, actor=actor, source=f"POST {router.prefix}/credentials"
    )


@router.get("/credentials/{credential_id}", summary="Read one token's metadata")
def read_credential(credential_id: str, headless: HeadlessBooking = HeadlessDep) -> dict[str, Any]:
    """The scope and the masked hint. Never the token."""
    return headless.get_credential(credential_id)


@router.delete("/credentials/{credential_id}", status_code=204, summary="Revoke a token")
def revoke_credential(
    credential_id: str,
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> Response:
    """Revoke by soft delete. The token stops verifying immediately.

    Soft rather than hard so the audit trail keeps the row and the fact that it
    was revoked: a credential's *history* outlives the credential, which is the
    point of an audit log.
    """
    headless.revoke_credential(
        credential_id, actor=actor, source=f"DELETE {router.prefix}/credentials/{credential_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Assets: the bookable targets
# --------------------------------------------------------------------------- #


@router.get("/assets", summary="List bookable assets")
def list_assets(
    room_id: str | None = Query(default=None),
    section: str | None = Query(default=None),
    link_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """The routers, scheduling links and handoff routers this deployment books.

    Filterable by section and by link type - both are JSON paths in each row's
    own payload, so a new link type needs no change here.
    """
    rows = headless.list_assets(
        room_id=room_id, section=section, link_type=link_type, enabled=enabled
    )
    return {
        "count": len(rows),
        "filter": {
            "room_id": room_id,
            "section": section,
            "link_type": link_type,
            "enabled": enabled,
        },
        "assets": rows,
    }


@router.post("/assets", status_code=201, summary="Declare a bookable asset")
def create_asset(
    room_id: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Declare a router, a scheduling link, or a handoff router.

    The three surfaces are addressed three different ways in the researched
    endpoints, and this route asks for what each needs: a Concierge asset wants
    a ``router_slug`` because its path carries ``{routerSlug}``; a handoff asset
    wants a ``workspace_id`` and a ``booker_id`` because its path carries
    ``{workspaceId}`` and ``{userId}``; a scheduling link wants a ``link_id`` and
    a ``link_type`` because its path carries neither.
    """
    return headless.create_asset(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/assets"
    )


@router.get("/assets/{asset_id}", summary="Read one asset")
def read_asset(asset_id: str, headless: HeadlessBooking = HeadlessDep) -> dict[str, Any]:
    """One asset, or 404 if it does not exist."""
    return headless.get_asset(asset_id)


@router.patch("/assets/{asset_id}", summary="Patch an asset")
def update_asset(
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Change the grid, the working hours, the provider, or the enabled flag.

    Re-validated against the merged result, so a patch cannot leave an asset
    that violates a configuration rule - a host with no calendar address, working
    hours that run backwards, a grid that is not a positive number of minutes.
    A patch that *is* well-formed can still leave a grid finer than the meeting,
    which is allowed on purpose: the overlap it permits is caught at the book
    call as the researched ``slot_taken`` refusal.
    """
    return headless.update_asset(
        asset_id, payload, actor=actor, source=f"PATCH {router.prefix}/assets/{asset_id}"
    )


@router.delete("/assets/{asset_id}", status_code=204, summary="Delete an asset")
def delete_asset(
    asset_id: str,
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> Response:
    """Soft-delete an asset. Its sessions and meetings stay, and stay readable."""
    headless.delete_asset(asset_id, actor=actor, source=f"DELETE {router.prefix}/assets/{asset_id}")
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Calendar: what the availability engine reads
# --------------------------------------------------------------------------- #


@router.get("/calendar", summary="List calendar blocks")
def list_calendar(
    room_id: str | None = Query(default=None),
    host_email: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """The busy blocks the slot engine reads, newest first.

    In a deployment these arrive from a Google or Outlook watch or a free/busy
    pull. This build has neither, so they are filed by whatever writes them - and
    they are keyed by *host*, not by asset, because one person's calendar is the
    same whichever surface books them.
    """
    rows = headless.list_calendar_blocks(room_id=room_id, host_email=host_email, limit=limit)
    return {"count": len(rows), "blocks": rows}


@router.post("/calendar", status_code=201, summary="File one calendar block")
def add_calendar_block(
    room_id: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """One busy block: ``{host_email, startsAt, endsAt}`` or ``{startTime, duration}``.

    A block needs an end. An unbounded busy block would make every later slot
    unavailable, and a booking system whose failure mode is "nothing is ever
    free" is not a failure mode, it is an outage.
    """
    return headless.add_calendar_block(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/calendar"
    )


# --------------------------------------------------------------------------- #
# Call #1 and call #2: the researched two-step, per room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/sessions", summary="A room's discovered sessions")
def list_sessions(
    room_id: str,
    section: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Every session opened for this room, newest first.

    Room-scoped because the researched flow's payload is a *lead* and a lead
    belongs to a room: a rep reads a room's bookings from that room rather than
    from a product-wide feed.
    """
    rows = headless.list_sessions(room_id=room_id, section=section, state=state, limit=limit)
    return {
        "room_id": room_id,
        "count": len(rows),
        "filter": {"section": section, "state": state, "limit": limit},
        "sessions": rows,
    }


@router.post("/rooms/{room_id}/sessions", status_code=201, summary="Call #1: discover or route")
def discover(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Step 1 of the researched flow. Returns a ``routeId`` and ``startTimes``.

    [sourced] "1. **Discover or route** - a first call returns a session with a
    list of available time slots and an identifier for the session (``routeId``)"

    So this route's response carries ``routeId`` and a ``schedulingData`` list,
    and the session it creates is **single-use and short-lived**: it is a hold on
    an availability list, not a reservation. The response says so in
    ``next_step``, and the second call is
    ``POST /rooms/{room_id}/sessions/{route_id}/book``.

    ``interval{startsAt,duration}`` is required, because it is what distinguishes
    this from the *return a booking URL* pattern - without a window there are no
    slots to pick from. The response also carries the researched ``Custom API``
    tab's contents - a copyable URL and starter body per call - because that
    panel is the thing this workflow tells a developer to hand them.
    """
    return headless.discover(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/sessions"
    )


@router.get("/rooms/{room_id}/sessions/{route_id}", summary="Read one session")
def read_session(
    room_id: str, route_id: str, headless: HeadlessBooking = HeadlessDep
) -> dict[str, Any]:
    """The session, its slot list, and whether it is still bookable.

    A read, so it never spends the session: a caller polling a session learns it
    has expired without a write. Only a book call changes ``state``.

    ``retry_with_same_route_id`` is the researched instruction as a boolean, so a
    client does not have to infer it from prose.
    """
    session = headless.get_session(route_id)
    if session.get("room_id") not in (None, room_id):
        raise NotFound(
            f"session {route_id} belongs to room {session['room_id']}, not {room_id}",
            resource="session",
            record_id=route_id,
            room_id=room_id,
        )
    return session


@router.post("/rooms/{room_id}/sessions/{route_id}/book", status_code=201, summary="Call #2: book")
def book(
    room_id: str,
    route_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Step 2 of the researched flow. Returns a ``meetingId``.

    [sourced] "2. **Book** - a second call passes the ``routeId`` and a chosen
    ``startTime`` to commit the meeting. Calendar invites are sent immediately."

    ``startTime`` must be one of the strings this session's ``schedulingData``
    returned, passed back verbatim, and the response records whether it *was*
    verbatim. A value that is not on the list is refused rather than rounded -
    a booking is a commitment to a specific time, and "close enough" is not a
    time.

    **A failure spends the session.** That is the researched rule and the reason
    the ``next_step`` in the response is always a fresh discover:
    "On a schedule failure, do not retry the schedule call with the same
    ``routeId`` - start again from the discover or route step." Calling this
    route twice with the same ``routeId`` returns ``session_consumed`` the second
    time, and the call log records both attempts so the mistake is visible.
    """
    return headless.book(
        room_id,
        route_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/sessions/{route_id}/book",
    )


# --------------------------------------------------------------------------- #
# Meetings
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/meetings", summary="A room's booked meetings")
def list_meetings(
    room_id: str,
    section: str | None = Query(default=None),
    host_email: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Every meeting this room booked, newest first.

    ``invites_sent`` is on every row and is ``false``: the invite is *recorded*,
    in the same transaction as the meeting, and not transmitted. The record says
    so rather than claiming a send that did not happen.
    """
    rows = headless.list_meetings(
        room_id=room_id, section=section, host_email=host_email, limit=limit
    )
    by_provider: dict[str, int] = {}
    for row in rows:
        name = str(row["data"].get("provider") or "unknown")
        by_provider[name] = by_provider.get(name, 0) + 1
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_provider": by_provider,
        "filter": {"section": section, "host_email": host_email, "limit": limit},
        "meetings": rows,
    }


@router.get("/rooms/{room_id}/meetings/{meeting_id}", summary="One meeting in full")
def read_meeting(
    room_id: str, meeting_id: str, headless: HeadlessBooking = HeadlessDep
) -> dict[str, Any]:
    """The meeting, its invite records, and its webhook event.

    One call for all three because they are written in one transaction: a reader
    who fetches the meeting and the invites separately can, in principle, see
    them disagree.
    """
    meeting = headless.get_meeting(meeting_id)
    if meeting.get("room_id") not in (None, room_id):
        raise NotFound(
            f"meeting {meeting_id} belongs to room {meeting['room_id']}, not {room_id}",
            resource="meeting",
            record_id=meeting_id,
            room_id=room_id,
        )
    return meeting


# --------------------------------------------------------------------------- #
# The call log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/calls", summary="The headless call log")
def list_calls(
    room_id: str,
    outcome: str | None = Query(default=None),
    route_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    headless: HeadlessBooking = HeadlessDep,
) -> dict[str, Any]:
    """Every call made for this room, including the refused ones.

    This is where the researched instruction becomes *visible* rather than merely
    obeyed. A caller that retries one ``routeId`` twice leaves two rows here, the
    second saying ``session_consumed`` - so the mistake is readable from the log
    without reading a line of code, and a person holding a token can see exactly
    what their integration did.
    """
    rows = headless.calls(room_id=room_id, outcome=outcome, route_id=route_id, limit=limit)
    by_outcome: dict[str, int] = {}
    for row in rows:
        name = str(row["data"].get("outcome") or "unknown")
        by_outcome[name] = by_outcome.get(name, 0) + 1
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_outcome": by_outcome,
        "filter": {"outcome": outcome, "route_id": route_id, "limit": limit},
        "calls": rows,
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The bookable assets the demo declares, one per researched surface and then
#: some. Deliberately mixed: a Concierge router, both a Personal and an
#: Ownership scheduling link (the second because ``guestEmail`` is *required*
#: for it, and a demo that only had Personal links would never show the rule),
#: a two-path handoff router, and one asset switched off.
DEMO_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "name": "Northwind — Enterprise evaluation (Concierge)",
        "section": "concierge",
        "router_slug": "northwind-enterprise",
        "host_name": "Dana Okafor",
        "host_email": "dana.okafor@example.com",
        "duration_minutes": 45,
        "work_start_hour": 9,
        "work_end_hour": 16,
        "work_days": [0, 1, 2, 3, 4],
        "utc_offset_minutes": 0,
        "provider": "zoom",
        "webhook_url": "https://hooks.example.com/chili-piper/new-meeting",
        "note": "the researched Concierge init path is /concierge/routers/{routerSlug}/rest",
    },
    {
        "name": "Contoso — Security review (Ownership link)",
        "section": "links",
        "link_id": "lnk-contoso-ownership",
        "link_type": "ownership",
        "host_name": "Sam Ibrahim",
        "host_email": "sam.ibrahim@example.com",
        "duration_minutes": 30,
        "work_start_hour": 8,
        "work_end_hour": 15,
        "utc_offset_minutes": 0,
        "provider": "gmeet",
        "note": (
            "Ownership links require guestEmail in the init call, so this asset refuses a session "
            "without one. That is the researched rule and it is the reason a Personal link is "
            "seeded beside it."
        ),
    },
    {
        "name": "Fabrikam — Intro call (Personal link)",
        "section": "links",
        "link_id": "lnk-fabrikam-personal",
        "link_type": "personal",
        "host_name": "Priya Raman",
        "host_email": "priya.raman@example.com",
        "duration_minutes": 30,
        "work_start_hour": 10,
        "work_end_hour": 17,
        "utc_offset_minutes": 330,  # +05:30
        "provider": "gong",
        "note": (
            "A non-zero UTC offset on purpose: the working hours are 10:00-17:00 in a +05:30 local "
            "time, which is 04:30-11:30 UTC. A reviewer can see that the grid follows local time."
        ),
    },
    {
        "name": "Adventure Works — Handoff to the AE pod",
        "section": "handoff",
        "workspace_id": "ws-ae-pod",
        "booker_id": "usr-sdr-leah",
        "host_name": "Leah Bergstrom (SDR, booker)",
        "host_email": "leah.bergstrom@example.com",
        "duration_minutes": 30,
        "work_start_hour": 9,
        "work_end_hour": 17,
        "utc_offset_minutes": -300,
        "provider": "zoom",
        "paths": [
            {
                "path_id": "path-emea",
                "label": "EMEA — Aisha Farouk",
                "host_name": "Aisha Farouk",
                "host_email": "aisha.farouk@example.com",
                "duration_minutes": 30,
                "work_start_hour": 9,
                "work_end_hour": 13,
                "utc_offset_minutes": 0,
            },
            {
                "path_id": "path-amer",
                "label": "AMER — Marcus Webb",
                "host_name": "Marcus Webb",
                "host_email": "marcus.webb@example.com",
                # A different length on the second path, which is the normal case
                # for a handoff router and the reason each path carries its own
                # availability. The grid is left to follow the length, so the
                # two slot lists have different step sizes and cannot contain
                # overlapping starts.
                "duration_minutes": 60,
                "work_start_hour": 13,
                "work_end_hour": 17,
                "utc_offset_minutes": -300,
            },
        ],
        "note": (
            "Two routing paths with different hosts, different hours and different lengths, so the "
            "init response's per-path startTimes is the shape the research documents."
        ),
    },
    {
        "name": "Wide World — paused (disabled)",
        "section": "concierge",
        "router_slug": "wide-world-paused",
        "host_name": "Tomas Vela",
        "host_email": "tomas.vela@example.com",
        "duration_minutes": 30,
        "enabled": False,
        "note": (
            "Switched off on purpose: a disabled bookable asset must refuse rather than hand out "
            "slots nothing will honour."
        ),
    },
)


def _demo_day(now: Any, *, margin_hours: int = 48) -> Any:
    """The UTC midnight this demo's windows and calendar blocks are built on.

    Two properties, and an earlier version of this seeder had neither:

    * **Far enough ahead** that the day's slots are still in the future whatever
      hour the seed happens to run at. An earlier version anchored on
      ``now + 1 day`` at the same time of day, which after 16:00 UTC produced a
      window that fell entirely outside a 09:00-16:00 desk.
    * **On a working day.** Every asset in :data:`DEMO_ASSETS` books Monday to
      Friday - the default ``work_days`` is ``{0, 1, 2, 3, 4}`` - so a window
      anchored on a Saturday contains no slots at all and the workflow refuses
      with *no availability*. The margin alone does not prevent that, because
      the margin does not know what day of the week it lands on: ``now + 1 day``
      lands on a Saturday every Friday the seed runs, and that is why this
      feature's seed has been failing. Anchoring on the next weekday is the fix.

    Returned as a midnight so a caller can build several windows on the same day
    without each one walking the calendar again.
    """
    from datetime import timedelta, timezone

    base = (now.astimezone(timezone.utc) + timedelta(hours=margin_hours)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for offset in range(0, 15):
        candidate = base + timedelta(days=offset)
        if candidate.isoweekday() <= 5:
            return candidate
    return base  # pragma: no cover - 15 days covers every weekday


def _stamp(spec: str, day: Any) -> str:
    """Resolve a demo stamp written as ``+1d 09:00`` against the demo day.

    Relative, because a calendar block dated in the past blocks nothing. The demo
    would keep its four rows and its reassuring count, and quietly stop being a
    demo of anything: a wall of free time is exactly what these blocks exist to
    break up.
    """
    from datetime import timedelta

    days_text, _, clock = spec.strip().partition(" ")
    offset = int(days_text.rstrip("dD"))
    hour, _, minute = clock.partition(":")
    moment = (day + timedelta(days=offset)).replace(
        hour=int(hour), minute=int(minute or 0), second=0, microsecond=0
    )
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


#: The calendar blocks the demo files, chosen so the slot lists are not a solid
#: wall of free time. Two different hosts, plus a block on *one* handoff path
#: only - which is what makes the per-path availability visible.
#:
#: The stamps are offsets from :func:`_demo_day` rather than absolute instants.
#: An absolute one is a demo that is silently wrong the day after, and - because
#: the booking window moves with the clock while the blocks do not - it also
#: drifts out of its own window entirely, leaving four calendar rows that no
#: longer block anything.
DEMO_CALENDAR: tuple[dict[str, Any], ...] = (
    {
        "host_email": "dana.okafor@example.com",
        "startsAt": "+0d 13:00",
        "endsAt": "+0d 15:00",
        "label": "Internal weekly",
        "source_system": "google_calendar",
    },
    {
        "host_email": "dana.okafor@example.com",
        "startsAt": "+1d 09:00",
        "endsAt": "+1d 12:00",
        "label": "Customer onsite",
        "source_system": "google_calendar",
    },
    {
        "host_email": "sam.ibrahim@example.com",
        "startsAt": "+0d 10:00",
        "endsAt": "+0d 11:30",
        "label": "Security review prep",
        "source_system": "outlook",
    },
    {
        "host_email": "aisha.farouk@example.com",
        "path_id": "path-emea",
        "startsAt": "+0d 10:00",
        "endsAt": "+0d 14:00",
        "label": "EMEA customer day",
        "source_system": "google_calendar",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed five assets, two tokens, four calendar blocks, and four sessions.

    Every session in the demo is produced by running the real
    :class:`~dsr.headless_booking.engine.HeadlessBooking` over the real store, so
    the demo cannot show a shape the workflow would not produce, and seeding
    opens no socket because the scheduler here is the audited store.

    It is deliberately mixed, because a demo of only successful bookings teaches
    nothing about the workflow's whole point. Between them the four sessions
    produce:

    * a **booked** meeting through the Concierge surface, with its invite records
      and its ``For New Meeting`` webhook event written in the same transaction;
    * a **failed** booking on a start time that was never offered, which spends
      the session - the researched "do not retry with the same ``routeId``" rule
      made visible;
    * a booking on a **handoff** session naming one of its two ``pathId``\\ s,
      so the per-path shape is real;
    * and a session **refused at creation** because an Ownership link had no
      ``guestEmail``, which is run here and expected to fail so the demo asserts
      the rule rather than asking a reader to take it on trust.

    Returns a short description, which the seeder prints.
    """
    store = RecordStore(db)
    now = context["now"]
    headless = HeadlessBooking(store, clock=lambda: now)
    actor = "dana"
    source = "seed"

    rooms = [
        (str(room_id), str(account))
        for room_id, account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        # The assets and tokens are still worth having with no room to scope a
        # session to; the seeder prints what was skipped. Filtered by existence
        # rather than trusted, because a seeder that aborts a whole feature over
        # one stale room id leaves a page nobody can review.
        return "0 sessions (no rooms to scope them to)"

    def room_at(index: int) -> str:
        return rooms[index % len(rooms)][0]

    assets: dict[str, dict[str, Any]] = {}
    for index, spec in enumerate(DEMO_ASSETS):
        record = headless.create_asset(room_at(index), spec, actor=actor, source=source)
        assets[str(spec["name"])] = record

    by_slug = {
        str(record["data"].get("router_slug")): record
        for record in assets.values()
        if record["data"].get("section") == "concierge"
    }
    by_link_type = {
        str(record["data"].get("link_type")): record
        for record in assets.values()
        if record["data"].get("section") == "links"
    }
    handoff = next(
        (record for record in assets.values() if record["data"].get("section") == "handoff"),
        None,
    )

    # One anchor for both the sessions' interval and the calendar blocks, so the
    # blocks land inside the window they are supposed to shape. See _demo_day.
    demo_day = _demo_day(now)
    interval = {
        "startsAt": demo_day.isoformat(timespec="seconds").replace("+00:00", "Z"),
        # 36 hours from that midnight spans a whole working day and part of the
        # next, so a desk in any timezone the demo configures has slots in it.
        "duration": 36 * 60,
    }

    for block in DEMO_CALENDAR:
        headless.add_calendar_block(
            room_at(0),
            {
                **block,
                "startsAt": _stamp(str(block["startsAt"]), demo_day),
                "endsAt": _stamp(str(block["endsAt"]), demo_day),
            },
            actor=actor,
            source=source,
        )

    # Two tokens: one that can do everything, and one that can only *read* - so
    # the per-section, per-permission scope rule is demonstrated rather than
    # described. The read-only one is generated by an admin, exactly as the
    # research requires, and its refusal is exercised below.
    admin_token = headless.create_credential(
        {"role": "admin", "label": "Northwind integration (full)", "note": "books and lists"},
        actor="dana",
        source=source,
    )
    read_only = headless.create_credential(
        {
            "role": "admin",
            "label": "Contoso analyst (read only)",
            "sections": ["links"],
            "permissions": ["read"],
        },
        actor="dana",
        source=source,
    )

    outcomes: list[str] = []

    # 1. The happy path, on the Concierge surface, with the admin token.
    concierge = by_slug.get("northwind-enterprise")
    if concierge is not None:
        session = headless.discover(
            room_at(0),
            {
                "section": "concierge",
                "asset_id": concierge["id"],
                "interval": interval,
                "guest": {"guestEmail": "buyer@northwind.example", "name": "Wen Li"},
                "credential_id": admin_token["id"],
            },
            actor=actor,
            source=source,
        )
        slots = session["schedulingData"][0]["startTimes"]
        if slots:
            headless.book(
                room_at(0),
                session["routeId"],
                {"startTime": slots[0], "guest": {"guestEmail": "buyer@northwind.example"}},
                actor=actor,
                source=source,
            )
            outcomes.append("booked")

    # 2. A booking on a start time that was never offered. This is the failure
    #    the research is most emphatic about, so the demo shows what it costs: a
    #    refused booking that still spends the session.
    if concierge is not None:
        session = headless.discover(
            room_at(1),
            {
                "section": "concierge",
                "asset_id": concierge["id"],
                "interval": interval,
                "guest": {"guestEmail": "ops@fabrikam.example", "name": "Alba Ries"},
                "credential_id": admin_token["id"],
            },
            actor=actor,
            source=source,
        )
        try:
            headless.book(
                room_at(1),
                session["routeId"],
                # Not on the list. A real caller gets this by rounding a
                # timestamp, and the researched remedy is a fresh discover.
                {
                    "startTime": "2026-01-01T09:00:00Z",
                    "guest": {"guestEmail": "ops@fabrikam.example"},
                },
                actor=actor,
                source=source,
            )
        except HeadlessBookingError:
            outcomes.append("failed_offered")
        # And the retry the research forbids, so the call log shows both.
        try:
            headless.book(
                room_at(1),
                session["routeId"],
                {
                    "startTime": "2026-01-01T09:00:00Z",
                    "guest": {"guestEmail": "ops@fabrikam.example"},
                },
                actor=actor,
                source=source,
            )
        except HeadlessBookingError:
            outcomes.append("retry_refused")

    # 3. A handoff booking naming one of its two paths.
    if handoff is not None:
        session = headless.discover(
            room_at(2),
            {
                "section": "handoff",
                "asset_id": handoff["id"],
                "interval": interval,
                "guest": {"guestEmail": "lead@adventure.example", "name": "Nadia Farouk"},
                "credential_id": admin_token["id"],
            },
            actor=actor,
            source=source,
        )
        for entry in session["schedulingData"]:
            if entry["pathId"] and entry["startTimes"]:
                headless.book(
                    room_at(2),
                    session["routeId"],
                    {
                        "startTime": entry["startTimes"][0],
                        "pathId": entry["pathId"],
                        "guest": {"guestEmail": "lead@adventure.example", "name": "Nadia Farouk"},
                    },
                    actor=actor,
                    source=source,
                )
                outcomes.append("booked_handoff")
                break

    # 4. An Ownership link with no guestEmail, which the research says is
    #    required so the owner can be resolved from the CRM. Run here and
    #    expected to fail, so the demo asserts the rule.
    ownership = by_link_type.get("ownership")
    ownership_refused = False
    if ownership is not None:
        try:
            headless.discover(
                room_at(3),
                {
                    "section": "links",
                    "asset_id": ownership["id"],
                    "interval": interval,
                    # No guest. Deliberate.
                    "guest": {"name": "Procurement"},
                },
                actor=actor,
                source=source,
            )
        except HeadlessBookingError:
            ownership_refused = True

    # 5. A read-only token trying to book, which its scope forbids.
    read_only_refused = False
    if concierge is not None:
        try:
            headless.discover(
                room_at(0),
                {
                    "section": "concierge",
                    "asset_id": concierge["id"],
                    "interval": interval,
                    "guest": {"guestEmail": "x@example.com"},
                    "credential_id": read_only["id"],
                },
                actor=actor,
                source=source,
            )
        except PermissionDenied:
            read_only_refused = True

    return (
        f"{len(assets)} bookable assets (concierge, personal and ownership links, a two-path "
        f"handoff router, one disabled), 2 scoped tokens, {len(DEMO_CALENDAR)} calendar blocks, "
        f"{len(outcomes)} sessions: "
        + ", ".join(outcomes)
        + (", 1 ownership link refused for a missing guestEmail" if ownership_refused else "")
        + (", 1 read-only token refused" if read_only_refused else "")
    )
