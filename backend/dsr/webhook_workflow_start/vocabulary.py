"""WF-114: the researched vocabulary, served as data.

Everything here is quoted from or directly derived from
``docs/research/digital-sales-room-workflows/wf/WF-114.md`` (section 11 of
``docs/research/raw/automation-engagement.md``). A client renders its trigger
picker, its variable-type picker, its mapping-operator picker and its method
table from :func:`describe` rather than from a list compiled into the page, so a
value added server-side reaches every client at once and the page can never
disagree with the validator about what is legal.

The two facts this workflow turns on
------------------------------------
**The URL is unique for each trigger, not for each workflow.** Quoted: *"A unique
Webhook URL will be generated for your workflow. Copy this URL for use in
external applications."* and *"(unique for each trigger)"*. So the unit of
uniqueness is the trigger, and :data:`ENDPOINT_PATH_TEMPLATE` resolves a token
that names exactly one trigger.

**The inbound trigger accepts POST, GET and PUT.** Quoted: *"The Inbound Webhook
Trigger captures incoming POST/GET/PUT requests sent to a HighLevel Webhook
URL"*. All three are served. A GET carries no body in HTTP, so its payload is the
query string; see :data:`METHOD_PAYLOAD_SOURCES` and the decision register in
:mod:`dsr.webhook_workflow_start.inferences`.

Reuse, not re-declaration
-------------------------
The signing rule, the signature and timestamp header names, the freshness
window, the endpoint-token shape and the dedupe-by-fingerprint rule are WF-113's.
This package may not import another workflow's package (the architectural guard
names that defect), so the rule is declared here and a test in
``backend/tests/test_wf114.py`` pins every one of these names to
``dsr.webhook_ingest`` so the two cannot drift.

ASCII only
----------
Every string here is ASCII. A single U+2192 RIGHTWARDS ARROW in one recovered
feature's seed return string broke the whole seeder on a Windows console, so this
workflow writes no character outside cp1252 anywhere, and a test encodes the seed
summary as cp1252 to prove it.
"""

from __future__ import annotations

from typing import Any

#: The prefix the WF-114 feature router mounts, and the only prefix this workflow
#: serves. Duplicated from the feature module's ``router.prefix`` so this domain
#: package does not have to import a FastAPI router to know where it is
#: published. The feature module asserts at import time that the two agree.
ROUTER_PREFIX = "/api/wf-114"

TICKET = "WF-114"

#: The generated webhook URL, per trigger. The research says the URL is generated
#: and copied into the external application: *"Generate Webhook URL: A unique
#: Webhook URL will be generated for your workflow."*
ENDPOINT_PATH_TEMPLATE = "/api/wf-114/hooks/{token}"

# --------------------------------------------------------------------------- #
# The trigger kind, quoted from the trigger menu
# --------------------------------------------------------------------------- #

#: Quoted: *"In HighLevel, open or create a workflow and select the trigger
#: **Inbound Webhook**."*
TRIGGER_KIND = "inbound_webhook"
TRIGGER_LABEL = "Inbound Webhook"

#: Quoted: *"inbound-webhook trigger also premium-tiered ('Workflow Premium
#: Trigger')"*. Served as a fact about the vendor's plan. This build models no
#: plan, so nothing here is refused on it; see
#: :data:`PREMIUM_TRIGGER_IS_NOT_A_GATE`.
PREMIUM_TRIGGER = True
PREMIUM_TRIGGER_IS_NOT_A_GATE = (
    "The research names the inbound-webhook trigger as premium-tiered. The repository "
    "models no plan, subscription or billing surface, so no delivery is refused on a "
    "tier. The tier is published as a fact and enforced by nobody."
)

# --------------------------------------------------------------------------- #
# The three methods the research names, and where each one carries its payload
# --------------------------------------------------------------------------- #

#: Quoted: *"captures incoming POST/GET/PUT requests"*. In the order the research
#: writes them.
METHOD_POST = "POST"
METHOD_GET = "GET"
METHOD_PUT = "PUT"
METHODS: tuple[str, ...] = (METHOD_POST, METHOD_GET, METHOD_PUT)

#: Where each method carries its payload.
#:
#: POST and PUT carry a JSON body; that is what the research's payload example is.
#: GET has no body in HTTP, so its payload is the query string and every value in
#: it is text. The research names GET without saying which of the three the body
#: rides on, so this is a derived decision rather than a researched one, and it is
#: recorded in :mod:`dsr.webhook_workflow_start.inferences`.
METHOD_PAYLOAD_SOURCE_BODY = "body"
METHOD_PAYLOAD_SOURCE_QUERY = "query"
METHOD_PAYLOAD_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "method": METHOD_POST,
        "source": METHOD_PAYLOAD_SOURCE_BODY,
        "accepts_body": True,
        "note": "The documented payload example arrives this way.",
    },
    {
        "method": METHOD_GET,
        "source": METHOD_PAYLOAD_SOURCE_QUERY,
        "accepts_body": False,
        "note": "HTTP defines no request body for GET, so the payload is the query string.",
    },
    {
        "method": METHOD_PUT,
        "source": METHOD_PAYLOAD_SOURCE_BODY,
        "accepts_body": True,
        "note": "The research names PUT beside POST and gives it the same shape.",
    },
)

#: The query parameter that switches a delivery into test mode. Step 4 of the
#: user flow is *"Send a test request to validate the integration"*, and a test
#: request must not start the workflow.
TEST_MODE_PARAM = "test"
TEST_MODE_VALUES: tuple[str, ...] = ("1", "true", "yes")

#: The two delivery modes, named.
MODE_LIVE = "live"
MODE_TEST = "test"
MODES: tuple[str, ...] = (MODE_LIVE, MODE_TEST)

# --------------------------------------------------------------------------- #
# Signing: one rule, reused from WF-113 and pinned by test
# --------------------------------------------------------------------------- #

#: The neutral header namespace WF-113 uses. The rule is WF-066's; the vendor
#: name is not. A test pins these two names to
#: ``dsr.webhook_ingest.signing.SIGNATURE_HEADER`` / ``TIMESTAMP_HEADER``.
SIGNATURE_HEADER = "X-DSR-Signature"
TIMESTAMP_HEADER = "X-DSR-Timestamp"

#: Quoted by the shared rule: the consumer applies a five-minute window. WF-114 is
#: the consumer, so it enforces the window. A test pins this to WF-113's value.
MAX_AGE_SECONDS = 300

#: The digest algorithm the shared rule names.
SIGNING_RULE = "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded"

#: What ``{raw_body}`` is when the method carries no body. A GET's payload is its
#: query string, so the signed text is the canonical JSON of that map: keys
#: sorted, no whitespace. A sender that computes it any other way will not verify,
#: which is why it is published here rather than left to be guessed.
SIGNED_INPUT_NOTE = (
    "POST and PUT sign the exact bytes of the request body. GET has no body, so it signs "
    "the canonical JSON of the query parameters: keys sorted, no spaces."
)

#: The research names no authentication rule at all for the generated URL, and a
#: minted URL that anyone can call is a published write endpoint. This build fails
#: closed: every trigger is created with a signing value and every delivery must
#: carry a valid signature. Recorded in the decision register.
FAIL_CLOSED = (
    "Every generated URL is signed. The research names no authentication rule for the "
    "inbound URL, and a URL anyone can call is a published write endpoint, so this build "
    "requires the shared signature on every delivery and issues the signing value once."
)

# --------------------------------------------------------------------------- #
# Variable types and mapping operators
# --------------------------------------------------------------------------- #

#: The variable store is named in the research's ``data_sources`` as *"workflow
#: variable store"*. The types below are derived: the research gives one payload
#: example with a string, a number and a string, and publishes no schema.
VARIABLE_TEXT = "text"
VARIABLE_NUMBER = "number"
VARIABLE_BOOLEAN = "boolean"
VARIABLE_JSON = "json"

VARIABLE_TYPES: tuple[dict[str, Any], ...] = (
    {"id": VARIABLE_TEXT, "label": "Text", "example": "CUST_001"},
    {"id": VARIABLE_NUMBER, "label": "Number", "example": "100"},
    {"id": VARIABLE_BOOLEAN, "label": "Boolean", "example": "true"},
    {"id": VARIABLE_JSON, "label": "JSON", "example": '{"key": "value"}'},
)

VARIABLE_TYPE_IDS: tuple[str, ...] = tuple(row["id"] for row in VARIABLE_TYPES)

#: The comparison a mapping's ``when`` may apply. Derived, because the research
#: names a "mapping step" that "binds payload keys to workflow variables/filters"
#: and lists no operators.
OPERATOR_EQUALS = "equals"
OPERATOR_NOT_EQUALS = "not_equals"
OPERATOR_CONTAINS = "contains"
OPERATOR_IN = "in"
OPERATOR_EXISTS = "exists"
OPERATOR_MISSING = "missing"
OPERATOR_GREATER_THAN = "greater_than"
OPERATOR_LESS_THAN = "less_than"

#: ``takes_value`` says whether the operator needs a comparison value. ``exists``
#: and ``missing`` do not, and asking for one would be a mapping a team cannot
#: save.
OPERATORS: tuple[dict[str, Any], ...] = (
    {"id": OPERATOR_EQUALS, "label": "equals", "takes_value": True},
    {"id": OPERATOR_NOT_EQUALS, "label": "does not equal", "takes_value": True},
    {"id": OPERATOR_CONTAINS, "label": "contains", "takes_value": True},
    {"id": OPERATOR_IN, "label": "is one of", "takes_value": True},
    {"id": OPERATOR_EXISTS, "label": "is present", "takes_value": False},
    {"id": OPERATOR_MISSING, "label": "is absent", "takes_value": False},
    {"id": OPERATOR_GREATER_THAN, "label": "is greater than", "takes_value": True},
    {"id": OPERATOR_LESS_THAN, "label": "is less than", "takes_value": True},
)

OPERATOR_IDS: tuple[str, ...] = tuple(row["id"] for row in OPERATORS)

# --------------------------------------------------------------------------- #
# Collections: plain JSON in records.data
# --------------------------------------------------------------------------- #

COLLECTION_WORKFLOW = "webhook_workflow"
COLLECTION_TRIGGER = "webhook_workflow_trigger"
COLLECTION_VARIABLE = "webhook_workflow_variable"
COLLECTION_INVOCATION = "webhook_workflow_invocation"
COLLECTION_RUN = "webhook_workflow_run"
COLLECTION_DEDUPE = "webhook_workflow_dedupe"

ALL_COLLECTIONS: tuple[str, ...] = (
    COLLECTION_WORKFLOW,
    COLLECTION_TRIGGER,
    COLLECTION_VARIABLE,
    COLLECTION_INVOCATION,
    COLLECTION_RUN,
    COLLECTION_DEDUPE,
)

# --------------------------------------------------------------------------- #
# Workflow and trigger lifecycle
# --------------------------------------------------------------------------- #

#: Step 6 of the user flow is *"Click Save Trigger, then add downstream actions
#: and publish."* So a workflow lands a draft, a trigger is saved by a deliberate
#: second act, and publishing is a third.
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUSES: tuple[str, ...] = (STATUS_DRAFT, STATUS_PUBLISHED)

#: A run is a start. The repository ships no pausable workflow engine for the
#: downstream actions the research lists, so a run records what it planned to do
#: rather than claiming to have done it. Recorded in the decision register.
RUN_STARTED = "started"
RUN_STATUSES: tuple[str, ...] = (RUN_STARTED,)

# --------------------------------------------------------------------------- #
# Invocation states: the WF-113 three, plus the one the seed needs
# --------------------------------------------------------------------------- #

#: ``accepted``, ``duplicate`` and ``rejected`` are WF-113's trigger states, used
#: with the same meaning. A test pins the three words to
#: ``dsr.webhook_ingest.vocabulary.TRIGGER_STATES``.
STATE_ACCEPTED = "accepted"
STATE_DUPLICATE = "duplicate"
STATE_REJECTED = "rejected"

#: The state the issue asks for by name: *"A request that arrived at a saved
#: trigger but matched no mapping is such a state."* A delivery whose mappings all
#: declined is recorded and starts nothing. It is not a refusal, because the
#: sender did nothing wrong.
STATE_UNMAPPED = "unmapped"

STATES: tuple[str, ...] = (
    STATE_ACCEPTED,
    STATE_DUPLICATE,
    STATE_UNMAPPED,
    STATE_REJECTED,
)

#: The three states shared with WF-113, as one tuple for the pinning test.
SHARED_TRIGGER_STATES: tuple[str, ...] = (STATE_ACCEPTED, STATE_DUPLICATE, STATE_REJECTED)

# --------------------------------------------------------------------------- #
# Refusal reasons, one per fix
# --------------------------------------------------------------------------- #

REASON_UNKNOWN_ENDPOINT = "unknown_endpoint"
REASON_TRIGGER_NOT_SAVED = "trigger_not_saved"
REASON_WORKFLOW_NOT_PUBLISHED = "workflow_not_published"
REASON_SIGNATURE_MISSING = "signature_missing"
REASON_SIGNATURE_MISMATCH = "signature_mismatch"
REASON_TIMESTAMP_MISSING = "timestamp_missing"
REASON_SIGNATURE_STALE = "signature_stale"
REASON_MALFORMED_PAYLOAD = "malformed_payload"
REASON_BODY_NOT_ALLOWED_ON_GET = "body_not_allowed_on_get"
REASON_REQUIRED_MAPPING_UNRESOLVED = "required_mapping_unresolved"
REASON_NO_MAPPING_MATCHED = "no_mapping_matched"

REASONS: tuple[str, ...] = (
    REASON_UNKNOWN_ENDPOINT,
    REASON_TRIGGER_NOT_SAVED,
    REASON_WORKFLOW_NOT_PUBLISHED,
    REASON_SIGNATURE_MISSING,
    REASON_SIGNATURE_MISMATCH,
    REASON_TIMESTAMP_MISSING,
    REASON_SIGNATURE_STALE,
    REASON_MALFORMED_PAYLOAD,
    REASON_BODY_NOT_ALLOWED_ON_GET,
    REASON_REQUIRED_MAPPING_UNRESOLVED,
    REASON_NO_MAPPING_MATCHED,
)

# --------------------------------------------------------------------------- #
# The documented payload example, quoted
# --------------------------------------------------------------------------- #

#: Quoted: *"Documented payload example ``{"customer_id": "CUST_001", "amount":
#: 100, "currency": "USD", "status": "successful"}``"*. One example is given and
#: no schema is published, so this build treats the payload shape as unvalidated
#: and refuses nothing for a key it was not told to require.
DOCUMENTED_PAYLOAD: dict[str, Any] = {
    "customer_id": "CUST_001",
    "amount": 100,
    "currency": "USD",
    "status": "successful",
}

#: Quoted: the documented payload feeds *"Update the lead's status to 'Paid.'"*,
#: *"Send a confirmation email to the customer"* and *"Trigger additional
#: follow-up actions based on the payment status"*.
DOCUMENTED_ACTIONS: tuple[str, ...] = (
    "Update the lead's status to 'Paid.'",
    "Send a confirmation email to the customer",
    "Trigger additional follow-up actions based on the payment status",
)

#: Quoted: the spec names the companion outbound surface - *"Webhook/Custom
#: Webhook: Sends data from HighLevel to external applications or services."* It
#: is named here so a reader knows the research pairs an inbound trigger with an
#: outbound action, and it is not built because this workflow starts a workflow.
OUTBOUND_COMPANION = (
    "Webhook/Custom Webhook: Sends data from HighLevel to external applications or services."
)

#: What is reused from WF-113 rather than re-declared here, for a reader who wants
#: to check the claim.
REUSED_FROM_WF113: tuple[str, ...] = (
    "the endpoint-token shape: a token row that resolves to exactly one subject",
    "the signing rule: HMAC-SHA256 over '{timestamp}.{raw_body}'",
    "the signing header namespace: X-DSR-Signature and X-DSR-Timestamp",
    "the freshness window: 300 seconds, because this side is the consumer",
    "dedupe by a fingerprint of the canonical JSON of the payload",
    "the trigger states accepted, duplicate and rejected",
)


def method_payload_source(method: str) -> dict[str, Any] | None:
    """The payload source for one method, or ``None`` when it is not served."""
    wanted = str(method or "").upper()
    for row in METHOD_PAYLOAD_SOURCES:
        if row["method"] == wanted:
            return dict(row)
    return None


def is_served_method(method: str) -> bool:
    return method_payload_source(method) is not None


def is_known_variable_type(variable_type: str) -> bool:
    return variable_type in VARIABLE_TYPE_IDS


def is_known_operator(operator: str) -> bool:
    return operator in OPERATOR_IDS


def operator_row(operator: str) -> dict[str, Any] | None:
    for row in OPERATORS:
        if row["id"] == operator:
            return dict(row)
    return None


def operator_takes_value(operator: str) -> bool:
    row = operator_row(operator)
    return bool(row and row["takes_value"])


def variable_type_row(variable_type: str) -> dict[str, Any] | None:
    for row in VARIABLE_TYPES:
        if row["id"] == variable_type:
            return dict(row)
    return None


def describe() -> dict[str, Any]:
    """Everything above, as one served document."""
    return {
        "ticket": TICKET,
        "router_prefix": ROUTER_PREFIX,
        "endpoint_path_template": ENDPOINT_PATH_TEMPLATE,
        "trigger_kind": TRIGGER_KIND,
        "trigger_label": TRIGGER_LABEL,
        "methods": list(METHODS),
        "method_payload_sources": [dict(row) for row in METHOD_PAYLOAD_SOURCES],
        "modes": list(MODES),
        "test_mode_param": TEST_MODE_PARAM,
        "test_mode_values": list(TEST_MODE_VALUES),
        "variable_types": [dict(row) for row in VARIABLE_TYPES],
        "operators": [dict(row) for row in OPERATORS],
        "statuses": list(STATUSES),
        "run_statuses": list(RUN_STATUSES),
        "states": list(STATES),
        "shared_trigger_states": list(SHARED_TRIGGER_STATES),
        "reasons": list(REASONS),
        "signature_header": SIGNATURE_HEADER,
        "timestamp_header": TIMESTAMP_HEADER,
        "signing_rule": SIGNING_RULE,
        "signed_input": SIGNED_INPUT_NOTE,
        "max_age_seconds": MAX_AGE_SECONDS,
        "fail_closed": FAIL_CLOSED,
        "premium_trigger": PREMIUM_TRIGGER,
        "premium_trigger_is_not_a_gate": PREMIUM_TRIGGER_IS_NOT_A_GATE,
        "documented_payload": dict(DOCUMENTED_PAYLOAD),
        "documented_actions": list(DOCUMENTED_ACTIONS),
        "outbound_companion": OUTBOUND_COMPANION,
        "reused_from_wf113": list(REUSED_FROM_WF113),
        "collections": list(ALL_COLLECTIONS),
    }


__all__ = [
    "ALL_COLLECTIONS",
    "COLLECTION_DEDUPE",
    "COLLECTION_INVOCATION",
    "COLLECTION_RUN",
    "COLLECTION_TRIGGER",
    "COLLECTION_VARIABLE",
    "COLLECTION_WORKFLOW",
    "DOCUMENTED_ACTIONS",
    "DOCUMENTED_PAYLOAD",
    "ENDPOINT_PATH_TEMPLATE",
    "FAIL_CLOSED",
    "MAX_AGE_SECONDS",
    "METHOD_GET",
    "METHOD_PAYLOAD_SOURCE_BODY",
    "METHOD_PAYLOAD_SOURCE_QUERY",
    "METHOD_PAYLOAD_SOURCES",
    "METHOD_POST",
    "METHOD_PUT",
    "METHODS",
    "MODE_LIVE",
    "MODE_TEST",
    "MODES",
    "OPERATORS",
    "OPERATOR_IDS",
    "OUTBOUND_COMPANION",
    "PREMIUM_TRIGGER",
    "PREMIUM_TRIGGER_IS_NOT_A_GATE",
    "REASONS",
    "REASON_BODY_NOT_ALLOWED_ON_GET",
    "REASON_MALFORMED_PAYLOAD",
    "REASON_NO_MAPPING_MATCHED",
    "REASON_REQUIRED_MAPPING_UNRESOLVED",
    "REASON_SIGNATURE_MISMATCH",
    "REASON_SIGNATURE_MISSING",
    "REASON_SIGNATURE_STALE",
    "REASON_TIMESTAMP_MISSING",
    "REASON_TRIGGER_NOT_SAVED",
    "REASON_UNKNOWN_ENDPOINT",
    "REASON_WORKFLOW_NOT_PUBLISHED",
    "REUSED_FROM_WF113",
    "ROUTER_PREFIX",
    "RUN_STARTED",
    "RUN_STATUSES",
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
    "TEST_MODE_PARAM",
    "TEST_MODE_VALUES",
    "TICKET",
    "TIMESTAMP_HEADER",
    "TRIGGER_KIND",
    "TRIGGER_LABEL",
    "VARIABLE_TYPES",
    "VARIABLE_TYPE_IDS",
    "describe",
    "is_known_operator",
    "is_known_variable_type",
    "is_served_method",
    "method_payload_source",
    "operator_row",
    "operator_takes_value",
    "variable_type_row",
]
