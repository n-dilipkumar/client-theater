"""The researched vocabulary of WF-061, in one place.

Every term here comes from
``docs/research/digital-sales-room-workflows/wf/WF-061.md``, which cites two
primary sources: Chili Piper's *Reminders and Messages* article and Cal.com's
*Create a workflow* API reference. Nothing in this module is invented; the
judgement calls live in :mod:`dsr.meeting_reminders.inferences` and are served
at ``/api/wf-061/inferences`` so a reviewer can disagree with one by name.

The terms fall into five groups.

**What is being sent.** A reminder is an *email* or an *SMS*
(``user_flow`` step 2: "Chooses **Reminder Type**: `Email` or `SMS`"). The two
channels have different delivery configuration, so their options do not
overlap: email picks a *recipient* and a *sender* and a *reply-to*; SMS picks a
*from number*.

**When it fires.** Three conditions, from ``user_flow`` step 4: *Before the
Meeting*; *Before the Meeting - if the Primary Guest did not respond*; and
*After the Meeting*. The first and third carry an offset in minutes, hours,
days or weeks.

**What can stop it.** Two *advanced gates* from step 6 - *Send only if meeting
starts on* (weekday checkboxes) and *Send if the meeting was booked more than
selected timeframe* (lead time) - plus the response-status condition that is
part of the firing condition itself.

**What happened.** Three statuses (``Scheduled`` / ``Sent`` / ``Skipped``) and
five documented skip reasons, quoted verbatim in :data:`SKIP_REASONS`.

**How it maps onto Cal.com.** The research's step 8 says the same logic is
"modelled as a Cal.com **Workflow**: trigger + ordered steps with templates",
and the Cal half contributes its own trigger enum, offset unit enum, step-action
enum and step-template enum. Those live here too, in the ``CAL_*`` constants,
so :mod:`dsr.meeting_reminders.cal` projects onto them without hard-coding
strings in a function body.
"""

from __future__ import annotations

from typing import Any

from dsr.meeting_reminders.errors import ReminderError

# --------------------------------------------------------------------------- #
# Channel
# --------------------------------------------------------------------------- #

EMAIL = "email"
SMS = "sms"

#: ``user_flow`` step 2: "Chooses **Reminder Type**: `Email` or `SMS`."
CHANNELS = (EMAIL, SMS)
DEFAULT_CHANNEL = EMAIL


def require_channel(value: Any) -> str:
    """The channel, or the researched default when the caller names none.

    ``DEFAULT_CHANNEL`` is published in :func:`published_vocabulary`, and a
    default nobody can reach is a documentation lie - so an absent value
    resolves to it rather than being refused.
    """
    text = str(value or "").strip().lower() or DEFAULT_CHANNEL
    if text not in CHANNELS:
        raise ReminderError(f"channel must be one of {list(CHANNELS)}; got {value!r}")
    return text


# --------------------------------------------------------------------------- #
# Delivery configuration
# --------------------------------------------------------------------------- #
#
# The four email options and the one SMS option are ``user_flow`` step 3, quoted:
# "`Send Email To` (Primary Guest or All Guests), `Send Email From` (Host
# address / Booker address / No-reply address on a custom domain), `Send Replies
# To` (Host / Booker / Assignee(s)), and for SMS `Send SMS From` (any number or
# a local area number)."

PRIMARY_GUEST = "primary_guest"
ALL_GUESTS = "all_guests"
EMAIL_TO = (PRIMARY_GUEST, ALL_GUESTS)
DEFAULT_EMAIL_TO = PRIMARY_GUEST


def require_email_to(value: Any) -> str:
    text = str(value or DEFAULT_EMAIL_TO).strip().lower()
    if text not in EMAIL_TO:
        raise ReminderError(f"emailTo must be one of {list(EMAIL_TO)}; got {value!r}")
    return text


HOST_ADDRESS = "host"
BOOKER_ADDRESS = "booker"
NO_REPLY = "noreply"
EMAIL_FROM = (HOST_ADDRESS, BOOKER_ADDRESS, NO_REPLY)
DEFAULT_EMAIL_FROM = HOST_ADDRESS

#: "No-reply address on a custom domain" is a *domain* property, so the
#: no-reply sender is only legal once an organisation has configured one. See
#: :func:`dsr.meeting_reminders.engine.ReminderEngine.validate_reminder`.
EMAIL_FROM_REQUIRES = {NO_REPLY: "noreply_domain"}


def require_email_from(value: Any) -> str:
    text = str(value or DEFAULT_EMAIL_FROM).strip().lower()
    if text not in EMAIL_FROM:
        raise ReminderError(f"emailFrom must be one of {list(EMAIL_FROM)}; got {value!r}")
    return text


REPLY_TO_HOST = "host"
REPLY_TO_BOOKER = "booker"
REPLY_TO_ASSIGNEES = "assignees"
REPLIES_TO = (REPLY_TO_HOST, REPLY_TO_BOOKER, REPLY_TO_ASSIGNEES)
DEFAULT_REPLIES_TO = REPLY_TO_HOST


def require_replies_to(value: Any) -> str:
    text = str(value or DEFAULT_REPLIES_TO).strip().lower()
    if text not in REPLIES_TO:
        raise ReminderError(f"repliesTo must be one of {list(REPLIES_TO)}; got {value!r}")
    return text


ANY_NUMBER = "any_number"
LOCAL_AREA_NUMBER = "local_area_number"
SMS_FROM = (ANY_NUMBER, LOCAL_AREA_NUMBER)
DEFAULT_SMS_FROM = ANY_NUMBER


def require_sms_from(value: Any) -> str:
    text = str(value or DEFAULT_SMS_FROM).strip().lower()
    if text not in SMS_FROM:
        raise ReminderError(f"smsFrom must be one of {list(SMS_FROM)}; got {value!r}")
    return text


#: What each email option resolves to, read off a booking. Published so a client
#: renders the picker's hint from the server's own map rather than repeating it.
EMAIL_TO_ROLES = {PRIMARY_GUEST: "the primary guest only", ALL_GUESTS: "every guest on the booking"}
EMAIL_FROM_ROLES = {
    HOST_ADDRESS: "the host's own address",
    BOOKER_ADDRESS: "the address of whoever booked the meeting",
    NO_REPLY: "a no-reply address on the organisation's own sending domain",
}
REPLIES_TO_ROLES = {
    REPLY_TO_HOST: "the host",
    REPLY_TO_BOOKER: "the booker",
    REPLY_TO_ASSIGNEES: "every assignee on the booking",
}
SMS_FROM_ROLES = {
    ANY_NUMBER: "any number the organisation has connected",
    LOCAL_AREA_NUMBER: "a local area number, so the guest sees a familiar prefix",
}


# --------------------------------------------------------------------------- #
# The firing condition
# --------------------------------------------------------------------------- #
#
# ``user_flow`` step 4 quotes all three: "Chooses the firing condition: `Before
# the Meeting`; `Before the Meeting - if the Primary Guest did not respond`; or
# `After the Meeting` (for follow-up emails) - with a minutes/hours/days/weeks
# offset."

BEFORE = "before_meeting"
BEFORE_IF_NO_RESPONSE = "before_meeting_if_no_response"
AFTER = "after_meeting"

#: The published order is the order the vendor lists them in, which is also the
#: order an administrator reads them: pre-meeting, the conditional pre-meeting
#: variant, then the post-meeting follow-up.
CONDITIONS = (BEFORE, BEFORE_IF_NO_RESPONSE, AFTER)
DEFAULT_CONDITION = BEFORE

#: Only the two "Before" conditions are offset from the meeting *start*; the
#: "After" condition is offset from the meeting *end*, because it is a
#: follow-up. :func:`dsr.meeting_reminders.conditions.fire_at` uses this.
CONDITION_ANCHOR = {BEFORE: "start", BEFORE_IF_NO_RESPONSE: "start", AFTER: "end"}

CONDITION_DETAIL = {
    BEFORE: "sent at a set offset before the meeting starts",
    BEFORE_IF_NO_RESPONSE: (
        "sent at a set offset before the meeting starts, and only if the primary guest has "
        "not responded to the invite (Accepted or Declined)"
    ),
    AFTER: "sent at a set offset after the meeting ends - the researched use is a follow-up email",
}

#: The evidence for the conditional pre-meeting variant, quoted from the
#: research. The parenthetical matters: *Declined* counts as having responded,
#: so a guest who declines is not chased. Getting this wrong means emailing
#: someone who said no.
NO_RESPONSE_QUOTE = (
    "**Before the Meeting - if the Primary Guest did not respond:** the Reminder will be sent "
    "before the meeting at a pre-defined time, however, only if the Primary Guest has not "
    "responded to the invite (Accepted or Declined)."
)

#: The research's ``data_sources`` names the calendar invite field and its three
#: values: "calendar invite `responseStatus` (accepted/declined/needsAction)".
RESPONSE_NEEDS_ACTION = "needsAction"
RESPONSE_ACCEPTED = "accepted"
RESPONSE_DECLINED = "declined"
RESPONSE_STATUSES = (RESPONSE_ACCEPTED, RESPONSE_DECLINED, RESPONSE_NEEDS_ACTION)
DEFAULT_RESPONSE_STATUS = RESPONSE_NEEDS_ACTION

#: Both ``accepted`` and ``declined`` are *responses*, per the quote above. Only
#: ``needsAction`` - or an absent value, which is what an un-answered invite
#: looks like - leaves the guest un-responded.
RESPONSED_STATUSES = frozenset({RESPONSE_ACCEPTED, RESPONSE_DECLINED})


def require_condition(value: Any) -> str:
    """The firing condition, or :data:`DEFAULT_CONDITION` when none is named.

    Spacing and hyphens are normalised to underscores, because a form that posts
    ``"Before the Meeting"`` - the researched label, verbatim - should be
    understood rather than refused.
    """
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_") or DEFAULT_CONDITION
    if text not in CONDITIONS:
        raise ReminderError(f"condition must be one of {list(CONDITIONS)}; got {value!r}")
    return text


# --------------------------------------------------------------------------- #
# The offset
# --------------------------------------------------------------------------- #
#
# Step 4's "minutes/hours/days/weeks offset". Note this is *not* Cal's unit
# enum, which is ``hour|minute|day`` and has no week. That asymmetry is real and
# is handled in :mod:`dsr.meeting_reminders.cal`.

MINUTES = "minutes"
HOURS = "hours"
DAYS = "days"
WEEKS = "weeks"
UNITS = (MINUTES, HOURS, DAYS, WEEKS)
DEFAULT_UNIT = HOURS
DEFAULT_OFFSET = 1

UNIT_MINUTES = {MINUTES: 1, HOURS: 60, DAYS: 60 * 24, WEEKS: 60 * 24 * 7}

#: The largest offset any unit can express, as a bound on the arithmetic rather
#: than a business rule. Five years is far past any real reminder and keeps a
#: careless ``value: 10 ** 9`` from producing a datetime the platform cannot
#: represent.
MAX_OFFSET_MINUTES = 60 * 24 * 365 * 5


def require_unit(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in UNITS:
        raise ReminderError(f"offset unit must be one of {list(UNITS)}; got {value!r}")
    return text


def require_offset(value: Any, unit: Any = None) -> dict[str, Any]:
    """Validate a ``{value, unit}`` offset into its normalised form.

    Accepts a bare number, meaning ``value`` in :data:`DEFAULT_UNIT`. Rejects
    zero and negatives, because an offset of zero is not one of the four
    intervals the research names and a negative one fires *after* a "Before the
    Meeting" reminder, which is the opposite of what was asked for.
    """
    resolved_unit = require_unit(unit) if unit not in (None, "") else DEFAULT_UNIT
    if isinstance(value, bool) or value is None or value == "":
        raise ReminderError(f"offset needs a positive value; got {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ReminderError(f"offset value must be a whole number; got {value!r}") from exc
    if number <= 0:
        raise ReminderError(f"offset value must be greater than zero; got {number}")
    minutes = number * UNIT_MINUTES[resolved_unit]
    if minutes > MAX_OFFSET_MINUTES:
        raise ReminderError(
            f"offset of {number} {resolved_unit} is more than five years out; a reminder that far "
            "from a meeting is a mistake rather than a schedule"
        )
    return {"value": number, "unit": resolved_unit, "minutes": minutes}


# --------------------------------------------------------------------------- #
# The advanced gates
# --------------------------------------------------------------------------- #
#
# Step 6, both quoted in the research's evidence:
#
#   "You can see two options: **No Restriction** … **Send only if meeting starts
#   on:** When this option is selected, you will see the weekdays and checkboxes"
#
#   "While enabled, this setting determines whether the reminder should be sent
#   only within a specific timeframe. For example … the reminder will be sent
#   only if the meeting is booked one week in advance from the current booking
#   date."

#: The product's own name for the absence of the weekday gate. Published
#: because it is the literal label on the other side of the checkbox.
NO_RESTRICTION = "none"
WEEKDAY_RESTRICTION = "weekday"
LEAD_TIME_RESTRICTION = "lead_time"
RESTRICTIONS = (NO_RESTRICTION, WEEKDAY_RESTRICTION, LEAD_TIME_RESTRICTION)
DEFAULT_RESTRICTION = NO_RESTRICTION

#: Monday-first, because the researched control is a row of weekday checkboxes
#: and every vendor writes them that way. The keys are the record's own
#: vocabulary, so a team can add a day by adding a key.
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
#: ``datetime.date.weekday()`` is Monday-zero, which happens to be the same
#: order as :data:`WEEKDAYS`. Stated rather than assumed, because a silent
#: off-by-one here would fire every weekend reminder on a weekday.
WEEKDAY_INDEX = {name: index for index, name in enumerate(WEEKDAYS)}


def require_weekdays(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [part.strip() for part in values.split(",") if part.strip()]
    unknown = [str(value) for value in values if str(value).strip().lower() not in WEEKDAY_INDEX]
    if unknown:
        raise ReminderError(f"unknown weekday(s) {unknown}; published days are {list(WEEKDAYS)}")
    chosen = {str(value).strip().lower() for value in values}
    return tuple(day for day in WEEKDAYS if day in chosen)


#: ``extensibility``: "condition groups with `matches: all|any|none`". The two
#: researched gates are shipped as rules inside one group with that semantics,
#: so a third gate is a new rule kind rather than a new code path.
MATCH_ALL = "all"
MATCH_ANY = "any"
MATCH_NONE = "none"
MATCH_MODES = (MATCH_ALL, MATCH_ANY, MATCH_NONE)
DEFAULT_MATCH = MATCH_ALL


def require_match(value: Any) -> str:
    """The group's match mode, or :data:`DEFAULT_MATCH` when none is named."""
    text = str(value or "").strip().lower() or DEFAULT_MATCH
    if text not in MATCH_MODES:
        raise ReminderError(f"match must be one of {list(MATCH_MODES)}; got {value!r}")
    return text


#: The researched rule names, in the order the two gates are listed in step 6.
RULE_WEEKDAY = "weekday"
RULE_LEAD_TIME = "lead_time"
RESEARCHED_RULES = (RULE_WEEKDAY, RULE_LEAD_TIME)

#: Which documented skip reason each failing gate produces. The mapping is not
#: arbitrary and is argued in
#: :mod:`dsr.meeting_reminders.inferences`: the response-status gate belongs to
#: the firing *condition*, so it is "condition not satisfied", and the two step-6
#: gates are the product's own "restriction", whose alternative label is "No
#: Restriction".
RULE_SKIP_REASON = {
    RULE_WEEKDAY: "violated_restriction",
    RULE_LEAD_TIME: "violated_restriction",
}


# --------------------------------------------------------------------------- #
# Statuses and skip reasons
# --------------------------------------------------------------------------- #
#
# ``automations``: "statuses `Scheduled` / `Sent` / `Skipped` with documented skip
# reasons (`Reminder schedule time in the past`, `Reminder condition not
# satisfied`, `Recipient not found`, `Phone not found`, `Violated restriction`)."

SCHEDULED = "scheduled"
SENT = "sent"
SKIPPED = "skipped"
STATUSES = (SCHEDULED, SENT, SKIPPED)

#: The five reasons, keyed by a machine-safe slug and carrying the vendor's own
#: wording. The wording is the contract: it is what an administrator reads in
#: *Meetings Activity* and what a support conversation quotes, so it is stored
#: and served rather than re-worded per surface.
SCHEDULE_IN_PAST = "schedule_in_past"
CONDITION_NOT_SATISFIED = "condition_not_satisfied"
RECIPIENT_NOT_FOUND = "recipient_not_found"
PHONE_NOT_FOUND = "phone_not_found"
VIOLATED_RESTRICTION = "violated_restriction"

SKIP_REASONS: dict[str, str] = {
    SCHEDULE_IN_PAST: "Reminder schedule time in the past",
    CONDITION_NOT_SATISFIED: "Reminder condition not satisfied",
    RECIPIENT_NOT_FOUND: "Recipient not found",
    PHONE_NOT_FOUND: "Phone not found",
    VIOLATED_RESTRICTION: "Violated restriction",
}

#: Which of the two researched things each reason belongs to. Used by the
#: summary and the activity feed so a reader can tell a timing problem from a
#: configuration problem without knowing the slugs.
SKIP_REASON_KINDS: dict[str, str] = {
    SCHEDULE_IN_PAST: "timing",
    CONDITION_NOT_SATISFIED: "condition",
    RECIPIENT_NOT_FOUND: "recipient",
    PHONE_NOT_FOUND: "recipient",
    VIOLATED_RESTRICTION: "restriction",
}

NEEDS_HUMAN_REASONS = frozenset({RECIPIENT_NOT_FOUND, PHONE_NOT_FOUND})


def skip_reason(slug: str) -> str:
    """The vendor's own wording for a skip reason slug."""
    try:
        return SKIP_REASONS[slug]
    except KeyError as exc:
        raise ReminderError(f"unknown skip reason {slug!r}") from exc


def require_skip_reason(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in SKIP_REASONS:
        raise ReminderError(f"skip reason must be one of {sorted(SKIP_REASONS)}; got {value!r}")
    return text


def require_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in STATUSES:
        raise ReminderError(f"status must be one of {list(STATUSES)}; got {value!r}")
    return text


# --------------------------------------------------------------------------- #
# The message options
# --------------------------------------------------------------------------- #
#
# ``features_tools`` names four of them: "`includeCalendarEvent` (.ics in the
# email), `skipNoShowAttendees`, `autoTranslateEnabled` + `sourceLocale`,
# `phoneRequired` on the booking". All four are Cal's names, kept verbatim
# because they are the vocabulary a team already has.


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def include_calendar_event(value: Any) -> bool:
    return _flag(value)


def skip_no_show_attendees(value: Any) -> bool:
    return _flag(value)


def auto_translate_enabled(value: Any) -> bool:
    return _flag(value)


#: Cal's ``sourceLocale``. The research pairs it with ``autoTranslateEnabled``
#: and no other statement, so this build publishes BCP-47-shaped tags and does
#: not claim a translation catalogue it has not seen.
SOURCE_LOCALE = "sourceLocale"
DEFAULT_SOURCE_LOCALE = "en"


def require_source_locale(value: Any) -> str:
    text = str(value or DEFAULT_SOURCE_LOCALE).strip()
    if not text:
        return DEFAULT_SOURCE_LOCALE
    if len(text) > 35 or any(character.isspace() for character in text):
        raise ReminderError(f"sourceLocale must be a single tag with no spaces; got {value!r}")
    return text


#: The no-show flag the ``skipNoShowAttendees`` option reads. Cal calls it a
#: booking field; a booking here carries it per attendee.
NO_SHOW = "noShow"


# --------------------------------------------------------------------------- #
# The Cal.com side
# --------------------------------------------------------------------------- #
#
# Step 8: "For a portable implementation, the same logic is modelled as a Cal.com
# **Workflow**: trigger + ordered steps with templates." The Cal API reference
# supplies the enums.

#: The header the Cal API reference requires on every workflow call.
CAL_API_VERSION_HEADER = "cal-api-version"
CAL_API_VERSION = "2024-08-13"
CAL_WORKFLOWS_PATH = "/v2/workflows"

#: "allowed triggers are beforeEvent,eventCancelled,newEvent,afterEvent,
#: rescheduleEvent,afterHostsCalVideoNoShow,afterGuestsCalVideoNoShow,
#: bookingRejected,bookingRequested,bookingPaymentInitiated,bookingPaid,
#: bookingNoShowUpdated"
CAL_TRIGGERS = (
    "beforeEvent",
    "eventCancelled",
    "newEvent",
    "afterEvent",
    "rescheduleEvent",
    "afterHostsCalVideoNoShow",
    "afterGuestsCalVideoNoShow",
    "bookingRejected",
    "bookingRequested",
    "bookingPaymentInitiated",
    "bookingPaid",
    "bookingNoShowUpdated",
)

#: The two conditions this build projects onto `beforeEvent`; the third onto
#: `afterEvent`. Named rather than inlined because the conditional pre-meeting
#: variant is *not* a Cal trigger - it becomes a filter step, which is the
#: single most interesting thing about the projection.
CAL_TRIGGER_BEFORE = "beforeEvent"
CAL_TRIGGER_AFTER = "afterEvent"

CAL_OFFSET_UNITS = ("hour", "minute", "day")
CAL_UNIT_FROM = {MINUTES: "minute", HOURS: "hour", DAYS: "day"}
#: Cal has no week. One week is seven days, and the conversion is exact, so a
#: weeks offset is projected rather than refused. See the inferences registry.
CAL_UNIT_TO = {"minute": "minute", "hour": "hour", "day": "day"}
CAL_DAYS_PER_WEEK = 7

CAL_STEP_ACTIONS = (
    "email_host",
    "email_attendee",
    "email_address",
    "sms_attendee",
    "sms_number",
    "whatsapp_attendee",
    "whatsapp_number",
    "cal_ai_phone_call",
)
#: A reminder is addressed to the guest, never the host, so only the two
#: attendee actions are reachable from this workflow.
CAL_ACTION_FROM_CHANNEL = {EMAIL: "email_attendee", SMS: "sms_attendee"}

CAL_STEP_TEMPLATES = (
    "reminder",
    "custom",
    "rescheduled",
    "completed",
    "rating",
    "cancelled",
)
#: The default template per condition, overridable per reminder. Argued in the
#: inferences registry: a pre-meeting reminder on Cal's own default copy is
#: `reminder`, and anything the administrator composed is `custom`.
CAL_TEMPLATE_DEFAULT = {BEFORE: "reminder", BEFORE_IF_NO_RESPONSE: "reminder", AFTER: "completed"}

#: "step outputs also model `paths` / `filter` / `delay` / `lead_enrichment`
#: steps". Only `filter` is reachable from a reminder as researched; the other
#: three are published so a client can see what Cal models and this build does
#: not yet produce.
CAL_STEP_KINDS = ("action", "paths", "filter", "delay", "lead_enrichment")
CAL_FILTER_KINDS = (RULE_WEEKDAY, RULE_LEAD_TIME, "response_status")

#: ``apis_hit`` quotes the Cal message tokens exactly, including the two that
#: are not identifiers: ``{START_TIME_h:mma}`` contains a colon and
#: ``{EVENT_DATE_ddd, MMM D, YYYY h:mma}`` contains a comma and spaces. Any
#: regular expression for these has to accept both. See
#: :mod:`dsr.meeting_reminders.tags`.
CAL_TOKENS = (
    "{EVENT_NAME}",
    "{ORGANIZER}",
    "{ATTENDEE}",
    "{LOCATION}",
    "{MEETING_URL}",
    "{START_TIME_h:mma}",
    "{TIMEZONE}",
    "{EVENT_DATE_ddd, MMM D, YYYY h:mma}",
)

#: ``apis_hit``: Cal's booking field `attendee.phoneNumber` "becomes required
#: when SMS reminders are enabled for the event type".
CAL_PHONE_FIELD = "attendee.phoneNumber"
PHONE_REQUIRED_QUOTE = (
    '`attendee.phoneNumber` - "becomes required when SMS reminders are enabled for the event type".'
)


# --------------------------------------------------------------------------- #
# Organisational preconditions
# --------------------------------------------------------------------------- #
#
# Three statements in the research make setup a precondition rather than a
# runtime hope.

TWILIO_QUOTE = (
    "For SMS reminders … A Chili Piper Admin must connect Twilio to your company's Command Center "
    "Integrations page."
)
#: The research flags this separately, with a warning glyph: "⚠️ **Warning:**
#: Forwarding SMS replies requires your company's own Twilio account to be
#: connected in Command Center." So there are two things: *a* connection, and
#: *your own* account. Reply forwarding needs the second.
TWILIO_REPLY_FORWARDING_QUOTE = (
    "When a guest replies to an SMS reminder, Chili Piper forwards the text to your team by email. + "
    "⚠️ **Warning:** Forwarding SMS replies requires your company's own Twilio account to be "
    "connected in Command Center."
)
#: "You must have a Phone field in your **Guest Form**"
GUEST_FORM_PHONE_QUOTE = "You must have a Phone field in your **Guest Form**"

#: The two admin settings a connection record carries. ``connected`` gates
#: enabling any SMS reminder; ``own_account`` additionally gates reply
#: forwarding.
CONNECTION_CONNECTED = "connected"
CONNECTION_OWN_ACCOUNT = "own_account"


# --------------------------------------------------------------------------- #
# The whole vocabulary, for the client
# --------------------------------------------------------------------------- #


def published_vocabulary() -> dict[str, Any]:
    """Every published term, in the shape a client builds its pickers from.

    Served as data rather than compiled into the frontend, so a term added here
    reaches every client at once and the validator and the UI cannot disagree
    about what the product accepts.
    """
    return {
        "channels": list(CHANNELS),
        "default_channel": DEFAULT_CHANNEL,
        "email_to": list(EMAIL_TO),
        "email_to_roles": dict(EMAIL_TO_ROLES),
        "email_from": list(EMAIL_FROM),
        "email_from_roles": dict(EMAIL_FROM_ROLES),
        "email_from_requires": dict(EMAIL_FROM_REQUIRES),
        "replies_to": list(REPLIES_TO),
        "replies_to_roles": dict(REPLIES_TO_ROLES),
        "sms_from": list(SMS_FROM),
        "sms_from_roles": dict(SMS_FROM_ROLES),
        "conditions": list(CONDITIONS),
        "condition_detail": dict(CONDITION_DETAIL),
        "condition_anchor": dict(CONDITION_ANCHOR),
        "no_response_quote": NO_RESPONSE_QUOTE,
        "offset_units": list(UNITS),
        "default_offset": {"value": DEFAULT_OFFSET, "unit": DEFAULT_UNIT},
        "weekdays": list(WEEKDAYS),
        "restrictions": list(RESTRICTIONS),
        "match_modes": list(MATCH_MODES),
        "researched_rules": list(RESEARCHED_RULES),
        "rule_skip_reason": dict(RULE_SKIP_REASON),
        "response_statuses": list(RESPONSE_STATUSES),
        "responded_statuses": sorted(RESPONSED_STATUSES),
        "statuses": list(STATUSES),
        "skip_reasons": dict(SKIP_REASONS),
        "skip_reason_kinds": dict(SKIP_REASON_KINDS),
        "needs_human_reasons": sorted(NEEDS_HUMAN_REASONS),
        "message_options": {
            "includeCalendarEvent": "attach the .ics to the email",
            "skipNoShowAttendees": "leave out attendees flagged as no-show",
            "autoTranslateEnabled": "render the message in the guest's locale when one is known",
            SOURCE_LOCALE: "the locale the composed message is authored in",
        },
        "no_reply": {"requires": "noreply_domain", "quote": TWILIO_QUOTE},
        "sms": {
            "requires": "a connected Twilio account on the Command Center",
            "reply_forwarding_requires": "an organisation-owned Twilio account",
            "reply_forwarding_quote": TWILIO_REPLY_FORWARDING_QUOTE,
            "guest_form_quote": GUEST_FORM_PHONE_QUOTE,
        },
        "cal": {
            "api_version_header": CAL_API_VERSION_HEADER,
            "api_version": CAL_API_VERSION,
            "path": CAL_WORKFLOWS_PATH,
            "triggers": list(CAL_TRIGGERS),
            "offset_units": list(CAL_OFFSET_UNITS),
            "step_actions": list(CAL_STEP_ACTIONS),
            "step_templates": list(CAL_STEP_TEMPLATES),
            "step_kinds": list(CAL_STEP_KINDS),
            "filter_kinds": list(CAL_FILTER_KINDS),
            "tokens": list(CAL_TOKENS),
            "phone_field": CAL_PHONE_FIELD,
            "phone_required_quote": PHONE_REQUIRED_QUOTE,
        },
    }
