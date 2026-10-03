"""WF-044: emit a webhook out of the CRM when a deal stage changes.

The domain behind the researched workflow. The research document is the
specification: ``docs/research/digital-sales-room-workflows/wf/WF-044.md``
(section 11 of ``docs/research/raw/crm-integration.md``).

The shape of it
---------------
A rep builds the automation in the CRM - *Automation -> Workflows -> + -> Data ops ->
Send a webhook* - and the room's half of that is an inbound endpoint. This package
is that endpoint and the rules around it:

* :mod:`~dsr.crm_outbound_webhooks.vocabulary` - the researched vocabulary: two
  methods, one URL rule, three authentication types, two body modes, three
  objects, two permissions, the 1,000-per-app cap, and the outcome/reason
  vocabulary a client renders from.
* :mod:`~dsr.crm_outbound_webhooks.signing` - the three ways a request proves
  where it came from, and the one scheme here these three sources do not publish
  (the first entry in :mod:`~dsr.crm_outbound_webhooks.inferences` says so).
* :mod:`~dsr.crm_outbound_webhooks.payloads` - the versioned payload contract, and
  the one rule that reads both researched body shapes.
* :mod:`~dsr.crm_outbound_webhooks.engine` - :class:`WebhookEngine`, which is the
  whole second half of the flow: authenticate, resolve the record, update the deal
  panel, notify the rep.
* :mod:`~dsr.crm_outbound_webhooks.errors` - one hierarchy, one HTTP handler.
* :mod:`~dsr.crm_outbound_webhooks.inferences` - every judgement call, named,
  traceable, bounded, and served.

The public surface is deliberately small: build an engine over a
:class:`~dsr.store.RecordStore` and call it. The HTTP layer lives in
``backend/dsr/features/wf044_emit_a_webhook_out_of_the_crm_when_a_d.py`` so that
nothing here depends on a router, and every writing method takes a required
``source=`` so the audit row names the route that actually served the write.
"""

from __future__ import annotations

from dsr.crm_outbound_webhooks.engine import URI_TEMPLATE, WebhookEngine, uri_for
from dsr.crm_outbound_webhooks.errors import (
    AlreadyPublished,
    AutomationLimitReached,
    EmptyPayload,
    EndpointExists,
    EndpointNotPublished,
    EndpointStateConflict,
    InvalidAuthSetting,
    InvalidEndpointUrl,
    InvalidRequest,
    InvalidTrigger,
    MalformedBody,
    MalformedPayload,
    MissingAppId,
    MissingPermission,
    MissingSecret,
    NoEndpoint,
    ObjectMismatch,
    UnauthenticatedDelivery,
    UnknownAuthMode,
    UnknownAutomation,
    UnknownBodyMode,
    UnresolvedDeal,
    UnsupportedMethod,
    UnsupportedPayloadVersion,
    WebhookError,
)
from dsr.crm_outbound_webhooks.inferences import INFERENCES, by_id, describe as describe_inferences
from dsr.crm_outbound_webhooks.payloads import (
    InboundPayload,
    coerce_body,
    read_payload,
    sample_body,
)
from dsr.crm_outbound_webhooks.signing import canonical_string, sign, sign_hex, verify
from dsr.crm_outbound_webhooks.vocabulary import (
    API_KEY_LOCATIONS,
    AUTH_MODES,
    BODY_INHERIT,
    BODY_MODES,
    COLLECTION_AUTOMATION,
    COLLECTION_DEAL,
    COLLECTION_DELIVERY,
    COLLECTION_ENDPOINT,
    COLLECTION_NOTICE,
    CONTRACT_VERSION,
    EFFECT_NONE,
    EFFECT_PROPERTIES_ONLY,
    EFFECT_STAGE_CHANGED,
    EFFECT_STAGE_UNCHANGED,
    METHODS,
    OBJECTS,
    OUTCOME_ACCEPTED,
    OUTCOME_DUPLICATE,
    OUTCOME_REFUSED,
    PERMISSIONS,
    PUBLISH_PERMISSIONS,
    REASONS,
    SETUP_PERMISSIONS,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    SUBSCRIPTION_LIMIT_PER_APP,
    describe as describe_vocabulary,
)

__all__ = [
    "API_KEY_LOCATIONS",
    "AUTH_MODES",
    "AlreadyPublished",
    "AutomationLimitReached",
    "BODY_INHERIT",
    "BODY_MODES",
    "COLLECTION_AUTOMATION",
    "COLLECTION_DEAL",
    "COLLECTION_DELIVERY",
    "COLLECTION_ENDPOINT",
    "COLLECTION_NOTICE",
    "CONTRACT_VERSION",
    "EFFECT_NONE",
    "EFFECT_PROPERTIES_ONLY",
    "EFFECT_STAGE_CHANGED",
    "EFFECT_STAGE_UNCHANGED",
    "EmptyPayload",
    "EndpointExists",
    "EndpointNotPublished",
    "EndpointStateConflict",
    "INFERENCES",
    "InboundPayload",
    "InvalidAuthSetting",
    "InvalidEndpointUrl",
    "InvalidRequest",
    "InvalidTrigger",
    "METHODS",
    "MalformedBody",
    "MalformedPayload",
    "MissingAppId",
    "MissingPermission",
    "MissingSecret",
    "NoEndpoint",
    "OBJECTS",
    "OUTCOME_ACCEPTED",
    "OUTCOME_DUPLICATE",
    "OUTCOME_REFUSED",
    "ObjectMismatch",
    "PERMISSIONS",
    "PUBLISH_PERMISSIONS",
    "REASONS",
    "SETUP_PERMISSIONS",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "SUBSCRIPTION_LIMIT_PER_APP",
    "URI_TEMPLATE",
    "UnauthenticatedDelivery",
    "UnknownAutomation",
    "UnknownAuthMode",
    "UnknownBodyMode",
    "UnresolvedDeal",
    "UnsupportedMethod",
    "UnsupportedPayloadVersion",
    "WebhookEngine",
    "WebhookError",
    "by_id",
    "canonical_string",
    "coerce_body",
    "describe_inferences",
    "describe_vocabulary",
    "read_payload",
    "sample_body",
    "sign",
    "sign_hex",
    "uri_for",
    "verify",
]
