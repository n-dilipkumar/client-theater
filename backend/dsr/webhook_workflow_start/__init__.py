"""WF-114: start a workflow from an external system's webhook.

The domain behind the researched workflow. The research document is the
specification: ``docs/research/digital-sales-room-workflows/wf/WF-114.md``, built
from section 11 of ``docs/research/raw/automation-engagement.md``.

The shape of it
---------------
A team creates a workflow and adds an **Inbound Webhook** trigger. This package
mints a unique webhook URL for that trigger, and the trigger - not the workflow -
is the unit of uniqueness. The team copies the URL and its one-time signing value
into an external service, declares the mapping that binds payload paths to
workflow variables, saves the trigger, and publishes the workflow. From then on
the external service sends a signed POST, GET or PUT to the URL; the payload of
any shape is parsed, bound and de-duplicated, and a run starts.

* :mod:`~dsr.webhook_workflow_start.vocabulary` - the researched vocabulary: the
  three methods and where each carries its payload, the variable types, the
  mapping operators, the states, the reasons, the collection names.
* :mod:`~dsr.webhook_workflow_start.signing` - the inbound signature check, which
  is WF-113's shipped rule held by a test, with no optional mode because this
  build fails closed.
* :mod:`~dsr.webhook_workflow_start.rules` - the pure half: parse any shape,
  resolve a dotted path, compare, coerce a value to a declared type, plan the
  bindings.
* :mod:`~dsr.webhook_workflow_start.engine` - :class:`WebhookWorkflowStart`, the
  store-facing half.
* :mod:`~dsr.webhook_workflow_start.errors` - one hierarchy, one HTTP handler.
* :mod:`~dsr.webhook_workflow_start.inferences` - every judgement call, named and
  served.

The public surface is small: build a :class:`WebhookWorkflowStart` over a
:class:`~dsr.store.RecordStore` and call it. Nothing here imports a framework or
an HTTP layer, so it can be unit tested against a plain store, and every writing
method takes a required ``source=`` so the audit row names the route that served
the write.

What was found, and not claimed
-------------------------------
The issue asks whether the repository already owns a workflow surface to mount
this on. It does own one - ``dsr/crm_workflows`` ships a ``WorkflowEngine`` - and
this workflow deliberately does not displace it: that engine's trigger is a
contact matching a saved activity criterion and its actions are CRM writes, with
no generated inbound URL and no variable store mapped from an external payload.
The finding is recorded in the decision register rather than skipped, per
``the-domain-package-is-new-not-crm-workflows``.
"""

from __future__ import annotations

from dsr.webhook_workflow_start import inferences, rules, signing, vocabulary
from dsr.webhook_workflow_start.engine import ERRORS, WebhookWorkflowStart
from dsr.webhook_workflow_start.errors import (
    BodyNotAllowedOnGet,
    InvalidTrigger,
    InvalidVariable,
    InvalidWorkflow,
    MalformedPayload,
    MethodNotAllowed,
    RequiredMappingUnresolved,
    SignatureMismatch,
    SignatureMissing,
    TriggerNotSaved,
    UnknownEndpoint,
    UnknownInvocation,
    UnknownRun,
    UnknownTrigger,
    UnknownVariable,
    UnknownWorkflow,
    WebhookWorkflowError,
    WorkflowNotPublished,
)
from dsr.webhook_workflow_start.signing import (
    MAX_AGE_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    headers_for,
    sign,
    signing_input,
    verify,
)
from dsr.webhook_workflow_start.vocabulary import (
    ALL_COLLECTIONS,
    COLLECTION_DEDUPE,
    COLLECTION_INVOCATION,
    COLLECTION_RUN,
    COLLECTION_TRIGGER,
    COLLECTION_VARIABLE,
    COLLECTION_WORKFLOW,
    DOCUMENTED_ACTIONS,
    DOCUMENTED_PAYLOAD,
    ENDPOINT_PATH_TEMPLATE,
    FAIL_CLOSED,
    METHOD_GET,
    METHOD_POST,
    METHOD_PUT,
    METHODS,
    MODE_LIVE,
    MODE_TEST,
    MODES,
    PREMIUM_TRIGGER_IS_NOT_A_GATE,
    REASONS,
    REUSED_FROM_WF113,
    ROUTER_PREFIX,
    RUN_STARTED,
    SHARED_TRIGGER_STATES,
    SIGNED_INPUT_NOTE,
    SIGNING_RULE,
    STATE_ACCEPTED,
    STATE_DUPLICATE,
    STATE_REJECTED,
    STATE_UNMAPPED,
    STATES,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    STATUSES,
    TEST_MODE_PARAM,
    TICKET,
    TRIGGER_KIND,
    TRIGGER_LABEL,
    VARIABLE_TYPE_IDS,
    VARIABLE_TYPES,
)

__all__ = [
    "ALL_COLLECTIONS",
    "BodyNotAllowedOnGet",
    "COLLECTION_DEDUPE",
    "COLLECTION_INVOCATION",
    "COLLECTION_RUN",
    "COLLECTION_TRIGGER",
    "COLLECTION_VARIABLE",
    "COLLECTION_WORKFLOW",
    "DOCUMENTED_ACTIONS",
    "DOCUMENTED_PAYLOAD",
    "ENDPOINT_PATH_TEMPLATE",
    "ERRORS",
    "FAIL_CLOSED",
    "InvalidTrigger",
    "InvalidVariable",
    "InvalidWorkflow",
    "MAX_AGE_SECONDS",
    "METHODS",
    "METHOD_GET",
    "METHOD_POST",
    "METHOD_PUT",
    "MODES",
    "MODE_LIVE",
    "MODE_TEST",
    "MalformedPayload",
    "MethodNotAllowed",
    "PREMIUM_TRIGGER_IS_NOT_A_GATE",
    "REASONS",
    "REUSED_FROM_WF113",
    "ROUTER_PREFIX",
    "RUN_STARTED",
    "RequiredMappingUnresolved",
    "SHARED_TRIGGER_STATES",
    "SIGNATURE_HEADER",
    "SIGNED_INPUT_NOTE",
    "SIGNING_RULE",
    "STATES",
    "STATUSES",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "STATE_ACCEPTED",
    "STATE_DUPLICATE",
    "STATE_REJECTED",
    "STATE_UNMAPPED",
    "SignatureMismatch",
    "SignatureMissing",
    "TEST_MODE_PARAM",
    "TICKET",
    "TIMESTAMP_HEADER",
    "TRIGGER_KIND",
    "TRIGGER_LABEL",
    "TriggerNotSaved",
    "UnknownEndpoint",
    "UnknownInvocation",
    "UnknownRun",
    "UnknownTrigger",
    "UnknownVariable",
    "UnknownWorkflow",
    "VARIABLE_TYPES",
    "VARIABLE_TYPE_IDS",
    "WebhookWorkflowError",
    "WebhookWorkflowStart",
    "WorkflowNotPublished",
    "headers_for",
    "inferences",
    "rules",
    "sign",
    "signing",
    "signing_input",
    "verify",
    "vocabulary",
]
