"""WF-059: provision a per-booking video-conference link (Meet / Zoom / Teams / Gong).

The researched workflow, in full. An admin sets the **Location** on the Meeting
Type - one of seven researched options, several of them, one the default -
connects the provider on the **Integrations** tab, and every booking taken
against a one-time Location mints a *fresh* conference whose link is written into
both researched Location fields. Move the meeting to a different tool, or swap a
rep's integration, and the booking's location is updated, the link is
re-provisioned, and the attendees are emailed the change. When the provider
fails, its own ``appsStatus[]`` report is recorded and read for a retry.

The domain logic is in :mod:`dsr.conference_links`, which this module does not own
and which no other feature could have written into its own path. What lives here
is the three things a workflow has to take out of shared files: the HTTP
surface, the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-059``, and room scoping is real.** A booking belongs to
the room the buyer was looking at - that is where the seller will join the call
from - so anything scoped to one is served under ``/rooms/{room_id}/...``. A
Meeting Type's *Location* and an Integrations *connection* are not room-scoped,
because they are properties of the host rather than of a deal: a Zoom credential
does not belong to one buyer, and putting a room in its key would make it
reconnectable once per room.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served
it. A hardcoded string inside a domain method is a defect, and the same class of
bug has shipped in this codebase before: a feature's audit log kept naming a path
the app had stopped serving. ``source`` is a *required* keyword on every writing
method of :class:`~dsr.conference_links.engine.ProvisioningEngine`, so omitting
it is a ``TypeError`` at the call site rather than an untraceable row.

**One handler for the whole error hierarchy.**
:class:`~dsr.conference_links.errors.ConferenceLinkError` is the base of every
refusal in :mod:`dsr.conference_links`, and each carries its own ``status`` and
``code``, so one handler can answer 400 for a malformed Location and 409 for one
that names a provider which has not been connected, without being told which.
It is a domain type, so registering it globally cannot intercept anything
unrelated elsewhere in the product. ``RecordNotFound`` is deliberately *not*
claimed: the core app already maps it to 404, and two handlers for one type is a
collision the host refuses.

**Two routes for one decision, on purpose.** ``/rooms/{room_id}/bookings``
provisions on create, because "On booking, the system creates a fresh conference"
is an automation and not something a caller has to ask for twice. The explicit
``.../provision`` route exists for the two cases the research names: a retry
after ``appsStatus`` reported a failure, and an ``Ask the Guest`` Location the
guest has since answered. A booking whose Location cannot mint a link is still a
booking - ``Conference Details`` is "for those who don't want to use one-time
links" - so the create route returns the researched outcome rather than
refusing a real meeting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.conference_links import ProvisioningEngine
from dsr.conference_links.errors import ConferenceLinkError
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-059-provision-a-per-booking-video-conferen",
    "ticket": "WF-059",
    "name": "Provision a per-booking video-conference link (Meet / Zoom / Teams / Gong)",
    "description": (
        "Set the Location on a Meeting Type, connect the provider on the Integrations tab, and "
        "have every booking mint a fresh conference whose link is written into both the booking "
        "location and the meetingLocation. Google Meet, Zoom, Gong (which redirects to Zoom), a "
        "static link, an in-person room, or Ask the Guest. Moving a meeting to a different tool "
        "re-provisions the link and emails the attendees, and the provider's own appsStatus "
        "report is recorded so a failure can be retried or fallen back from."
    ),
    "nav": [{"id": "conference-links", "label": "Meeting links"}],
}

router = APIRouter(prefix="/api/wf-059", tags=["wf059"])


def get_engine(store: RecordStore = StoreDep) -> ProvisioningEngine:
    """A :class:`ProvisioningEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return ProvisioningEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _conference_error(request: Request, exc: ConferenceLinkError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy.
    :class:`~dsr.conference_links.errors.ConferenceLinkError` is the base of every
    refusal in :mod:`dsr.conference_links` - a Location naming an option the
    picker does not contain, a connection that has not been made, a conference
    another booking already holds, a swap that would change nothing - and all of
    them are the caller's to fix. The status rides on the exception rather than
    being decided here, because a malformed body and a conflict with state that
    already exists are both this package's errors and only one of them conflicts
    with something that exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {ConferenceLinkError: _conference_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """Every published rule, served as data.

    The seven Location options, the thirty Cal integrations, the eight location
    types, the two dynamic tags, the four ``appsStatus`` fields, Google's
    ``conferenceDataVersion`` and ``createRequest``, Cal's API version and scope,
    and the retry budget. A client renders its pickers from this rather than from
    a list compiled into the page, so a value added here reaches every client at
    once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the seven Location options, the thirty integrations, the
    two dynamic tags, the mandatory connection and Google's reuse prohibition. It
    does not say what ``Custom`` produces on the wire, or how many times a
    failed provision is retried, or what happens when a swap names the location
    a booking already has - so those are collected here, named, traceable and
    served, rather than left as comments in function bodies. The sourced half
    comes back beside the inferred half, because the point of the endpoint is to
    see where the line falls.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# The Location picker
# --------------------------------------------------------------------------- #


@router.get("/location-kinds")
def list_location_kinds(engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """The seven researched options, with what each one generates and needs.

    Every entry says whether it mints a one-time conference, whether that
    requires a connection, the outcome a booking on it always reaches, and the
    sentence the research uses. The admin page renders its picker from this, so
    "why do I need to connect Zoom?" is answered by the same endpoint that lists
    the options rather than by a tooltip compiled into the page.
    """
    return engine.vocabulary()["location_catalogue"]


@router.get("/location-kinds/{kind}")
def read_location_kind(kind: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One Location option, in full.

    Accepts the picker label as well as the wire slug, so a client holding
    "Ask the Guest (Provide My Own)" does not have to know the slug.
    """
    from dsr.conference_links.locations import describe_kind

    return describe_kind(kind)


@router.get("/meeting-locations")
def list_meeting_locations(
    kind: str | None = Query(default=None),
    include_defaulted: bool = Query(default=True),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """The configured Location options, newest first.

    ``one_time`` is how many actually mint a link. A picker full of options that
    produce no conference is a picker reporting the number of *choices* rather
    than the number of *links*, and the two are not the same number.
    """
    listed = engine.meeting_locations(kind=kind, include_defaulted=include_defaulted)
    return {
        "count": len(listed),
        "one_time": sum(1 for row in listed if row.get("mints_conference")),
        "defaults": sum(1 for row in listed if row.get("is_default")),
        "with_gaps": sum(1 for row in listed if row.get("gaps")),
        "meeting_locations": listed,
    }


@router.post("/meeting-locations", status_code=201)
def create_meeting_location(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Add one option to a Meeting Type's Location picker.

    The researched picker carries "multiple locations with a 'Set as Default'",
    so a first Location becomes the default by itself and a *second* one
    claiming the default is refused. The response carries ``gaps``: what this
    option still needs. A one-time option with no connection reports the
    researched mandatory step by name rather than refusing - the option is
    real, and the connection is a second request.
    """
    return engine.create_location(payload, actor=actor, source=f"POST {router.prefix}/meeting-locations")


@router.get("/meeting-locations/{location_id}")
def read_meeting_location(location_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One Location option, its wire shape, and whether it is ready to provision."""
    return engine.meeting_location(location_id)


@router.patch("/meeting-locations/{location_id}")
def amend_meeting_location(
    location_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Change a Location option, or be told every reason why not.

    The constraint is this build's and has one rule: an option that has already
    provisioned conferences has decided where real people clicked, so the field
    that decides *which* provider gets the link is frozen. Renaming and
    re-describing are still allowed, because neither can invalidate a link
    somebody already used. Every offending field comes back at once.
    """
    return engine.amend_location(
        location_id, payload, actor=actor, source=f"PATCH {router.prefix}/meeting-locations/{{location_id}}"
    )


@router.delete("/meeting-locations/{location_id}")
def remove_meeting_location(
    location_id: str,
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Remove a Location option. The default one has to move first.

    A Location that has provisioned bookings cannot be removed at all: those
    bookings name it, and the audit trail must not point at nothing. That is the
    same reason the core store soft-deletes rather than hard-deletes, and this
    route inherits it.
    """
    return engine.remove_location(
        location_id, actor=actor, source=f"DELETE {router.prefix}/meeting-locations/{{location_id}}"
    )


@router.post("/meeting-locations/{location_id}/set-default")
def set_default_location(
    location_id: str,
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """The researched "Set as Default" control, in one atomic step.

    Clearing the old default and setting the new one as two writes would let a
    crash between them leave a Meeting Type with no default, which is the state
    this workflow refuses. The two writes share one transaction, and each still
    gets its own audit row so the log says when the switch was thrown. Setting
    the default that is already default answers with what is true and writes
    nothing.
    """
    return engine.set_default_location(
        location_id, actor=actor, source=f"POST {router.prefix}/meeting-locations/{{location_id}}/set-default"
    )


# --------------------------------------------------------------------------- #
# The Integrations tab
# --------------------------------------------------------------------------- #


@router.get("/providers")
def list_providers(engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """The providers, split into the three the picker offers and the rest of Cal's enum.

    The split is served rather than blurred because a page showing "30 providers"
    when only three can actually be provisioned is the failure this endpoint
    exists to prevent. ``escape_hatch`` names the researched ``link`` option for
    anyone who wants a static URL instead.
    """
    return engine.vocabulary()["providers"]


@router.get("/connections")
def list_connections(
    provider: str | None = Query(default=None),
    ready: bool | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Every connected provider, with its readiness.

    ``ready`` counts the connections that can actually provision a conference
    now. "Connecting Zoom on the Integrations tab is mandatory for this one to
    work" means a connection that exists but has lapsed provisions nothing, and a
    list that reported only the total would be reporting the number of saved
    credentials rather than the number of working ones.
    """
    listed = engine.provider_connections(provider=provider, ready=ready)
    return {
        "count": len(listed),
        "ready": sum(1 for row in listed if (row.get("readiness") or {}).get("ready")),
        "not_ready": sum(1 for row in listed if not (row.get("readiness") or {}).get("ready")),
        "connections": listed,
    }


@router.post("/connections", status_code=201)
def connect_provider(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Connect a provider on the Integrations tab. The researched mandatory step.

    One connection per host per provider, so a second is a 409 rather than a
    second credential the Location picker would have to choose between with
    nothing to choose on. A ``token`` in the body is refused outright: this
    product writes an audit row in the same transaction as every change and
    mirrors it to JSONL, so a credential on a record would be a credential in the
    audit log. Record ``token_present`` instead.
    """
    return engine.connect(payload, actor=actor, source=f"POST {router.prefix}/connections")


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One connection, with what is still missing before it can provision."""
    return engine.provider_connection(connection_id)


@router.patch("/connections/{connection_id}")
def amend_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Change a connection's note or state - and nothing that moves it.

    The provider and the host are what every Location naming this connection
    resolves to, so changing either is the re-authorisation route rather than a
    patch. A patch that did it silently would make the same change unreachable
    by name.
    """
    return engine.amend_connection(
        connection_id, payload, actor=actor, source=f"PATCH {router.prefix}/connections/{{connection_id}}"
    )


@router.post("/connections/{connection_id}/reauthorize")
def reauthorize_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Re-point a connection at a new credential or a new host.

    This is the researched "a rep's integration is swapped" half of step 4, and
    it is a distinct operation from a patch because it re-points every Location
    naming it. The response says how many bookings were already provisioned
    through it, and that they keep their links: the researched swap re-provisions
    a booking when it is asked to, and does not retroactively re-issue links
    nobody asked to change.
    """
    return engine.reauthorize(
        connection_id, payload, actor=actor, source=f"POST {router.prefix}/connections/{{connection_id}}/reauthorize"
    )


@router.delete("/connections/{connection_id}")
def disconnect_provider(
    connection_id: str,
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Remove a connection, refusing while a Location option still names it.

    "Connecting Zoom on the Integrations tab is mandatory for this one to work"
    cuts both ways: a Location naming a connection that no longer exists is a
    Location that cannot book, and the seller finds out when a prospect is
    waiting. The message says how many options would be stranded.
    """
    return engine.disconnect(
        connection_id, actor=actor, source=f"DELETE {router.prefix}/connections/{{connection_id}}"
    )


# --------------------------------------------------------------------------- #
# Bookings - the automation, scoped to a room
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/bookings", status_code=201)
def book(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Take a booking and provision its Location. user_flow steps 1 to 3, in one call.

    The Location option is resolved (named, or the researched default), the
    mandatory connection is enforced, a **fresh** conference is minted for this
    booking alone, and the link is written into both researched Location fields -
    booking ``location`` and meeting ``meetingLocation``.

    A booking whose Location cannot produce a conference is still a booking, and
    the response says which researched outcome it reached. What refuses is a
    caller mistake, and a provider that has not been connected.
    """
    return engine.book(room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings")


@router.get("/rooms/{room_id}/bookings")
def list_bookings(
    room_id: str,
    location_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="the researched Location states"),
    provider: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """This room's bookings, newest first, with their Location states.

    ``with_links`` and ``without_links`` are the two numbers a rep reads first.
    ``provider_failures`` is counted on its own because a report from a
    provisioning chain that partly succeeded does not take a working link away,
    and rendering it as "no link" would be a thing a rep acts on - they would
    stop sending the invite.
    """
    listed = engine.bookings(room_id, location_id=location_id, state=state, provider=provider, limit=limit)
    return {
        "room_id": room_id,
        "count": len(listed),
        "with_links": sum(1 for row in listed if row.get("conference_id")),
        "without_links": sum(1 for row in listed if not row.get("conference_id")),
        "provider_failures": sum(
            1 for row in listed if row.get("provision_state") in ("failed", "retrying")
        ),
        "bookings": listed,
    }


@router.get("/rooms/{room_id}/bookings/{booking_uid}")
def read_booking(
    room_id: str, booking_uid: str, engine: ProvisioningEngine = EngineDep
) -> dict[str, Any]:
    """One booking, with its conference, its rendered invite and its swap history.

    The whole of a meeting's Location story in one read, because a rep asking
    "where is this meeting and has it moved?" should not have to call three
    endpoints to find out.
    """
    return engine.booking(room_id, booking_uid)


@router.get("/rooms/{room_id}/bookings/{booking_uid}/conference")
def read_conference(
    room_id: str, booking_uid: str, engine: ProvisioningEngine = EngineDep
) -> dict[str, Any]:
    """The conference on this booking, including the outbound request it would send.

    Google's shape is sourced verbatim, including the ``conferenceDataVersion=1``
    and the ``createRequest`` the reuse prohibition depends on. The request is
    built and stored rather than sent, and the response says so.
    """
    return engine.conference(room_id, booking_uid)


@router.post("/rooms/{room_id}/bookings/{booking_uid}/provision")
def provision(
    room_id: str,
    booking_uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Provision again for a booking that has no link yet.

    Provisioning is automatic on booking, so this exists for the two cases the
    research names: a retry after ``appsStatus`` reported a failure, and an
    ``Ask the Guest`` Location the guest has since answered - pass
    ``guest_location`` for the second. A booking that already holds a conference
    is refused, because "always generate a unique conference for each event"
    makes a second one for one event wrong rather than merely redundant.
    """
    return engine.provision(
        room_id, booking_uid, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings/{booking_uid}/provision"
    )


@router.post("/rooms/{room_id}/bookings/{booking_uid}/location")
def swap_location(
    room_id: str,
    booking_uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Move a booking to a different tool. user_flow step 4, in one call.

    Cal's researched endpoint "also provisions a conference link" and "Attendees
    are notified of the location change by email", so this does both: a new
    conference is minted under the new provider, the researched
    ``previousLocation``/``location`` pair is written, and the notification is
    recorded beside them. Name a configured ``location_id`` to move onto another
    Location option, or supply ``kind`` inline to move one meeting without
    re-pointing the whole Meeting Type. A swap to the location the booking
    already has is refused, because the researched notification would then email
    every attendee that nothing changed.
    """
    return engine.swap(
        room_id, booking_uid, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings/{booking_uid}/location"
    )


@router.get("/rooms/{room_id}/bookings/{booking_uid}/history")
def read_history(
    room_id: str, booking_uid: str, engine: ProvisioningEngine = EngineDep
) -> dict[str, Any]:
    """The researched ``previousLocation`` trail, oldest first.

    One entry per swap. The order is oldest first on purpose: the researched
    webhook payload "mirrors the standard booking payload, with one addition:
    ``previousLocation`` holds the location before the change", and reading a
    trail newest-first makes the previous value read as the current one.
    """
    listed = engine.history(room_id, booking_uid)
    return {"room_id": room_id, "booking_uid": booking_uid, "count": len(listed), "history": listed}


@router.get("/rooms/{room_id}/bookings/{booking_uid}/invite")
def read_invite(
    room_id: str, booking_uid: str, engine: ProvisioningEngine = EngineDep
) -> dict[str, Any]:
    """The invite body, with ``CP.Meeting.RescheduleUrl`` and ``CP.Meeting.CancelUrl`` resolved."""
    return engine.invite(room_id, booking_uid)


@router.post("/invite-preview")
def preview_invite(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Render an invite body without booking anything.

    A pure read of the caller's own template, so the admin page can show the two
    researched tags resolved before a booking exists to resolve them against. An
    unresolved tag is left visible in the body and reported, rather than replaced
    with a blank.
    """
    return engine.preview_invite(payload)


@router.post("/rooms/{room_id}/bookings/{booking_uid}/apps-status")
def report_provider_status(
    room_id: str,
    booking_uid: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Record the provider's own failure report and apply the researched retry rule.

    "on provider failure Cal reports ``appsStatus[]`` per app (``appName``,
    ``success``, ``failures``, ``errors``) in the booking/webhook payload, which
    is what an integration should watch to retry or fall back". The report is
    normalised to those four fields and judged against the retry budget.

    This is a *record*, not an exception: the booking happened and the prospect
    holds a slot, so the useful thing to say is that the link is missing, which
    app failed, and whether a retry is left. When the budget is spent, the
    response names the researched fallback.
    """
    return engine.report_provider_status(
        room_id, booking_uid, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/bookings/{booking_uid}/apps-status"
    )


@router.get("/rooms/{room_id}/bookings/{booking_uid}/provider-status")
def read_provider_status(
    room_id: str, booking_uid: str, engine: ProvisioningEngine = EngineDep
) -> dict[str, Any]:
    """Every failure report recorded for this booking, oldest first."""
    listed = engine.provider_status_history(room_id, booking_uid)
    return {
        "room_id": room_id,
        "booking_uid": booking_uid,
        "count": len(listed),
        "provider_status": listed,
    }


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room, and the automation note beside them.

    Counted over this room's bookings rather than the whole collection, so a
    room's header says what happened in that room. The three numbers worth
    reading first are ``one_time_linked``, ``missing_links`` and
    ``locations_without_a_connection`` - together they say how much of this
    workflow is actually running.
    """
    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The Meeting Type Location options the demo configures. Six of the seven
#: researched options; ``Custom`` is left unconfigured so the page has something
#: a reviewer can *add* rather than everything already present.
DEMO_LOCATIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "Zoom (one-time link)",
        "kind": "zoom",
        "is_default": True,
    },
    {
        "name": "Google Meet (one-time link)",
        "kind": "google-meet",
    },
    {
        # The researched option whose link redirects. A demo that omitted it
        # would make "Gong will redirect you to Zoom" a claim rather than a row.
        "name": "Gong (records, then redirects to Zoom)",
        "kind": "gong",
    },
    {
        # "normally used to include links, like static Zoom ones, for those who
        # don't want to use one-time links"
        "name": "Conference Details (static Zoom room)",
        "kind": "conference-details",
        "conference_details": "https://example.zoom.us/j/9876543210",
    },
    {
        "name": "In-Person Meeting",
        "kind": "in-person",
        "custom_text": "Level 12 boardroom, 1 Market St, San Francisco",
    },
    {
        # The option where the guest supplies the location. Seeded with a prompt
        # so the page has something to show for it.
        "name": "Ask the Guest (Provide My Own)",
        "kind": "attendee-defined",
        "attendee_prompt": "Where would you like to meet? Add a room, a link, or a phone number.",
    },
)

#: The providers the demo connects.
#:
#: Zoom and Gong are connected and ready, so a fresh one-time link and a
#: swap-onto-Gong are both reachable. **Google Meet is connected but its
#: credential was revoked**, and that is the point: "Connecting Zoom on the
#: Integrations tab is mandatory for this one to work" is only visible when a
#: one-time provider sits on the picker and *cannot* provision, so the demo
#: attempts a Meet booking and records the refusal.
DEMO_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {
        "provider": "zoom",
        "host": "dana@northwind.example",
        "calendar_id": "dana@northwind.example",
        "link_host": "northwind.zoom.us",
    },
    {
        "provider": "gong",
        "host": "dana@northwind.example",
        "calendar_id": "dana@northwind.example",
        "link_host": "northwind.gong.io",
        "note": "Gong org-level connection; the per-user mapping is dana.",
    },
    {
        "provider": "google-meet",
        "host": "sam@contoso.example",
        "state": "revoked",
        "token_present": False,
        "note": "The Workspace admin revoked the calendar scope; this provider cannot provision.",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Six Location options, three connections, and the states that are not all success.

    The rows are produced by running the real
    :class:`~dsr.conference_links.engine.ProvisioningEngine`, so the demo cannot
    show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only green teaches a
    reviewer nothing:

    * **Zoom and Gong connected and ready; Google Meet connected but revoked.**
      "Connected" and "usable" are different facts, and a demo of only live
      connections would make the readiness report and the researched mandatory
      connection invisible. The demo *attempts* a Meet booking and records the
      refusal, so the gate is a row rather than a sentence.
    * **all six configured Location options**, including the static link "for
      those who don't want to use one-time links", the in-person room, and the
      Ask the Guest prompt. ``Custom`` is deliberately left off so the picker has
      something to add.
    * **bookings in five researched states**: a Zoom booking with a fresh
      per-booking link, a second Zoom booking **swapped to Gong** so the
      researched ``previousLocation`` trail and the Gong redirect are both rows,
      a static-link booking, an in-person booking, an Ask the Guest booking still
      waiting on the guest, and the Meet booking that was refused.
    * **two provider failure reports in two different places on the retry
      schedule**: one ``appsStatus[]`` naming two apps - one succeeded, one
      failed - left mid-budget so ``retryable`` is true, and one that used the
      whole budget so ``failed`` is reported and the researched fallback is
      named. Both keep the booking's working link, because "one app failed" is
      not the same fact as "the guest has nowhere to go".
    * **a second room** with one booking, so the room scoping is a row.
    * **an attempted re-provision of a booking that already holds a conference**,
      refused by Google's warning about reusing conference data across different
      events - the single most important rule in this workflow, and the one a
      green-only demo would hide.
    """
    store = RecordStore(db)
    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    source = "seed"

    # A clock the demo can move, so every timestamp it leaves behind is a
    # function of the seeder's input rather than of the wall clock.
    clock = {"at": base}
    engine = ProvisioningEngine(store, now=lambda: clock["at"].isoformat(timespec="milliseconds"))

    if not rooms:
        return "0 Location options, 0 connections, 0 bookings (no rooms to scope bookings to)"

    # -- the Integrations tab ------------------------------------------------ #
    connected: list[dict[str, Any]] = []
    for spec in DEMO_CONNECTIONS:
        try:
            connected.append(engine.connect(spec, actor="dana", source=source))
        except ConferenceLinkError:
            continue
    ready_providers = {
        row["provider"] for row in connected if (row.get("readiness") or {}).get("ready")
    }
    connection_id = {row["provider"]: row["id"] for row in connected}

    # -- the Location picker ------------------------------------------------- #
    by_kind: dict[str, dict[str, Any]] = {}
    for spec in DEMO_LOCATIONS:
        body = dict(spec)
        # Each one-time option names the connection it will provision through.
        # Meet's is revoked, so that Location is refused at provision time -
        # which is the researched mandatory connection made visible.
        body["connection_id"] = connection_id.get(body["kind"])
        try:
            by_kind[body["kind"]] = engine.create_location(body, actor="dana", source=source)
        except ConferenceLinkError:
            continue

    # -- bookings, in the researched states ---------------------------------- #
    room_id, account = rooms[0]
    other_room = rooms[1][0] if len(rooms) > 1 else room_id
    domain = str(account).split()[0].lower()
    refusals: list[str] = []

    def book(
        kind: str,
        who: str,
        *,
        target_room: str | None = None,
        minutes_ago: int = 0,
        **overrides: Any,
    ) -> dict[str, Any]:
        """One booking on one Location option, through the real automation.

        A refusal is collected rather than raised: the whole point of the Meet
        row is that this call *fails*, and a seed that aborted on it would leave
        the rest of the demo unwritten.
        """
        location = by_kind.get(kind)
        if location is None:
            return {}
        clock["at"] = base - timedelta(minutes=minutes_ago)
        payload: dict[str, Any] = {
            "location_id": location["id"],
            "booking_uid": f"bk_{kind.replace('-', '_')}_{who}",
            "attendee_email": f"{who}@{domain}.example",
            "attendee_name": who.title(),
            "starts_at": (base + timedelta(days=1)).isoformat(timespec="seconds"),
        }
        payload.update(overrides)
        try:
            return engine.book(target_room or room_id, payload, actor="dana", source=source)
        except ConferenceLinkError as exc:
            refusals.append(f"{kind} for {who}: {exc}")
            return {}

    # 1. Two Zoom bookings. The first stays on Zoom; the second is swapped to
    #    Gong, which mints a fresh conference *and* leaves the researched
    #    previousLocation trail and the Gong redirect in one row.
    zoom_booking = book("zoom", "priya")
    swap_booking = book("zoom", "omar")
    if swap_booking and "gong" in by_kind:
        try:
            engine.swap(
                room_id,
                swap_booking["booking_uid"],
                {
                    "kind": "gong",
                    "reason": "meeting-moved-tool",
                    "detail": (
                        "Buyer asked for a recorded call; Gong covers recording and "
                        "redirects to Zoom."
                    ),
                    "connection_id": connection_id.get("gong"),
                },
                actor="dana",
                source=source,
            )
        except ConferenceLinkError as exc:
            refusals.append(f"swap onto gong: {exc}")

    # 2. The three researched outcomes that mint no conference, one each.
    book("conference-details", "hana")
    book("in-person", "lars")
    book("attendee-defined", "mei")

    # 3. The attempted Meet booking. Its provider's credential was revoked, so
    #    the researched mandatory connection refuses the provision.
    book("google-meet", "noor")

    # 4. Two provision failures in two different places on the retry schedule.
    retryable_booking = book("zoom", "rafa")
    spent_booking = book("zoom", "tomas")
    if retryable_booking:
        try:
            engine.report_provider_status(
                room_id,
                retryable_booking["booking_uid"],
                {
                    "appsStatus": [
                        {"appName": "zoom", "success": True, "failures": 0, "errors": []},
                        {
                            "appName": "google-meet",
                            "success": False,
                            "failures": 1,
                            "errors": ["quotaExceeded: no conference could be created"],
                        },
                    ]
                },
                actor="dana",
                source=source,
            )
        except ConferenceLinkError as exc:
            refusals.append(f"apps-status retryable: {exc}")
    if spent_booking:
        try:
            engine.report_provider_status(
                room_id,
                spent_booking["booking_uid"],
                {
                    "appsStatus": [
                        {
                            "appName": "zoom",
                            "success": False,
                            "failures": 99,
                            "errors": ["provider unreachable"],
                        }
                    ]
                },
                actor="dana",
                source=source,
            )
        except ConferenceLinkError as exc:
            refusals.append(f"apps-status spent: {exc}")

    # 5. A second room, so the room scoping is a row rather than a claim.
    second_room_booking = book("zoom", "kofi", target_room=other_room, minutes_ago=30)

    # 6. The reuse prohibition, attempted and refused. On a booking that already
    #    holds a conference, so the only thing standing in the way is Google's
    #    warning about reusing conference data across different events.
    reuse_refused = ""
    if zoom_booking and zoom_booking.get("conference_id"):
        try:
            engine.provision(
                room_id, zoom_booking["booking_uid"], {}, actor="dana", source=source
            )
            reuse_refused = "NOT refused - a linked booking accepted a second conference"
        except ConferenceLinkError:
            reuse_refused = "a re-provision of a linked booking attempted and refused"

    # -- the summary line, counted rather than asserted ---------------------- #
    clock["at"] = base
    room_rows = engine.bookings(room_id, limit=500)
    stats = engine.summary(room_id)
    states: dict[str, int] = {}
    for row in room_rows:
        states[str(row.get("state"))] = states.get(str(row.get("state")), 0) + 1
    linked = [row for row in room_rows if row.get("conference_id")]
    failed_reports = [row for row in room_rows if row.get("provision_state") == "failed"]
    retryable_reports = [row for row in room_rows if row.get("provision_state") == "retrying"]
    fallback_named = [
        row for row in room_rows if (row.get("fallback") or {}).get("fallback")
    ]
    one_time = ("zoom", "google-meet", "gong")

    parts = [
        f"{len(by_kind)} Location options "
        f"({len([k for k in by_kind if k in one_time])} one-time, "
        f"{len([r for r in by_kind.values() if r.get('is_default')])} default)",
        f"{len(connected)} provider connections ({len(ready_providers)} ready, "
        f"{len(connected) - len(ready_providers)} revoked or needing re-auth)",
        f"{len(room_rows)} bookings in one room: {len(linked)} with a fresh per-booking link, "
        f"{states.get('swapped', 0)} swapped to a different tool with a previousLocation trail, "
        f"{states.get('static', 0)} static, {states.get('in-person', 0)} in-person, "
        f"{states.get('awaiting-guest', 0)} awaiting the guest",
        f"{len(failed_reports) + len(retryable_reports)} provider failure report(s) "
        f"({len(retryable_reports)} still retryable, {len(fallback_named)} with a researched "
        "fallback named)",
        f"{stats.get('locations_without_a_connection', 0)} one-time option(s) refused for a "
        "missing or lapsed connection",
        f"{1 if second_room_booking else 0} booking in a second room, so the room scoping is a row",
        f"{len(refusals)} refusals, including the mandatory-connection gate"
        if refusals
        else "0 refusals",
        reuse_refused,
    ]

    return "; ".join(part for part in parts if part) + "."
