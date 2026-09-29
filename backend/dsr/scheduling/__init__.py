"""WF-064: reschedule or cancel a meeting, and propagate the change.

The researched workflow, in the order the research states it: an attendee opens
the reschedule or cancel URL embedded in the invite, or the host works from the
Meetings Activity panel; a reschedule re-opens the same Distribution/Meeting Type
context and recomputes availability - releasing the booking's own slot, because
that is what ``bookingUidToReschedule`` is for; a cancel captures an optional
reason, releases the meeting, and applies the Meeting Type's ``Delete Event``; the
calendar event moves or is cancelled, the CRM ``Event`` is updated or deleted, the
webhooks are pushed, and an Events History row records who did it, to whom, when,
and from which of the three researched sources.

Module map, in dependency order:

``errors``
    The one error type the host registers, and the two subclasses that draw the
    line between "you typed it wrong" and "the world moved on".
``timeutil``
    Every timestamp is an aware UTC datetime, and the one boundary the research
    describes - "a meeting has happened" - is read in exactly one place.
``vocabulary``
    The researched terms, each with the sentence it comes from, plus the ones this
    build chose, which say so.
``meeting_types``
    The Distribution/Meeting Type context, its two researched settings, and its
    availability window.
``availability``
    The recomputed availability and the ``bookingUidToReschedule`` release.
``links``
    The two invite tags, their tokens, and the ``Expire Reschedule Link`` guard.
``propagation``
    Step 4: calendar, CRM, webhooks, triggers, reminders, notifications. Write-only,
    because everything it does is one transaction or nothing.
``engine``
    The flow end to end, over the audited store.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name.
"""

from __future__ import annotations

from dsr.scheduling.availability import (
    MAX_RANGE_DAYS,
    MAX_SLOTS,
    available_slots,
    explain_missing,
    find_slot,
    slot_end_for,
)
from dsr.scheduling.engine import (
    BOOKING_COLLECTION,
    CHANGE_COLLECTION,
    MEETING_TYPE_COLLECTION,
    REQUEST_COLLECTION,
    MeetingChangeEngine,
)
from dsr.scheduling.errors import (
    BookingConflict,
    LinkExpired,
    MeetingChangeError,
    MeetingNotFound,
)
from dsr.scheduling.links import (
    CANCEL_URL_TAG,
    LINK_PATH,
    RESCHEDULE_URL_TAG,
    TAG_FOR_KIND,
    LinkState,
    build_url,
    invite_body,
    mint_pair,
    mint_token,
    state_for,
)
from dsr.scheduling.meeting_types import (
    DEFAULT_DELETE_EVENT,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_EXPIRE_RESCHEDULE_LINK,
    MAX_RESCHEDULE_HORIZON_DAYS,
    delete_event,
    expire_reschedule_link,
    normalise_meeting_type,
    window_for,
)
from dsr.scheduling.propagation import (
    CALENDAR_EVENT_COLLECTION,
    CRM_EVENT_COLLECTION,
    DEFAULT_CHANNELS,
    NOTIFICATION_COLLECTION,
    TEMPLATE_FOR_CHANGE,
    TRIGGER_FOR_CHANGE,
    WEBHOOK_COLLECTION,
    channels_for,
    drop_reminders,
    move_calendar_event,
    propagate_crm_event,
    rebase_reminders,
    record_notifications,
    record_webhooks,
    webhook_envelopes,
)
from dsr.scheduling.timeutil import UTC, has_happened, iso, overlaps, parse, slot_end, utcnow
from dsr.scheduling.vocabulary import (
    ACTOR_KIND_NAMES,
    BOOKING_STATUSES,
    BOOKING_CANCELLED,
    BOOKING_LOCATION_UPDATED,
    BOOKING_NO_SHOW_UPDATED,
    BOOKING_RESCHEDULED,
    BOOKED,
    CANCEL,
    CANCELLED,
    CANCEL_SCOPE_NAMES,
    CANCEL_SCOPES,
    CANCEL_URL_TAG,
    CHANGE_CANCELLED,
    CHANGE_LOCATION_UPDATED,
    CHANGE_RESCHEDULE_REQUESTED,
    CHANGE_RESCHEDULED,
    CHANGE_TYPE_NAMES,
    CHANGE_TYPES,
    CHILICAL_HOME,
    CALENDAR_EVENT,
    CRM_SOBJECT,
    HOST,
    INTENT_NAMES,
    INTENTS,
    LIVE_STATUSES,
    NOTIFICATION_CHANNEL_NAMES,
    NOTIFICATION_TEMPLATES,
    REQUEST_RESCHEDULE,
    RESCHEDULE,
    RESCHEDULE_LINK,
    RESCHEDULE_SOURCE_NAMES,
    RESCHEDULE_SOURCES,
    RESCHEDULED,
    SCOPE_ALL,
    SCOPE_THIS,
    SYSTEM,
    TERMINAL_STATUSES,
    WORKFLOW_TRIGGERS,
    published_vocabulary,
    require_actor_kind,
    require_cancel_scope,
    require_change_type,
    require_reschedule_source,
)

__all__ = [
    "ACTOR_KIND_NAMES",
    "BOOKED",
    "CALENDAR_EVENT",
    "BOOKING_CANCELLED",
    "BOOKING_COLLECTION",
    "BOOKING_LOCATION_UPDATED",
    "BOOKING_NO_SHOW_UPDATED",
    "BOOKING_RESCHEDULED",
    "BOOKING_STATUSES",
    "BookingConflict",
    "CALENDAR_EVENT_COLLECTION",
    "CANCEL",
    "CANCELLED",
    "CANCEL_SCOPE_NAMES",
    "CANCEL_SCOPES",
    "CANCEL_URL_TAG",
    "CHANGE_CANCELLED",
    "CHANGE_COLLECTION",
    "CHANGE_LOCATION_UPDATED",
    "CHANGE_RESCHEDULE_REQUESTED",
    "CHANGE_RESCHEDULED",
    "CHANGE_TYPE_NAMES",
    "CHANGE_TYPES",
    "CHILICAL_HOME",
    "CRM_EVENT_COLLECTION",
    "CRM_SOBJECT",
    "DEFAULT_CHANNELS",
    "DEFAULT_DELETE_EVENT",
    "DEFAULT_DURATION_MINUTES",
    "DEFAULT_EXPIRE_RESCHEDULE_LINK",
    "HOST",
    "INTENTS",
    "INTENT_NAMES",
    "LINK_PATH",
    "LIVE_STATUSES",
    "LinkExpired",
    "LinkState",
    "MAX_RANGE_DAYS",
    "MAX_RESCHEDULE_HORIZON_DAYS",
    "MAX_SLOTS",
    "MEETING_TYPE_COLLECTION",
    "MeetingChangeEngine",
    "MeetingChangeError",
    "MeetingNotFound",
    "NOTIFICATION_CHANNEL_NAMES",
    "NOTIFICATION_COLLECTION",
    "NOTIFICATION_TEMPLATES",
    "REQUEST_COLLECTION",
    "REQUEST_RESCHEDULE",
    "RESCHEDULE",
    "RESCHEDULE_LINK",
    "RESCHEDULE_SOURCES",
    "RESCHEDULE_SOURCE_NAMES",
    "RESCHEDULE_URL_TAG",
    "RESCHEDULED",
    "SCOPE_ALL",
    "SCOPE_THIS",
    "SYSTEM",
    "TAG_FOR_KIND",
    "TEMPLATE_FOR_CHANGE",
    "TERMINAL_STATUSES",
    "TRIGGER_FOR_CHANGE",
    "UTC",
    "WEBHOOK_COLLECTION",
    "WORKFLOW_TRIGGERS",
    "available_slots",
    "build_url",
    "channels_for",
    "delete_event",
    "drop_reminders",
    "expire_reschedule_link",
    "explain_missing",
    "find_slot",
    "has_happened",
    "invite_body",
    "iso",
    "mint_pair",
    "mint_token",
    "move_calendar_event",
    "normalise_meeting_type",
    "overlaps",
    "parse",
    "propagate_crm_event",
    "published_vocabulary",
    "rebase_reminders",
    "record_notifications",
    "record_webhooks",
    "require_actor_kind",
    "require_cancel_scope",
    "require_change_type",
    "require_reschedule_source",
    "slot_end",
    "slot_end_for",
    "state_for",
    "utcnow",
    "webhook_envelopes",
    "window_for",
]
