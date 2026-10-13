"""WF-108: the researched vocabulary of SLA response timers, and its only normalisation.

An SLA policy carries four targets - first reply time (FRT), next reply time (NRT),
time to close (TTC) and, for tickets, time to resolution (TTR). Each runs as a timer
that counts office hours and pauses while the conversation is snoozed or waiting on
the customer. The timer's state is written back to the conversation row as a badge
and a nearest target, and the aggregate hit/miss/fixed rates feed the SLAs report.

Where the terms come from
-------------------------

The conversation vocabulary is **not** re-declared here. WF-107 (chase unresponsive
buyers, merged) already owns the conversation states ``open``, ``snoozed`` and
``closed``, and the office-hours model. This module imports that vocabulary and
re-exports the parts an SLA reads, so two workflows cannot drift apart on what
``snoozed`` means.

The SLA-specific terms are quoted from the workflow specification in issue #151,
which quotes the research. Each constant carries the sentence it came from.
"""

from __future__ import annotations

from typing import Any

# The conversation vocabulary and office-hours model are WF-107's. Imported and
# re-exported rather than copied, because a second copy of "snoozed" is a second
# answer to a question that already has one.
from dsr.conversation_chase import vocabulary as chase

TICKET = "WF-108"

NAME = "Run SLA response timers with office-hour awareness"


# --------------------------------------------------------------------------- #
# Quoted evidence
# --------------------------------------------------------------------------- #
#
# Kept verbatim so a rule that cites one can be checked against it.

#: The load-bearing statement about the mechanism: "The SLA timer is a continuous
#: background timer, not a discrete job."
CONTINUOUS_TIMER = "The SLA timer is a continuous background timer, not a discrete job."

#: "Only one active SLA per conversation" is the stated invariant: "Intercom supports
#: only one active SLA per conversation, so when a Workflow applies a new SLA later,
#: the existing SLA is automatically removed."
ONE_ACTIVE_SLA = (
    "Intercom supports only one active SLA per conversation, so when a Workflow applies "
    "a new SLA later, the existing SLA is automatically removed."
)

#: The pause rule: "Toggle pause rules: pause the timer when the conversation is
#: `snoozed` or `waiting on customer`."
PAUSE_RULE = (
    "Toggle pause rules: pause the timer when the conversation is snoozed or waiting on customer."
)

#: The auto-unsnooze rule, in tension with the pause rule: "Note: If an SLA is missed
#: while a conversation is snoozed, the conversation will be automatically unsnoozed
#: and re-opened."
AUTO_UNSNOOZE = (
    "If an SLA is missed while a conversation is snoozed, the conversation will be "
    "automatically unsnoozed and re-opened."
)

#: The badge thresholds, exactly: "Red: Overdue. Orange: Less than 5 minutes left on
#: the timer. Grey: More than 5 minutes left on the timer."
BADGE_THRESHOLDS = (
    "Red: Overdue. Orange: Less than 5 minutes left on the timer. Grey: More than 5 "
    "minutes left on the timer."
)

#: The office-hours worked example, the same one WF-107 carries: "if your office hours
#: are set to 9am - 6pm, and your SLA first response time is 15 minutes, a message
#: received at 5:50pm will have an expected response time of 9:05am on the next working
#: day."
OFFICE_HOURS_QUOTE = chase.OFFICE_HOURS_QUOTE

#: The report wording the spec uses, which names the three rates without defining the
#: third: "aggregate hit/miss/fixed rates flow into the SLAs report".
REPORT_RATES = "aggregate hit/miss/fixed rates flow into the SLAs report"

#: The event the conversation log records when an SLA is applied: "[SLA name] was
#: applied by [Workflow name]".
APPLIED_BY = "[SLA name] was applied by [Workflow name]"


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# ``wf108_``-prefixed so no other workflow can collide with one by name, and so the
# ``collections()`` route shows which workflow owns a set of rows. The conversation
# records these timers read are WF-107's and are named from there.

POLICIES = "wf108_sla_policy"
TIMERS = "wf108_sla_timer"
ACTIVITY = "wf108_sla_activity"

OWNED_COLLECTIONS: tuple[str, ...] = (POLICIES, TIMERS, ACTIVITY)

#: The conversation records and message parts an SLA reads. WF-107's collections.
CONVERSATIONS = chase.CONVERSATIONS
PARTS = chase.PARTS

#: The office-hours calendar an SLA links to. WF-107's collection, and the same one
#: WF-107's expected-reply-time reads, so a room has one schedule and not two.
OFFICE_HOURS = chase.OFFICE_HOURS

#: The payload-side room reference. Not ``room_id``: that key is part of the record
#: envelope and ``AuditedDatabase`` strips it out of ``data`` before the index is built,
#: so a payload storing its room there would be unfilterable through ``find()``.
ROOM_REF = "room_ref"


# --------------------------------------------------------------------------- #
# Conversation state
# --------------------------------------------------------------------------- #
#
# Re-exported from WF-107 so the two workflows cannot disagree about a state name.

STATE_OPEN = chase.STATE_OPEN
STATE_SNOOZED = chase.STATE_SNOOZED
STATE_CLOSED = chase.STATE_CLOSED

CONVERSATION_STATES = chase.CONVERSATION_STATES
CONVERSATION_STATE_LABELS = chase.CONVERSATION_STATE_LABELS

#: The state a reopened conversation returns to, and the one the timer resumes in.
STATE_REOPENED = chase.STATE_OPEN


# --------------------------------------------------------------------------- #
# The four metrics
# --------------------------------------------------------------------------- #

#: "First Reply Time (FRT)".
METRIC_FRT = "frt"

#: "Next Reply Time (NRT)".
METRIC_NRT = "nrt"

#: "Time to Close (TTC)".
METRIC_TTC = "ttc"

#: "and - for tickets - Time to Resolution (TTR)".
METRIC_TTR = "ttr"

METRIC_KINDS: tuple[str, ...] = (METRIC_FRT, METRIC_NRT, METRIC_TTC, METRIC_TTR)

METRIC_LABELS: dict[str, str] = {
    METRIC_FRT: "First reply time",
    METRIC_NRT: "Next reply time",
    METRIC_TTC: "Time to close",
    METRIC_TTR: "Time to resolution",
}

METRIC_SHORT: dict[str, str] = {
    METRIC_FRT: "FRT",
    METRIC_NRT: "NRT",
    METRIC_TTC: "TTC",
    METRIC_TTR: "TTR",
}

#: The reply metrics, measured to a teammate's reply. The close metric is measured to
#: the conversation's terminal state.
REPLY_METRICS: tuple[str, ...] = (METRIC_FRT, METRIC_NRT)

#: The two terminal metrics, selected by the object's kind. "TTC" is a conversation
#: metric and "TTR" a ticket metric; the room product's conversations have one terminal
#: state, close, so both measure to it and differ only in the name the object earns.
TERMINAL_METRICS: tuple[str, ...] = (METRIC_TTC, METRIC_TTR)


# --------------------------------------------------------------------------- #
# Object kind
# --------------------------------------------------------------------------- #

#: A conversation: TTC applies and TTR does not.
KIND_CONVERSATION = "conversation"

#: A ticket: TTR applies and TTC does not.
KIND_TICKET = "ticket"

OBJECT_KINDS: tuple[str, ...] = (KIND_CONVERSATION, KIND_TICKET)

OBJECT_KIND_LABELS: dict[str, str] = {
    KIND_CONVERSATION: "Conversation",
    KIND_TICKET: "Ticket",
}

DEFAULT_OBJECT_KIND = KIND_CONVERSATION

#: Which of the two terminal metrics applies to each kind. The other is reported
#: ``not_applicable`` rather than hidden, so a reader can see the rule.
TERMINAL_METRIC_FOR: dict[str, str] = {
    KIND_CONVERSATION: METRIC_TTC,
    KIND_TICKET: METRIC_TTR,
}


def metrics_for_kind(kind: str) -> tuple[str, ...]:
    """The metrics that apply to a conversation or a ticket.

    A conversation gets FRT, NRT and TTC. A ticket gets FRT, NRT and TTR. The metric
    that does not apply is left out rather than computed against a target the object
    kind does not earn.
    """

    terminal = TERMINAL_METRIC_FOR[require_object_kind(kind)]
    return (*REPLY_METRICS, terminal)


def require_object_kind(value: Any) -> str:
    text = _slug(value) or DEFAULT_OBJECT_KIND
    if text not in OBJECT_KINDS:
        raise ValueError(f"unknown object kind {value!r}; it is one of {', '.join(OBJECT_KINDS)}")
    return text


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
#
# The spec names no numbers, so the defaults are derived and the derivation is
# recorded in the inferences register. The bounds are stated so a target cannot be
# zero or a year.

#: A target must be at least a minute and at most 30 days.
MIN_TARGET_SECONDS = 60
MAX_TARGET_SECONDS = 30 * 24 * 60 * 60

#: The derived defaults. FRT takes the 15 minutes of the spec's own worked example.
#: The others are a stated derivation rather than a research finding: an hour to the
#: next reply, a working day to close, two days to resolution.
DEFAULT_TARGETS: dict[str, int] = {
    METRIC_FRT: 15 * 60,
    METRIC_NRT: 60 * 60,
    METRIC_TTC: 8 * 60 * 60,
    METRIC_TTR: 2 * 24 * 60 * 60,
}


def default_targets() -> dict[str, int]:
    return dict(DEFAULT_TARGETS)


# --------------------------------------------------------------------------- #
# Pause rules
# --------------------------------------------------------------------------- #
#
# "pause the timer when the conversation is snoozed or waiting on customer".

PAUSE_SNOOZED = "snoozed"
PAUSE_WAITING_ON_CUSTOMER = "waiting_on_customer"

PAUSE_REASONS: tuple[str, ...] = (PAUSE_SNOOZED, PAUSE_WAITING_ON_CUSTOMER)

PAUSE_LABELS: dict[str, str] = {
    PAUSE_SNOOZED: "Pause while the conversation is snoozed",
    PAUSE_WAITING_ON_CUSTOMER: "Pause while the conversation is waiting on the customer",
}

#: Both pauses are on by default: the flow says the seller toggles them, and the
#: researched default is that the timer respects office hours and the conversation's
#: own pauses.
DEFAULT_PAUSE_REASONS: tuple[str, ...] = (PAUSE_SNOOZED, PAUSE_WAITING_ON_CUSTOMER)


def require_pause_reason(value: Any) -> str:
    text = _slug(value)
    if text not in PAUSE_REASONS:
        raise ValueError(f"unknown pause reason {value!r}; it is one of {', '.join(PAUSE_REASONS)}")
    return text


# --------------------------------------------------------------------------- #
# Metric and timer states
# --------------------------------------------------------------------------- #

#: The target has not been reached and the clock is running.
METRIC_PENDING = "pending"

#: The target was reached inside its budget.
METRIC_MET = "met"

#: The budget was spent before the milestone.
METRIC_BREACHED = "breached"

#: The clock is paused by a pause rule.
METRIC_PAUSED = "paused"

#: The metric does not apply to this object kind, or has no anchor yet.
METRIC_NOT_APPLICABLE = "not_applicable"

METRIC_STATES: tuple[str, ...] = (
    METRIC_PENDING,
    METRIC_MET,
    METRIC_BREACHED,
    METRIC_PAUSED,
    METRIC_NOT_APPLICABLE,
)

METRIC_STATE_LABELS: dict[str, str] = {
    METRIC_PENDING: "Countdown running",
    METRIC_MET: "Met",
    METRIC_BREACHED: "Breached",
    METRIC_PAUSED: "Paused",
    METRIC_NOT_APPLICABLE: "Not applicable",
}

TIMER_RUNNING = "running"
TIMER_PAUSED = "paused"
TIMER_MET = "met"
TIMER_BREACHED = "breached"

TIMER_STATES: tuple[str, ...] = (TIMER_RUNNING, TIMER_PAUSED, TIMER_MET, TIMER_BREACHED)

TIMER_STATE_LABELS: dict[str, str] = {
    TIMER_RUNNING: "Running",
    TIMER_PAUSED: "Paused",
    TIMER_MET: "Met",
    TIMER_BREACHED: "Breached",
}


# --------------------------------------------------------------------------- #
# Badge
# --------------------------------------------------------------------------- #
#
# The three the spec names, and no fourth.

BADGE_GREY = "grey"
BADGE_ORANGE = "orange"
BADGE_RED = "red"

BADGES: tuple[str, ...] = (BADGE_GREY, BADGE_ORANGE, BADGE_RED)

BADGE_LABELS: dict[str, str] = {
    BADGE_GREY: "More than 5 minutes left",
    BADGE_ORANGE: "Less than 5 minutes left",
    BADGE_RED: "Overdue",
}

#: "Less than 5 minutes left on the timer" is the orange boundary, so exactly five
#: minutes is still grey and anything under it is orange.
BADGE_ORANGE_SECONDS = 5 * 60


# --------------------------------------------------------------------------- #
# Report outcomes
# --------------------------------------------------------------------------- #
#
# "aggregate hit/miss/fixed rates". The first two are named by the sentence. "fixed"
# is derived: see the inferences register. A breach that the auto-unsnooze rule
# reopened and that then completed counts as fixed rather than as a plain miss.

OUTCOME_HIT = "hit"
OUTCOME_MISS = "miss"
OUTCOME_FIXED = "fixed"

OUTCOMES: tuple[str, ...] = (OUTCOME_HIT, OUTCOME_MISS, OUTCOME_FIXED)

OUTCOME_LABELS: dict[str, str] = {
    OUTCOME_HIT: "Hit",
    OUTCOME_MISS: "Miss",
    OUTCOME_FIXED: "Fixed",
}


# --------------------------------------------------------------------------- #
# Activity types
# --------------------------------------------------------------------------- #

ACTIVITY_SLA_APPLIED = "SLA applied"
ACTIVITY_SLA_REMOVED = "SLA removed"
ACTIVITY_SLA_REOPENED = "Conversation unsnoozed and reopened"
ACTIVITY_SLA_BREACHED = "SLA breached"
ACTIVITY_SLA_MET = "SLA met"

ACTIVITY_TYPES: tuple[str, ...] = (
    ACTIVITY_SLA_APPLIED,
    ACTIVITY_SLA_REMOVED,
    ACTIVITY_SLA_REOPENED,
    ACTIVITY_SLA_BREACHED,
    ACTIVITY_SLA_MET,
)


# --------------------------------------------------------------------------- #
# The webhooks the research names
# --------------------------------------------------------------------------- #
#
# The spec's extensibility names these topics. This product has no inbound webhook
# surface for them (WF-082 owns inbound verification), so they are published as the
# events the timer observes rather than as routes this feature serves.

DOCUMENTED_WEBHOOKS: tuple[str, ...] = (
    "ticket.created",
    "ticket.state.updated",
    "ticket.resolved",
    "ticket.closed",
    "ticket.admin.snoozed",
    "ticket.admin.unsnoozed",
    "conversation.admin.snoozed",
)


# --------------------------------------------------------------------------- #
# Refusal codes
# --------------------------------------------------------------------------- #
#
# One table, every code this feature can refuse with, each carrying the status the
# router answers. A refusal a caller cannot name is a refusal a caller cannot handle,
# so the vocabulary serves this table and the page renders it.

ERROR_CODES: dict[str, tuple[int, str]] = {
    # 422: the caller sent something the rules forbid.
    "unknown_metric": (422, "Unknown SLA metric."),
    "unknown_object_kind": (422, "Unknown object kind."),
    "unknown_pause_reason": (422, "Unknown pause reason."),
    "unknown_conversation_state": (422, "Unknown conversation state."),
    "target_not_a_number": (422, "An SLA target must be a number of seconds."),
    "target_out_of_range": (422, "An SLA target is outside the permitted range."),
    "not_an_instant": (422, "A timestamp is not an ISO 8601 instant."),
    "policy_needs_targets": (422, "An SLA policy needs at least one target."),
    "policy_needs_a_name": (422, "An SLA policy needs a name."),
    "invalid_office_hours": (422, "The office-hours schedule is not usable."),
    # 404: no such row.
    "policy_not_found": (404, "No such SLA policy."),
    "timer_not_found": (404, "No such SLA timer."),
    "conversation_not_found": (404, "No such conversation."),
    "decision_not_found": (404, "No such recorded decision."),
    # 409: the request was well formed and conflicts with current state.
    "no_active_sla": (409, "This conversation has no active SLA."),
    "policy_archived": (409, "This SLA policy is archived."),
    "conversation_closed": (409, "This conversation is closed."),
}


def error_sentence(code: str) -> str:
    return ERROR_CODES.get(code, (422, "This request is not one this workflow accepts."))[1]


def error_status(code: str) -> int:
    return ERROR_CODES.get(code, (422, "This request is not one this workflow accepts."))[0]


# --------------------------------------------------------------------------- #
# The published vocabulary
# --------------------------------------------------------------------------- #


def _slug(value: Any) -> str:
    """Lowercase, underscore-separated, trimmed.

    The normalisation every enum in this module shares, so ``"First Reply Time"``,
    ``"frt"`` and ``" FRT "`` are one metric and a bound cannot be walked past by
    respelling a value.
    """

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return "_".join(text.split())


def published_vocabulary() -> dict[str, Any]:
    """Every term this workflow enforces against, with the sentence it came from.

    Served as data, not as prose in the page, so the two cannot drift: the dropdowns
    render from this and the validator raises from the same tuples.
    """

    return {
        "ticket": TICKET,
        "name": NAME,
        "collections": {
            "policies": POLICIES,
            "timers": TIMERS,
            "activity": ACTIVITY,
            "conversations": CONVERSATIONS,
            "parts": PARTS,
            "office_hours": OFFICE_HOURS,
        },
        "metrics": list(METRIC_KINDS),
        "metric_labels": dict(METRIC_LABELS),
        "metric_short": dict(METRIC_SHORT),
        "reply_metrics": list(REPLY_METRICS),
        "terminal_metrics": list(TERMINAL_METRICS),
        "object_kinds": list(OBJECT_KINDS),
        "object_kind_labels": dict(OBJECT_KIND_LABELS),
        "terminal_metric_for": dict(TERMINAL_METRIC_FOR),
        "pause_reasons": list(PAUSE_REASONS),
        "pause_labels": dict(PAUSE_LABELS),
        "default_pause_reasons": list(DEFAULT_PAUSE_REASONS),
        "conversation_states": list(CONVERSATION_STATES),
        "conversation_state_labels": dict(CONVERSATION_STATE_LABELS),
        "metric_states": list(METRIC_STATES),
        "metric_state_labels": dict(METRIC_STATE_LABELS),
        "timer_states": list(TIMER_STATES),
        "timer_state_labels": dict(TIMER_STATE_LABELS),
        "badges": list(BADGES),
        "badge_labels": dict(BADGE_LABELS),
        "badge_orange_seconds": BADGE_ORANGE_SECONDS,
        "outcomes": list(OUTCOMES),
        "outcome_labels": dict(OUTCOME_LABELS),
        "activity_types": list(ACTIVITY_TYPES),
        "bounds": {
            "min_target_seconds": MIN_TARGET_SECONDS,
            "max_target_seconds": MAX_TARGET_SECONDS,
        },
        "defaults": {
            "targets": default_targets(),
            "pause_reasons": list(DEFAULT_PAUSE_REASONS),
            "object_kind": DEFAULT_OBJECT_KIND,
        },
        "documented_webhooks": list(DOCUMENTED_WEBHOOKS),
        "error_codes": {
            code: {"status": status, "detail": detail}
            for code, (status, detail) in ERROR_CODES.items()
        },
        "evidence": {
            "continuous_timer": CONTINUOUS_TIMER,
            "one_active_sla": ONE_ACTIVE_SLA,
            "pause_rule": PAUSE_RULE,
            "auto_unsnooze": AUTO_UNSNOOZE,
            "badge_thresholds": BADGE_THRESHOLDS,
            "office_hours_example": OFFICE_HOURS_QUOTE,
            "report_rates": REPORT_RATES,
            "applied_by": APPLIED_BY,
        },
    }
