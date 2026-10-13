"""WF-113: ingest third-party data by webhook and fan it out as events.

The domain behind the researched workflow. The research document is the
specification: ``docs/research/digital-sales-room-workflows/wf/WF-113.md``, built
from section 10 of ``docs/research/raw/automation-engagement.md``.

The shape of it
---------------
A team creates an automation in a room and this package generates a webhook URL
for it. An external service POSTs JSON of any shape to that URL. The package
parses the body into a Trigger data object, de-duplicates it, and fans it out as
internal events, which downstream automations consume.

* :mod:`~dsr.webhook_ingest.vocabulary` - the researched vocabulary: the four
  actions plus the optional Slack block, the two value types, the identifier
  types, the batch ceiling, the quoted rate limits, the collection names.
* :mod:`~dsr.webhook_ingest.signing` - the inbound signature check, which is
  WF-066's shipped rule with a neutral header namespace and a receiver's window.
* :mod:`~dsr.webhook_ingest.rules` - the pure half: parse any shape, resolve a
  Liquid or JavaScript mapping, plan the fan-out.
* :mod:`~dsr.webhook_ingest.engine` - :class:`WebhookIngest`, the store-facing
  half.
* :mod:`~dsr.webhook_ingest.errors` - one hierarchy, one HTTP handler.
* :mod:`~dsr.webhook_ingest.inferences` - every judgement call, named and served.

The public surface is small: build a :class:`WebhookIngest` over a
:class:`~dsr.store.RecordStore` and call it. Nothing here imports a framework or
an HTTP layer, so it can be unit tested against a plain store, and every writing
method takes a required ``source=`` so the audit row names the route that served
the write.

The one hard constraint
-----------------------
The webhook is the subject of the automation, never the person. A mapping that
references customer data resolves to the empty string and does not error; see
:data:`~dsr.webhook_ingest.vocabulary.CUSTOMER_DATA_PLACEHOLDER`.
"""

from __future__ import annotations

from dsr.webhook_ingest import inferences, rules, signing, vocabulary
from dsr.webhook_ingest.engine import WebhookIngest
from dsr.webhook_ingest.errors import (
    AutomationNotStarted,
    BatchLimitExceeded,
    InvalidAutomation,
    InvalidValueType,
    MalformedPayload,
    SignatureMismatch,
    SignatureMissing,
    UnknownAction,
    UnknownAutomation,
    UnknownDelivery,
    UnknownEndpoint,
    UnknownEvent,
    UnknownTrigger,
    WebhookIngestError,
)
from dsr.webhook_ingest.signing import (
    MAX_AGE_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    headers_for,
    sign,
    signing_input,
    verify,
)
from dsr.webhook_ingest.vocabulary import (
    ACTIONS,
    ALL_COLLECTIONS,
    BATCH_LIMIT,
    COLLECTION_AUTOMATION,
    COLLECTION_DEDUPE,
    COLLECTION_DELIVERY,
    COLLECTION_ENDPOINT,
    COLLECTION_EVENT,
    COLLECTION_TRIGGER,
    CUSTOMER_DATA_PLACEHOLDER,
    ENDPOINT_PATH_TEMPLATE,
    IDENTIFIER_TYPES,
    OUTCOME_DELIVERED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOMES,
    RATE_LIMITS,
    ROUTER_PREFIX,
    SEMANTIC_EVENTS,
    SKIP_REASONS,
    STATUS_DRAFT,
    STATUS_STARTED,
    TICKET,
    TRIGGER_ACCEPTED,
    TRIGGER_DUPLICATE,
    TRIGGER_REJECTED,
    TRIGGER_STATES,
    VALUE_TYPE_JAVASCRIPT,
    VALUE_TYPE_LIQUID,
    VALUE_TYPES,
    WEBHOOK_IS_THE_SUBJECT,
)

__all__ = [
    "ACTIONS",
    "ALL_COLLECTIONS",
    "AutomationNotStarted",
    "BATCH_LIMIT",
    "BatchLimitExceeded",
    "COLLECTION_AUTOMATION",
    "COLLECTION_DEDUPE",
    "COLLECTION_DELIVERY",
    "COLLECTION_ENDPOINT",
    "COLLECTION_EVENT",
    "COLLECTION_TRIGGER",
    "CUSTOMER_DATA_PLACEHOLDER",
    "ENDPOINT_PATH_TEMPLATE",
    "IDENTIFIER_TYPES",
    "InvalidAutomation",
    "InvalidValueType",
    "MAX_AGE_SECONDS",
    "MalformedPayload",
    "OUTCOMES",
    "OUTCOME_DELIVERED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "RATE_LIMITS",
    "ROUTER_PREFIX",
    "SEMANTIC_EVENTS",
    "SIGNATURE_HEADER",
    "SKIP_REASONS",
    "STATUS_DRAFT",
    "STATUS_STARTED",
    "SignatureMismatch",
    "SignatureMissing",
    "TICKET",
    "TIMESTAMP_HEADER",
    "TRIGGER_ACCEPTED",
    "TRIGGER_DUPLICATE",
    "TRIGGER_REJECTED",
    "TRIGGER_STATES",
    "UnknownAction",
    "UnknownAutomation",
    "UnknownDelivery",
    "UnknownEndpoint",
    "UnknownEvent",
    "UnknownTrigger",
    "VALUE_TYPES",
    "VALUE_TYPE_JAVASCRIPT",
    "VALUE_TYPE_LIQUID",
    "WEBHOOK_IS_THE_SUBJECT",
    "WebhookIngest",
    "WebhookIngestError",
    "headers_for",
    "inferences",
    "rules",
    "sign",
    "signing",
    "signing_input",
    "verify",
    "vocabulary",
]
