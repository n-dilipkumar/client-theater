"""WF-113: the researched vocabulary, served as data.

Everything here is quoted from or directly derived from
``docs/research/digital-sales-room-workflows/wf/WF-113.md`` (section 10 of
``docs/research/raw/automation-engagement.md``). A client renders its action
picker, its identifier picker, its value-type picker and its rate-limit table
from :func:`describe` rather than from a list compiled into the page, so a value
added server-side reaches every client at once and the page can never disagree
with the validator about what is legal.

The two facts this workflow turns on
------------------------------------
**The webhook is the subject of the automation, never the person.** Quoted: *"You
cannot reference existing customer attributes in webhook-triggered automations
because people aren't the subject of the automation: the webhook is."* And: *"Any
attempt to reference customer data will return ``""``"*. :data:`CUSTOMER_DATA_PLACEHOLDER`
is that empty string, and :func:`dsr.webhook_ingest.rules.resolve_expression`
returns it for any path that does not start at the trigger object.

**Deletes are events.** Quoted: *"the Pipelines API only uses ``POST``
operations. You'll perform deletes and other operations by sending events with
specific names."* The two names the research gives are in :data:`SEMANTIC_EVENTS`,
so a Delete or Suppress is an event this workflow fans out like any other.

ASCII only
----------
Every string here is ASCII. A single U+2192 RIGHTWARDS ARROW in one recovered
feature's seed return string broke the whole seeder on a Windows console, so this
workflow writes no character outside cp1252 anywhere, and a test encodes the seed
summary as cp1252 to prove it.
"""

from __future__ import annotations

from typing import Any

#: The prefix the WF-113 feature router mounts, and the only prefix this workflow
#: serves. Duplicated from the feature module's ``router.prefix`` so this domain
#: package does not have to import a FastAPI router to know where it is published.
#: The feature module asserts at import time that the two agree.
ROUTER_PREFIX = "/api/wf-113"

TICKET = "WF-113"

#: The generated webhook URL, per automation. The research says the URL is
#: generated and copied into the external service: *"Customer.io generates a
#: Webhook URL. You'll provide this URL to the service or platform that you
#: capture data from"*.
ENDPOINT_PATH_TEMPLATE = "/api/wf-113/hooks/{token}"

# --------------------------------------------------------------------------- #
# The four researched actions, plus the optional Slack block
# --------------------------------------------------------------------------- #

ACTION_SEND_EVENT = "send_event"
ACTION_CREATE_OR_UPDATE_PERSON = "create_or_update_person"
ACTION_BATCH_UPDATE = "batch_update"
ACTION_SEND_DATA = "send_data"
ACTION_SLACK = "slack"

#: The actions a user drags in, in the order the user flow lists them. Slack is
#: listed last and marked optional, because the flow says "Optionally append a
#: Slack block, because webhook automations have no person to message".
ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": ACTION_SEND_EVENT,
        "label": "Send Event",
        "produces": "an internal event with a name and a data object",
        "requires": "an event name",
        "optional": False,
        "quote": "Send Event writes a person-scoped event (name + data) into the workspace",
    },
    {
        "key": ACTION_CREATE_OR_UPDATE_PERSON,
        "label": "Create or Update Person",
        "produces": "a person upsert keyed on an identifier",
        "requires": "a resolved identifier",
        "optional": False,
        "quote": "Create or Update Person",
    },
    {
        "key": ACTION_BATCH_UPDATE,
        "label": "Batch Update",
        "produces": "one event per matched member, up to the stated ceiling",
        "requires": "a group of members",
        "optional": False,
        "quote": "A batch update lets you associate your incoming data with a group of up to 1000 people",
    },
    {
        "key": ACTION_SEND_DATA,
        "label": "Send data",
        "produces": "a forward of the trigger data to a downstream target",
        "requires": "a target URL",
        "optional": False,
        "quote": "Use the Send data action to send a webhook. This forwards data to another service outside Customer.io",
    },
    {
        "key": ACTION_SLACK,
        "label": "Slack",
        "produces": "a channel message",
        "requires": "a channel",
        "optional": True,
        "quote": "Optionally append a Slack block, because webhook automations have no person to message",
    },
)

ACTION_KEYS: tuple[str, ...] = tuple(row["key"] for row in ACTIONS)

# --------------------------------------------------------------------------- #
# Value types: Liquid and JavaScript
# --------------------------------------------------------------------------- #

VALUE_TYPE_LIQUID = "liquid"
VALUE_TYPE_JAVASCRIPT = "javascript"

#: Both are shipped. The research quotes one example of each: Liquid
#: ``{{trigger.identifiers.id}}`` and JavaScript ``return triggers.identifiers.id;``.
#: The JavaScript example uses the plural root ``triggers``; the Liquid example
#: uses the singular ``trigger``. Both are accepted, because a build that accepted
#: only one spelling would reject the exact string the research quotes.
VALUE_TYPES: tuple[dict[str, Any], ...] = (
    {
        "id": VALUE_TYPE_LIQUID,
        "label": "Liquid",
        "example": "{{trigger.identifiers.id}}",
        "root": "trigger",
    },
    {
        "id": VALUE_TYPE_JAVASCRIPT,
        "label": "JavaScript",
        "example": "return triggers.identifiers.id;",
        "root": "triggers",
    },
)

VALUE_TYPE_IDS: tuple[str, ...] = tuple(row["id"] for row in VALUE_TYPES)

# --------------------------------------------------------------------------- #
# The identifier types a person is matched on
# --------------------------------------------------------------------------- #

#: Quoted: identifier type is "id, email, phone, or custom". Each type names the
#: paths this build reads out of the *incoming* payload - not out of an existing
#: person, because the research forbids reading the person.
IDENTIFIER_TYPES: tuple[dict[str, Any], ...] = (
    {
        "id": "id",
        "label": "id",
        "paths": ("identifiers.id", "id", "person_id", "customer_id"),
    },
    {
        "id": "email",
        "label": "email",
        "paths": ("identifiers.email", "email", "person_email", "customer_email"),
    },
    {
        "id": "phone",
        "label": "phone",
        "paths": ("identifiers.phone", "phone", "phone_number"),
    },
    {
        "id": "custom",
        "label": "custom",
        "paths": (),
    },
)

IDENTIFIER_TYPE_IDS: tuple[str, ...] = tuple(row["id"] for row in IDENTIFIER_TYPES)

# --------------------------------------------------------------------------- #
# Deletes are events
# --------------------------------------------------------------------------- #

#: The two semantic event names the research gives: *"semantic event names such as
#: Delete Person / Suppress Person"*.
SEMANTIC_EVENTS: tuple[str, ...] = ("Delete Person", "Suppress Person")

# --------------------------------------------------------------------------- #
# The stated batch ceiling
# --------------------------------------------------------------------------- #

#: Quoted twice: "up to 1000 people". A stated limit, not a tuning knob, so an
#: over-limit batch is refused rather than truncated.
BATCH_LIMIT = 1000

# --------------------------------------------------------------------------- #
# Rate limits, quoted with their numbers
# --------------------------------------------------------------------------- #

#: Quoted: "Track/Pipelines 3,000 req / 3 s (soft); App API 10 req/s;
#: transactional 3,000 req / 3 s". The soft one is marked soft: this build
#: publishes the numbers and does not enforce them, because the research calls the
#: Track/Pipelines bound soft and no vendor behaviour is described for crossing it.
RATE_LIMITS: tuple[dict[str, Any], ...] = (
    {
        "surface": "pipelines",
        "label": "Track / Pipelines",
        "limit": 3000,
        "window_seconds": 3,
        "soft": True,
        "quote": "Track/Pipelines 3,000 req / 3 s (soft)",
    },
    {
        "surface": "app_api",
        "label": "App API",
        "limit": 10,
        "window_seconds": 1,
        "soft": False,
        "quote": "App API 10 req/s",
    },
    {
        "surface": "transactional",
        "label": "Transactional",
        "limit": 3000,
        "window_seconds": 3,
        "soft": False,
        "quote": "transactional 3,000 req / 3 s",
    },
)

# --------------------------------------------------------------------------- #
# The hard constraint about the person
# --------------------------------------------------------------------------- #

#: Quoted: *"Any attempt to reference customer data will return ``""``"*. The
#: empty string, and not an error, so a mapping that references the person
#: resolves and renders blank rather than failing the whole ingest.
CUSTOMER_DATA_PLACEHOLDER = ""

WEBHOOK_IS_THE_SUBJECT = (
    "You cannot reference existing customer attributes in webhook-triggered "
    "automations because people aren't the subject of the automation: the webhook is."
)

# --------------------------------------------------------------------------- #
# Collections: plain JSON in records.data
# --------------------------------------------------------------------------- #

COLLECTION_AUTOMATION = "webhook_automation"
COLLECTION_ENDPOINT = "webhook_endpoint"
COLLECTION_TRIGGER = "webhook_trigger"
COLLECTION_EVENT = "webhook_event"
COLLECTION_DELIVERY = "webhook_fanout_delivery"
COLLECTION_DEDUPE = "webhook_ingest_dedupe"

ALL_COLLECTIONS: tuple[str, ...] = (
    COLLECTION_AUTOMATION,
    COLLECTION_ENDPOINT,
    COLLECTION_TRIGGER,
    COLLECTION_EVENT,
    COLLECTION_DELIVERY,
    COLLECTION_DEDUPE,
)

# --------------------------------------------------------------------------- #
# Automation lifecycle
# --------------------------------------------------------------------------- #

#: A new automation lands a draft, because the researched flow is *"Review items
#: -> Start Automation"* and starting is a deliberate second act.
STATUS_DRAFT = "draft"
STATUS_STARTED = "started"
STATUSES: tuple[str, ...] = (STATUS_DRAFT, STATUS_STARTED)

# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #

#: The delivery vocabulary is the shipped outbound one, reused rather than
#: re-declared. ``dsr.meeting_webhook_fanout.vocabulary`` defines the same three
#: words, and a test in ``test_wf113.py`` pins them to each other so the two
#: cannot drift.
OUTCOME_DELIVERED = "delivered"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"
OUTCOMES: tuple[str, ...] = (OUTCOME_DELIVERED, OUTCOME_FAILED, OUTCOME_SKIPPED)

SKIP_NO_IDENTIFIER = "no_identifier"
SKIP_NO_BATCH = "no_batch"
SKIP_NO_CHANNEL = "no_channel"
SKIP_NO_TARGET = "no_target"
SKIP_REASONS: tuple[str, ...] = (
    SKIP_NO_IDENTIFIER,
    SKIP_NO_BATCH,
    SKIP_NO_CHANNEL,
    SKIP_NO_TARGET,
)

FAIL_EVENT_NAME_MISSING = "event_name_missing"

#: The state of one ingest, from the sender's side. ``duplicate`` is an outcome of
#: a successful call, not an error: a retry the provider repeated is normal.
TRIGGER_ACCEPTED = "accepted"
TRIGGER_DUPLICATE = "duplicate"
TRIGGER_REJECTED = "rejected"
TRIGGER_STATES: tuple[str, ...] = (TRIGGER_ACCEPTED, TRIGGER_DUPLICATE, TRIGGER_REJECTED)


def action(key: str) -> dict[str, Any] | None:
    """One action row, or ``None`` when this build does not serve it."""
    for row in ACTIONS:
        if row["key"] == key:
            return row
    return None


def is_known_action(key: str) -> bool:
    return action(key) is not None


def is_known_identifier_type(identifier_type: str) -> bool:
    return identifier_type in IDENTIFIER_TYPE_IDS


def is_known_value_type(value_type: str) -> bool:
    return value_type in VALUE_TYPE_IDS


def identifier_paths(identifier_type: str) -> tuple[str, ...]:
    """The incoming-payload paths one identifier type reads."""
    for row in IDENTIFIER_TYPES:
        if row["id"] == identifier_type:
            return tuple(row["paths"])
    return ()


def describe() -> dict[str, Any]:
    """Everything above, as one served document."""
    return {
        "ticket": TICKET,
        "router_prefix": ROUTER_PREFIX,
        "endpoint_path_template": ENDPOINT_PATH_TEMPLATE,
        "actions": [dict(row) for row in ACTIONS],
        "action_keys": list(ACTION_KEYS),
        "value_types": [dict(row) for row in VALUE_TYPES],
        "identifier_types": [
            {"id": row["id"], "label": row["label"], "paths": list(row["paths"])}
            for row in IDENTIFIER_TYPES
        ],
        "semantic_events": list(SEMANTIC_EVENTS),
        "batch_limit": BATCH_LIMIT,
        "rate_limits": [dict(row) for row in RATE_LIMITS],
        "statuses": list(STATUSES),
        "outcomes": list(OUTCOMES),
        "skip_reasons": list(SKIP_REASONS),
        "trigger_states": list(TRIGGER_STATES),
        "collections": list(ALL_COLLECTIONS),
        "webhook_is_the_subject": WEBHOOK_IS_THE_SUBJECT,
        "customer_data_placeholder": CUSTOMER_DATA_PLACEHOLDER,
        "rate_limits_enforced": False,
        "batch_limit_is_a_ceiling": True,
        "slack_is_optional": True,
        "send_data_is_the_bridge": (
            "A Send data action returns the data to another system, closing the loop "
            "the research describes as a bridge between data platforms."
        ),
    }


__all__ = [
    "ACTION_BATCH_UPDATE",
    "ACTION_CREATE_OR_UPDATE_PERSON",
    "ACTION_KEYS",
    "ACTION_SEND_DATA",
    "ACTION_SEND_EVENT",
    "ACTION_SLACK",
    "ACTIONS",
    "ALL_COLLECTIONS",
    "BATCH_LIMIT",
    "COLLECTION_AUTOMATION",
    "COLLECTION_DEDUPE",
    "COLLECTION_DELIVERY",
    "COLLECTION_ENDPOINT",
    "COLLECTION_EVENT",
    "COLLECTION_TRIGGER",
    "CUSTOMER_DATA_PLACEHOLDER",
    "ENDPOINT_PATH_TEMPLATE",
    "FAIL_EVENT_NAME_MISSING",
    "IDENTIFIER_TYPES",
    "IDENTIFIER_TYPE_IDS",
    "OUTCOMES",
    "OUTCOME_DELIVERED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "RATE_LIMITS",
    "ROUTER_PREFIX",
    "SEMANTIC_EVENTS",
    "SKIP_NO_BATCH",
    "SKIP_NO_CHANNEL",
    "SKIP_NO_IDENTIFIER",
    "SKIP_NO_TARGET",
    "SKIP_REASONS",
    "STATUSES",
    "STATUS_DRAFT",
    "STATUS_STARTED",
    "TICKET",
    "TRIGGER_ACCEPTED",
    "TRIGGER_DUPLICATE",
    "TRIGGER_REJECTED",
    "TRIGGER_STATES",
    "VALUE_TYPES",
    "VALUE_TYPE_IDS",
    "VALUE_TYPE_JAVASCRIPT",
    "VALUE_TYPE_LIQUID",
    "WEBHOOK_IS_THE_SUBJECT",
    "action",
    "describe",
    "identifier_paths",
    "is_known_action",
    "is_known_identifier_type",
    "is_known_value_type",
]
