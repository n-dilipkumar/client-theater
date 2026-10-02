"""The researched vocabulary of WF-058, and the limits the research puts on it.

Everything here is either quoted from
``docs/research/digital-sales-room-workflows/wf/WF-058.md`` or is a constant that
exists only to give a sourced sentence a name. Nothing in this module is a
judgement call: the judgement calls live in :mod:`dsr.inroom_scheduling.inferences`
and are served at ``/api/wf-058/inferences`` so a reviewer can disagree with a
*named* entry rather than reading a function body.

The sourced parts
-----------------

**Two API versions, one header.** ``GET /v2/slots`` is called with
``cal-api-version: 2024-09-04`` and ``POST /v2/bookings`` with
``cal-api-version: 2026-02-25``. They are different, so the header is a
per-call constant and not one global: :data:`SLOTS_API_VERSION` and
:data:`BOOKINGS_API_VERSION` are never compared to one another.

**Five minutes.** "you can also specify custom duration for how long the slot
should be reserved for (defaults to 5 minutes)". That is the default hold, and it
is the reason every reservation carries a ``reservationUntil`` computed from a
duration rather than a wall-clock deadline somebody chose.

**Thirty-two.** "supports standard, recurring (``recurrenceCount``, max 32), and
instant (``\"instant\": true``, team events only) bookings". The maximum is a
documented number, so a request for 33 is refused rather than truncated.

**Three metadata limits.** "Metadata must have at most 50 keys, each key up to 40
characters, and string values up to 500 characters." Three separate numbers, so
three separate checks: a payload can break any one of them while satisfying the
other two.

**Two or more people.** "Checking slots by usernames is used mainly for dynamic
events where there is no specific event but we just want to know when 2 or more
people are available." A ``usernames`` query of one name is not that feature, so
it is refused rather than answered as a one-person availability check.

**The routing read saves nothing.** "It will not actually save the response just
return the routed event type and slots when it can be booked." That sentence is
the whole reason :func:`dsr.inroom_scheduling.routing.routed_slots` has no write
path at all.

**Atoms are in maintenance mode.** "``@calcom/atoms`` is in maintenance mode … the
next generation of Atoms will be distributed as copy-and-paste components built on
coss ui and API v2." This is why the embed in this product is rendered by this
product's own React over API v2 and not by importing the SDK: the research says
the package is being retired *towards* exactly this shape, and a build that
imported the package would be building on the part being retired.
"""

from __future__ import annotations

from typing import Any

from dsr.inroom_scheduling.errors import SchedulingError

# --------------------------------------------------------------------------- #
# The API versions the research quotes, and the one header that carries them
# --------------------------------------------------------------------------- #

#: The header name on both endpoints. Quoted as ``cal-api-version`` in the
#: research's ``apis_hit`` list, once per endpoint.
API_VERSION_HEADER = "cal-api-version"

#: "GET /v2/slots … header `cal-api-version: 2024-09-04`."
SLOTS_API_VERSION = "2024-09-04"

#: "POST /v2/bookings — header `cal-api-version: 2026-02-25`."
BOOKING_API_VERSION = "2026-02-25"

#: The two endpoints, beside their versions, so no caller re-spells either.
API_VERSIONS: dict[str, str] = {
    "GET /v2/slots": SLOTS_API_VERSION,
    "POST /v2/slots/reservations": SLOTS_API_VERSION,
    "GET /v2/slots/reservation/{reservationUid}": SLOTS_API_VERSION,
    "PATCH /v2/slots/reservation/{reservationUid}": SLOTS_API_VERSION,
    "DELETE /v2/slots/reservation/{reservationUid}": SLOTS_API_VERSION,
    "POST /v2/bookings": BOOKING_API_VERSION,
    "GET /v2/routing-forms/slots": SLOTS_API_VERSION,
}

# --------------------------------------------------------------------------- #
# Slots
# --------------------------------------------------------------------------- #

#: "GET /v2/slots?eventTypeId=…&start=…&end=…&timeZone=… (also
#: `eventTypeSlug`+`username`+`organizationSlug`, or `usernames=alice,bob` … or
#: `teamSlug` for team events)". The four ways of naming what to ask about, in the
#: order the research lists them. A query names exactly one.
SLOT_SELECTORS: tuple[str, ...] = ("event_type_id", "username", "team_slug", "usernames")

#: The query parameters the research writes out literally.
SLOT_QUERY_PARAMS: tuple[str, ...] = ("start", "end", "timeZone", "bookingUidToReschedule")

#: "we just want to know when 2 or more people are available", so a dynamic query
#: naming one host is not a dynamic query. Enforced, not documented.
MIN_DYNAMIC_USERNAMES = 2

#: How far ahead a slot query may look. Not sourced - see the ``slot-window`` entry
#: in :mod:`dsr.inroom_scheduling.inferences` - but it must be bounded, because an
#: unbounded window over a five-day-a-week schedule is a request for a year of
#: slots and a reviewer would have to trust us to have capped it.
MAX_SLOT_WINDOW_DAYS = 62

#: ``timeZone`` is a query parameter in the research, so a caller may name any
#: IANA zone. Zones are resolved by the standard library, and an unknown one is
#: refused rather than silently treated as UTC.

# --------------------------------------------------------------------------- #
# Slot reservations
# --------------------------------------------------------------------------- #

#: "Make a slot not available for others to book for a certain period of time …
#: you can also specify custom duration for how long the slot should be reserved
#: for (defaults to 5 minutes)."
DEFAULT_RESERVATION_DURATION_MINUTES = 5

#: The three fields ``POST /v2/slots/reservations`` returns, spelled the way the
#: research spells them. The record keeps the researched names rather than
#: snake_case so a reviewer can match them against the source without a map.
RESERVATION_RESPONSE_FIELDS: tuple[str, ...] = (
    "reservationUid",
    "reservationDuration",
    "reservationUntil",
)

#: The states a hold can be in. ``expired`` is reached without anybody acting: "no
#: user action needed for the hold to expire — a reservation auto-expires after
#: ``reservationDuration``".
RESERVATION_STATES: tuple[str, ...] = ("held", "consumed", "released", "expired")

#: A hold that is neither of these is live, and a live hold is what makes a slot
#: unavailable to everybody else.
LIVE_RESERVATION_STATES: frozenset[str] = frozenset({"held"})

# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #

#: "supports standard, recurring (`recurrenceCount`, max 32), and instant
#: (`\"instant\": true`, team events only) bookings."
BOOKING_KINDS: tuple[str, ...] = ("standard", "recurring", "instant")

#: The documented ceiling on ``recurrenceCount``. A request above it is refused,
#: never truncated: silently booking 32 of the 40 meetings somebody asked for is a
#: worse answer than a 400.
MAX_RECURRENCE_COUNT = 32

#: The three documented metadata limits, each named so a refusal can say which one
#: was broken.
METADATA_LIMITS: dict[str, int] = {
    "max_keys": 50,
    "max_key_length": 40,
    "max_value_length": 500,
}

#: The sentence they come from, published so the page can show it beside the
#: numbers rather than asserting limits of its own.
METADATA_QUOTE = (
    "Metadata must have at most 50 keys, each key up to 40 characters, and string "
    "values up to 500 characters."
)

#: "``bookingUidToReschedule``: When rescheduling an existing booking, provide the
#: booking's unique identifier to exclude its time slot from busy time
#: calculations." The only field whose *purpose* the research states outright.
RESCHEDULE_PARAM = "bookingUidToReschedule"

#: The event types the research distinguishes. "team event types" and "seated
#: events" are named in ``features_tools``; ``personal`` and ``routing`` are the
#: remaining shapes the flow needs - a routing event type is what a routing form
#: routes to.
EVENT_TYPE_KINDS: tuple[str, ...] = ("personal", "team", "routing", "seated")

#: The data_sources line: "Zoom/Google Meet/Teams/Webex (conference)".
CONFERENCE_PROVIDERS: tuple[str, ...] = ("zoom", "google_meet", "ms_teams", "webex")

#: A booking whose location is a phone number or a postal address has no video
#: link, and the record says so rather than inventing one.
NON_VIDEO_LOCATIONS: tuple[str, ...] = ("phone", "address", "none")

#: "On success, ``BOOKING_CREATED`` webhook fires; downstream automations can
#: chain (see #16)." The one automation the research names for this workflow.
BOOKING_CREATED = "BOOKING_CREATED"

BOOKING_EVENTS: tuple[str, ...] = (BOOKING_CREATED,)

# --------------------------------------------------------------------------- #
# The embed
# --------------------------------------------------------------------------- #

#: "@calcom/atoms (Booker, Availability, EventType, CalendarView,
#: Google/Outlook/Apple calendar connect, Stripe Connect, PaymentForm,
#: BookerEmbed, OnboardingEmbed)". The components the research lists. The
#: calendar-connect buttons and the payment form are separate components here
#: because ``features_tools`` names them separately from CalendarView.
EMBED_COMPONENTS: tuple[str, ...] = (
    "booker",
    "booker_embed",
    "availability",
    "event_type",
    "calendar_connect",
    "payment_form",
)

#: Which of those render a meeting and which are configuration. A page that shows
#: only ``event_type`` has no way to book, and one that shows only ``booker``
#: cannot be configured in-room; the split is published so the UI can say so.
EMBED_BOOKING_COMPONENTS: tuple[str, ...] = ("booker", "booker_embed")

#: "calendar-connect buttons for Google/Outlook/Apple" and the data_sources line
#: "connected Google/Outlook/Apple calendars".
CALENDAR_PROVIDERS: tuple[str, ...] = ("google", "outlook", "apple")

#: "Embed events + CSS custom properties for styling". The research names the
#: mechanism and cites two documentation pages for it, but quotes neither the
#: event names nor the property names. This build therefore publishes *its own*
#: namespaced set rather than guessing Cal's - see the ``embed-event-names`` and
#: ``embed-css-variables`` entries in :mod:`dsr.inroom_scheduling.inferences`.
EMBED_EVENTS: tuple[str, ...] = (
    "onReady",
    "onSlotSelect",
    "onSlotHoldStart",
    "onSlotHoldEnd",
    "onBookingCreated",
    "onBookingCancelled",
)

#: The custom properties the embed accepts, all under the ``--dsr-`` prefix for
#: the same reason the events are namespaced.
EMBED_CSS_VARIABLES: tuple[str, ...] = (
    "--dsr-cal-accent",
    "--dsr-cal-background",
    "--dsr-cal-foreground",
    "--dsr-cal-border",
    "--dsr-cal-radius",
    "--dsr-cal-font",
    "--dsr-cal-slot-height",
)

#: "`@calcom/atoms` is in maintenance mode … the next generation of Atoms will be
#: distributed as copy-and-paste components built on coss ui and API v2." The
#: decision this build acts on: the embed is rendered here, over API v2.
ATOMS_MAINTENANCE_QUOTE = (
    "`@calcom/atoms` is in maintenance mode … the next generation of Atoms will be "
    "distributed as copy-and-paste components built on coss ui and API v2."
)

#: Step 1 of the user flow: "Builder installs the embeddable booking components and
#: stands up an OAuth client so the app can act on behalf of a scheduling user."
OAUTH_FLOW_QUOTE = (
    "Builder installs the embeddable booking components and stands up an OAuth "
    "client so the app can act on behalf of a scheduling user."
)

#: Step 5: "The prospect books entirely in-room; no Chili-Piper-like external page is
#: shown." The reason every write route in this feature is room-scoped.
IN_ROOM_QUOTE = "The prospect books entirely in-room; no Chili-Piper-like external page is shown."

# --------------------------------------------------------------------------- #
# Routing forms
# --------------------------------------------------------------------------- #

#: "GET /v2/routing-forms/slots (Calculate slots based on routing form response)".
ROUTING_SLOTS_QUOTE = (
    "It will not actually save the response just return the routed event type and "
    "slots when it can be booked."
)


# --------------------------------------------------------------------------- #
# The published set
# --------------------------------------------------------------------------- #


def published_vocabulary() -> dict[str, Any]:
    """Everything the page renders a picker or an explanation from.

    Served as data rather than compiled into the frontend, so a term added here
    reaches every client at once and the frontend ships no second list to keep in
    step with this one.
    """
    return {
        "api_versions": dict(API_VERSIONS),
        "api_version_header": API_VERSION_HEADER,
        "slot_selectors": list(SLOT_SELECTORS),
        "slot_query_params": list(SLOT_QUERY_PARAMS),
        "min_dynamic_usernames": MIN_DYNAMIC_USERNAMES,
        "max_slot_window_days": MAX_SLOT_WINDOW_DAYS,
        "reservation_response_fields": list(RESERVATION_RESPONSE_FIELDS),
        "reservation_states": list(RESERVATION_STATES),
        "default_reservation_duration_minutes": DEFAULT_RESERVATION_DURATION_MINUTES,
        "booking_kinds": list(BOOKING_KINDS),
        "max_recurrence_count": MAX_RECURRENCE_COUNT,
        "metadata_limits": dict(METADATA_LIMITS),
        "metadata_quote": METADATA_QUOTE,
        "reschedule_param": RESCHEDULE_PARAM,
        "event_type_kinds": list(EVENT_TYPE_KINDS),
        "conference_providers": list(CONFERENCE_PROVIDERS),
        "non_video_locations": list(NON_VIDEO_LOCATIONS),
        "booking_events": list(BOOKING_EVENTS),
        "embed_components": list(EMBED_COMPONENTS),
        "embed_booking_components": list(EMBED_BOOKING_COMPONENTS),
        "embed_events": list(EMBED_EVENTS),
        "embed_css_variables": list(EMBED_CSS_VARIABLES),
        "calendar_providers": list(CALENDAR_PROVIDERS),
        "quotes": {
            "atoms_maintenance": ATOMS_MAINTENANCE_QUOTE,
            "oauth_flow": OAUTH_FLOW_QUOTE,
            "in_room": IN_ROOM_QUOTE,
            "routing_slots": ROUTING_SLOTS_QUOTE,
        },
    }


def require_component(name: str) -> str:
    """The embed component called ``name``, or a refusal naming the set."""
    if name not in EMBED_COMPONENTS:
        raise SchedulingError(
            f"unknown embed component {name!r}; the published set is " + ", ".join(EMBED_COMPONENTS)
        )
    return name


def require_css_variable(name: str) -> str:
    """The embed custom property called ``name``, or a refusal naming the set."""
    if name not in EMBED_CSS_VARIABLES:
        raise SchedulingError(
            f"unknown embed custom property {name!r}; the published set is "
            + ", ".join(EMBED_CSS_VARIABLES)
        )
    return name


def require_selector(name: str) -> str:
    """The slot selector kind called ``name``, or a refusal naming the set."""
    if name not in SLOT_SELECTORS:
        raise SchedulingError(
            f"unknown slot selector {name!r}; a query names exactly one of "
            + ", ".join(SLOT_SELECTORS)
        )
    return name


def require_calendar_provider(name: str) -> str:
    """The calendar provider called ``name``, or a refusal naming the set."""
    if name not in CALENDAR_PROVIDERS:
        raise SchedulingError(
            f"unknown calendar provider {name!r}; the research names "
            + ", ".join(CALENDAR_PROVIDERS)
        )
    return name


def require_conference_provider(name: str) -> str:
    """The conference provider called ``name``, or a refusal naming the set."""
    if name not in CONFERENCE_PROVIDERS:
        raise SchedulingError(
            f"unknown conference provider {name!r}; the research names "
            + ", ".join(CONFERENCE_PROVIDERS)
        )
    return name


def require_event_type_kind(name: str) -> str:
    """The event type kind called ``name``, or a refusal naming the set."""
    if name not in EVENT_TYPE_KINDS:
        raise SchedulingError(
            f"unknown event type kind {name!r}; the published set is " + ", ".join(EVENT_TYPE_KINDS)
        )
    return name
