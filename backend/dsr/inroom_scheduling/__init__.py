"""WF-058: embed a bookable calendar inside the sales room.

The researched workflow, in the order the research states it: a builder stands up
an OAuth client and installs the embeddable booking components, the room renders a
Booker, the prospect picks a slot, optionally holds it, answers the booking fields
the event type asks, and submits - entirely in-room, with no external page.

Module map, in dependency order:

``errors``
    One base type and one subclass per refusal a caller can act on, so the page can
    say which of two different problems it is showing.
``vocabulary``
    The researched terms, the two API versions, the three metadata limits, the
    five-minute hold default, the 32-recurrence ceiling, and the four documented
    slot selectors. Nothing here is a judgement call.
``schedules``
    Working hours, slot grids and interval arithmetic. Pure: no records, no HTTP.
``availability``
    What ``GET /v2/slots`` answers - the four selectors, the "2 or more people"
    dynamic rule, and the ``bookingUidToReschedule`` exclusion.
``holds``
    ``POST /v2/slots/reservations`` and the auto-expiry that needs no user action.
``attendees``
    The ``attendee`` object, ``metadata`` and its three limits, and
    ``bookingFieldsResponses``.
``bookings``
    ``POST /v2/bookings``: standard, recurring and instant, and the
    ``BOOKING_CREATED`` event that fires on success.
``routing``
    ``GET /v2/routing-forms/slots``, which saves nothing and always falls through
    to a catch-all.
``events``
    Event types, booking fields with prefill and read-only, and the three calendar
    connects.
``embeds``
    The embed configuration, the OAuth client and the access token standing in
    front of it.
``engine``
    The flow end to end over the audited store, with ``source`` required on every
    write.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name.
"""

from __future__ import annotations

from dsr.inroom_scheduling.attendees import (
    ROOM_METADATA_KEYS,
    booking_fields_responses,
    normalise_attendee,
    reschedule_uid,
    room_metadata,
    validate_metadata,
)
from dsr.inroom_scheduling.availability import (
    UNAVAILABLE_REASONS,
    Occupancy,
    Selector,
    busy_intervals,
    first_free,
    read_selector,
    require_bookable,
    reschedule_uid as slot_reschedule_uid,
    selector_label,
    slot_grid,
)
from dsr.inroom_scheduling.bookings import (
    BOOKING_FIELDS,
    CANCELLED,
    INSTANT,
    RECURRING,
    STANDARD,
    BookingRequest,
    booking_created_event,
    booking_payload,
    cancel_payload,
    describe_window,
    read_booking_request,
    require_cancellable,
    require_team_event_for_instant,
    resolve_instant_start,
)
from dsr.inroom_scheduling.embeds import (
    OAUTH_SCOPES,
    SECRET_FIELD_NAMES,
    embed_summary,
    grant_token,
    normalise_embed,
    normalise_oauth_client,
    require_live_token,
    token_state,
)
from dsr.inroom_scheduling.engine import (
    BOOKING_COLLECTION,
    CALENDAR_COLLECTION,
    CLIENT_COLLECTION,
    DEFAULT_HORIZON_DAYS,
    DYNAMIC_DEFAULT_LENGTH_MINUTES,
    EVENT_LOG_COLLECTION,
    EVENT_TYPE_COLLECTION,
    FORM_COLLECTION,
    RESERVATION_COLLECTION,
    ROOM_BOOKING_LIMIT,
    ROOM_FIELD,
    WEBHOOK_COLLECTION,
    SchedulingEngine,
)
from dsr.inroom_scheduling.errors import (
    BookingConflict,
    BookingFieldRejected,
    EmbedConfigError,
    HoldExpired,
    HoldRequired,
    InstantNeedsTeamEvent,
    MetadataOutOfRange,
    RecurrenceOutOfRange,
    RoutingError,
    SchedulingError,
    SelectorError,
    SlotUnavailable,
    TokenExpired,
    UnknownEventType,
)
from dsr.inroom_scheduling.events import (
    BOOKING_FIELD_TYPES,
    apply_booking_fields,
    calendar_summary,
    normalise_booking_fields,
    normalise_calendar_connection,
    normalise_event_type,
)
from dsr.inroom_scheduling.holds import (
    CONSUMED,
    DEAD_STATES,
    EXPIRED,
    HELD,
    MAX_RESERVATION_DURATION_MINUTES,
    RELEASED,
    HoldView,
    expired_uids,
    live_holds,
    new_hold_payload,
    normalise_duration,
    read_hold,
    require_live,
    reservation_until,
    reservation_uid,
)
from dsr.inroom_scheduling.routing import (
    OPERATORS,
    normalise_form,
    require_operator,
    route,
    rule_matches,
    routed_slots_response,
)
from dsr.inroom_scheduling.schedules import (
    EVERY_DAY,
    candidate_starts,
    iso,
    merge_ranges,
    normalise_host,
    overlaps,
    parse_instant,
    parse_window,
    resolve_zone,
    snap_to_grid,
    working_windows,
)
from dsr.inroom_scheduling.vocabulary import (
    API_VERSION_HEADER,
    API_VERSIONS,
    ATOMS_MAINTENANCE_QUOTE,
    BOOKING_API_VERSION,
    BOOKING_CREATED,
    BOOKING_EVENTS,
    BOOKING_KINDS,
    CALENDAR_PROVIDERS,
    CONFERENCE_PROVIDERS,
    DEFAULT_RESERVATION_DURATION_MINUTES,
    EMBED_BOOKING_COMPONENTS,
    EMBED_COMPONENTS,
    EMBED_CSS_VARIABLES,
    EMBED_EVENTS,
    EVENT_TYPE_KINDS,
    IN_ROOM_QUOTE,
    MAX_RECURRENCE_COUNT,
    MAX_SLOT_WINDOW_DAYS,
    METADATA_LIMITS,
    METADATA_QUOTE,
    MIN_DYNAMIC_USERNAMES,
    NON_VIDEO_LOCATIONS,
    OAUTH_FLOW_QUOTE,
    RESCHEDULE_PARAM,
    RESERVATION_RESPONSE_FIELDS,
    RESERVATION_STATES,
    ROUTING_SLOTS_QUOTE,
    SLOT_QUERY_PARAMS,
    SLOT_SELECTORS,
    SLOTS_API_VERSION,
    published_vocabulary,
    require_calendar_provider,
    require_component,
    require_conference_provider,
    require_css_variable,
    require_event_type_kind,
    require_selector,
)

__all__ = [
    "API_VERSIONS",
    "API_VERSION_HEADER",
    "ATOMS_MAINTENANCE_QUOTE",
    "BOOKING_API_VERSION",
    "BOOKING_COLLECTION",
    "BOOKING_CREATED",
    "BOOKING_EVENTS",
    "BOOKING_FIELDS",
    "BOOKING_FIELD_TYPES",
    "BOOKING_KINDS",
    "BookingConflict",
    "BookingFieldRejected",
    "BookingRequest",
    "CALENDAR_COLLECTION",
    "CALENDAR_PROVIDERS",
    "CANCELLED",
    "CLIENT_COLLECTION",
    "CONFERENCE_PROVIDERS",
    "CONSUMED",
    "DEFAULT_HORIZON_DAYS",
    "DEFAULT_RESERVATION_DURATION_MINUTES",
    "DEAD_STATES",
    "DYNAMIC_DEFAULT_LENGTH_MINUTES",
    "EMBED_BOOKING_COMPONENTS",
    "EMBED_COMPONENTS",
    "EMBED_CSS_VARIABLES",
    "EMBED_EVENTS",
    "EmbedConfigError",
    "EVERY_DAY",
    "EVENT_LOG_COLLECTION",
    "EVENT_TYPE_COLLECTION",
    "EVENT_TYPE_KINDS",
    "EXPIRED",
    "FORM_COLLECTION",
    "HELD",
    "HoldExpired",
    "HoldRequired",
    "HoldRequired",
    "HoldView",
    "IN_ROOM_QUOTE",
    "INSTANT",
    "InstantNeedsTeamEvent",
    "MAX_RECURRENCE_COUNT",
    "MAX_RESERVATION_DURATION_MINUTES",
    "MAX_SLOT_WINDOW_DAYS",
    "METADATA_LIMITS",
    "METADATA_QUOTE",
    "MIN_DYNAMIC_USERNAMES",
    "MetadataOutOfRange",
    "NON_VIDEO_LOCATIONS",
    "OAUTH_FLOW_QUOTE",
    "OAUTH_SCOPES",
    "OPERATORS",
    "Occupancy",
    "RELEASED",
    "RECURRING",
    "RESCHEDULE_PARAM",
    "RESERVATION_COLLECTION",
    "RESERVATION_RESPONSE_FIELDS",
    "RESERVATION_STATES",
    "ROOM_BOOKING_LIMIT",
    "ROOM_FIELD",
    "ROOM_METADATA_KEYS",
    "ROUTING_SLOTS_QUOTE",
    "RecurrenceOutOfRange",
    "RoutingError",
    "SLOTS_API_VERSION",
    "SLOT_QUERY_PARAMS",
    "SLOT_SELECTORS",
    "STANDARD",
    "SECRET_FIELD_NAMES",
    "SchedulingEngine",
    "SchedulingError",
    "Selector",
    "SelectorError",
    "SlotUnavailable",
    "TokenExpired",
    "UNAVAILABLE_REASONS",
    "UnknownEventType",
    "WEBHOOK_COLLECTION",
    "apply_booking_fields",
    "booking_created_event",
    "booking_fields_responses",
    "booking_payload",
    "busy_intervals",
    "calendar_summary",
    "candidate_starts",
    "cancel_payload",
    "describe_window",
    "embed_summary",
    "expired_uids",
    "first_free",
    "grant_token",
    "iso",
    "live_holds",
    "merge_ranges",
    "new_hold_payload",
    "normalise_attendee",
    "normalise_booking_fields",
    "normalise_calendar_connection",
    "normalise_duration",
    "normalise_embed",
    "normalise_event_type",
    "normalise_form",
    "normalise_host",
    "normalise_oauth_client",
    "overlaps",
    "parse_instant",
    "parse_window",
    "published_vocabulary",
    "read_booking_request",
    "read_hold",
    "read_selector",
    "require_bookable",
    "require_calendar_provider",
    "require_cancellable",
    "require_component",
    "require_conference_provider",
    "require_css_variable",
    "require_event_type_kind",
    "require_live",
    "require_live_token",
    "require_operator",
    "require_selector",
    "require_team_event_for_instant",
    "reschedule_uid",
    "reservation_until",
    "reservation_uid",
    "resolve_instant_start",
    "resolve_zone",
    "room_metadata",
    "route",
    "routed_slots_response",
    "rule_matches",
    "selector_label",
    "slot_grid",
    "slot_reschedule_uid",
    "snap_to_grid",
    "token_state",
    "validate_metadata",
    "working_windows",
]
