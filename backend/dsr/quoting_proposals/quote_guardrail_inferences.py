"""WF-090: every judgement call this workflow makes, in one inspectable place.

The research for WF-090 pins the surface tightly. It gives the Settings -> Objects ->
Quotes -> **Rules** flow, the two outcome types and the sentence for each, the switch
on the Manage tab, the five DSL forms that are the test corpus, and two stated limits.
What it does not do is decide the joints those statements leave open, and the joints
are where a build has to choose something.

Those decisions are collected here rather than left as comments in function bodies,
because a judgement call in a comment is one nobody re-reads and a wrong one becomes
product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to change
  it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-090/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

The two entries that carry a Jev audit are the two the model was actually asked. The
copy is the decision envelope's own fields, so what a reviewer reads here is what was
put to the validator.
"""

from __future__ import annotations

from typing import Any

from dsr.quoting_proposals import quote_guardrail_vocabulary as vocab

#: The sentences from the research that govern the surface below.
SOURCED_QUOTES: tuple[str, ...] = (
    vocab.EVIDENCE_RULES,
    "Rule outcome type ... Show warning: display a warning when the rule "
    "requirements aren't met. Block publish: prevent users from publishing quotes "
    "when the rule requirements aren't met.",
    "Rule definitions are stored as text in the quote rules configuration.",
    "Rules can be toggled on/off per rule; changes are staged in a sandbox environment "
    "for testing.",
    f'Limitation: "{vocab.LIMITATION_QUOTE_DISCOUNT}."',
    f'"{vocab.LIMITATION_ARITHMETIC}."',
)

#: The decision envelope's own fields, verbatim enough to replay the call.
JEV_QUANTIFIER_QUESTION: dict[str, Any] = {
    "decision": (
        "Read HubSpot's two set constructs. A true evaluation is a violation, so the "
        "reading decides which quotes a rule blocks."
    ),
    "evidence": [
        'SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("A","B","C")',
        'INCOMPATIBLE FROM line_item WHERE [platform] IN ("legacy","modern")',
        "the specification's data flow: a true evaluation is a violation",
    ],
    "questions": {
        "options": {
            "all_present_and_mixed": "SOLD_TOGETHER true when all named values are present; INCOMPATIBLE true when two or more are mixed",
            "any_present_for_both": "both true when at least one named value is present",
            "none_present": "both true when a named value is absent",
        }
    },
    "verification": {
        "audit_id": "jev-20261005T125448-31592-88054",
        "verdict": "pass",
        "selected": "all_present_and_mixed",
        "confidence": 0.95,
    },
}

JEV_MISSING_PROPERTY_QUESTION: dict[str, Any] = {
    "decision": (
        "What a rule returns when a property it reads is absent, or an aggregate "
        "matches nothing, under a Block publish outcome."
    ),
    "evidence": [
        "the specification: a true evaluation is a violation",
        "WF-092's branch engine already reports a missing property as unverifiable "
        "rather than refusing",
        "the editor is where a malformed rule is fixed, and a seller cannot supply a "
        "property the schema never asked for",
    ],
    "questions": {
        "options": {
            "unverifiable_third_state": "a third state that is neither violation nor pass, and never blocks",
            "missing_is_violation": "block until the property is supplied",
            "missing_is_pass": "silently pass",
        }
    },
    "verification": {
        "audit_id": "jev-20261005T125509-31196-09357",
        "verdict": "pass",
        "selected": "unverifiable_third_state",
        "confidence": 0.99,
        "note": (
            "A first pass at this question returned uncertain at 0.74 with no "
            "evidence beyond the spec sentence. The WF-092 precedent was added as "
            "evidence and the second pass selected unverifiable at 0.99. The "
            "uncertain first pass is recorded so the escalation is visible."
        ),
    },
}

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "DERIVED_SOLD_TOGETHER_MEANS_ALL_PRESENT",
        "topic": "the reading of SOLD_TOGETHER",
        "basis": (
            "The specification quotes the form but not its evaluation. 'SOLD_TOGETHER "
            'FROM line_item WHERE [hs_product_id] IN ("A","B","C")\' is a '
            "construct name and a set; nothing in the evidence says what makes it true."
        ),
        "chosen": "all_named_values_present",
        "value": (
            "SOLD_TOGETHER is true when every value in the WHERE set is present among "
            "the line items, and false when any is missing."
        ),
        "rejected": "true when at least one named value is present",
        "rejection_cost": (
            "If any-present were the reading, SOLD_TOGETHER and INCOMPATIBLE would be "
            "the same predicate under two names, so a rule author could not express a "
            "bundle requirement and the two constructs would be indistinguishable in "
            "the vocabulary."
        ),
        "decision_audit": "jev-20261005T125448-31592-88054 (pass, confidence 0.95)",
        "change_it": "_evaluate_quantifier in quote_guardrail_rules.py",
    },
    {
        "id": "DERIVED_INCOMPATIBLE_MEANS_MIXED_VALUES",
        "topic": "the reading of INCOMPATIBLE",
        "basis": (
            "The specification quotes 'INCOMPATIBLE FROM line_item WHERE [platform] IN "
            '("legacy","modern")\' and gives no evaluation. The word names a '
            "relationship between the values, not the presence of one."
        ),
        "chosen": "two_or_more_named_values_mixed",
        "value": (
            "INCOMPATIBLE is true when two or more of the WHERE set's values appear "
            "together on the quote, which is what it means for them to be incompatible."
        ),
        "rejected": "true when a single named value is present",
        "rejection_cost": (
            "A quote carrying only 'legacy' would be blocked even though nothing is "
            "being mixed, so the rule would fire on exactly the quotes it was meant to "
            "allow."
        ),
        "decision_audit": "jev-20261005T125448-31592-88054 (same call as SOLD_TOGETHER)",
        "change_it": "_evaluate_quantifier in quote_guardrail_rules.py",
    },
    {
        "id": "DERIVED_MISSING_PROPERTY_IS_UNVERIFIABLE",
        "topic": "what a rule does when a property is absent",
        "basis": (
            "The spec says 'a true evaluation is a violation'. An expression over an "
            "absent property did not evaluate to true, and a Block publish rule would "
            "otherwise stop a quote over a property the seller cannot see."
        ),
        "chosen": "unverifiable_third_state",
        "value": (
            "A missing property, or an aggregate over an empty matching set, is "
            "reported as unverifiable. It is not a violation, it never blocks, and the "
            "editor shows which property was missing."
        ),
        "rejected": "treat a missing property as a violation",
        "rejection_cost": (
            "A quote would be permanently unpublishable with a message no seller can "
            "act on, and the block would rest on data the quote schema never asked for."
        ),
        "decision_audit": "jev-20261005T125509-31196-09357 (pass, confidence 0.99)",
        "change_it": "_evaluate_property and _evaluate_aggregate in quote_guardrail_rules.py",
    },
    {
        "id": "DERIVED_QUANTIFIER_READS_THE_WHOLE_COLLECTION",
        "topic": "which records a quantifier reads",
        "basis": (
            "The quoted quantifiers put the set inside the WHERE: 'FROM line_item WHERE "
            "[hs_product_id] IN (...)'. Reading only the WHERE-matched subset would "
            "make the set and the filter the same clause, and the mixed-values test "
            "could never see more than one value."
        ),
        "chosen": "whole_collection",
        "value": (
            "A quantifier with an IN condition scans every record in its scope and asks "
            "which of the named values are present."
        ),
        "rejected": "scan only the records the WHERE matched",
        "rejection_cost": (
            "INCOMPATIBLE could never fire, because a record matches a membership test "
            "for one value at a time, so the mixed-values reading would be dead code."
        ),
        "change_it": "_evaluate_quantifier in quote_guardrail_rules.py",
    },
    {
        "id": "DERIVED_RELATED_RECORDS_RIDE_ON_THE_QUOTE",
        "topic": "how a related record reaches an evaluation",
        "basis": (
            "The data flow names 'related records (deal, company, contacts, contract, "
            "quote template, current user)' and the extensibility note addresses "
            "[deal.x], [company.x], [recipient_contact.x] and [current_user.x]. The "
            "research does not name the collection each one lives in."
        ),
        "chosen": "embedded_on_the_quote_record",
        "value": (
            "The engine reads the scopes from the quote record's own payload "
            "(quote.deal, quote.company, quote.recipient_contact, quote.current_user) "
            "and the line items from wf086_line_item by quote_id, because those are the "
            "rows WF-086 actually writes."
        ),
        "rejected": "require a collection per scope from another workflow",
        "rejection_cost": (
            "No workflow in this domain provisions a deal or a company collection, so "
            "a rule addressing [deal.x] would be unusable in the demo and untestable "
            "without inventing a schema for records this workflow does not own."
        ),
        "change_it": "QuoteGuardrailEngine.context_for in quote_guardrail_engine.py",
    },
    {
        "id": "DERIVED_DISABLED_RULE_IS_SKIPPED",
        "topic": "what a disabled rule reports",
        "basis": (
            "'Rules can be toggled on/off per rule' and 'a disabled rule must not "
            "evaluate'. A disabled rule is neither satisfied nor violated."
        ),
        "chosen": "skipped_with_a_reason_code",
        "value": (
            "A disabled rule returns verdict skipped and code guardrail_rule_disabled, "
            "so a page shows the switch being off rather than an absent rule."
        ),
        "rejected": "omit disabled rules from the evaluation response",
        "rejection_cost": (
            "A seller who cannot see the rule cannot tell whether it is off or whether "
            "their quote satisfies it, and the two have different fixes."
        ),
        "change_it": "evaluate_rule in quote_guardrail_rules.py",
    },
    {
        "id": "DERIVED_BLOCKED_PUBLISH_IS_A_409",
        "topic": "the status code for a blocked publish",
        "basis": (
            "The vendor's outcome is a hard stop: 'prevent users from publishing quotes "
            "when the rule requirements aren't met.' The request is well formed and the "
            "rules are what refuse it."
        ),
        "chosen": "409_conflict",
        "value": (
            "A blocked publish answers 409 and carries the blocking rule's message and "
            "its published reason code. A malformed rule definition answers 422 from the "
            "editor instead."
        ),
        "rejected": "422 for both a malformed rule and a blocked publish",
        "rejection_cost": (
            "The editor's refusal and the publish gate's refusal would share one status, "
            "so a client could not tell a rule that needs fixing from a quote that "
            "needs editing."
        ),
        "change_it": "PublishBlocked.status in quote_guardrail_rules.py",
    },
    {
        "id": "SHARED_LINE_ITEM_SHAPE_WITH_WF086",
        "topic": "the quote and line-item record shape",
        "basis": (
            "WF-086 authors the quote and its line items and is on main. WF-091, WF-092, "
            "WF-093 and WF-098 all read wf086_quote, and their vocabularies declare the "
            "same two collection names."
        ),
        "chosen": "read_wf086_quote_and_wf086_line_item",
        "value": (
            "SOURCE_QUOTES = wf086_quote and LINE_ITEMS = wf086_line_item, declared in "
            "this workflow's vocabulary rather than imported from another feature, "
            "because a feature must not import another feature's module but may agree "
            "with its published collection names."
        ),
        "rejected": "provision a guardrail-specific quote collection",
        "rejection_cost": (
            "A second quote collection would split the product's quotes in two, and a "
            "rule configured against one would silently not see the other."
        ),
        "change_it": "SOURCE_QUOTES and LINE_ITEMS in quote_guardrail_vocabulary.py",
    },
)

NOT_BUILT: tuple[dict[str, Any], ...] = (
    {
        "id": "no-breeze-assistant-authoring",
        "what_is_not_built": (
            "The 'suggested prompt' half of the researched editor, where a Breeze "
            "Assistant asks clarifying questions and drafts the rule definition."
        ),
        "why": (
            "This product has no language model behind the API and no prompt surface. "
            "A prompt box that silently produced nothing would be worse than one that "
            "is absent."
        ),
        "instead": (
            "POST /api/wf-090/rules/validate is the dry run: it parses a hand-written "
            "definition and returns the normalised form or the exact refusal."
        ),
    },
    {
        "id": "no-separate-sandbox-environment",
        "what_is_not_built": (
            "A separate sandbox environment to stage rule changes before they go live."
        ),
        "why": (
            "The research says changes are staged in a sandbox environment for testing. "
            "This build has one workspace and no environment dimension in the store "
            "envelope, so adding one would be a schema decision this workflow does not "
            "own."
        ),
        "instead": (
            "the Status switch: a rule is saved disabled, tested against a real quote "
            "through the evaluation route, then enabled, which is the same order a "
            "sandbox enforces without a second environment."
        ),
    },
    {
        "id": "publishing-is-wf094",
        "what_is_not_built": "Rendering the quote to the buyer or creating the public link.",
        "why": (
            "WF-094 owns publishing. This workflow is the gate: it stops a publish, and "
            "it does not perform one."
        ),
        "instead": (
            "POST /api/wf-090/quotes/{id}/publish is the gate the publisher calls. It "
            "answers allowed or refuses with the blocking rule's message."
        ),
    },
    {
        "id": "quote-authoring-is-wf086",
        "what_is_not_built": "Creating a quote, its line items or its totals.",
        "why": (
            "WF-086 provisions the authored quote. A rule reads a quote at evaluation "
            "time; how the quote came to exist is another workflow's question."
        ),
        "instead": "the engine reads wf086_quote and wf086_line_item as data",
    },
)


def describe() -> dict[str, Any]:
    """The judgement calls, served so a reviewer reads the list instead of the diff."""
    return {
        "sourced_quotes": list(SOURCED_QUOTES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "not_built": [dict(entry) for entry in NOT_BUILT],
        "jev": [JEV_QUANTIFIER_QUESTION, JEV_MISSING_PROPERTY_QUESTION],
    }


def count() -> int:
    """How many judgement calls this workflow makes. Asserted by the HTTP test."""
    return len(INFERENCES)
