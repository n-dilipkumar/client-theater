"""WF-114: every judgement call this workflow rests on, named and served.

The issue is explicit that several decisions were left open by the research, and
asks for each to be derived and recorded. Each entry here names the options that
were considered, the option chosen, why the others were rejected, the evidence,
and the file to change. A served register means a reviewer can disagree with a
specific decision and find the line, rather than reading a docstring and guessing
which parts were the research and which parts this build invented.

None of these is a research fact. They are the joins between the facts.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "the-domain-package-is-new-not-crm-workflows",
        "topic": "where the domain rules live",
        "question": (
            "The issue says 'if the repository already owns a workflow surface, mount this "
            "feature on it'. backend/dsr/crm_workflows/ ships a WorkflowEngine, and "
            "backend/dsr/booking_approval.py lists WORKFLOW_TRIGGERS. Reuse one, or add a "
            "sibling package?"
        ),
        "chosen": "a new sibling package, backend/dsr/webhook_workflow_start/",
        "options": [
            "a new sibling package, backend/dsr/webhook_workflow_start/",
            "mount on backend/dsr/crm_workflows/",
            "mount on backend/dsr/booking_approval.py's WORKFLOW_TRIGGERS",
        ],
        "rejected_because": (
            "What was found, recorded rather than skipped: dsr/crm_workflows/ is a workflow "
            "engine whose trigger is a contact matching a saved activity criterion, fired "
            "from DSR events, and whose actions are CRM writes. It has no generated inbound "
            "URL, no per-trigger token and no variable store mapped from an external payload, "
            "which is the whole of this workflow. booking_approval.py's WORKFLOW_TRIGGERS are "
            "approval-chain milestones, not triggers. Displacing either would make another "
            "ticket's engine answerable for a payload shape its research never described. The "
            "issue's own dependency note says the pending list names no workflow surface for "
            "this, and the guard forbids a domain package importing another workflow's package."
        ),
        "evidence": (
            "backend/dsr/crm_workflows/engine.py defines WorkflowEngine; its vocabulary.py "
            "and criteria.py are contact-and-activity based. backend/dsr/booking_approval.py "
            "line 1237 names workflow_triggers as approval milestones."
        ),
        "change_it": "backend/dsr/webhook_workflow_start/__init__.py",
    },
    {
        "id": "the-url-is-unique-per-trigger-not-per-workflow",
        "topic": "what the generated URL identifies",
        "question": (
            "The research says both 'a unique Webhook URL will be generated for your "
            "workflow' and '(unique for each trigger)'. Is the unit a workflow or a trigger?"
        ),
        "chosen": "per trigger: one token row resolves to exactly one trigger",
        "options": [
            "per trigger: one token row resolves to exactly one trigger",
            "per workflow: one URL shared by the workflow's triggers",
        ],
        "rejected_because": (
            "The parenthetical is the more specific statement and the user flow copies the URL "
            "while the Inbound Webhook trigger is selected, which is the trigger. A per-workflow "
            "URL would also make a mapping ambiguous when a workflow carried two triggers."
        ),
        "evidence": "'A unique Webhook URL will be generated for your workflow.' and the evidence line '(unique for each trigger)'.",
        "change_it": "backend/dsr/webhook_workflow_start/vocabulary.py:ENDPOINT_PATH_TEMPLATE",
    },
    {
        "id": "post-and-put-carry-the-body-get-carries-the-query-string",
        "topic": "whether GET carries a payload, and where",
        "question": (
            "The research names POST, GET and PUT without saying which of them the JSON body "
            "rides on. Does GET carry a payload at all?"
        ),
        "chosen": "POST and PUT read a JSON body; GET reads its payload from the query string",
        "options": [
            "POST and PUT read a JSON body; GET reads its payload from the query string",
            "GET reads a body too, because the research lists it beside POST",
            "refuse GET entirely",
        ],
        "rejected_because": (
            "HTTP defines no request body for GET, so a body-reading GET would depend on "
            "client behaviour the research never describes. Refusing GET would drop one of "
            "the three methods the research names. Reading the query string is the only "
            "reading that serves all three methods with a payload each of them can carry. "
            "The consequence is stated rather than hidden: a GET payload's values are text, "
            "so number and boolean variables are coerced from text."
        ),
        "evidence": "'The Inbound Webhook Trigger captures incoming POST/GET/PUT requests sent to a HighLevel Webhook URL'.",
        "change_it": "backend/dsr/webhook_workflow_start/vocabulary.py:METHOD_PAYLOAD_SOURCES",
    },
    {
        "id": "the-test-request-validates-and-starts-nothing",
        "topic": "what step 4's test request returns",
        "question": (
            "Step 4 is 'Send a test request to validate the integration', and the research "
            "documents no validation response, no validation state and no error shape."
        ),
        "chosen": "validate the mapping and start nothing, returning the bindings it would have made",
        "options": [
            "validate the mapping and start nothing, returning the bindings it would have made",
            "treat the test request as a live delivery and start the workflow",
            "validate but return only a boolean",
        ],
        "rejected_because": (
            "The flow sends the test request at step 4, before Save Trigger at step 6, so a "
            "test that started the workflow would run it before it was saved and published. "
            "Returning only a boolean would answer 'did it work' without answering 'what did "
            "it read', which is what the sender is validating: the mapping. The bindings "
            "preview is the smallest response that answers that."
        ),
        "evidence": "user flow step 4: 'Send a test request to validate the integration.'",
        "change_it": "backend/dsr/webhook_workflow_start/engine.py:deliver",
    },
    {
        "id": "the-payload-shape-is-unvalidated-except-for-a-required-mapping",
        "topic": "what a payload may carry",
        "question": (
            "One payload example is documented and no schema is published. Reject a payload "
            "that omits keys, or accept any shape?"
        ),
        "chosen": "accept any shape; refuse only a required mapping whose path did not resolve",
        "options": [
            "accept any shape; refuse only a required mapping whose path did not resolve",
            "refuse a payload that omits any documented key",
            "accept any shape always, binding nothing when a key is absent",
        ],
        "rejected_because": (
            "The issue says in words: 'Do not reject a payload for missing keys unless a "
            "mapping step requires one.' Refusing documented keys would invent a schema the "
            "research never published. Binding nothing silently is the opposite failure: it "
            "starts a workflow with an unbound variable the team believes is bound."
        ),
        "evidence": "'One example is given and no schema is published. Treat the payload shape as unvalidated.'",
        "change_it": "backend/dsr/webhook_workflow_start/rules.py:plan_bindings",
    },
    {
        "id": "every-generated-url-is-signed",
        "topic": "how a delivery proves it came from the external service",
        "question": (
            "The spec gives no authentication, signature or allowlist rule for the inbound "
            "URL. Do nothing, allow-list source addresses, or require a signature?"
        ),
        "chosen": "require the shared signature on every delivery, always",
        "options": [
            "require the shared signature on every delivery, always",
            "trust the per-trigger URL token alone and check no signature",
            "check the source address against an allowlist",
        ],
        "rejected_because": (
            "The issue states the fault in words: 'A minted URL that anyone can call is a "
            "published write endpoint.' An allowlist was rejected because the research names "
            "no fixed set of senders - Zapier, Stripe and a feed are all named - and a build "
            "that had to be told an address before it accepted a delivery would refuse the "
            "first representative request the flow sends. Reusing the shipped rule rather "
            "than declaring a new one is WF-113's decision, held by a test that pins this "
            "module's rule to dsr.webhook_ingest.signing."
        ),
        "evidence": "'The spec gives no authentication, signature or allowlist rule for the inbound URL. Decide one, fail closed.'",
        "change_it": "backend/dsr/webhook_workflow_start/signing.py:verify",
    },
    {
        "id": "the-signing-value-is-shown-once-at-creation",
        "topic": "how the external service learns the signing value",
        "question": (
            "A signed delivery needs the signing value. Serve it on every read of the trigger, "
            "or show it once when the trigger is created?"
        ),
        "chosen": "serve the signing value once, in the response that creates the trigger",
        "options": [
            "serve the signing value once, in the response that creates the trigger",
            "serve the signing value on every read of the trigger",
            "hide it entirely, as WF-113 hides an automation's secret",
        ],
        "rejected_because": (
            "Hiding it entirely would make a fail-closed delivery impossible to send, because "
            "the flow gives the external service one URL and no other channel. Serving it on "
            "every read publishes the secret to anything that can read a trigger, which is "
            "strictly more exposure than the copy step needs. Showing it once is the shape "
            "every API key has, and reads carry has_signing_value instead."
        ),
        "evidence": "user flow step 2: 'Copy the generated unique Webhook URL.' A signed delivery needs the value that URL is signed with.",
        "change_it": "backend/dsr/webhook_workflow_start/engine.py:create_trigger",
    },
    {
        "id": "the-premium-trigger-is-a-published-fact-not-a-gate",
        "topic": "whether the repository models a plan tier",
        "question": (
            "The research says the inbound-webhook trigger is premium-tiered ('Workflow "
            "Premium Trigger'). Does this build enforce a tier gate?"
        ),
        "chosen": "no gate: publish the tier as a fact",
        "options": [
            "no gate: publish the tier as a fact",
            "refuse a delivery when the workflow's plan is not premium",
            "add a plan field to the workflow and gate on it",
        ],
        "rejected_because": (
            "The repository ships no plan, subscription or billing surface, so a plan field "
            "would be a number this build invented and nothing could set. A gate nothing can "
            "set is either always-open, which is a gate in name only, or always-closed, which "
            "would refuse every delivery the workflow exists to accept."
        ),
        "evidence": "'inbound-webhook trigger also premium-tiered (\"Workflow Premium Trigger\")'.",
        "change_it": "backend/dsr/webhook_workflow_start/vocabulary.py:PREMIUM_TRIGGER_IS_NOT_A_GATE",
    },
    {
        "id": "duplicates-are-recorded-not-dropped",
        "topic": "what a repeated delivery does",
        "question": (
            "An external service retries a delivery it did not see acknowledged. Drop the "
            "second one, or record it?"
        ),
        "chosen": "record the second as a duplicate invocation and start nothing",
        "options": [
            "record the second as a duplicate invocation and start nothing",
            "drop the second silently",
            "start the workflow a second time",
        ],
        "rejected_because": (
            "This is WF-113's rule and it is reused rather than re-derived: a retry is normal "
            "traffic, dropping it hides a real retry from the operator, and starting twice runs "
            "the workflow twice for one external event. The fingerprint is WF-113's too - "
            "sha256 of the canonical JSON of the payload, so a re-serialised retry is still "
            "recognised. A test pins the state word to dsr.webhook_ingest.vocabulary."
        ),
        "evidence": "WF-113's brief: 'duplicates are recorded as duplicates rather than dropped silently'.",
        "change_it": "backend/dsr/webhook_workflow_start/engine.py:deliver",
    },
    {
        "id": "a-delivery-that-matches-no-mapping-is-recorded-not-refused",
        "topic": "what a payload that binds nothing does",
        "question": (
            "A payload arrives at a saved trigger and every mapping row declines it. Refuse "
            "the sender, or record the delivery?"
        ),
        "chosen": "record the invocation as unmapped and answer with a success",
        "options": [
            "record the invocation as unmapped and answer with a success",
            "refuse the delivery with 422",
            "record nothing and answer with a success",
        ],
        "rejected_because": (
            "The sender did nothing wrong: the payload is well-formed, signed and addressed to "
            "a saved trigger, and the mapping simply matched nothing, which is a property of "
            "the trigger's own conditions. A 4xx would send the sender looking for a fault in "
            "its own request. Recording nothing would hide the delivery from the operator who "
            "wondered why the workflow did not run. The issue names this state explicitly as "
            "the non-success the seed must show."
        ),
        "evidence": "'The seed must print at least one state that is not a success. A request that arrived at a saved trigger but matched no mapping is such a state.'",
        "change_it": "backend/dsr/webhook_workflow_start/engine.py:deliver",
    },
    {
        "id": "a-run-records-its-plan-not-its-effects",
        "topic": "how far this build takes 'start the workflow'",
        "question": (
            "The data flow ends with downstream actions executing (update a contact field, "
            "create a task, post to Google Sheets). Does this build execute them?"
        ),
        "chosen": "record the declared downstream actions as the run's plan",
        "options": [
            "record the declared downstream actions as the run's plan",
            "execute the downstream actions against the repository's own surfaces",
            "record only that a run started, with no plan",
        ],
        "rejected_because": (
            "Every downstream action the research names belongs to a surface this repository "
            "does not own for this workflow: a contact field is the CRM's, a task is the "
            "seller-task surface's, and Google Sheets is an external service. The research "
            "names no pausable workflow engine and no ticket in the pending list provides "
            "one, so executing would mean inventing integrations other tickets own. Recording "
            "the plan is the claim this build can actually support: the workflow started, and "
            "these are the actions it declared."
        ),
        "evidence": "data_flow: 'downstream HighLevel actions (e.g. update contact field, create a contact-less task, post to Google Sheets) execute.'",
        "change_it": "backend/dsr/webhook_workflow_start/engine.py:_start_run",
    },
    {
        "id": "the-services-host-is-not-called",
        "topic": "whether this build calls the vendor REST host",
        "question": (
            "The research cites the HighLevel v2 REST base host as "
            "'https://services.leadconnectorhq.com/...' and truncates it. Build a caller?"
        ),
        "chosen": "build no caller against the host",
        "options": [
            "build no caller against the host",
            "construct the URL from the visible prefix and call it",
        ],
        "rejected_because": (
            "The issue says in words: 'The cited URL is truncated in the spec. Do not build a "
            "caller against it.' The host is also another vendor's, and this repository's "
            "inbound workflow has no reason to reach it: the external system posts to the "
            "generated URL, and this side answers."
        ),
        "evidence": "'HighLevel v2 REST base host is visible in the Marketplace trigger docs as https://services.leadconnectorhq.com/... (the cited URL is truncated)'.",
        "change_it": "backend/dsr/webhook_workflow_start/__init__.py (documented, nothing to change)",
    },
)


def count() -> int:
    return len(INFERENCES)


def describe() -> list[dict[str, Any]]:
    return [dict(row) for row in INFERENCES]


def by_id(decision_id: str) -> dict[str, Any] | None:
    for row in INFERENCES:
        if row["id"] == decision_id:
            return dict(row)
    return None


def describe_one(decision_id: str) -> dict[str, Any] | None:
    return by_id(decision_id)


def known_ids() -> list[str]:
    return [row["id"] for row in INFERENCES]


__all__ = ["INFERENCES", "by_id", "count", "describe", "describe_one", "known_ids"]
