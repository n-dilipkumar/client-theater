"""WF-113: every judgement call this workflow rests on, named and served.

The research is specific about the flow and quiet about several decisions a
build has to make anyway. Each one is recorded here with the options that were
considered, the option chosen, why the others were rejected, and where to change
it. A served register means a reviewer can disagree with a specific decision and
find the line, rather than reading a docstring and guessing which parts were the
research and which parts this build invented.

None of these is a research fact. They are the joins between the facts, which is
exactly the layer a reader cannot fact-check by reading the sources again.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "the-domain-package-is-new-not-automation-engagement",
        "topic": "where the domain rules live",
        "question": "The issue names backend/dsr/automation_engagement/, but that package already ships and holds WF-105's series engine. Extend it, or add a sibling package?",
        "chosen": "a new sibling package, backend/dsr/webhook_ingest/",
        "options": [
            "a new sibling package, backend/dsr/webhook_ingest/",
            "add modules to the existing backend/dsr/automation_engagement/",
        ],
        "rejected_because": (
            "The existing package is another ticket's and is populated. Two features "
            "editing one package is the collision the feature host exists to prevent, and "
            "the brief owns the new path explicitly."
        ),
        "evidence": "backend/dsr/automation_engagement/ already contains errors.py, rules.py, vocabulary.py and series/.",
        "change_it": "backend/dsr/webhook_ingest/__init__.py",
    },
    {
        "id": "any-shape-parses-instead-of-being-refused",
        "topic": "what an inbound body may be",
        "question": "The research says a service POSTs 'JSON of any shape'. Refuse a body that is not an object, or accept it?",
        "chosen": "accept every JSON value; wrap a non-object as body and still derive identifiers and attributes",
        "options": [
            "accept every JSON value; wrap a non-object as body and still derive identifiers and attributes",
            "refuse anything that is not a JSON object",
        ],
        "rejected_because": (
            "The research says 'JSON of any shape' and does not qualify it. A build that "
            "refused an array or a scalar would reject the exact payloads the workflow "
            "was specified to accept, and the sender has no way to know why."
        ),
        "evidence": "data_flow: 'POST JSON of any shape to the generated Webhook URL'.",
        "change_it": "backend/dsr/webhook_ingest/rules.py:trigger_data",
    },
    {
        "id": "customer-data-resolves-to-the-empty-string",
        "topic": "what a mapping that references the person renders",
        "question": "A Liquid or JavaScript mapping may name a path that is not the trigger. Fail the ingest, or render the empty string?",
        "chosen": "render the empty string and do not error",
        "options": [
            "render the empty string and do not error",
            "refuse the ingest with 422",
        ],
        "rejected_because": (
            "The research states the empty string as the behaviour in two places, and "
            "says the alternative is to reference a person the automation does not have."
        ),
        "evidence": "hard constraint: 'Any attempt to reference customer data will return \"\"'.",
        "change_it": "backend/dsr/webhook_ingest/rules.py:resolve_expression",
    },
    {
        "id": "the-batch-ceiling-is-refused-not-truncated",
        "topic": "what a Batch Update over 1,000 people does",
        "question": "Truncate an over-limit group to the first 1,000, or refuse the whole ingest?",
        "chosen": "refuse the ingest and record why",
        "options": [
            "refuse the ingest and record why",
            "truncate to the first 1,000 silently",
            "truncate to the first 1,000 and note it",
        ],
        "rejected_because": (
            "The research states the ceiling as a fact - 'a group of up to 1000 people' - "
            "and a silent truncation updates some people and leaves a sender believing it "
            "updated all of them. That is worse than an error the sender can act on."
        ),
        "evidence": "'A batch update lets you associate your incoming data with a group of up to 1000 people.'",
        "change_it": "backend/dsr/webhook_ingest/rules.py:plan_fanout",
    },
    {
        "id": "duplicates-are-recorded-not-dropped",
        "topic": "what a repeated delivery does",
        "question": "The research describes a service that retries a delivery it did not see acknowledged. Drop the second one, or record it?",
        "chosen": "record the second as a duplicate trigger and fan out nothing",
        "options": [
            "record the second as a duplicate trigger and fan out nothing",
            "drop the second silently",
            "fan the second out again",
        ],
        "rejected_because": (
            "Dropping it silently hides a real retry from the operator, and fanning it "
            "out again runs the automation twice for one event. Recording it makes the "
            "retry visible and the dedupe auditable."
        ),
        "evidence": "brief: 'duplicates are recorded as duplicates rather than dropped silently'.",
        "change_it": "backend/dsr/webhook_ingest/engine.py:ingest",
    },
    {
        "id": "inbound-signature-reuses-the-wf066-rule",
        "topic": "how an inbound delivery proves it came from the service",
        "question": "Reuse WF-066's signed rule, or declare a new one, or do no check when the URL token is secret?",
        "chosen": "reuse WF-066's rule, declared in the domain package and pinned by test",
        "options": [
            "reuse WF-066's rule, declared in the domain package and pinned by test",
            "import dsr.meeting_webhook_fanout.signing from the domain package",
            "trust the per-automation URL token alone and check no signature",
        ],
        "rejected_because": (
            "Importing another workflow's package from a domain module is the defect the "
            "enforced architectural guard names, and trusting the URL alone means one "
            "leaked URL lets anyone write events. Jev chose the pinned-reuse option at "
            "0.97, audit jev-20261013T172714-13864-34127."
        ),
        "evidence": "WF-066 ships the rule as X-Chili-Signature / X-Chili-Timestamp over '{timestamp}.{raw_body}'.",
        "change_it": "backend/dsr/webhook_ingest/signing.py",
    },
    {
        "id": "the-signature-is-optional-per-automation",
        "topic": "whether a signature is required on every delivery",
        "question": "Require a signature on every ingest, or only when the automation has a secret?",
        "chosen": "require it exactly when the automation carries a secret",
        "options": [
            "require it exactly when the automation carries a secret",
            "require it always",
        ],
        "rejected_because": (
            "The research flow copies one generated URL into the external service and "
            "names no signing step, so a fresh automation that required a signature would "
            "refuse the first representative payload the flow says to send."
        ),
        "evidence": "research does not mention an inbound signature; WF-082's Why field says the hardening pairing runs the other way.",
        "change_it": "backend/dsr/webhook_ingest/engine.py:ingest",
    },
    {
        "id": "an-ingest-into-a-draft-is-refused",
        "topic": "what happens when data arrives before the automation is started",
        "question": "Buffer an ingest into a draft automation, drop it, or refuse it?",
        "chosen": "refuse it with 409",
        "options": [
            "refuse it with 409",
            "buffer it until the automation starts",
            "accept it and run nothing",
        ],
        "rejected_because": (
            "The data flow runs with 'no operator action' only once started. A buffer with "
            "no defined flush is a promise this build cannot keep, and accepting-but-not-"
            "running is indistinguishable to the sender from a lost event."
        ),
        "evidence": "user flow step 6: 'Review items -> Start Automation'.",
        "change_it": "backend/dsr/webhook_ingest/engine.py:ingest",
    },
    {
        "id": "deletes-are-events-not-operations",
        "topic": "how a delete or a suppress is expressed",
        "question": "Model Delete Person / Suppress Person as verbs, or as event names?",
        "chosen": "as event names, fanned out like any other event",
        "options": [
            "as event names, fanned out like any other event",
            "as distinct delete operations",
        ],
        "rejected_because": (
            "The research quotes the Pipelines API: it 'only uses POST operations' and "
            "deletes 'are performed by sending events with specific names'. A verb would "
            "add an operation the ingress does not have."
        ),
        "evidence": "'the Pipelines API only uses POST operations. You'll perform deletes and other operations by sending events with specific names.'",
        "change_it": "backend/dsr/webhook_ingest/vocabulary.py:SEMANTIC_EVENTS",
    },
    {
        "id": "rate-limits-are-published-not-enforced",
        "topic": "what this build does with the quoted rate limits",
        "question": "Enforce the quoted request limits, or publish them as data?",
        "chosen": "publish them as data and enforce nothing",
        "options": [
            "publish them as data and enforce nothing",
            "enforce the Track/Pipelines 3,000 per 3 s bound",
        ],
        "rejected_because": (
            "The research calls the Track/Pipelines bound soft and describes no behaviour "
            "for crossing it. Enforcing a soft vendor bound locally would refuse deliveries "
            "the vendor would have accepted, which is a throttle bug wearing a control."
        ),
        "evidence": "'Rate limits: Track/Pipelines 3,000 req / 3 s (soft)'.",
        "change_it": "backend/dsr/webhook_ingest/vocabulary.py:RATE_LIMITS",
    },
    {
        "id": "the-slack-block-is-optional",
        "topic": "whether Slack is part of the required flow",
        "question": "Is the Slack block a required action or an optional one?",
        "chosen": "optional, and marked so in the served vocabulary",
        "options": [
            "optional, and marked so in the served vocabulary",
            "required, because webhook automations have no person to message",
        ],
        "rejected_because": (
            "The user flow says 'Optionally append a Slack block'. Marking it required "
            "would make the research's own example flow unbuildable without Slack."
        ),
        "evidence": "user flow step 5: 'Optionally append a Slack block'.",
        "change_it": "backend/dsr/webhook_ingest/vocabulary.py:ACTIONS",
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
