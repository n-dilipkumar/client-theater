"""WF-109: the researched vocabulary of SLA-breach escalation, and its only normalisation.

The room watches the live SLA countdown on each conversation and fires a workflow a
chosen lead time before an SLA target is due, or at the moment of breach, so the inbox
escalates in priority. Two triggers sit under the **During a conversation** trigger
category: **Before SLA breach** and **When SLA breaches**.

Where the terms come from
-------------------------

The SLA targets, the live timer and the conversation vocabulary are **not** re-declared
here. WF-108 (run SLA response timers, merged) owns the four metrics ``frt``, ``nrt``,
``ttc`` and ``ttr``, the timer record and the office-hours-aware deadline. WF-107 (chase
unresponsive buyers, merged) owns the conversation states ``open``, ``snoozed`` and
``closed``, the conversation and message-part records, and the office-hours calendar.
This module imports both and adds only the escalation-specific terms, so two workflows
cannot drift apart on what ``snoozed`` means or which metric ``frt`` names.

Every SLA-specific term is quoted from the workflow specification in issue #139, which
quotes the research. Each constant carries the sentence it came from.
"""

from __future__ import annotations

from typing import Any

# The three vocabularies this workflow reads but does not own.
from dsr.conversation_chase import vocabulary as chase
from dsr.sla_timers import vocabulary as sla

TICKET = "WF-109"

NAME = "Escalate on SLA breach with a pre-warning head start"

#: The research domain the specification names for this workflow.
DOMAIN = "automation-engagement"


# --------------------------------------------------------------------------- #
# Quoted evidence
# --------------------------------------------------------------------------- #
#
# Kept verbatim so a rule that cites one can be checked against it.

#: The first of the two triggers.
BEFORE_BREACH_QUOTE = (
    "Before SLA breach - runs a chosen amount of time before an SLA target is due to breach."
)

#: The second of the two triggers.
ON_BREACH_QUOTE = "When SLA breaches - runs the moment an SLA target is missed."

#: The load-bearing validation rule for the before-breach trigger.
LEAD_TIME_QUOTE = (
    "For the Before SLA breach trigger, the lead time must be shorter than the SLA "
    "target's duration, or the Workflow won't run."
)

#: The timer resolution: the room needs a scheduler and the check is roughly a minute.
ONE_MINUTE_QUOTE = (
    "Intercom checks SLAs about once a minute, so a Workflow runs within roughly a minute "
    "of the target time, rather than to the exact second."
)

#: The three conditions that suppress a run.
GATED_QUOTE = (
    "A Workflow runs while the conversation is still open and the SLA is still active. If "
    "a teammate replies, snoozes, or closes the conversation before the trigger point, the "
    "Workflow doesn't run."
)

#: The plan gate, quoted so the page can state it rather than hide it.
EXPERT_BETA_QUOTE = (
    "SLA Workflow triggers are available on the Expert plan and are currently in beta."
)

#: The object kinds the SLA triggers do not support.
NOT_BACK_OFFICE_QUOTE = (
    "SLA Workflow triggers ... The SLA-based Workflow trigger is not supported for "
    "Back-office Tickets or Tracker Tickets."
)

#: The data-connector execution result, quoted with the failure shape the page shows.
EXECUTION_QUOTE = (
    'data_connector.execution.completed -> "source_type": "workflow", "success": false, '
    '"http_status": 400, "error_type": "request_configuration_error"'
)

#: The extensibility sentence that makes the Data Connector the outbound call.
DATA_CONNECTOR_QUOTE = (
    "Data connectors allow workflows and AI agents to make HTTP requests to external APIs."
)


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# ``wf109_``-prefixed so no other workflow can collide with one by name, and so the
# ``collections()`` route shows which workflow owns a set of rows. The SLA timer, the
# conversation and the half-messages these workflows read belong to WF-108 and WF-107
# and are named from there.

WORKFLOWS = "wf109_escalation_workflow"
RUNS = "wf109_escalation_run"
EXECUTIONS = "wf109_alert_execution"
ACTIVITY = "wf109_escalation_activity"

OWNED_COLLECTIONS: tuple[str, ...] = (WORKFLOWS, RUNS, EXECUTIONS, ACTIVITY)

#: The live timer this workflow watches. WF-108's collection.
TIMERS = sla.TIMERS

#: The conversations and message parts an escalation reads. WF-107's collections.
CONVERSATIONS = chase.CONVERSATIONS
PARTS = chase.PARTS

#: The office-hours calendar the SLA deadline is computed against. WF-107's collection.
OFFICE_HOURS = chase.OFFICE_HOURS

#: The payload-side room reference. Not ``room_id``: that key is part of the record
#: envelope and ``AuditedDatabase`` strips it out of ``data`` before the index is built,
#: so a payload storing its room there would be unfilterable through ``find()``.
ROOM_REF = chase.ROOM_REF


# --------------------------------------------------------------------------- #
# The SLA targets
# --------------------------------------------------------------------------- #
#
# The spec names four: First Response Time, Next Response Time, Resolution Time and
# Time to Close. WF-108 owns the four metric ids and their short forms; this module
# re-publishes them with the spec's own label wording so the picker names the target the
# flow names while the record carries WF-108's id.

METRIC_KINDS: tuple[str, ...] = sla.METRIC_KINDS

#: The four metric ids, re-exported so a caller can name one without reaching into WF-108.
METRIC_FRT = sla.METRIC_FRT
METRIC_NRT = sla.METRIC_NRT
METRIC_TTC = sla.METRIC_TTC
METRIC_TTR = sla.METRIC_TTR

METRIC_LABELS: dict[str, str] = dict(sla.METRIC_LABELS)

METRIC_SHORT: dict[str, str] = dict(sla.METRIC_SHORT)

#: The spec's own wording for each target, in the flow's order.
METRIC_SPEC_LABELS: dict[str, str] = {
    sla.METRIC_FRT: "First Response Time",
    sla.METRIC_NRT: "Next Response Time",
    sla.METRIC_TTC: "Time to Close",
    sla.METRIC_TTR: "Resolution Time",
}

DEFAULT_METRIC = sla.METRIC_FRT


# --------------------------------------------------------------------------- #
# The conversation state
# --------------------------------------------------------------------------- #
#
# Re-exported from WF-107 so the two workflows cannot disagree about a state name.

STATE_OPEN = chase.STATE_OPEN
STATE_SNOOZED = chase.STATE_SNOOZED
STATE_CLOSED = chase.STATE_CLOSED

CONVERSATION_STATES = chase.CONVERSATION_STATES
CONVERSATION_STATE_LABELS = chase.CONVERSATION_STATE_LABELS

AUTHOR_TEAMMATE = chase.AUTHOR_TEAMMATE
AUTHOR_CUSTOMER = chase.AUTHOR_CUSTOMER
AUTHOR_SYSTEM = chase.AUTHOR_SYSTEM


# --------------------------------------------------------------------------- #
# The two triggers
# --------------------------------------------------------------------------- #

#: The trigger category both triggers sit under.
TRIGGER_CATEGORY = "during_a_conversation"
TRIGGER_CATEGORY_LABEL = "During a conversation"

#: "Before SLA breach".
TRIGGER_BEFORE_BREACH = "before_breach"

#: "When SLA breaches".
TRIGGER_ON_BREACH = "on_breach"

TRIGGER_KINDS: tuple[str, ...] = (TRIGGER_BEFORE_BREACH, TRIGGER_ON_BREACH)

TRIGGER_LABELS: dict[str, str] = {
    TRIGGER_BEFORE_BREACH: "Before SLA breach",
    TRIGGER_ON_BREACH: "When SLA breaches",
}

TRIGGER_QUOTES: dict[str, str] = {
    TRIGGER_BEFORE_BREACH: BEFORE_BREACH_QUOTE,
    TRIGGER_ON_BREACH: ON_BREACH_QUOTE,
}

#: The trigger that carries a lead time. ``on_breach`` has none.
LEAD_TRIGGERS: frozenset[str] = frozenset({TRIGGER_BEFORE_BREACH})

DEFAULT_TRIGGER = TRIGGER_BEFORE_BREACH


def require_trigger_kind(value: Any) -> str:
    """Normalise and validate a trigger kind.

    Both kinds are checked with the same function so a caller cannot write
    ``before_breach`` on one route and ``Before SLA breach`` on another and have the two
    records disagree about what they are.
    """

    text = _slug(value) or DEFAULT_TRIGGER
    if text not in TRIGGER_KINDS:
        raise ValueError(
            f"unknown trigger kind {value!r}; this workflow offers {', '.join(TRIGGER_KINDS)}"
        )
    return text


# --------------------------------------------------------------------------- #
# The lead time
# --------------------------------------------------------------------------- #
#
# "Set the lead time (e.g. 10 minutes) - must be shorter than the target." The spec's
# example is 10 minutes, so that is the shipped default.

MIN_LEAD_SECONDS = 60

#: The research's own example: "Set the lead time (e.g. 10 minutes)".
DEFAULT_LEAD_SECONDS = 10 * 60

#: The one rule the spec states about the number: it must be shorter than the target.
LEAD_MUST_BE_SHORTER = LEAD_TIME_QUOTE


def lead_is_shorter(lead_seconds: int, target_seconds: int) -> bool:
    """Is the lead time strictly shorter than the target it watches?

    Strict, because "must be shorter than" is not "must not exceed". A lead equal to the
    target would fire the workflow at the moment the timer starts, which is not a
    pre-warning.
    """

    return 0 < int(lead_seconds) < int(target_seconds)


# --------------------------------------------------------------------------- #
# The actions
# --------------------------------------------------------------------------- #
#
# Step 5: "Add actions: assign the conversation, notify a teammate or Slack channel, add
# a tag, leave a note." The Slack alert is an outbound HTTP call routed through a Data
# Connector, so it is a fifth action kind rather than a synonym for "notify".

ACTION_ASSIGN = "assign"
ACTION_NOTIFY = "notify"
ACTION_TAG = "tag"
ACTION_NOTE = "note"
ACTION_ALERT = "alert"

ACTION_KINDS: tuple[str, ...] = (
    ACTION_ASSIGN,
    ACTION_NOTIFY,
    ACTION_TAG,
    ACTION_NOTE,
    ACTION_ALERT,
)

ACTION_LABELS: dict[str, str] = {
    ACTION_ASSIGN: "Assign the conversation",
    ACTION_NOTIFY: "Notify a teammate or channel",
    ACTION_TAG: "Add a tag",
    ACTION_NOTE: "Leave an internal note",
    ACTION_ALERT: "Send a Data Connector alert",
}

#: The action the specification routes through a Data Connector and reports on
#: ``data_connector.execution.completed``.
CONNECTOR_ACTIONS: frozenset[str] = frozenset({ACTION_ALERT})


def require_action_kind(value: Any) -> str:
    text = _slug(value)
    if text not in ACTION_KINDS:
        raise ValueError(
            f"unknown action kind {value!r}; the builder offers {', '.join(ACTION_KINDS)}"
        )
    return text


#: Where a notify action sends its message.
NOTIFY_TEAMMATE = "teammate"
NOTIFY_SLACK = "slack"

NOTIFY_VIAS: tuple[str, ...] = (NOTIFY_TEAMMATE, NOTIFY_SLACK)

NOTIFY_LABELS: dict[str, str] = {
    NOTIFY_TEAMMATE: "A teammate",
    NOTIFY_SLACK: "A Slack channel",
}

DEFAULT_NOTIFY_VIA = NOTIFY_TEAMMATE

#: The tag a breach escalation writes by default. WF-107's tag vocabulary is closed and
#: this is a new, escalation-specific tag: the vocabulary accepts free text (see
#: :func:`dsr.conversation_chase.vocabulary.require_tag`), so the demoted tag is a value
#: this workflow chooses and publishes rather than one it re-invents per call.
DEFAULT_ESCALATION_TAG = "sla escalation"


def require_notify_via(value: Any) -> str:
    text = _slug(value) or DEFAULT_NOTIFY_VIA
    if text not in NOTIFY_VIAS:
        raise ValueError(f"unknown notify channel {value!r}; it is one of {', '.join(NOTIFY_VIAS)}")
    return text


# --------------------------------------------------------------------------- #
# Escalation level
# --------------------------------------------------------------------------- #
#
# "the inbox conversation is escalated in priority". A pre-warning is one step below a
# breach, so the inbox sorts a breached conversation above a warned one.

LEVEL_PRE_WARNING = "pre_warning"
LEVEL_BREACH = "breach"

LEVELS: tuple[str, ...] = (LEVEL_PRE_WARNING, LEVEL_BREACH)

LEVEL_LABELS: dict[str, str] = {
    LEVEL_PRE_WARNING: "Pre-warning",
    LEVEL_BREACH: "Breached",
}

LEVEL_FOR_TRIGGER: dict[str, str] = {
    TRIGGER_BEFORE_BREACH: LEVEL_PRE_WARNING,
    TRIGGER_ON_BREACH: LEVEL_BREACH,
}

#: The escalation rank a level carries. A breach outranks a pre-warning, which is what
#: "escalates in priority" means when the inbox is sorted.
LEVEL_RANK: dict[str, int] = {
    LEVEL_PRE_WARNING: 1,
    LEVEL_BREACH: 2,
}


def level_for_trigger(trigger: Any) -> str:
    return LEVEL_FOR_TRIGGER[require_trigger_kind(trigger)]


# --------------------------------------------------------------------------- #
# The three cancellation conditions
# --------------------------------------------------------------------------- #
#
# "If a teammate replies, snoozes, or closes the conversation before the trigger point,
# the Workflow doesn't run." All three are named, so all three are implemented.

CANCEL_TEAMMATE_REPLY = "teammate_replied"
CANCEL_SNOOZED = "conversation_snoozed"
CANCEL_CLOSED = "conversation_closed"

CANCEL_REASONS: tuple[str, ...] = (
    CANCEL_TEAMMATE_REPLY,
    CANCEL_SNOOZED,
    CANCEL_CLOSED,
)

CANCEL_LABELS: dict[str, str] = {
    CANCEL_TEAMMATE_REPLY: "A teammate replied before the trigger point.",
    CANCEL_SNOOZED: "The conversation was snoozed before the trigger point.",
    CANCEL_CLOSED: "The conversation was closed before the trigger point.",
}

#: The cancel reason that is read off the message parts rather than the conversation
#: state.
PART_CANCEL_REASONS: frozenset[str] = frozenset({CANCEL_TEAMMATE_REPLY})


# --------------------------------------------------------------------------- #
# Skip reasons
# --------------------------------------------------------------------------- #
#
# A skip is a decision not to run, and the conditions that suppress a run are the hardest
# to verify. Every one is a published code, returned by the sweep, so a suppressed run is
# visible rather than silent.

SKIP_WORKFLOW_NOT_LIVE = "workflow_not_live"
SKIP_NO_ACTIVE_SLA = "no_active_sla"
SKIP_POLICY_MISMATCH = "policy_mismatch"
SKIP_METRIC_NOT_APPLICABLE = "metric_not_applicable"
SKIP_METRIC_ALREADY_MET = "metric_already_met"
SKIP_ALREADY_FIRED = "already_fired_for_metric"
SKIP_LEAD_WINDOW_NOT_REACHED = "lead_window_not_reached"
SKIP_HEAD_START_MISSED = "head_start_missed"
SKIP_METRIC_NOT_BREACHED = "metric_not_breached"
SKIP_LEAD_NOT_SHORTER = "lead_time_not_shorter_than_target"
SKIP_CANCELLED = "cancelled"

SKIP_REASONS: tuple[str, ...] = (
    SKIP_WORKFLOW_NOT_LIVE,
    SKIP_NO_ACTIVE_SLA,
    SKIP_POLICY_MISMATCH,
    SKIP_METRIC_NOT_APPLICABLE,
    SKIP_METRIC_ALREADY_MET,
    SKIP_ALREADY_FIRED,
    SKIP_LEAD_WINDOW_NOT_REACHED,
    SKIP_HEAD_START_MISSED,
    SKIP_METRIC_NOT_BREACHED,
    SKIP_LEAD_NOT_SHORTER,
    SKIP_CANCELLED,
)

SKIP_REASON_TEXT: dict[str, str] = {
    SKIP_WORKFLOW_NOT_LIVE: "The workflow has not been saved and set live.",
    SKIP_NO_ACTIVE_SLA: "The conversation has no active SLA timer to watch.",
    SKIP_POLICY_MISMATCH: "The conversation's SLA is not the policy this workflow watches.",
    SKIP_METRIC_NOT_APPLICABLE: "The watched SLA target does not apply to this conversation.",
    SKIP_METRIC_ALREADY_MET: "The watched SLA target was already met.",
    SKIP_ALREADY_FIRED: "The workflow already fired for this target on this conversation.",
    SKIP_LEAD_WINDOW_NOT_REACHED: "The lead-time window has not been reached yet.",
    SKIP_HEAD_START_MISSED: "The target breached before the pre-warning window was seen.",
    SKIP_METRIC_NOT_BREACHED: "The watched SLA target has not been missed.",
    SKIP_LEAD_NOT_SHORTER: "The lead time is not shorter than the target's duration.",
    SKIP_CANCELLED: "A teammate replied, snoozed, or closed the conversation first.",
}

#: The two reasons that are research rules rather than states of this room. Kept separate
#: so a page can show which skips come from the spec.
SPECIFICATION_SKIPS: frozenset[str] = frozenset(
    {
        SKIP_LEAD_NOT_SHORTER,
        SKIP_CANCELLED,
        SKIP_HEAD_START_MISSED,
    }
)


# --------------------------------------------------------------------------- #
# The Data Connector execution result
# --------------------------------------------------------------------------- #
#
# The spec routes the Slack alert through a Data Connector and reports the outcome on
# ``data_connector.execution.completed`` with ``success``, ``http_status`` and
# ``error_type``. The result is a record, not a log line, so the page can show a failed
# alert.

SOURCE_TYPE_WORKFLOW = "workflow"

SUCCESS_HTTP_STATUS = 200
CONFIGURATION_HTTP_STATUS = 400

ERROR_NONE = "none"
ERROR_REQUEST_CONFIGURATION = "request_configuration_error"
ERROR_HTTP = "http_error"

ERROR_TYPES: tuple[str, ...] = (ERROR_NONE, ERROR_REQUEST_CONFIGURATION, ERROR_HTTP)

ERROR_TYPE_LABELS: dict[str, str] = {
    ERROR_NONE: "No error",
    ERROR_REQUEST_CONFIGURATION: "The connector request is not configured correctly.",
    ERROR_HTTP: "The connector returned an error status.",
}

#: The action fields an alert needs to be a usable request.
ALERT_REQUIRED_FIELDS: tuple[str, ...] = ("connector_id", "url")


# --------------------------------------------------------------------------- #
# Honesty
# --------------------------------------------------------------------------- #
#
# This product makes no outbound HTTP call and holds no connector credential. The alert
# is recorded as attempted with the outcome this build can determine from the action's
# own configuration, and the honesty flag says so.

SENT_BY_THIS_PRODUCT = False

SENT_BY_THIS_PRODUCT_QUOTE = (
    "This product makes no outbound HTTP call and holds no connector credential, so the "
    "alert is recorded as attempted and its outcome as determined from the action, not as "
    "delivered."
)


# --------------------------------------------------------------------------- #
# Refusal codes
# --------------------------------------------------------------------------- #
#
# One table, every code this feature can refuse with, each carrying the status the router
# answers. A refusal a caller cannot name is a refusal a caller cannot handle, so the
# vocabulary serves this table and the page renders it.

ERROR_CODES: dict[str, tuple[int, str]] = {
    # 422: the caller sent something the rules forbid.
    "workflow_needs_a_name": (422, "An escalation workflow needs a name."),
    "unknown_trigger_kind": (422, "Unknown trigger kind."),
    "unknown_metric": (422, "Unknown SLA target."),
    "unknown_action_kind": (422, "Unknown action kind."),
    "unknown_notify_channel": (422, "Unknown notify channel."),
    "unknown_conversation_state": (422, "Unknown conversation state."),
    "not_an_instant": (422, "A timestamp is not an ISO 8601 instant."),
    "lead_not_a_number": (422, "The lead time must be a number of seconds."),
    "lead_out_of_range": (422, "The lead time is outside the permitted range."),
    "lead_not_shorter_than_target": (
        422,
        "The lead time must be shorter than the SLA target's duration.",
    ),
    "workflow_needs_actions": (422, "An escalation workflow needs at least one action."),
    "action_needs_a_target": (422, "An action needs the field it acts on."),
    # 404: no such row.
    "workflow_not_found": (404, "No such escalation workflow."),
    "conversation_not_found": (404, "No such conversation."),
    "run_not_found": (404, "No such escalation run."),
    "policy_not_found": (404, "No such SLA policy to check the lead time against."),
    "decision_not_found": (404, "No such recorded decision."),
    # 409: the request was well formed and conflicts with current state.
    "workflow_already_live": (409, "This escalation workflow is already live."),
    "workflow_archived": (409, "This escalation workflow is archived."),
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

    The normalisation every enum in this module shares, so ``"Before SLA breach"``,
    ``"before-breach"`` and ``" BEFORE_BREACH "`` are one kind and a bound cannot be
    walked past by respelling a value.
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
        "domain": DOMAIN,
        "collections": {
            "workflows": WORKFLOWS,
            "runs": RUNS,
            "executions": EXECUTIONS,
            "activity": ACTIVITY,
            "timers": TIMERS,
            "conversations": CONVERSATIONS,
            "parts": PARTS,
            "office_hours": OFFICE_HOURS,
        },
        "trigger_category": TRIGGER_CATEGORY,
        "trigger_category_label": TRIGGER_CATEGORY_LABEL,
        "trigger_kinds": list(TRIGGER_KINDS),
        "trigger_labels": dict(TRIGGER_LABELS),
        "trigger_quotes": dict(TRIGGER_QUOTES),
        "lead_triggers": sorted(LEAD_TRIGGERS),
        "metrics": list(METRIC_KINDS),
        "metric_labels": dict(METRIC_LABELS),
        "metric_short": dict(METRIC_SHORT),
        "metric_spec_labels": dict(METRIC_SPEC_LABELS),
        "action_kinds": list(ACTION_KINDS),
        "action_labels": dict(ACTION_LABELS),
        "connector_actions": sorted(CONNECTOR_ACTIONS),
        "notify_vias": list(NOTIFY_VIAS),
        "notify_labels": dict(NOTIFY_LABELS),
        "level_for_trigger": dict(LEVEL_FOR_TRIGGER),
        "levels": list(LEVELS),
        "level_labels": dict(LEVEL_LABELS),
        "level_rank": dict(LEVEL_RANK),
        "conversation_states": list(CONVERSATION_STATES),
        "conversation_state_labels": dict(CONVERSATION_STATE_LABELS),
        "cancel_reasons": list(CANCEL_REASONS),
        "cancel_labels": dict(CANCEL_LABELS),
        "skip_reasons": list(SKIP_REASONS),
        "skip_reason_text": dict(SKIP_REASON_TEXT),
        "specification_skips": sorted(SPECIFICATION_SKIPS),
        "error_types": list(ERROR_TYPES),
        "error_type_labels": dict(ERROR_TYPE_LABELS),
        "alert_required_fields": list(ALERT_REQUIRED_FIELDS),
        "bounds": {
            "min_lead_seconds": MIN_LEAD_SECONDS,
            "default_lead_seconds": DEFAULT_LEAD_SECONDS,
        },
        "defaults": {
            "trigger": DEFAULT_TRIGGER,
            "metric": DEFAULT_METRIC,
            "lead_seconds": DEFAULT_LEAD_SECONDS,
            "notify_via": DEFAULT_NOTIFY_VIA,
            "tag": DEFAULT_ESCALATION_TAG,
        },
        "honesty": {"sent_by_this_product": SENT_BY_THIS_PRODUCT},
        "error_codes": {
            code: {"status": status, "detail": detail}
            for code, (status, detail) in ERROR_CODES.items()
        },
        "evidence": {
            "before_breach": BEFORE_BREACH_QUOTE,
            "on_breach": ON_BREACH_QUOTE,
            "lead_time": LEAD_TIME_QUOTE,
            "one_minute_check": ONE_MINUTE_QUOTE,
            "gated": GATED_QUOTE,
            "expert_plan_beta": EXPERT_BETA_QUOTE,
            "not_back_office": NOT_BACK_OFFICE_QUOTE,
            "execution_result": EXECUTION_QUOTE,
            "data_connector": DATA_CONNECTOR_QUOTE,
            "sent_by_this_product": SENT_BY_THIS_PRODUCT_QUOTE,
        },
    }
