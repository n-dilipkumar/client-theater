"""WF-107: the researched vocabulary of chasing and reroute, and its only normalisation.

Every term here is one the research names, with the sentence it comes from kept next to
it. :func:`published_vocabulary` serves the lot at ``/api/wf-107/vocabulary`` so the
pickers on the page render from the same source the validator enforces against, and a
term added in one place reaches every client at once.

The four sources the research cites, and what each contributes:

* ``7155449-automations-of-snoozed-conversations`` - the Wait and Snooze actions, the
  configurable interruption events, and the rule that a workflow containing either takes
  precedence over the global auto-close setting.
* ``7434613-how-to-trigger-a-workflow`` - the two purely time-based triggers, the
  inactivity timer, the duration bounds, the "once per customer message" limit, the
  first-message anchor for the teammate trigger, and the API-created exception.
* ``api.intercom.io/messages`` - ``create_conversation_without_contact_reply`` and its
  ``false`` default.
* ``api.intercom.io/tickets`` - the tag and reply endpoints the Close/Tag/Assign
  actions stand in for.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Quoted evidence
# --------------------------------------------------------------------------- #
#
# Kept verbatim and complete enough to check a decision against. Each constant is
# referenced by the rule it justifies, so a change to a rule that drops its quote is
# visible in the diff.


#: "Set trigger timer to 10 minutes - meaning that the workflow will only trigger 10
#: minutes after there's been no response from the customer."
INACTIVITY_TIMER = (
    "Set trigger timer to 10 minutes - meaning that the workflow will only trigger 10 "
    "minutes after there's been no response from the customer."
)

#: "The duration must be longer than 30 seconds and shorter than 14 days."
DURATION_BOUNDS = "The duration must be longer than 30 seconds and shorter than 14 days."

#: "is evaluated against customer's first message. This means if the customer sends 3
#: messages in a row, the timer will be set against their first message, not last."
FIRST_MESSAGE_ANCHOR = (
    "is evaluated against customer's first message. This means if the customer sends 3 "
    "messages in a row, the timer will be set against their first message, not last."
)

#: The research's data flow, which fixes the other trigger's clock: "Last-message
#: timestamp on the Conversation object -> inactivity elapsed -> trigger fires".
LAST_MESSAGE_ANCHOR = (
    "Last-message timestamp on the Conversation object -> inactivity elapsed -> trigger fires"
)

#: The once-per-message limit, as the automation constraints record it: the workflow
#: "can only trigger once per customer message".
ONCE_PER_MESSAGE = "can only trigger once per customer message"

#: "This workflow won't trigger for conversations created via our REST API."
API_CREATED_EXEMPT = "This workflow won't trigger for conversations created via our REST API."

#: "Any workflow containing a Wait or Snooze action will take precedence", over the
#: global auto-close setting offered "under Settings > AI & Automation".
WAIT_PRECEDENCE = "Any workflow containing a Wait or Snooze action will take precedence"

#: "configure the duration and which interruption events cancel the wait (teammate and
#: customer messages)". Named with the ``_QUOTE`` suffix because
#: :data:`INTERRUPTION_EVENTS` is the tuple of the two events themselves.
INTERRUPTION_EVENTS_QUOTE = (
    "configure the duration and which interruption events cancel the wait (teammate and "
    "customer messages)"
)

#: "create_conversation_without_contact_reply - Whether a conversation should be opened
#: in the inbox for the message without the contact replying. Defaults to false if not
#: provided."
CREATE_WITHOUT_CONTACT_REPLY = (
    "create_conversation_without_contact_reply - Whether a conversation should be opened in "
    "the inbox for the message without the contact replying. Defaults to false if not "
    "provided."
)

#: "Then add an action to **Assign conversation** to reroute the conversation to the
#: desired Inbox."
ASSIGN_CONVERSATION = (
    "Then add an action to Assign conversation to reroute the conversation to the desired Inbox."
)

#: Step 6 of the flow: the reroute workflow adds "a message or a **Show expected reply
#: time** step (uses office hours), then **Mark as priority** + **Tag conversation**
#: ('delayed response') and **Assign conversation** to another inbox."
REROUTE_STEPS = (
    "a message or a Show expected reply time step (uses office hours), then Mark as "
    "priority + Tag conversation ('delayed response') and Assign conversation to another inbox"
)

#: Step 5: "Add a closing message block, then a **Close** conversation action, then
#: **Tag conversation**."
CLOSE_SEQUENCE = (
    "Add a closing message block, then a Close conversation action, then Tag conversation."
)

#: The data flow's own statement that a message "creating a conversation if none
#: exists", which is what ``create_conversation_without_contact_reply`` stands for.
OPENS_WITHOUT_CONTACT_REPLY = (
    "message part written to the conversation (creating a conversation if none exists)"
)

#: The webhooks the research lists as this workflow's event sources, kept so the
#: vocabulary can name the surface an activity came from without inventing a mapping.
DOCUMENTED_WEBHOOKS = (
    "conversation.admin.snoozed",
    "conversation.admin.unsnoozed",
    "conversation.admin.closed",
    "conversation.user.replied",
    "conversation.read",
)


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# ``wf107_``-prefixed so no other feature can collide with one by name, and so the
# ``collections()`` route shows at a glance which workflow owns a set of rows.

#: One row per conversation: the object the research's data flow starts from. It holds
#: the origin, the assigned inbox, the tags, the priority flag and the state.
CONVERSATIONS = "wf107_conversation"

#: One row per message. The research models these as conversation parts: "message part
#: written to the conversation", "Tag written to the conversation part". One row per
#: part is what lets a test assert which instant the anchor rules compare.
PARTS = "wf107_conversation_part"

#: One row per configured trigger, draft or live.
TRIGGERS = "wf107_trigger"

#: One row per firing of a trigger. A run holds the step cursor and the wait state, so
#: "the Wait/Snooze timer runs and can be interrupted" is a field rather than a
#: background process nobody can observe.
RUNS = "wf107_run"

#: The conversation event log. "Close action transitions the conversation to a closed
#: state -> Tag written to the conversation part -> assignment moves the conversation
#: between teams" is three events a reviewer reads in order.
ACTIVITY = "wf107_activity"

#: The derived office-hours schedule. Derived rather than researched: see
#: :mod:`dsr.conversation_chase.inferences`.
OFFICE_HOURS = "wf107_office_hours"


# --------------------------------------------------------------------------- #
# The payload-side room reference
# --------------------------------------------------------------------------- #
#
# Not ``room_id``: that key is part of the record envelope and ``AuditedDatabase``
# strips it out of ``data`` before the dynamic index is built, so a payload that stored
# its room there would be unfilterable through ``find()``.

ROOM_REF = "room_ref"


# --------------------------------------------------------------------------- #
# The two triggers
# --------------------------------------------------------------------------- #

#: Step 1 of the flow: **"If customer has been unresponsive"**.
CUSTOMER_IDLE = "customer_idle"

#: Step 6 of the flow: **"If teammate has been unresponsive"**.
TEAMMATE_IDLE = "teammate_idle"

TRIGGER_KINDS: tuple[str, ...] = (CUSTOMER_IDLE, TEAMMATE_IDLE)

TRIGGER_KIND_LABELS: dict[str, str] = {
    CUSTOMER_IDLE: "If customer has been unresponsive",
    TEAMMATE_IDLE: "If teammate has been unresponsive",
}

#: The research calls both "purely time-based, automatic triggers (no seller action)",
#: which is why neither needs a rep to press anything before it can fire.
PURELY_TIME_BASED = "Two purely time-based, automatic triggers (no seller action)."

DEFAULT_TRIGGER_KIND = CUSTOMER_IDLE


def require_trigger_kind(value: Any) -> str:
    """Normalise and validate a trigger kind.

    Both kinds are checked with the same function so a caller cannot write
    ``customer_idle`` on one route and ``customer-idle`` on another and have the two
    records disagree about what they are.
    """

    text = _slug(value) or DEFAULT_TRIGGER_KIND
    if text not in TRIGGER_KINDS:
        raise ValueError(
            f"unknown trigger kind {value!r}; this workflow offers {', '.join(TRIGGER_KINDS)}"
        )
    return text


# --------------------------------------------------------------------------- #
# The anchor each trigger reads
# --------------------------------------------------------------------------- #
#
# The two triggers anchor differently and the research says so in both places. This is
# the one place that choice is made.

#: The teammate trigger's clock: "is evaluated against customer's first message".
ANCHOR_FIRST_CUSTOMER_MESSAGE = "first_customer_message"

#: The customer trigger's clock: "Last-message timestamp on the Conversation object".
ANCHOR_LAST_ACTIVITY = "last_activity"

TRIGGER_ANCHORS: dict[str, str] = {
    CUSTOMER_IDLE: ANCHOR_LAST_ACTIVITY,
    TEAMMATE_IDLE: ANCHOR_FIRST_CUSTOMER_MESSAGE,
}

ANCHOR_LABELS: dict[str, str] = {
    ANCHOR_FIRST_CUSTOMER_MESSAGE: "Customer's first message",
    ANCHOR_LAST_ACTIVITY: "Last message of any kind",
}

#: The first-message anchor exists so a burst does not reset the clock three times.
#: The research's own example: three customer messages in a row anchor to the first.
FIRST_MESSAGE_RULE = (
    "if the customer sends 3 messages in a row, the timer will be set against their first "
    "message, not last"
)


def anchor_for(kind: str) -> str:
    """Which timestamp a trigger kind measures its inactivity from."""

    return TRIGGER_ANCHORS[require_trigger_kind(kind)]


# --------------------------------------------------------------------------- #
# Durations
# --------------------------------------------------------------------------- #

#: "The duration must be longer than 30 seconds and shorter than 14 days." Both bounds
#: are exclusive, so 30 exactly and 14 days exactly are both refused.
MIN_DURATION_SECONDS = 30
MAX_DURATION_SECONDS = 14 * 24 * 60 * 60

#: The research's worked example: "Set trigger timer to 10 minutes". Used as the
#: default so the shipped default is the number the spec names rather than a round
#: figure chosen here.
DEFAULT_TRIGGER_SECONDS = 10 * 60

#: The research names no default for the Wait block, so it inherits the trigger's own
#: timer length: the Wait is for "as long as the buyer was given to reply".
DEFAULT_WAIT_SECONDS = DEFAULT_TRIGGER_SECONDS

DURATION_BOUNDS_QUOTE = DURATION_BOUNDS


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

#: Step 3: a **message** block.
STEP_MESSAGE = "message"

#: Step 4: a **Wait** block.
STEP_WAIT = "wait"

#: Step 5's counterpart in step 4 of the second workflow: the **Snooze** action.
STEP_SNOOZE = "snooze"

#: Step 5: a closing message block, written before the Close action so the buyer sees
#: why the conversation ended.
STEP_CLOSE_MESSAGE = "close_message"

#: Step 6: the **Show expected reply time** step, "uses office hours".
STEP_SHOW_EXPECTED_REPLY_TIME = "show_expected_reply_time"

#: Step 6: **Mark as priority**.
STEP_MARK_PRIORITY = "mark_priority"

#: Step 5 and step 6: **Tag conversation**. Step 6 names the tag: 'delayed response'.
STEP_TAG = "tag"

#: Step 5: a **Close** conversation action.
STEP_CLOSE = "close"

#: Step 6: **Assign conversation** to another inbox.
STEP_ASSIGN = "assign"

STEP_KINDS: tuple[str, ...] = (
    STEP_MESSAGE,
    STEP_WAIT,
    STEP_SNOOZE,
    STEP_CLOSE_MESSAGE,
    STEP_SHOW_EXPECTED_REPLY_TIME,
    STEP_MARK_PRIORITY,
    STEP_TAG,
    STEP_CLOSE,
    STEP_ASSIGN,
)

STEP_LABELS: dict[str, str] = {
    STEP_MESSAGE: "Message",
    STEP_WAIT: "Wait",
    STEP_SNOOZE: "Snooze",
    STEP_CLOSE_MESSAGE: "Closing message",
    STEP_SHOW_EXPECTED_REPLY_TIME: "Show expected reply time",
    STEP_MARK_PRIORITY: "Mark as priority",
    STEP_TAG: "Tag conversation",
    STEP_CLOSE: "Close conversation",
    STEP_ASSIGN: "Assign conversation",
}

#: Steps that carry a ``duration_seconds`` of their own and are therefore bound-checked
#: separately from the trigger's timer. A Wait and a Snooze hold the run while it
#: elapses; **Show expected reply time** only reads the number, which is why it is in
#: this set and not in :data:`HOLDING_STEPS`.
TIMED_STEPS: frozenset[str] = frozenset({STEP_WAIT, STEP_SNOOZE})

#: Every step that carries a duration. The superset, so validation and the trigger-edit
#: re-check both read one tuple rather than two that can drift.
DURATION_STEPS: frozenset[str] = TIMED_STEPS | {STEP_SHOW_EXPECTED_REPLY_TIME}

#: Steps that stop the conversation being swept by the two triggers.
TERMINAL_STEPS: frozenset[str] = frozenset({STEP_CLOSE})

#: Steps that hold the run until a later advance. "the Wait/Snooze timer runs", so the
#: run cannot finish in the same advance that starts it.
HOLDING_STEPS: frozenset[str] = frozenset({STEP_WAIT, STEP_SNOOZE})

#: "Any workflow containing a Wait or Snooze action will take precedence" over the
#: global auto-close setting. Read off the step list, not stored as a flag, so a
#: trigger cannot claim precedence it does not have.
PRECEDENCE_STEPS: frozenset[str] = frozenset({STEP_WAIT, STEP_SNOOZE})

PRECEDENCE_QUOTE = WAIT_PRECEDENCE


def require_step_kind(value: Any) -> str:
    text = _slug(value)
    if text not in STEP_KINDS:
        raise ValueError(f"unknown step kind {value!r}; the builder offers {', '.join(STEP_KINDS)}")
    return text


# --------------------------------------------------------------------------- #
# Interruption events
# --------------------------------------------------------------------------- #

#: "which interruption events cancel the wait (teammate and customer messages)". Only
#: those two are named, so only those two are accepted: accepting a third would invent
#: a cancellation the research does not describe.
INTERRUPTION_CUSTOMER_MESSAGE = "customer_message"
INTERRUPTION_TEAMMATE_MESSAGE = "teammate_message"

INTERRUPTION_EVENTS: tuple[str, ...] = (
    INTERRUPTION_CUSTOMER_MESSAGE,
    INTERRUPTION_TEAMMATE_MESSAGE,
)

INTERRUPTION_LABELS: dict[str, str] = {
    INTERRUPTION_CUSTOMER_MESSAGE: "Customer message",
    INTERRUPTION_TEAMMATE_MESSAGE: "Teammate message",
}

INTERRUPTION_QUOTE = INTERRUPTION_EVENTS

#: Which part author kind each interruption event is triggered by. The mapping is one to
#: one because the research names two events and two message kinds, and a message from a
#: third party cancelling a teammate-idle wait would be a rule nothing sources.
#:
#: Read the other way round by :func:`event_for_author_kind`, because the rule asks "a
#: part of kind X arrived, and the step listed event Y -- do those match".
AUTHOR_KIND_FOR_EVENT: dict[str, str] = {
    INTERRUPTION_CUSTOMER_MESSAGE: "customer",
    INTERRUPTION_TEAMMATE_MESSAGE: "teammate",
}


def event_for_author_kind(author_kind: str) -> str | None:
    """The interruption event a message from this author raises, if any.

    The inverse of :data:`AUTHOR_KIND_FOR_EVENT`. ``None`` for ``system``, which is the
    point of the function: the workflow's own message block raises no event, so it cannot
    cancel the wait it is waiting on.
    """

    for event, author in AUTHOR_KIND_FOR_EVENT.items():
        if author == _slug(author_kind):
            return event
    return None


# --------------------------------------------------------------------------- #
# Who wrote a part
# --------------------------------------------------------------------------- #

AUTHOR_CUSTOMER = "customer"
AUTHOR_TEAMMATE = "teammate"

#: The workflow's own writes. Not a third party: the research's message blocks are the
#: workflow speaking, and they must not cancel a wait the workflow itself is holding.
AUTHOR_SYSTEM = "system"

AUTHOR_KINDS: tuple[str, ...] = (AUTHOR_CUSTOMER, AUTHOR_TEAMMATE, AUTHOR_SYSTEM)

AUTHOR_LABELS: dict[str, str] = {
    AUTHOR_CUSTOMER: "Customer",
    AUTHOR_TEAMMATE: "Teammate",
    AUTHOR_SYSTEM: "Workflow",
}


def require_author_kind(value: Any) -> str:
    text = _slug(value) or AUTHOR_CUSTOMER
    if text not in AUTHOR_KINDS:
        raise ValueError(
            f"unknown author kind {value!r}; parts are written by {', '.join(AUTHOR_KINDS)}"
        )
    return text


# --------------------------------------------------------------------------- #
# Conversation origin
# --------------------------------------------------------------------------- #
#
# The API-created exception needs a flag to be an exception rather than an omission.

#: A conversation a buyer opened from the inbox.
ORIGIN_INBOX = "inbox"

#: A conversation created through the REST API, which "won't trigger" this workflow.
ORIGIN_API = "api"

ORIGINS: tuple[str, ...] = (ORIGIN_INBOX, ORIGIN_API)

ORIGIN_LABELS: dict[str, str] = {
    ORIGIN_INBOX: "Opened in the inbox",
    ORIGIN_API: "Created via the REST API",
}

#: The one origin the triggers never fire on.
API_CREATED_ORIGIN = ORIGIN_API

API_CREATED_QUOTE = API_CREATED_EXEMPT


def require_origin(value: Any) -> str:
    text = _slug(value) or ORIGIN_INBOX
    if text not in ORIGINS:
        raise ValueError(f"unknown origin {value!r}; a conversation is one of {', '.join(ORIGINS)}")
    return text


# --------------------------------------------------------------------------- #
# create_conversation_without_contact_reply
# --------------------------------------------------------------------------- #
#
# The spec: "Defaults to false if not provided." Made a module constant and passed
# explicitly on every path, so the default is a decision this module states rather than
# a behaviour inherited from a caller that may or may not send the flag.

CREATE_WITHOUT_CONTACT_REPLY = "create_conversation_without_contact_reply"

CREATE_WITHOUT_CONTACT_REPLY_DEFAULT = False

CREATE_WITHOUT_CONTACT_REPLY_QUOTE = CREATE_WITHOUT_CONTACT_REPLY


# --------------------------------------------------------------------------- #
# Conversation state
# --------------------------------------------------------------------------- #

STATE_OPEN = "open"
STATE_SNOOZED = "snoozed"
STATE_CLOSED = "closed"

CONVERSATION_STATES: tuple[str, ...] = (STATE_OPEN, STATE_SNOOZED, STATE_CLOSED)

CONVERSATION_STATE_LABELS: dict[str, str] = {
    STATE_OPEN: "Open",
    STATE_SNOOZED: "Snoozed",
    STATE_CLOSED: "Closed",
}

#: A snoozed conversation is paused, not finished. It is skipped by the sweep for the
#: same reason the research's Snooze automation exists, and "Note: If an SLA is missed
#: while a conversation is snoozed, the conversation will be automatically unsnoozed
#: and re-opened" is the same idea in the same source family.
SNOOZED_QUOTE = (
    "If an SLA is missed while a conversation is snoozed, the conversation will be "
    "automatically unsnoozed and re-opened."
)

#: Only an open conversation is swept. A closed one is history and a snoozed one is
#: deliberately paused.
SWEEPABLE_STATES: frozenset[str] = frozenset({STATE_OPEN})

DEFAULT_STATE = STATE_OPEN


def require_state(value: Any) -> str:
    text = _slug(value) or DEFAULT_STATE
    if text not in CONVERSATION_STATES:
        raise ValueError(
            f"unknown conversation state {value!r}; a conversation is one of "
            f"{', '.join(CONVERSATION_STATES)}"
        )
    return text


# --------------------------------------------------------------------------- #
# Run state
# --------------------------------------------------------------------------- #

RUN_RUNNING = "running"
RUN_WAITING = "waiting"
RUN_INTERRUPTED = "interrupted"
RUN_FINISHED = "finished"

RUN_STATES: tuple[str, ...] = (RUN_RUNNING, RUN_WAITING, RUN_INTERRUPTED, RUN_FINISHED)

RUN_STATE_LABELS: dict[str, str] = {
    RUN_RUNNING: "Running",
    RUN_WAITING: "Waiting",
    RUN_INTERRUPTED: "Interrupted",
    RUN_FINISHED: "Finished",
}

#: A run in one of these has no next step to take, so advancing it is a conflict
#: rather than a no-op.
CLOSED_RUN_STATES: frozenset[str] = frozenset({RUN_INTERRUPTED, RUN_FINISHED})

#: The research says an interruption "cancels the wait", not "pauses" it. The run is
#: therefore not resumable, which is why there is no ``resume`` step kind.
INTERRUPTED_IS_TERMINAL = True


# --------------------------------------------------------------------------- #
# Tags, priority and inboxes
# --------------------------------------------------------------------------- #

#: Step 6 names the tag the reroute writes: 'delayed response'. Published as a constant
#: so a test and the page's tag picker read the same string.
DELAYED_RESPONSE_TAG = "delayed response"

#: The tags a workflow may write. Deliberately a closed set: the research names one tag
#: and the vocabulary should not grow a picker the spec does not justify.
RESERVED_TAGS: tuple[str, ...] = (DELAYED_RESPONSE_TAG,)

DEFAULT_TAG = DELAYED_RESPONSE_TAG


def require_tag(value: Any) -> str:
    """Normalise a tag name.

    Kept as free text rather than a closed list: the research names one tag for the
    reroute workflow and says nothing about restricting the Close-then-Tag sequence of
    the first workflow. Rejecting an untagged tag name would refuse a documented step.
    An empty tag is refused, because a tag with no name is not a tag.
    """

    text = " ".join(str(value or "").split()).lower()
    if not text:
        raise ValueError("a tag step needs a tag name")
    return text


# --------------------------------------------------------------------------- #
# Channels, audience, scheduling, goal
# --------------------------------------------------------------------------- #
#
# Step 2: "Set the inactivity timer (e.g. 10 minutes) and the trigger's `Channels`,
# `Audience`, `Scheduling`, `Goal`." The research names the four fields and gives no
# vocabulary for three of them, so the values below are this build's and are published
# so the picker and the validator agree.

CHANNEL_MESSENGER = "messenger"
CHANNEL_EMAIL = "email"
CHANNEL_API = "api"

CHANNELS: tuple[str, ...] = (CHANNEL_MESSENGER, CHANNEL_EMAIL, CHANNEL_API)

CHANNEL_LABELS: dict[str, str] = {
    CHANNEL_MESSENGER: "Messenger",
    CHANNEL_EMAIL: "Email",
    CHANNEL_API: "API",
}

DEFAULT_CHANNELS: tuple[str, ...] = (CHANNEL_MESSENGER,)

#: Step 2's four named fields, so the page renders the research's own labels.
TRIGGER_FIELDS: tuple[str, ...] = ("channels", "audience", "scheduling", "goal")

TRIGGER_FIELD_LABELS: dict[str, str] = {
    "duration_seconds": "Inactivity timer",
    "channels": "Channels",
    "audience": "Audience",
    "scheduling": "Scheduling",
    "goal": "Goal",
}


# --------------------------------------------------------------------------- #
# Office hours
# --------------------------------------------------------------------------- #
#
# Derived, not researched. The spec names "Office hours configuration" as a data source
# and sources no model, so this is one model, stated here and recorded in the inferences
# module. Seven weekday rows, each closed or a pair of minutes.

#: Monday to Sunday, matching the research's own "Monday to Friday" office-hours
#: worked example.
WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

#: The research corpus's worked example is "if your office hours are set to 9am - 6pm",
#: which is why the derived default is 09:00 to 18:00 rather than a round 08:00 or a
#: nine-to-five that would contradict the sentence.
DEFAULT_OFFICE_OPEN = 9 * 60
DEFAULT_OFFICE_CLOSE = 18 * 60

#: "a message received at 5:50pm will have an expected response time of 9:05am on the
#: next working day" - the sentence the walk is modelled on.
OFFICE_HOURS_QUOTE = (
    "if your office hours are set to 9am - 6pm, and your SLA first response time is 15 "
    "minutes, a message received at 5:50pm will have an expected response time of 9:05am "
    "on the next working day"
)

#: Weekdays open, weekends closed, which is the default the quote above describes.
DEFAULT_OFFICE_WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
)

OFFICE_OPEN = "open"
OFFICE_CLOSE = "close"

MINUTES_PER_DAY = 24 * 60
SECONDS_PER_MINUTE = 60

#: How many days :func:`dsr.conversation_chase.rules.expected_reply_time` will walk
#: forward before giving up and returning the raw target. The researched maximum
#: duration is under 14 days, so a schedule with at least one open day always answers
#: inside this bound; a schedule with none terminates instead of spinning.
MAX_WALK_DAYS = 32


# --------------------------------------------------------------------------- #
# Skip reasons
# --------------------------------------------------------------------------- #
#
# A skip is a row for the same reason a fire is: the conditions that suppress a trigger
# are the hardest to verify, and a suppression nobody can see is a suppression nobody
# can check. Every one is a published code, reachable from ``/vocabulary``.

#: "This workflow won't trigger for conversations created via our REST API."
SKIP_API_CREATED = "api_created_conversation"

#: A trigger whose ``live`` switch is off. Step 7: "Save both and set live", so nothing
#: fires before that step.
SKIP_TRIGGER_NOT_LIVE = "trigger_not_live"

#: The trigger already fired for this customer message: "can only trigger once per
#: customer message".
SKIP_ALREADY_FIRED = "already_fired_for_message"

#: The timer has not elapsed yet. The common case, and not an error.
SKIP_NOT_ELIGIBLE = "inactivity_window_not_elapsed"

#: A snoozed conversation is paused. Not eligible, and not a failure.
SKIP_SNOOZED = "conversation_snoozed"

#: A closed conversation is history.
SKIP_CLOSED = "conversation_closed"

#: The conversation has no message from the anchor's side yet, so there is no instant
#: to measure from.
SKIP_NO_ANCHOR_MESSAGE = "no_anchor_message"

#: No trigger of this kind is configured for the room, so there is nothing to fire.
SKIP_NO_TRIGGER = "no_trigger_of_this_kind"

SKIP_REASONS: tuple[str, ...] = (
    SKIP_API_CREATED,
    SKIP_TRIGGER_NOT_LIVE,
    SKIP_ALREADY_FIRED,
    SKIP_NOT_ELIGIBLE,
    SKIP_SNOOZED,
    SKIP_CLOSED,
    SKIP_NO_ANCHOR_MESSAGE,
    SKIP_NO_TRIGGER,
)

SKIP_REASON_TEXT: dict[str, str] = {
    SKIP_API_CREATED: ("This workflow won't trigger for conversations created via our REST API."),
    SKIP_TRIGGER_NOT_LIVE: "The trigger has not been saved and set live.",
    SKIP_ALREADY_FIRED: "The workflow can only trigger once per customer message.",
    SKIP_NOT_ELIGIBLE: "The inactivity window has not elapsed yet.",
    SKIP_SNOOZED: "The conversation is snoozed, so its timer is paused.",
    SKIP_CLOSED: "The conversation is closed.",
    SKIP_NO_ANCHOR_MESSAGE: "The conversation has no message of the kind this trigger measures.",
    SKIP_NO_TRIGGER: "No trigger of this kind is configured for the room.",
}

#: The two reasons that are research rules rather than states of this room. Kept
#: separate so a page can show which skips come from the spec.
SPECIFICATION_SKIPS: frozenset[str] = frozenset({SKIP_API_CREATED, SKIP_ALREADY_FIRED})


# --------------------------------------------------------------------------- #
# Activity types
# --------------------------------------------------------------------------- #

ACTIVITY_TRIGGER_FIRED = "Trigger fired"
ACTIVITY_MESSAGE_SENT = "Message sent"
ACTIVITY_WAIT_STARTED = "Wait started"
ACTIVITY_WAIT_INTERRUPTED = "Wait interrupted"
ACTIVITY_CONVERSATION_CLOSED = "Conversation closed"
ACTIVITY_TAGGED = "Tagged"
ACTIVITY_MARKED_PRIORITY = "Marked as priority"
ACTIVITY_REROUTED = "Rerouted to another inbox"
ACTIVITY_SNOOZED = "Conversation snoozed"
ACTIVITY_EXPECTED_REPLY_TIME = "Expected reply time shown"

ACTIVITY_TYPES: tuple[str, ...] = (
    ACTIVITY_TRIGGER_FIRED,
    ACTIVITY_MESSAGE_SENT,
    ACTIVITY_WAIT_STARTED,
    ACTIVITY_WAIT_INTERRUPTED,
    ACTIVITY_CONVERSATION_CLOSED,
    ACTIVITY_TAGGED,
    ACTIVITY_MARKED_PRIORITY,
    ACTIVITY_REROUTED,
    ACTIVITY_SNOOZED,
    ACTIVITY_EXPECTED_REPLY_TIME,
)

ACTIVITY_TYPE_CODES: tuple[str, ...] = (
    "trigger_fired",
    "message_sent",
    "wait_started",
    "wait_interrupted",
    "conversation_closed",
    "tagged",
    "marked_priority",
    "rerouted",
    "snoozed",
    "expected_reply_time",
)

#: This product sends no HTTP and holds no email credential, so nothing here claims a
#: message left the building. The research's extensibility (POST /messages replay,
#: Data Connectors, signed webhooks) needs a credential this product does not have.
SENT_BY_THIS_PRODUCT = False

SENT_BY_THIS_PRODUCT_QUOTE = (
    "This product makes no outbound HTTP call and holds no Intercom credential, so the "
    "message is recorded as written and not as delivered."
)


# --------------------------------------------------------------------------- #
# Refusal codes
# --------------------------------------------------------------------------- #
#
# One table, every code this feature can refuse with, each carrying the status the
# router answers. A refusal a caller cannot name is a refusal a caller cannot handle,
# which is why the vocabulary serves this table and the page renders it.

ERROR_CODES: dict[str, tuple[int, str]] = {
    # 422: the caller sent something the researched rules forbid.
    "duration_out_of_range": (
        422,
        f"The duration must be longer than {MIN_DURATION_SECONDS} seconds and shorter "
        f"than 14 days.",
    ),
    "duration_not_a_number": (422, "The duration must be a number of seconds."),
    "unknown_trigger_kind": (422, "Unknown trigger kind."),
    "unknown_step_kind": (422, "Unknown step kind."),
    "unknown_interruption_event": (422, "Unknown interruption event."),
    "unknown_channel": (422, "Unknown channel."),
    "unknown_tag": (422, "A tag needs a name."),
    "unknown_author_kind": (422, "Unknown message author kind."),
    "unknown_origin": (422, "Unknown conversation origin."),
    "unknown_conversation_state": (422, "Unknown conversation state."),
    "not_an_instant": (422, "A timestamp is not an ISO 8601 instant."),
    "invalid_office_hours": (422, "The office-hours schedule is not usable."),
    # 404: no such row.
    "trigger_not_found": (404, "No such trigger."),
    "conversation_not_found": (404, "No such conversation."),
    "run_not_found": (404, "No such run."),
    # 409: the request was well formed and conflicts with current state.
    "trigger_already_live": (409, "This trigger is already live."),
    "trigger_not_live": (409, "This trigger is not live yet."),
    "run_not_advancing": (409, "This run has no next step to take."),
    "run_not_waiting": (409, "This run is not in a wait to resolve."),
    "conversation_closed": (409, "This conversation is closed."),
    "reroute_to_same_inbox": (409, "The conversation is already in that inbox."),
    "inbox_unknown": (409, "No such inbox on this account."),
}


def error_sentence(code: str) -> str:
    """The sentence a refusal code is published with."""

    return ERROR_CODES.get(code, (422, "This request is not one this workflow accepts."))[1]


def error_status(code: str) -> int:
    return ERROR_CODES.get(code, (422, "This request is not one this workflow accepts."))[0]


# --------------------------------------------------------------------------- #
# The published vocabulary
# --------------------------------------------------------------------------- #


def _slug(value: Any) -> str:
    """Lowercase, underscore-separated, trimmed.

    The normalisation every enum in this module shares, so ``"If Customer Has Been
    Unresponsive"``, ``"customer-idle"`` and ``" CUSTOMER_IDLE "`` are one kind and a
    lock or a bound cannot be walked past by respelling a value.
    """

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "_".join(text.split())


def published_vocabulary() -> dict[str, Any]:
    """Every researched term, with the sentence it comes from.

    Served as data, not as prose in the page, so the two cannot drift: the dropdowns
    render from this and the validator raises from the same tuples.
    """

    return {
        "collections": {
            "conversations": CONVERSATIONS,
            "parts": PARTS,
            "triggers": TRIGGERS,
            "runs": RUNS,
            "activity": ACTIVITY,
            "office_hours": OFFICE_HOURS,
        },
        "trigger_kinds": list(TRIGGER_KINDS),
        "trigger_kind_labels": dict(TRIGGER_KIND_LABELS),
        "trigger_fields": list(TRIGGER_FIELDS),
        "trigger_field_labels": dict(TRIGGER_FIELD_LABELS),
        "trigger_anchors": dict(TRIGGER_ANCHORS),
        "anchor_labels": dict(ANCHOR_LABELS),
        "channels": list(CHANNELS),
        "channel_labels": dict(CHANNEL_LABELS),
        "step_kinds": list(STEP_KINDS),
        "step_labels": dict(STEP_LABELS),
        "timed_steps": sorted(TIMED_STEPS),
        "duration_steps": sorted(DURATION_STEPS),
        "holding_steps": sorted(HOLDING_STEPS),
        "precedence_steps": sorted(PRECEDENCE_STEPS),
        "terminal_steps": sorted(TERMINAL_STEPS),
        "interruption_events": list(INTERRUPTION_EVENTS),
        "interruption_labels": dict(INTERRUPTION_LABELS),
        "author_kinds": list(AUTHOR_KINDS),
        "author_labels": dict(AUTHOR_LABELS),
        "origins": list(ORIGINS),
        "origin_labels": dict(ORIGIN_LABELS),
        "conversation_states": list(CONVERSATION_STATES),
        "conversation_state_labels": dict(CONVERSATION_STATE_LABELS),
        "sweepable_states": sorted(SWEEPABLE_STATES),
        "run_states": list(RUN_STATES),
        "run_state_labels": dict(RUN_STATE_LABELS),
        "closed_run_states": sorted(CLOSED_RUN_STATES),
        "activity_types": list(ACTIVITY_TYPES),
        "activity_type_codes": list(ACTIVITY_TYPE_CODES),
        "skip_reasons": list(SKIP_REASONS),
        "skip_reason_text": dict(SKIP_REASON_TEXT),
        "specification_skips": sorted(SPECIFICATION_SKIPS),
        "residual_tags": list(RESERVED_TAGS),
        "weekdays": list(WEEKDAYS),
        "default_office_weekdays": list(DEFAULT_OFFICE_WEEKDAYS),
        "default_office_open_minutes": DEFAULT_OFFICE_OPEN,
        "default_office_close_minutes": DEFAULT_OFFICE_CLOSE,
        "error_codes": {
            code: {"status": status, "detail": detail}
            for code, (status, detail) in ERROR_CODES.items()
        },
        "bounds": {
            "min_duration_seconds": MIN_DURATION_SECONDS,
            "max_duration_seconds": MAX_DURATION_SECONDS,
            "bounds_are_exclusive": True,
        },
        "defaults": {
            "trigger_kind": DEFAULT_TRIGGER_KIND,
            "trigger_seconds": DEFAULT_TRIGGER_SECONDS,
            "wait_seconds": DEFAULT_WAIT_SECONDS,
            "conversation_state": DEFAULT_STATE,
            "author_kind": AUTHOR_CUSTOMER,
            "origin": ORIGIN_INBOX,
            "tag": DEFAULT_TAG,
            "create_conversation_without_contact_reply": (CREATE_WITHOUT_CONTACT_REPLY_DEFAULT),
        },
        "honesty": {"sent_by_this_product": SENT_BY_THIS_PRODUCT},
        "documented_webhooks": list(DOCUMENTED_WEBHOOKS),
        "evidence": {
            "inactivity_timer": INACTIVITY_TIMER,
            "duration_bounds": DURATION_BOUNDS,
            "first_message_anchor": FIRST_MESSAGE_ANCHOR,
            "last_message_anchor": LAST_MESSAGE_ANCHOR,
            "once_per_message": ONCE_PER_MESSAGE,
            "api_created_exempt": API_CREATED_EXEMPT,
            "wait_precedence": WAIT_PRECEDENCE,
            "interruption_events": INTERRUPTION_EVENTS_QUOTE,
            "create_without_contact_reply": CREATE_WITHOUT_CONTACT_REPLY,
            "assign_conversation": ASSIGN_CONVERSATION,
            "reroute_steps": REROUTE_STEPS,
            "close_sequence": CLOSE_SEQUENCE,
            "opens_without_contact_reply": OPENS_WITHOUT_CONTACT_REPLY,
            "office_hours_example": OFFICE_HOURS_QUOTE,
            "snoozed_reopened": SNOOZED_QUOTE,
            "purely_time_based": PURELY_TIME_BASED,
            "first_message_rule": FIRST_MESSAGE_RULE,
            "sent_by_this_product": SENT_BY_THIS_PRODUCT_QUOTE,
        },
    }
