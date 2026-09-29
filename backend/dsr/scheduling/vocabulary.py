"""The researched vocabulary for WF-064, published as data.

Every term the workflow uses is here with the sentence it comes from, and every
term this build had to choose for itself says so instead of borrowing a quote it
does not have. :func:`published_vocabulary` is what ``GET /api/wf-064/vocabulary``
serves, and a client renders its pickers from it rather than from a list compiled
into the page - so a deployment that widens a vocabulary ships a record here
rather than a change to shared code.

The distinction the brief insists on - sourced behaviour versus assumed behaviour
- is carried on the entries themselves. An entry either carries ``sourced_from``
(the quoted sentence) or ``inference`` (the id of the registry entry in
:mod:`dsr.scheduling.inferences` that owns the choice). A test asserts every entry
is one or the other and never neither, which is what stops an assumption from
quietly acquiring a quotation.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The five sources
# --------------------------------------------------------------------------- #

SOURCES: dict[str, str] = {
    "cal_api_reference": "https://cal.com/docs/_llms/api-v2-reference.md",
    "cal_slots": "https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type",
    "cal_webhooks": "https://cal.com/docs/developing/guides/automation/webhooks",
    "chilipiper_meeting_types": (
        "https://help.chilipiper.com/hc/en-us/articles/27994909516563-Meeting-Types-in-MyApp"
    ),
    "chilipiper_webhooks": "https://help.chilipiper.com/hc/en-us/articles/31428605286931",
}

# --------------------------------------------------------------------------- #
# The intents: the three things this workflow can be asked to do
# --------------------------------------------------------------------------- #

RESCHEDULE = "reschedule"
REQUEST_RESCHEDULE = "request_reschedule"
CANCEL = "cancel"

INTENTS: tuple[dict[str, Any], ...] = (
    {
        "intent": RESCHEDULE,
        "label": "Reschedule now",
        "cal_endpoint": "POST /v2/bookings/{bookingUid}/reschedule",
        "cancels_the_current_booking": False,
        "creates": "a new booking carrying the researched reschedule chain fields",
        "sourced_from": "Reschedule a booking or seated booking.",
        "source": "cal_api_reference",
    },
    {
        "intent": REQUEST_RESCHEDULE,
        "label": "Ask the attendee to pick a new time",
        "cal_endpoint": "POST /v2/bookings/{bookingUid}/request-reschedule",
        "cancels_the_current_booking": True,
        "creates": "a pending reschedule request the attendee completes from a link",
        "sourced_from": (
            "Request to reschedule a booking. The booking will be cancelled and the attendee "
            "will receive an email with a link to reschedule."
        ),
        "source": "cal_api_reference",
    },
    {
        "intent": CANCEL,
        "label": "Cancel the meeting",
        "cal_endpoint": "POST /v2/bookings/{bookingUid}/cancel",
        "cancels_the_current_booking": True,
        "creates": "a cancellation with an optional reason, propagated downstream",
        "sourced_from": (
            ":bookingUid can be ... of an usual booking, individual recurrence or recurring "
            "booking to cancel all recurrences."
        ),
        "source": "cal_api_reference",
    },
)

INTENT_NAMES: tuple[str, ...] = tuple(entry["intent"] for entry in INTENTS)

# --------------------------------------------------------------------------- #
# The rescheduling source
#
# Sourced exactly: "we will display who rescheduled it, to whom, when, and the
# rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)".
# --------------------------------------------------------------------------- #

CALENDAR_EVENT = "calendar_event"
CHILICAL_HOME = "chilical_home"
RESCHEDULE_LINK = "reschedule_link"

RESCHEDULE_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "source": CALENDAR_EVENT,
        "label": "Calendar event",
        "note": "the change was made in the calendar provider, not in the room",
        "sourced_from": "the rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)",
        "source_doc": "chilipiper_webhooks",
    },
    {
        "source": CHILICAL_HOME,
        "label": "ChiliCal Home",
        "note": "the host used the Meetings Activity panel",
        "sourced_from": "the rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)",
        "source_doc": "chilipiper_webhooks",
    },
    {
        "source": RESCHEDULE_LINK,
        "label": "Reschedule Link",
        "note": "the attendee opened the link in the invite body; the only source a token guards",
        "sourced_from": "the rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)",
        "source_doc": "chilipiper_webhooks",
    },
)

RESCHEDULE_SOURCE_NAMES: tuple[str, ...] = tuple(entry["source"] for entry in RESCHEDULE_SOURCES)

# --------------------------------------------------------------------------- #
# Cancellation scope
# --------------------------------------------------------------------------- #

SCOPE_THIS = "this"
SCOPE_ALL = "all"

CANCEL_SCOPES: tuple[dict[str, Any], ...] = (
    {
        "scope": SCOPE_THIS,
        "label": "This occurrence only",
        "cancels": "one booking - an ordinary booking, or a single recurrence of a series",
        "sourced_from": (
            ":bookingUid can be ... of an usual booking, individual recurrence or recurring "
            "booking to cancel all recurrences."
        ),
        "source": "cal_api_reference",
    },
    {
        "scope": SCOPE_ALL,
        "label": "Every remaining occurrence",
        "cancels": "every live booking in the recurring series the booking belongs to",
        "sourced_from": "recurring booking to cancel all recurrences.",
        "source": "cal_api_reference",
    },
)

CANCEL_SCOPE_NAMES: tuple[str, ...] = tuple(entry["scope"] for entry in CANCEL_SCOPES)

# --------------------------------------------------------------------------- #
# The webhooks the research names
# --------------------------------------------------------------------------- #

BOOKING_RESCHEDULED = "BOOKING_RESCHEDULED"
BOOKING_CANCELLED = "BOOKING_CANCELLED"
BOOKING_LOCATION_UPDATED = "BOOKING_LOCATION_UPDATED"
BOOKING_NO_SHOW_UPDATED = "BOOKING_NO_SHOW_UPDATED"

#: Pushed by this workflow's own reschedule and cancel paths.
EMITTED_WEBHOOKS: tuple[dict[str, Any], ...] = (
    {
        "webhook": BOOKING_RESCHEDULED,
        "triggered_by": RESCHEDULE,
        "carries": ["rescheduleId", "rescheduleUid", "rescheduleStartTime", "rescheduleEndTime"],
        "sourced_from": (
            '"rescheduleId": 200, "rescheduleUid": "previous-booking-unique-id", '
            '"rescheduleStartTime": "2024-01-05T14:30:00Z"'
        ),
        "source": "cal_webhooks",
    },
    {
        "webhook": BOOKING_CANCELLED,
        "triggered_by": CANCEL,
        "carries": ["cancellationReason", "cancelledByEmail"],
        "sourced_from": '"cancellationReason": "I am no longer able to attend this session."',
        "source": "cal_webhooks",
    },
    {
        "webhook": BOOKING_LOCATION_UPDATED,
        "triggered_by": RESCHEDULE,
        "carries": ["uid", "location", "rescheduleUid"],
        "sourced_from": (
            "the research lists BOOKING_LOCATION_UPDATED among the webhooks this section "
            "pushes, and does not give it a payload or a separate trigger"
        ),
        "source": "cal_webhooks",
    },
)

#: Named by the research in this section's ``apis_hit`` but produced by no step of
#: this workflow. Published so it is visibly a decision rather than an omission.
UNEMITTED_WEBHOOKS: tuple[dict[str, Any], ...] = (
    {
        "webhook": BOOKING_NO_SHOW_UPDATED,
        "emitted": False,
        "why": (
            "The research lists it among the webhooks this section pushes, but the researched "
            "user flow has no step that marks a no-show - it is about moving or releasing a "
            "meeting. A workflow here would be inventing a requirement the research does not "
            "make, and a no-show written by a reschedule would be worse than no no-show."
        ),
        "sourced_from": "BOOKING_NO_SHOW_UPDATED appears in apis_hit with no flow step behind it",
        "source": "cal_webhooks",
    },
)

#: Chili Piper's two webhooks, and the shape the research gives the cancel one.
CHILIPIPER_WEBHOOKS: tuple[dict[str, Any], ...] = (
    {
        "webhook": "Meeting Update",
        "triggered_by": RESCHEDULE,
        "carries": ["rescheduleId", "rescheduleUid", "rescheduleStartTime", "rescheduleEndTime"],
        "sourced_from": "the webhooks pushed: Meeting Update / Meeting Update + type: Deleted",
        "source": "chilipiper_webhooks",
    },
    {
        "webhook": "Meeting Update",
        "triggered_by": CANCEL,
        "carries": ["type"],
        "type_value": "Deleted",
        "sourced_from": (
            "Meeting Update + type: \"Deleted\"; For Canceled Meeting ... triggers when a user "
            "or prospect cancels the meeting from any via source"
        ),
        "source": "chilipiper_webhooks",
    },
)

#: Cal workflow triggers. ``rescheduleEvent`` / ``eventCancelled``.
WORKFLOW_TRIGGERS: tuple[dict[str, Any], ...] = (
    {
        "trigger": "rescheduleEvent",
        "fired_by": RESCHEDULE,
        "sends": "the re-send, and the reminders re-based on the new time",
        "sourced_from": "Cal workflow triggers rescheduleEvent, eventCancelled.",
        "source": "cal_api_reference",
    },
    {
        "trigger": "eventCancelled",
        "fired_by": CANCEL,
        "sends": "the cancellation notice, and the reminders dropped with the booking",
        "sourced_from": "Cal workflow triggers rescheduleEvent, eventCancelled.",
        "source": "cal_api_reference",
    },
)

# --------------------------------------------------------------------------- #
# The link tags injected into the invite body
# --------------------------------------------------------------------------- #

RESCHEDULE_URL_TAG = "CP.Meeting.RescheduleUrl"
CANCEL_URL_TAG = "CP.Meeting.CancelUrl"

LINK_TAGS: tuple[dict[str, Any], ...] = (
    {
        "tag": RESCHEDULE_URL_TAG,
        "kind": RESCHEDULE,
        "sourced_from": (
            "Chili Piper injects CP.Meeting.RescheduleUrl / CP.Meeting.CancelUrl into the "
            "Description"
        ),
        "source": "chilipiper_meeting_types",
    },
    {
        "tag": CANCEL_URL_TAG,
        "kind": CANCEL,
        "sourced_from": (
            "Chili Piper injects CP.Meeting.RescheduleUrl / CP.Meeting.CancelUrl into the "
            "Description"
        ),
        "source": "chilipiper_meeting_types",
    },
)

# --------------------------------------------------------------------------- #
# The fields the data flow names
# --------------------------------------------------------------------------- #

CHAIN_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "field": "bookingUidToReschedule",
        "on": "the slots lookup",
        "sourced_from": (
            'bookingUidToReschedule=abc123def456 - "will ensure that the original booking time '
            'appears within the returned available slots when rescheduling."'
        ),
        "source": "cal_slots",
    },
    {
        "field": "rescheduledFromUid",
        "on": "a new booking",
        "sourced_from": "new booking with rescheduledFromUid/rescheduledToUid",
        "source": "cal_api_reference",
    },
    {
        "field": "rescheduledToUid",
        "on": "the old booking",
        "sourced_from": "new booking with rescheduledFromUid/rescheduledToUid",
        "source": "cal_api_reference",
    },
    {
        "field": "rescheduleId",
        "on": "a new booking and on the BOOKING_RESCHEDULED payload",
        "sourced_from": '"rescheduleId": 200',
        "source": "cal_webhooks",
    },
    {
        "field": "rescheduleReason",
        "on": "a new booking and on the change row",
        "sourced_from": (
            "rescheduleReason is captured and shipped in the webhook, so a third party can build "
            '"reschedule churn" alerting'
        ),
        "source": "cal_webhooks",
    },
    {
        "field": "cancellationReason",
        "on": "the BOOKING_CANCELLED payload and the change row",
        "sourced_from": '"cancellationReason": "I am no longer able to attend this session."',
        "source": "cal_webhooks",
    },
    {
        "field": "cancelledByEmail",
        "on": "the BOOKING_CANCELLED payload and the change row",
        "sourced_from": "BOOKING_CANCELLED (carries cancellationReason, cancelledByEmail)",
        "source": "cal_webhooks",
    },
)

#: The three words Events History has to display for a reschedule.
HISTORY_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "field": "who",
        "stored_as": ["actor_email", "actor_kind"],
        "sourced_from": "we will display who rescheduled it",
        "source": "chilipiper_webhooks",
    },
    {
        "field": "to whom",
        "stored_as": ["to_host_email", "to_attendee_email", "to_start_at", "to_end_at"],
        "sourced_from": "we will display ... to whom",
        "source": "chilipiper_webhooks",
    },
    {
        "field": "when",
        "stored_as": ["at"],
        "sourced_from": "we will display ... when",
        "source": "chilipiper_webhooks",
    },
    {
        "field": "rescheduling source",
        "stored_as": ["reschedule_source"],
        "sourced_from": "and the rescheduling source (Calendar event, ChiliCal Home, or Reschedule Link)",
        "source": "chilipiper_webhooks",
    },
)

# --------------------------------------------------------------------------- #
# Per-Meeting-Type settings
# --------------------------------------------------------------------------- #

EXPIRE_RESCHEDULE_LINK = "expire_reschedule_link"
DELETE_EVENT = "delete_event"

MEETING_TYPE_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "setting": EXPIRE_RESCHEDULE_LINK,
        "type": "boolean",
        "effect": "the reschedule link stops working once the meeting has happened",
        "sourced_from": (
            "Expire Reschedule Link ... This setting allows you to decide if the reschedule link "
            "should expire after a meeting has happened. It can help with reporting purposes and "
            "tracking interactions with customers."
        ),
        "source": "chilipiper_meeting_types",
    },
    {
        "setting": DELETE_EVENT,
        "type": "boolean",
        "effect": (
            "on cancel, the CRM Event is deleted rather than updated to cancelled; this is the "
            "admin toggle the research names"
        ),
        "sourced_from": (
            "Delete Event - You can define if the Salesforce Event will be deleted if the "
            "meeting is canceled from the Dashboard or deleted from your calendar provider."
        ),
        "source": "chilipiper_meeting_types",
    },
)

# --------------------------------------------------------------------------- #
# Statuses, actors, change types, channels, templates
# --------------------------------------------------------------------------- #

BOOKED = "booked"
RESCHEDULED = "rescheduled"
CANCELLED = "cancelled"

BOOKING_STATUSES: tuple[dict[str, Any], ...] = (
    {
        "status": BOOKED,
        "live": True,
        "note": "the booking is holding its slot",
        "sourced_from": "old booking record - the thing a reschedule or a cancel acts on",
        "source": "cal_api_reference",
    },
    {
        "status": RESCHEDULED,
        "live": False,
        "note": "superseded by a new booking; its slot is the new booking's",
        "sourced_from": "old booking record -> new slot lookup -> new booking",
        "source": "cal_api_reference",
    },
    {
        "status": CANCELLED,
        "live": False,
        "note": "released; a cancelled booking cannot be rescheduled or cancelled again",
        "sourced_from": "the meeting is released",
        "source": "cal_api_reference",
    },
)

LIVE_STATUSES: frozenset[str] = frozenset(
    str(entry["status"]) for entry in BOOKING_STATUSES if entry["live"]
)
TERMINAL_STATUSES: frozenset[str] = frozenset(
    str(entry["status"]) for entry in BOOKING_STATUSES if not entry["live"]
)

ATTENDEE = "attendee"
HOST = "host"
SYSTEM = "system"

ACTOR_KINDS: tuple[dict[str, Any], ...] = (
    {
        "kind": ATTENDEE,
        "sourced_from": "the guest's own reschedule link, and cancelledByEmail",
        "source": "cal_webhooks",
    },
    {
        "kind": HOST,
        "sourced_from": "the host uses the Meetings Activity panel (Update Date/Time, Cancel Meeting)",
        "source": "chilipiper_webhooks",
    },
    {
        "kind": SYSTEM,
        "sourced_from": "automation: rescheduleEvent / eventCancelled fire re-sends and notifications",
        "source": "cal_api_reference",
    },
)

ACTOR_KIND_NAMES: tuple[str, ...] = tuple(entry["kind"] for entry in ACTOR_KINDS)

CHANGE_RESCHEDULED = "rescheduled"
CHANGE_RESCHEDULE_REQUESTED = "reschedule_requested"
CHANGE_CANCELLED = "cancelled"
CHANGE_LOCATION_UPDATED = "location_updated"

CHANGE_TYPES: tuple[dict[str, Any], ...] = (
    {
        "type": CHANGE_RESCHEDULED,
        "label": "Rescheduled",
        "intent": RESCHEDULE,
        "sourced_from": "an Events History audit row written for every change",
        "source": "chilipiper_webhooks",
    },
    {
        "type": CHANGE_RESCHEDULE_REQUESTED,
        "label": "Reschedule requested",
        "intent": REQUEST_RESCHEDULE,
        "sourced_from": (
            "Request to reschedule a booking. The booking will be cancelled and the attendee "
            "will receive an email with a link to reschedule."
        ),
        "source": "cal_api_reference",
    },
    {
        "type": CHANGE_CANCELLED,
        "label": "Cancelled",
        "intent": CANCEL,
        "sourced_from": "For Canceled Meeting ... triggers when a user or prospect cancels the meeting",
        "source": "chilipiper_webhooks",
    },
    {
        "type": CHANGE_LOCATION_UPDATED,
        "label": "Location updated",
        "intent": RESCHEDULE,
        "sourced_from": "BOOKING_LOCATION_UPDATED is listed among the webhooks this section pushes",
        "source": "cal_webhooks",
    },
)

CHANGE_TYPE_NAMES: tuple[str, ...] = tuple(entry["type"] for entry in CHANGE_TYPES)

CHANNEL_EMAIL = "email"
CHANNEL_SLACK = "slack"

NOTIFICATION_CHANNELS: tuple[dict[str, Any], ...] = (
    {
        "channel": CHANNEL_EMAIL,
        "sourced_from": "notification channels (email/Slack)",
        "source": "cal_api_reference",
    },
    {
        "channel": CHANNEL_SLACK,
        "sourced_from": "notification channels (email/Slack)",
        "source": "cal_api_reference",
    },
)

NOTIFICATION_CHANNEL_NAMES: tuple[str, ...] = tuple(
    entry["channel"] for entry in NOTIFICATION_CHANNELS
)

TEMPLATE_RESCHEDULED = "rescheduled"
TEMPLATE_RESCHEDULE_REQUESTED = "reschedule_requested"
TEMPLATE_CANCELLED = "cancelled"

NOTIFICATION_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template": TEMPLATE_RESCHEDULED,
        "trigger": "rescheduleEvent",
        "sourced_from": "rescheduled template in Cal workflows",
        "source": "cal_api_reference",
    },
    {
        "template": TEMPLATE_RESCHEDULE_REQUESTED,
        "trigger": "request-reschedule",
        "sourced_from": (
            "the attendee will receive an email with a link to reschedule"
        ),
        "source": "cal_api_reference",
    },
    {
        "template": TEMPLATE_CANCELLED,
        "trigger": "eventCancelled",
        "sourced_from": "reschedule/cancel templates in Cal booking emails",
        "source": "cal_api_reference",
    },
)

NOTIFICATION_TEMPLATE_NAMES: tuple[str, ...] = tuple(
    entry["template"] for entry in NOTIFICATION_TEMPLATES
)

#: The CRM object this workflow writes to, and the two operations it performs.
CRM_SOBJECT = "Event"

CRM_EVENT_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "action": "delete",
        "when": "cancel, with delete_event on",
        "sourced_from": (
            "You can define if the Salesforce Event will be deleted if the meeting is canceled "
            "from the Dashboard or deleted from your calendar provider."
        ),
        "source": "chilipiper_meeting_types",
    },
    {
        "action": "update",
        "when": "reschedule, and cancel with delete_event off",
        "sourced_from": (
            "calendar event moved/cancelled, CRM Event update/delete - the delete is the "
            "configured behaviour and the update is what happens when it is not configured"
        ),
        "source": "cal_api_reference",
    },
)

# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _require(value: Any, allowed: tuple[str, ...], label: str) -> str:
    """Reject a value outside a published vocabulary.

    Raises :class:`~dsr.scheduling.errors.MeetingChangeError` rather than a bare
    ``ValueError``, and that is load-bearing rather than cosmetic: the feature
    module registers one handler for the domain hierarchy and nothing else, so a
    plain ``ValueError`` raised here would escape it and answer 500 for what every
    other out-of-vocabulary value in this package answers 400 for. A vocabulary
    that rejects with the wrong exception type is not a vocabulary.
    """
    text = str(value or "").strip()
    if text not in allowed:
        from dsr.scheduling.errors import MeetingChangeError

        raise MeetingChangeError(f"{label} must be one of {', '.join(allowed)}; got {value!r}")
    return text


def require_intent(value: Any) -> str:
    return _require(value, INTENT_NAMES, "intent")


def require_reschedule_source(value: Any) -> str:
    return _require(value, RESCHEDULE_SOURCE_NAMES, "reschedule_source")


def require_cancel_scope(value: Any) -> str:
    return _require(value, CANCEL_SCOPE_NAMES, "scope")


def require_change_type(value: Any) -> str:
    return _require(value, CHANGE_TYPE_NAMES, "type")


def require_actor_kind(value: Any) -> str:
    return _require(value, ACTOR_KIND_NAMES, "actor_kind")


def require_channel(value: Any) -> str:
    return _require(value, NOTIFICATION_CHANNEL_NAMES, "channel")


def require_template(value: Any) -> str:
    return _require(value, NOTIFICATION_TEMPLATE_NAMES, "template")


def require_status(value: Any) -> str:
    return _require(value, tuple(str(e["status"]) for e in BOOKING_STATUSES), "status")


def published_vocabulary() -> dict[str, Any]:
    """Every researched term, served so a client renders its pickers from here."""
    return {
        "sources": dict(SOURCES),
        "intents": [dict(entry) for entry in INTENTS],
        "intent_names": list(INTENT_NAMES),
        "reschedule_sources": [dict(entry) for entry in RESCHEDULE_SOURCES],
        "reschedule_source_names": list(RESCHEDULE_SOURCE_NAMES),
        "cancel_scopes": [dict(entry) for entry in CANCEL_SCOPES],
        "cancel_scope_names": list(CANCEL_SCOPE_NAMES),
        "webhooks": [dict(entry) for entry in EMITTED_WEBHOOKS],
        "webhooks_not_emitted": [dict(entry) for entry in UNEMITTED_WEBHOOKS],
        "chilipiper_webhooks": [dict(entry) for entry in CHILIPIPER_WEBHOOKS],
        "workflow_triggers": [dict(entry) for entry in WORKFLOW_TRIGGERS],
        "link_tags": [dict(entry) for entry in LINK_TAGS],
        "chain_fields": [dict(entry) for entry in CHAIN_FIELDS],
        "history_fields": [dict(entry) for entry in HISTORY_FIELDS],
        "meeting_type_settings": [dict(entry) for entry in MEETING_TYPE_SETTINGS],
        "booking_statuses": [dict(entry) for entry in BOOKING_STATUSES],
        "actor_kinds": [dict(entry) for entry in ACTOR_KINDS],
        "actor_kind_names": list(ACTOR_KIND_NAMES),
        "change_types": [dict(entry) for entry in CHANGE_TYPES],
        "change_type_names": list(CHANGE_TYPE_NAMES),
        "notification_channels": [dict(entry) for entry in NOTIFICATION_CHANNELS],
        "notification_channel_names": list(NOTIFICATION_CHANNEL_NAMES),
        "notification_templates": [dict(entry) for entry in NOTIFICATION_TEMPLATES],
        "notification_template_names": list(NOTIFICATION_TEMPLATE_NAMES),
        "crm_sobject": CRM_SOBJECT,
        "crm_event_actions": [dict(entry) for entry in CRM_EVENT_ACTIONS],
    }
