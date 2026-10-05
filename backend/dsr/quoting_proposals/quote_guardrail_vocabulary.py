"""WF-090: every researched term for quote rules, quoted from the specification.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-090.md``,
quoted in full in issue 135, and the underlying research is
``docs/research/raw/quoting-proposals.md`` section 5. Every constant below carries
the sentence it came from, because a guardrail nobody can trace to its source is a
guardrail somebody disables one day without knowing what it protected.

Nothing here reads or writes. These are the words, the reason codes and the limits.

Where the specification left a joint open -- the reading of ``SOLD_TOGETHER`` and
``INCOMPATIBLE``, and how a related record reaches an evaluation -- the gap is
recorded in :mod:`dsr.quoting_proposals.quote_guardrail_inferences` rather than
quietly filled in here, so the difference between what the evidence says and what
this build chose stays readable.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
#
# Ticket-prefixed, the uniform convention in this package. The host has no
# collection-collision check, so the prefix is what keeps two workflows from
# quietly sharing one.
# --------------------------------------------------------------------------- #

#: The quote rules configuration. "Rule definitions are stored as text in the
#: quote rules configuration." One row per rule, and the definition is text.
RULE_COLLECTION = "wf090_quote_rule"

#: One row per rule evaluation that was recorded. A GET evaluation writes nothing;
#: the publish gate records the evaluation beside the attempt with one transaction.
EVALUATION_COLLECTION = "wf090_rule_evaluation"

#: One row per attempt to publish, allowed or blocked. "Block publish: prevent users
#: from publishing quotes when the rule requirements aren't met." The refusal is a row
#: a reviewer can find rather than a gap in the log.
PUBLISH_ATTEMPT_COLLECTION = "wf090_publish_attempt"

#: WF-086's authored quote and its line items, read as data. This workflow evaluates
#: them and never creates one, because a quote has one writer and it is not this one.
SOURCE_QUOTES = "wf086_quote"
LINE_ITEMS = "wf086_line_item"


# --------------------------------------------------------------------------- #
# The rule row
# --------------------------------------------------------------------------- #

FIELD_NAME = "name"
FIELD_DEFINITION = "rule_definition"
FIELD_OUTCOME = "outcome"
FIELD_MESSAGE = "message"
FIELD_STATUS = "status"
FIELD_ENABLED = "enabled"
FIELD_NORMALISED = "normalised_definition"

#: The switch on the Manage tab. "toggle the rule's **Status** off to disable".
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUSES = (STATUS_ENABLED, STATUS_DISABLED)

#: The two outcome types, spelled as the vendor spells them. They are not
#: interchangeable: one draws a banner, the other stops the publish.
OUTCOME_WARNING = "show_warning"
OUTCOME_BLOCK = "block_publish"
OUTCOMES = (OUTCOME_WARNING, OUTCOME_BLOCK)

OUTCOME_LABELS = {
    OUTCOME_WARNING: "Show warning",
    OUTCOME_BLOCK: "Block publish",
}

#: The researched sentences for the two outcomes, kept whole so the editor can show
#: the vendor's words beside the picker.
OUTCOME_SENTENCES = {
    OUTCOME_WARNING: ("Show warning: display a warning when the rule requirements aren't met."),
    OUTCOME_BLOCK: (
        "Block publish: prevent users from publishing quotes when the rule requirements aren't met."
    ),
}

#: The reading the data flow fixes and this build must not invert:
#: "a true evaluation is a violation". Named so a reader can find it, and asserted
#: in the tests, because inverting a guardrail predicate is a silent way to ship a
#: product that blocks exactly the quotes it was meant to allow.
TRUE_IS_A_VIOLATION = "a true evaluation is a violation"


# --------------------------------------------------------------------------- #
# Verdicts and reason codes
#
# A guardrail is a refusal, so every refusal names the rule it enforces and what it
# protects. These codes are the published vocabulary; a client branches on the code,
# never on the sentence beside it.
# --------------------------------------------------------------------------- #

VERDICT_VIOLATION = "violation"
VERDICT_CLEAR = "clear"
VERDICT_UNVERIFIABLE = "unverifiable"
VERDICT_SKIPPED = "skipped"
VERDICTS = (VERDICT_VIOLATION, VERDICT_CLEAR, VERDICT_UNVERIFIABLE, VERDICT_SKIPPED)

REASON_WARNING = "guardrail_warning"
REASON_BLOCKED = "guardrail_block_publish"
REASON_SATISFIED = "guardrail_satisfied"
REASON_UNVERIFIABLE = "guardrail_unverifiable"
REASON_RULE_DISABLED = "guardrail_rule_disabled"
REASON_RULE_UNPARSEABLE = "guardrail_rule_unparseable"
REASON_UNSUPPORTED_FORM = "guardrail_unsupported_form"
REASON_UNKNOWN_SCOPE = "guardrail_unknown_scope"
REASON_MISSING_PROPERTY = "guardrail_missing_property"
REASON_QUOTE_DISCOUNT_UNAVAILABLE = "guardrail_quote_discount_unavailable"
REASON_ARITHMETIC_UNSUPPORTED = "guardrail_arithmetic_unsupported"
REASON_RULE_INVALID = "guardrail_rule_invalid"
REASON_RULE_NOT_FOUND = "guardrail_rule_not_found"

#: What each code enforces and what it protects. The sentence is shown to the seller
#: and written into the audit trail.
REASON_TEXTS: dict[str, str] = {
    REASON_WARNING: (
        "A quote rule the administrator set to Show warning is violated. The quote "
        "may still be published, but the seller is shown this rule's message."
    ),
    REASON_BLOCKED: (
        "A quote rule the administrator set to Block publish is violated. The quote "
        "is stopped before it reaches the buyer."
    ),
    REASON_SATISFIED: (
        "The rule's expression evaluated false, and the specification says a true "
        "evaluation is a violation. Nothing is refused."
    ),
    REASON_UNVERIFIABLE: (
        "The quote does not carry a property the rule reads, so the rule could not "
        "be checked. An unanswered question is reported, and it is not a violation."
    ),
    REASON_RULE_DISABLED: (
        "The rule's Status switch is off, and a disabled rule must not evaluate."
    ),
    REASON_RULE_UNPARSEABLE: (
        "The rule definition is not a sentence the quote-rules grammar can read, so "
        "the editor refuses it instead of saving a rule that can never fire."
    ),
    REASON_UNSUPPORTED_FORM: (
        "The rule uses a form the researched DSL states it does not support. The "
        "editor names the limit rather than silently accepting a rule that would "
        "never behave as written."
    ),
    REASON_UNKNOWN_SCOPE: (
        "The rule addresses a scope the DSL does not publish, so no record could be read for it."
    ),
    REASON_MISSING_PROPERTY: (
        "The rule reads a property the quote or a related record does not carry. "
        "The rule is reported as unverifiable rather than treated as satisfied."
    ),
    REASON_QUOTE_DISCOUNT_UNAVAILABLE: (
        "The rule reads a quote-level discount property. The specification's own "
        "limitation says quote-level discount properties are not available in the "
        "DSL."
    ),
    REASON_ARITHMETIC_UNSUPPORTED: (
        "The rule does arithmetic inside an aggregate function. The specification "
        "states arithmetic inside aggregate functions is not supported."
    ),
    REASON_RULE_INVALID: (
        "The rule is missing a field the editor requires, or names an outcome or a "
        "status that is not in the published vocabulary."
    ),
    REASON_RULE_NOT_FOUND: "No rule with that id exists in this workspace.",
}


# --------------------------------------------------------------------------- #
# The DSL
# --------------------------------------------------------------------------- #

#: "custom properties on any scope (`[quote.my_custom_property]`, `[line_item.x]`,
#: `[deal.x]`, `[company.x]`, `[recipient_contact.x]`, `[current_user.x]`) are
#: addressable". These six are the published scopes, in the order the evidence
#: lists them.
SCOPE_QUOTE = "quote"
SCOPE_LINE_ITEM = "line_item"
SCOPE_DEAL = "deal"
SCOPE_COMPANY = "company"
SCOPE_RECIPIENT_CONTACT = "recipient_contact"
SCOPE_CURRENT_USER = "current_user"
SCOPES = (
    SCOPE_QUOTE,
    SCOPE_LINE_ITEM,
    SCOPE_DEAL,
    SCOPE_COMPANY,
    SCOPE_RECIPIENT_CONTACT,
    SCOPE_CURRENT_USER,
)

#: Which scopes hold one record and which hold many. An aggregate or a quantifier
#: reads a collection; a plain property comparison reads one record.
SCOPE_CARDINALITY = {
    SCOPE_QUOTE: "one",
    SCOPE_LINE_ITEM: "many",
    SCOPE_DEAL: "one",
    SCOPE_COMPANY: "one",
    SCOPE_RECIPIENT_CONTACT: "many",
    SCOPE_CURRENT_USER: "one",
}

PROPERTY_OPEN = "["
PROPERTY_CLOSE = "]"

#: The aggregate functions the grammar reads. "SUM([quantity])", "MIN([hs_discount_percentage])".
AGGREGATES = ("SUM", "MIN", "MAX", "AVG", "COUNT")

#: The two quantifier constructs the evidence quotes. They are the only two
#: constructs that read a set of records without an aggregate.
QUANTIFIER_SOLD_TOGETHER = "SOLD_TOGETHER"
QUANTIFIER_INCOMPATIBLE = "INCOMPATIBLE"
QUANTIFIERS = (QUANTIFIER_SOLD_TOGETHER, QUANTIFIER_INCOMPATIBLE)

#: The comparison operators. The evidence spells them the way SQL does, and it
#: spells the same operator two ways (`=` and `==`), so both spellings are read and
#: one is stored.
COMPARISON_OPERATORS = ("=", "!=", ">", ">=", "<", "<=")
COMPARISON_ALIASES = {"==": "=", "<>": "!="}

#: The set operators. `IN ("A","B","C")` is how every quoted quantifier example scopes
#: its set.
SET_OPERATORS = ("IN", "NOT IN")

#: The five forms the specification quotes as its test corpus. They are the grammar's
#: acceptance examples and the tests assert every one parses and evaluates.
DSL_EXAMPLES = (
    'SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("A","B","C")',
    'INCOMPATIBLE FROM line_item WHERE [platform] IN ("legacy","modern")',
    'SUM([quantity]) FROM line_item WHERE [hs_sku] = "ENT-LICENSE" > 5',
    "[quote.hs_quote_amount] > 100000",
    "MIN([hs_discount_percentage]) FROM line_item >= 5",
)

#: Two limitations the specification states and this build must not quietly fix.
#: Each is quoted verbatim so a reader can see the vendor's own words, and the editor
#: raises a clear error when a rule uses either form.
LIMITATION_QUOTE_DISCOUNT = "quote-level discount properties are not available in the DSL"
LIMITATION_ARITHMETIC = (
    "Arithmetic inside aggregate functions (e.g., SUM([quantity] * [price])) is not supported"
)

#: The property-name fragment that means a quote-level discount. A rule addressing
#: `[quote.<anything with discount>]` is refused with :data:`LIMITATION_QUOTE_DISCOUNT`.
QUOTE_DISCOUNT_FRAGMENT = "discount"

#: The evidence sentences the whole workflow rests on, kept whole.
EVIDENCE_RULES = (
    "Use rules to display warnings when users build quotes or prevent them from "
    "publishing quotes until rule requirements are met."
)
EVIDENCE_EVALUATION = (
    "Rules evaluate continuously while the quote is edited (warnings appear during "
    "build) and gate the publish step."
)
EVIDENCE_STAGED = "changes are staged in a sandbox environment for testing"


def catalogue() -> dict[str, Any]:
    """The researched surface, served so a page renders it rather than repeating it.

    A client reads its pickers, its reason codes and its limits from here, so a value
    added to this module reaches every client at once, and the page cannot tell a
    seller a rule the grammar would refuse.
    """
    return {
        "collections": {
            "rules": RULE_COLLECTION,
            "evaluations": EVALUATION_COLLECTION,
            "publish_attempts": PUBLISH_ATTEMPT_COLLECTION,
            "source_quotes": SOURCE_QUOTES,
            "line_items": LINE_ITEMS,
        },
        "rule": {
            "fields": [
                FIELD_NAME,
                FIELD_DEFINITION,
                FIELD_OUTCOME,
                FIELD_MESSAGE,
                FIELD_STATUS,
            ],
            "outcomes": [
                {
                    "outcome": outcome,
                    "label": OUTCOME_LABELS[outcome],
                    "sentence": OUTCOME_SENTENCES[outcome],
                }
                for outcome in OUTCOMES
            ],
            "statuses": list(STATUSES),
        },
        "grammar": {
            "scopes": [
                {"scope": scope, "cardinality": SCOPE_CARDINALITY[scope]} for scope in SCOPES
            ],
            "aggregates": list(AGGREGATES),
            "quantifiers": list(QUANTIFIERS),
            "comparison_operators": list(COMPARISON_OPERATORS),
            "set_operators": list(SET_OPERATORS),
            "property": {"open": PROPERTY_OPEN, "close": PROPERTY_CLOSE},
            "examples": list(DSL_EXAMPLES),
        },
        "verdicts": list(VERDICTS),
        "reason_codes": [{"code": code, "text": text} for code, text in REASON_TEXTS.items()],
        "limitations": [
            {"code": REASON_QUOTE_DISCOUNT_UNAVAILABLE, "text": LIMITATION_QUOTE_DISCOUNT},
            {"code": REASON_ARITHMETIC_UNSUPPORTED, "text": LIMITATION_ARITHMETIC},
        ],
        "evidence": {
            "rules": EVIDENCE_RULES,
            "evaluation": EVIDENCE_EVALUATION,
            "true_is_a_violation": TRUE_IS_A_VIOLATION,
            "staged": EVIDENCE_STAGED,
        },
    }
