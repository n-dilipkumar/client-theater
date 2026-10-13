"""WF-110: the researched vocabulary for tickets and their owning teams.

The workflow is "Escalate a converted conversation into an owned ticket". The
research is ``docs/research/digital-sales-room-workflows/wf/WF-110.md``, which is
itself built from section 7 of ``docs/research/raw/automation-engagement.md``.

Every term a page renders or a validator enforces is declared here beside the
sentence it came from. Three kinds of term live here:

* a **researched** term carries ``source`` and ``quote``: the research states it.
* an **inferred** term carries ``inference``: the research leaves it open and this
  build chose. The choice is listed in :mod:`dsr.conversation_tickets.inferences`.
* a **collection name** is this build's own storage key and carries neither.

The module holds strings, tuples and the published report. It imports no
framework and opens no database.

Conversation vocabulary is reused, not re-declared
--------------------------------------------------

WF-107 (``dsr.conversation_chase``) owns conversation records. This workflow
converts one of those records into a ticket, so it reads the conversation
collection name and the conversation states from WF-107 rather than inventing a
second spelling. A mismatch between the two would silently unlink every
converted conversation. :data:`CONVERSATION_COLLECTION`,
:data:`CONVERSATION_CLOSED`, :data:`CONVERSATION_OPEN` and
:data:`CONVERSATION_ORIGIN_API` are aliases onto the WF-107 definitions.
"""

from __future__ import annotations

from typing import Any

from dsr.conversation_chase.vocabulary import (
    API_CREATED_ORIGIN,
    CONVERSATIONS,
    ORIGIN_API,
    ORIGINS,
    STATE_CLOSED as WF107_STATE_CLOSED,
    STATE_OPEN as WF107_STATE_OPEN,
)

# --------------------------------------------------------------------------- #
# Collections: ordinary JSON in records.data, no migration and no typed column
# --------------------------------------------------------------------------- #

TICKET_COLLECTION = "wf110_ticket"
TAG_COLLECTION = "wf110_tag"
TRIGGER_COLLECTION = "wf110_trigger"
EVALUATION_COLLECTION = "wf110_evaluation"
NOTIFICATION_COLLECTION = "wf110_notification"
WEBHOOK_COLLECTION = "wf110_webhook"
ACTIVITY_COLLECTION = "wf110_ticket_activity"
SLA_COLLECTION = "wf110_sla_policy"

#: The record envelope's room key, spelled as WF-107 spells it.
ROOM_REF = "room_ref"

# --------------------------------------------------------------------------- #
# Conversation vocabulary, read from WF-107 rather than re-declared
# --------------------------------------------------------------------------- #

#: The conversation record a ticket may be converted from.
CONVERSATION_COLLECTION = CONVERSATIONS
#: The conversation state WF-107 closes a conversation into.
CONVERSATION_CLOSED = WF107_STATE_CLOSED
#: The conversation state WF-107 opens a conversation into.
CONVERSATION_OPEN = WF107_STATE_OPEN
#: A conversation created through the REST API, per WF-107.
CONVERSATION_ORIGIN_API = ORIGIN_API
#: Every conversation origin WF-107 recognises.
CONVERSATION_ORIGINS = ORIGINS
#: WF-107's own alias for the REST-API origin.
CONVERSATION_API_CREATED_ORIGIN = API_CREATED_ORIGIN

# --------------------------------------------------------------------------- #
# Teams, ticket types and ticket states
# --------------------------------------------------------------------------- #

#: "change the assigned team" and "notify the responsible team in Slack".
#: The names match WF-107's known inboxes so a conversation and its ticket name
#: the same destination.
TEAM_SALES = "sales"
TEAM_ENTERPRISE = "enterprise"
TEAM_ESCALATIONS = "escalations"
TEAM_SUPPORT = "support"
TEAMS: tuple[str, ...] = (TEAM_SALES, TEAM_ENTERPRISE, TEAM_ESCALATIONS, TEAM_SUPPORT)
TEAM_LABELS: dict[str, str] = {
    TEAM_SALES: "Sales",
    TEAM_ENTERPRISE: "Enterprise",
    TEAM_ESCALATIONS: "Escalations",
    TEAM_SUPPORT: "Support",
}

#: "narrow to the relevant ticket type (e.g. escalation tickets)". The ticket
#: object's ``ticket_type`` is the researched field name.
TYPE_ESCALATION = "escalation"
TYPE_SUPPORT = "support"
TYPE_OPERATIONAL = "operational"
TYPE_BILLING = "billing"
TICKET_TYPES: tuple[str, ...] = (TYPE_ESCALATION, TYPE_SUPPORT, TYPE_OPERATIONAL, TYPE_BILLING)
TICKET_TYPE_LABELS: dict[str, str] = {
    TYPE_ESCALATION: "Escalation",
    TYPE_SUPPORT: "Support",
    TYPE_OPERATIONAL: "Operational",
    TYPE_BILLING: "Billing",
}
#: The type step 2 of the user flow narrows to in its own example.
DEFAULT_TICKET_TYPE = TYPE_ESCALATION

#: The ``ticket_state`` values. ``resolved`` and ``closed`` are two separate
#: states on purpose: "ticket.resolved ... This is a state-based event" and
#: "ticket.closed ... This is separate from state transitions".
STATE_SUBMITTED = "submitted"
STATE_IN_PROGRESS = "in_progress"
STATE_WAITING = "waiting_on_customer"
STATE_ON_HOLD = "on_hold"
STATE_RESOLVED = "resolved"
STATE_CLOSED = "closed"
TICKET_STATES: tuple[str, ...] = (
    STATE_SUBMITTED,
    STATE_IN_PROGRESS,
    STATE_WAITING,
    STATE_ON_HOLD,
    STATE_RESOLVED,
    STATE_CLOSED,
)
TICKET_STATE_LABELS: dict[str, str] = {
    STATE_SUBMITTED: "Submitted",
    STATE_IN_PROGRESS: "In progress",
    STATE_WAITING: "Waiting on customer",
    STATE_ON_HOLD: "On hold",
    STATE_RESOLVED: "Resolved",
    STATE_CLOSED: "Closed",
}
DEFAULT_TICKET_STATE = STATE_SUBMITTED

#: The states whose transition emits a dedicated webhook beside
#: ``ticket.state.updated``.
STATE_EVENT_TOPICS: dict[str, str] = {
    STATE_RESOLVED: "ticket.resolved",
}

# --------------------------------------------------------------------------- #
# How the ticket came to exist
# --------------------------------------------------------------------------- #

#: "A conversation is converted to a ticket (or a ticket is created via API /
#: inbound)". ``tracker`` is the exclusion: "the workflow will not trigger from
#: tracker ticket creation".
VIA_CONVERSATION = "conversation"
VIA_API = "api"
VIA_INBOUND = "inbound"
VIA_TRACKER = "tracker"
CREATED_VIA: tuple[str, ...] = (VIA_CONVERSATION, VIA_API, VIA_INBOUND, VIA_TRACKER)
CREATED_VIA_LABELS: dict[str, str] = {
    VIA_CONVERSATION: "Converted from a conversation",
    VIA_API: "Created through the REST API",
    VIA_INBOUND: "Created inbound",
    VIA_TRACKER: "Created by the tracker",
}
DEFAULT_CREATED_VIA = VIA_CONVERSATION

# --------------------------------------------------------------------------- #
# The two triggers
# --------------------------------------------------------------------------- #

#: Step 1: "the trigger 'A ticket is created'".
TRIGGER_TICKET_CREATED = "ticket_created"
#: Step 4, optional in the research: "a second Workflow on 'Teammate changes the
#: state of a ticket'". This build ships it, so a state change notifies the team.
TRIGGER_STATE_CHANGED = "ticket_state_changed"
TRIGGER_KINDS: tuple[str, ...] = (TRIGGER_TICKET_CREATED, TRIGGER_STATE_CHANGED)
TRIGGER_KIND_LABELS: dict[str, str] = {
    TRIGGER_TICKET_CREATED: "A ticket is created",
    TRIGGER_STATE_CHANGED: "Teammate changes the state of a ticket",
}
DEFAULT_TRIGGER_KIND = TRIGGER_TICKET_CREATED

#: The researched limit on the ticket-created trigger.
BACKGROUND_ONLY = "Only background actions are available under this trigger."
#: The researched exclusion for tracker-created tickets.
TRACKER_EXEMPTION = (
    "While using the workflow trigger 'ticket is created' the workflow will not "
    "trigger from tracker ticket creation."
)
#: The researched precondition on three actions.
CONVERSION_GATE = (
    "Tag conversation, Apply SLA, and Set CDA actions will only work if the ticket "
    "acted upon was converted from a conversation."
)
#: The researched event split.
RESOLVED_IS_STATE_BASED = (
    "ticket.resolved - Fires when a ticket's state transitions to 'resolved'. This "
    "is a state-based event."
)
CLOSED_IS_SEPARATE = (
    "ticket.closed - Fires when a ticket conversation is closed. This is separate "
    "from state transitions."
)
CLOSED_PAYLOAD_SHAPE = "ticket.closed - the Ticket is nested under a ticket field on data.item"

# --------------------------------------------------------------------------- #
# The actions
# --------------------------------------------------------------------------- #

#: "notify the responsible team in Slack".
ACTION_NOTIFY_TEAM = "notify_team"
#: "change the assigned team".
ACTION_ASSIGN_TEAM = "assign_team"
#: "apply an SLA".
ACTION_APPLY_SLA = "apply_sla"
#: "tag the linked conversation".
ACTION_TAG_CONVERSATION = "tag_conversation"
ACTION_KINDS: tuple[str, ...] = (
    ACTION_NOTIFY_TEAM,
    ACTION_ASSIGN_TEAM,
    ACTION_APPLY_SLA,
    ACTION_TAG_CONVERSATION,
)
ACTION_LABELS: dict[str, str] = {
    ACTION_NOTIFY_TEAM: "Notify the owning team in Slack",
    ACTION_ASSIGN_TEAM: "Assign the owning team",
    ACTION_APPLY_SLA: "Apply an SLA",
    ACTION_TAG_CONVERSATION: "Tag the linked conversation",
}
#: "Only background actions are available under this trigger". Every action this
#: build models is background: none of them asks a seller to act.
BACKGROUND_ACTIONS: frozenset[str] = frozenset(ACTION_KINDS)
#: "Set CDA" is the third action the researched gate names. It needs a reusable
#: bot on the ticket trigger, so this build does not model it. The omission is
#: recorded in :mod:`dsr.conversation_tickets.inferences`.
SET_CDA_ACTION = "set_cda"
#: The actions the researched precondition gates. "will only work if the ticket
#: acted upon was converted from a conversation".
CONVERSION_REQUIRED_ACTIONS: frozenset[str] = frozenset({ACTION_APPLY_SLA, ACTION_TAG_CONVERSATION})

#: What one action did. A skip is an outcome, not an error.
ACTION_RAN = "ran"
ACTION_SKIPPED_NOT_CONVERTED = "skipped_not_converted"
ACTION_SKIPPED_UNKNOWN_TEAM = "skipped_unknown_team"
ACTION_SKIPPED_UNKNOWN_POLICY = "skipped_unknown_sla_policy"
ACTION_SKIPPED_UNKNOWN_TAG = "skipped_unknown_tag"
ACTION_OUTCOMES: tuple[str, ...] = (
    ACTION_RAN,
    ACTION_SKIPPED_NOT_CONVERTED,
    ACTION_SKIPPED_UNKNOWN_TEAM,
    ACTION_SKIPPED_UNKNOWN_POLICY,
    ACTION_SKIPPED_UNKNOWN_TAG,
)

# --------------------------------------------------------------------------- #
# Why a trigger did not fire
# --------------------------------------------------------------------------- #

SKIP_TRIGGER_NOT_LIVE = "trigger_not_live"
SKIP_KIND_NOT_MATCHED = "trigger_kind_not_matched"
SKIP_TYPE_NOT_MATCHED = "ticket_type_not_matched"
SKIP_TEAM_NOT_MATCHED = "ticket_team_not_matched"
SKIP_STATE_NOT_MATCHED = "ticket_state_not_matched"
SKIP_TRACKER_CREATED = "tracker_created_ticket"
SKIP_REASONS: tuple[str, ...] = (
    SKIP_TRIGGER_NOT_LIVE,
    SKIP_KIND_NOT_MATCHED,
    SKIP_TYPE_NOT_MATCHED,
    SKIP_TEAM_NOT_MATCHED,
    SKIP_STATE_NOT_MATCHED,
    SKIP_TRACKER_CREATED,
)
SKIP_REASON_TEXT: dict[str, str] = {
    SKIP_TRIGGER_NOT_LIVE: "The trigger is a draft, so it fires nothing until it is set live.",
    SKIP_KIND_NOT_MATCHED: "The trigger watches a different ticket event.",
    SKIP_TYPE_NOT_MATCHED: "The ticket's type is outside the trigger's conditions.",
    SKIP_TEAM_NOT_MATCHED: "The ticket's team is outside the trigger's conditions.",
    SKIP_STATE_NOT_MATCHED: "The ticket's new state is outside the trigger's conditions.",
    SKIP_TRACKER_CREATED: TRACKER_EXEMPTION,
}
#: The two skips that are specification rules rather than states of one room.
SPECIFICATION_SKIPS: frozenset[str] = frozenset({SKIP_TRACKER_CREATED})

# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

#: The Slack channel message is recorded, not delivered. WF-112 owns Slack
#: delivery and may not be present, so this build writes a queued notification and
#: says so on the record.
NOTIFY_QUEUED = "queued"
NOTIFY_SKIPPED = "skipped"
NOTIFICATION_STATES: tuple[str, ...] = (NOTIFY_QUEUED, NOTIFY_SKIPPED)
DELIVERED_BY_THIS_PRODUCT = False
DELIVERED_BY_THIS_PRODUCT_QUOTE = (
    "The Slack app in Intercom is the delivery path; this build records the "
    "notification and leaves delivery to the Slack surface."
)

# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #

TOPIC_TICKET_CREATED = "ticket.created"
TOPIC_TEAM_ASSIGNED = "ticket.team.assigned"
TOPIC_ADMIN_ASSIGNED = "ticket.admin.assigned"
TOPIC_STATE_UPDATED = "ticket.state.updated"
TOPIC_CLOSED = "ticket.closed"
TOPIC_RESOLVED = "ticket.resolved"
TOPIC_CONTACT_ATTACHED = "ticket.contact.attached"
WEBHOOK_TOPICS: tuple[str, ...] = (
    TOPIC_TICKET_CREATED,
    TOPIC_TEAM_ASSIGNED,
    TOPIC_ADMIN_ASSIGNED,
    TOPIC_STATE_UPDATED,
    TOPIC_CLOSED,
    TOPIC_RESOLVED,
    TOPIC_CONTACT_ATTACHED,
)

#: Payload shapes. "Note the ticket.closed vs ticket.resolved payload-shape
#: difference in v2.15+". The version boundary is named but not dated, so it is a
#: recorded inference.
PAYLOAD_FLAT = "flat_item"
PAYLOAD_NESTED = "nested_under_ticket"
PAYLOAD_SHAPES: tuple[str, ...] = (PAYLOAD_FLAT, PAYLOAD_NESTED)
#: "the Ticket is nested under a ticket field on data.item".
NESTED_TICKET_TOPICS: frozenset[str] = frozenset({TOPIC_CLOSED})
WEBHOOK_PAYLOAD_SHAPES: dict[str, str] = {
    topic: (PAYLOAD_NESTED if topic in NESTED_TICKET_TOPICS else PAYLOAD_FLAT)
    for topic in WEBHOOK_TOPICS
}
#: The version at which the two shapes diverge. The research names the boundary
#: and dates nothing, so the default is recorded in the inferences.
API_VERSION = "2.15"
API_VERSION_BOUNDARY = "v2.15+"

# --------------------------------------------------------------------------- #
# Activity entries
# --------------------------------------------------------------------------- #

ACTIVITY_TICKET_CREATED = "Ticket created"
ACTIVITY_CONVERSATION_LINKED = "Conversation linked"
ACTIVITY_CONVERSATION_UNLINKED = "Conversation unlinked"
ACTIVITY_TEAM_ASSIGNED = "Owning team assigned"
ACTIVITY_ADMIN_ASSIGNED = "Admin assigned"
ACTIVITY_TAG_ADDED = "Tag added"
ACTIVITY_TAG_REMOVED = "Tag removed"
ACTIVITY_REPLIED = "Admin replied"
ACTIVITY_STATE_CHANGED = "State changed"
ACTIVITY_CONVERSATION_CLOSED = "Conversation closed"
ACTIVITY_WEBHOOK = "Webhook emitted"
ACTIVITY_TYPES: tuple[str, ...] = (
    ACTIVITY_TICKET_CREATED,
    ACTIVITY_CONVERSATION_LINKED,
    ACTIVITY_CONVERSATION_UNLINKED,
    ACTIVITY_TEAM_ASSIGNED,
    ACTIVITY_ADMIN_ASSIGNED,
    ACTIVITY_TAG_ADDED,
    ACTIVITY_TAG_REMOVED,
    ACTIVITY_REPLIED,
    ACTIVITY_STATE_CHANGED,
    ACTIVITY_CONVERSATION_CLOSED,
    ACTIVITY_WEBHOOK,
)

# --------------------------------------------------------------------------- #
# SLA policy fields
# --------------------------------------------------------------------------- #

#: "Apply SLA" is the researched action. WF-108 owns SLA timers, so this build
#: stores a policy name and a due instant and depends on nothing in WF-108.
SLA_METRIC_TIME_TO_RESOLUTION = "time_to_resolution"
SLA_METRIC_TIME_TO_CLOSE = "time_to_close"
SLA_METRICS: tuple[str, ...] = (SLA_METRIC_TIME_TO_RESOLUTION, SLA_METRIC_TIME_TO_CLOSE)
DEFAULT_SLA_METRIC = SLA_METRIC_TIME_TO_RESOLUTION
DEFAULT_SLA_HOURS = 24

# --------------------------------------------------------------------------- #
# Errors, with their status
# --------------------------------------------------------------------------- #

#: Every refusal code this workflow publishes, and the status it answers with.
#: A missing row is never a server fault.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "invalid_ticket_request": (400, "A value or a step this workflow will not accept."),
    "ticket_conflict": (409, "A well-formed request that conflicts with current state."),
    "ticket_not_found": (404, "The named ticket, trigger, tag or policy does not exist."),
    "trigger_not_found": (404, "The named trigger does not exist."),
    "tag_not_in_workspace": (400, "A tag the workspace does not have."),
    "sla_policy_not_found": (404, "The named SLA policy does not exist."),
    "team_unknown": (400, "A team outside the known set."),
    "ticket_type_unknown": (400, "A ticket type outside the researched set."),
    "ticket_state_unknown": (400, "A ticket state outside the researched set."),
    "conversation_not_found": (404, "The conversation to link does not exist."),
}

# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

SOURCE_WORKFLOW_HELP = "https://www.intercom.com/help/en/articles/7434613-how-to-trigger-a-workflow"
SOURCE_TICKETS_API = (
    "https://developers.intercom.com/docs/references/rest-api/api.intercom.io/tickets"
)
SOURCE_WEBHOOK_MODELS = "https://developers.intercom.com/docs/references/webhooks/webhook-models"
SOURCES: tuple[dict[str, str], ...] = (
    {"id": "trigger_a_workflow", "url": SOURCE_WORKFLOW_HELP},
    {"id": "tickets_api", "url": SOURCE_TICKETS_API},
    {"id": "webhook_models", "url": SOURCE_WEBHOOK_MODELS},
)


def _researched(
    term: str, value: Any, quote: str, source: str = SOURCE_TICKETS_API
) -> dict[str, Any]:
    return {"term": term, "value": value, "quote": quote, "source": source}


def published_vocabulary() -> dict[str, Any]:
    """Every published term, as data, so a client renders from one source.

    A picker built from this endpoint cannot drift from the validator that
    enforces the same list. That is the whole reason the term is served rather
    than compiled into the page.
    """

    return {
        "trigger_kinds": _researched(
            "trigger_kinds",
            list(TRIGGER_KINDS),
            "Create a Workflow with the trigger 'A ticket is created'. Optionally add a "
            "second Workflow on 'Teammate changes the state of a ticket'.",
            source=SOURCE_WORKFLOW_HELP,
        ),
        "ticket_types": _researched(
            "ticket_types",
            list(TICKET_TYPES),
            "In the audience/conditions, narrow to the relevant ticket type (e.g. "
            "escalation tickets).",
            source=SOURCE_WORKFLOW_HELP,
        ),
        "ticket_states": _researched(
            "ticket_states",
            list(TICKET_STATES),
            RESOLVED_IS_STATE_BASED + " " + CLOSED_IS_SEPARATE,
            source=SOURCE_WEBHOOK_MODELS,
        ),
        "actions": _researched(
            "actions",
            list(ACTION_KINDS),
            "Add actions: notify the responsible team in Slack, change the assigned team, "
            "apply an SLA, tag the linked conversation.",
            source=SOURCE_WORKFLOW_HELP,
        ),
        "background_only": _researched(
            "background_only",
            True,
            BACKGROUND_ONLY,
            source=SOURCE_WORKFLOW_HELP,
        ),
        "conversion_gate": _researched(
            "conversion_gate",
            list(sorted(CONVERSION_REQUIRED_ACTIONS)),
            CONVERSION_GATE,
            source=SOURCE_WORKFLOW_HELP,
        ),
        "tracker_exemption": _researched(
            "tracker_exemption",
            VIA_TRACKER,
            TRACKER_EXEMPTION,
            source=SOURCE_WORKFLOW_HELP,
        ),
        "closed_payload_shape": _researched(
            "closed_payload_shape",
            PAYLOAD_NESTED,
            CLOSED_PAYLOAD_SHAPE,
            source=SOURCE_WEBHOOK_MODELS,
        ),
        "webhook_topics": _researched(
            "webhook_topics",
            list(WEBHOOK_TOPICS),
            "Webhooks: ticket.created, ticket.team.assigned, ticket.admin.assigned, "
            "ticket.state.updated, ticket.closed, ticket.resolved, ticket.contact.attached.",
            source=SOURCE_WEBHOOK_MODELS,
        ),
        "api_version_boundary": _researched(
            "api_version_boundary",
            API_VERSION_BOUNDARY,
            "Note the ticket.closed vs ticket.resolved payload-shape difference in v2.15+.",
            source=SOURCE_TICKETS_API,
        ),
        "teams": {"term": "teams", "value": list(TEAMS), "labels": dict(TEAM_LABELS)},
        "created_via": {"term": "created_via", "value": list(CREATED_VIA)},
        "notification_states": {"term": "notification_states", "value": list(NOTIFICATION_STATES)},
        "webhook_payload_shapes": {
            "term": "webhook_payload_shapes",
            "value": dict(WEBHOOK_PAYLOAD_SHAPES),
        },
        "skip_reasons": {"term": "skip_reasons", "value": list(SKIP_REASONS)},
        "action_outcomes": {"term": "action_outcomes", "value": list(ACTION_OUTCOMES)},
        "sources": {"term": "sources", "value": [dict(item) for item in SOURCES]},
    }
