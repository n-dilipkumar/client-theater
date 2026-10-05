"""WF-091: every judgement call the specification left open, and the reading taken.

The research for this workflow is quoted in full in issue 179. Seven points in it are
open, and this module records each one with the alternative that was rejected, what
the rejection would have cost, and the Jev audit that chose it.

Nothing here reads or writes. It exists so the difference between what the evidence
says and what this build chose stays readable on the page rather than buried in a
docstring somebody has to go and find.

The rule the module exists for
-------------------------------

A number in a control that gates money and cannot be traced to a decision is a
number somebody changes one day without knowing why. So each entry here names the
decision, the options that were on the table, the one that was chosen, the reason
the others lost, and the audit.
"""

from __future__ import annotations

from typing import Any

from dsr.quoting_proposals import quote_approval_vocabulary as vocab

#: The audit that chose the self-approval rule, after a first three-way question
#: came back uncertain at confidence 0.44.
SELF_APPROVAL_AUDIT = "jev-20261005T064607-13024-67413"

#: The audit that came back uncertain and is recorded rather than hidden. A caller
#: reading this file can see that the first question was too close to decide and
#: what changed before the second one was asked.
SELF_APPROVAL_FIRST_AUDIT = "jev-20261005T064542-8564-42977"


_DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "SELF_APPROVAL_IS_RESOLVED_AT_ENROLMENT",
        "question": (
            "The research says two things that do not agree. A quote whose creator is "
            "one of the configured approvers is handled how?"
        ),
        "sourced": [
            vocab.SELF_APPROVAL_EXEMPTION_QUOTE,
            vocab.NO_SELF_APPROVAL_QUOTE,
        ],
        "options": {
            "enrolment_removal": (
                "At enrolment, drop every approver who created the quote. The remaining "
                "approvers decide. If none remain, the quote needs no approval and is "
                "publishable."
            ),
            "strict_no_own_vote": (
                "Every matching quote requires approval. The creator is dropped from the "
                "approver list. If that empties the list, the enrolment is refused and the "
                "quote stays DRAFT and unshareable."
            ),
        },
        "chosen": "enrolment_removal",
        "rejected_because": (
            "strict_no_own_vote satisfies the second sentence by contradicting the only "
            "sourced sentence that describes when a matching quote does NOT require "
            "approval. It would also deadlock a room with one approver, which is a "
            "legitimate configuration in a product where no field is required."
        ),
        "cost_of_rejection": (
            "A one-approver discount policy could never publish a discounted quote, so "
            "the workflow would refuse its own stated use case."
        ),
        "jev_audit_id": SELF_APPROVAL_AUDIT,
        "jev_confidence": 1.0,
        "supersedes": SELF_APPROVAL_FIRST_AUDIT,
        "superseded_note": (
            "The first question offered three candidates and came back uncertain at "
            "confidence 0.44. The candidates were narrowed to two and the measured context "
            "was added rather than the same question asked again."
        ),
        "why_one_rule_satisfies_both": (
            "The two sentences are about different moments. The first is about the "
            "approver LIST at enrolment and resolve_approvers is it. The second is about a "
            "VOTE, and a creator who was removed from the list has no vote to cast."
        ),
    },
    {
        "id": "DERIVED_FILTER_OPERATORS",
        "question": "Which operators does a property filter support?",
        "sourced": [
            "Under And these conditions are met click + Add filter, pick a property, "
            "then configure the filter.",
            "Require approval on quotes where a specific line item is above a certain "
            "discount amount.",
        ],
        "options": {
            "equality_only": "is and is not, nothing else.",
            "comparison_set": (
                "The ten operators in the vocabulary, including the four numeric "
                "comparisons and the two list operators."
            ),
            "expression_language": "A nested expression tree per filter.",
        },
        "chosen": "comparison_set",
        "rejected_because": (
            "equality_only cannot express the one operator the evidence names, which is a "
            "discount amount above a threshold. expression_language invents a language the "
            "research does not describe and that a seller has to learn."
        ),
        "cost_of_rejection": (
            "The sourced rule 'above a certain discount amount' would be unsaveable, and "
            "the ticket's own title would have no implementation."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "note": (
            "The operator set is served on the page through "
            "filters.operators_choice_note so a reader can see it is this build's set and "
            "not a quoted vocabulary."
        ),
    },
    {
        "id": "DERIVED_LINE_ITEM_FILTER_MATCHES_ANY",
        "question": "A line-item filter is satisfied when one line item matches, or when all of them do?",
        "sourced": [
            "Require approval on quotes where a specific line item is above a certain "
            "discount amount.",
        ],
        "options": {
            "any_line_item": "One line item over the threshold requires approval.",
            "all_line_items": "Every line item must satisfy the filter.",
        },
        "chosen": "any_line_item",
        "rejected_because": (
            "The sourced sentence is about a specific line item, and a discount policy "
            "reads the worst line rather than the average one. all_line_items would also "
            "make the rule un-triggerable on a quote with one deeply discounted line."
        ),
        "cost_of_rejection": (
            "A quote with a single 40 percent discounted line would publish with no "
            "approval, which is the exact case the research describes."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
    },
    {
        "id": "DERIVED_RESUBMISSION_SINGLE_APPROVER",
        "question": "What does a re-submitted quote need when there is only one approver?",
        "sourced": [
            vocab.RESUBMISSION_QUOTE,
        ],
        "options": {
            "requirement_met_at_once": (
                "With one approver there is nobody left to approve again, so the "
                "re-submitted quote is APPROVED at once."
            ),
            "back_to_pending": (
                "Every re-submission goes to PENDING_APPROVAL whatever the approver "
                "count, so the seller waits on the same person who rejected."
            ),
        },
        "chosen": "requirement_met_at_once",
        "rejected_because": (
            "The sourced sentence is scoped to 'if there's more than one approver'. The "
            "edge it does not name resolves from the requirement itself: one approver, "
            "that approver approves, and the requirement is met."
        ),
        "cost_of_rejection": (
            "back_to_pending would create a state the seller can leave but cannot "
            "progress, because the only approver is the one who asked for the changes."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "note": vocab.SINGLE_APPROVER_RESUBMISSION_NOTE,
    },
    {
        "id": "DERIVED_NOTIFICATIONS_ARE_RECORDED",
        "question": "The research names five notification channels and no transport. What does this workflow do?",
        "sourced": [
            "Approver: from the bell/email notification (Go to quote).",
            "Approval notifications can be delivered to third-party apps (Google Chat, "
            "Microsoft Teams, Slack) via Settings, Notifications, Other apps.",
        ],
        "options": {
            "record_only": (
                "Write a notification row per approver per channel and mark it recorded, "
                "not delivered."
            ),
            "integrate_each_channel": "Implement a Slack, a Teams and a Google Chat client.",
            "email_only": "Send the email and drop the other three.",
        },
        "chosen": "record_only",
        "rejected_because": (
            "This product has no mail transport and no chat integration, and adding one "
            "would mean editing shared files this workflow may not touch. email_only "
            "would drop two channels the research names."
        ),
        "cost_of_rejection": (
            "A response that read like a delivery receipt would be something a reviewer "
            "has to find in a diff rather than being told, so every row carries "
            "dispatched_by: recorded."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "note": vocab.RECORDED_NOT_DELIVERED_NOTE,
    },
    {
        "id": "DERIVED_THE_QUOTE_IS_READ_AS_DATA",
        "question": "Who owns the quote record this workflow approves?",
        "sourced": [
            "A quote created via the API will automatically be set to DRAFT. Other states "
            "such as PENDING_APPROVAL, APPROVED, and REJECTED are primarily managed via the "
            "UI and approval workflows.",
            "Quote state is exposed on the object: hs_status via GET/PATCH "
            "/crm/objects/{version}/quotes/{quoteId}.",
        ],
        "options": {
            "read_foreign_and_mirror": (
                "Read wf086_quote as data and mirror this workflow's state onto its own "
                "enrolment record. Never write the foreign quote."
            ),
            "own_the_quote": "Create, price and update the quote here.",
            "wait_for_the_owner": "Refuse to build until the quote workflow lands.",
        },
        "chosen": "read_foreign_and_mirror",
        "rejected_because": (
            "own_the_quote would give the quote two writers, and the evidence says the "
            "quote-to-template association is settable only at creation, so a second "
            "writer breaks a field another ticket owns. wait_for_the_owner leaves the "
            "workflow unbuilt and the tests unwritable."
        ),
        "cost_of_rejection": (
            "This product ships an API, so it must expose the transitions the vendor says "
            "are UI-only. It does so through its own enrolment records and a derived view, "
            "and says so in the answer rather than writing hs_status on a record it does "
            "not own."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
        "exposed_transitions": [
            {
                "from": vocab.STATE_DRAFT,
                "to": vocab.STATE_PENDING_APPROVAL,
                "via": "submit_approval",
            },
            {"from": vocab.STATE_PENDING_APPROVAL, "to": vocab.STATE_APPROVED, "via": "approve"},
            {
                "from": vocab.STATE_PENDING_APPROVAL,
                "to": vocab.STATE_REJECTED,
                "via": "request_changes",
            },
            {
                "from": vocab.STATE_REJECTED,
                "to": vocab.STATE_PENDING_APPROVAL,
                "via": "submit_approval",
            },
            {"from": vocab.STATE_APPROVED, "to": vocab.STATE_SHARED, "via": "share"},
        ],
    },
    {
        "id": "DERIVED_TWO_SIMPLIFICATIONS_NOT_OFFERED",
        "question": "The research names two simplifications and does not pick between them. Are they built?",
        "sourced": [
            "You can turn off the Create custom line items permission to force standard pricing.",
            "Legacy quotes offer a simplified all quotes approved by one user mode.",
        ],
        "options": {
            "offer_both": "Build the standard-pricing lock and the single-approver legacy mode.",
            "offer_neither": (
                "Build neither, and say so on the page with the evidence, because both "
                "reduce or bypass a control."
            ),
        },
        "chosen": "offer_neither",
        "rejected_because": (
            "offer_both would add a setting that forces pricing and a mode that "
            "self-approves, and the research states neither as a requirement. The "
            "single-approver case this workflow already handles through the sourced "
            "enrolment removal, which is the researched behaviour rather than a legacy "
            "mode."
        ),
        "cost_of_rejection": (
            "Two capabilities the platform documents are absent. That is a gap, and it "
            "is recorded here rather than left for a reader to assume."
        ),
        "jev_audit_id": None,
        "jev_confidence": None,
    },
)


def count() -> int:
    """How many decisions this module records."""
    return len(_DECISIONS)


def describe() -> list[dict[str, Any]]:
    """Every decision with its options, its rejection and its audit."""
    return [dict(entry) for entry in _DECISIONS]


def describe_one(decision_id: str) -> dict[str, Any] | None:
    """One decision, or ``None`` rather than a guess."""
    for entry in _DECISIONS:
        if entry["id"] == decision_id:
            return dict(entry)
    return None


def self_approval() -> dict[str, Any]:
    """The self-approval reading on its own, for a page to show beside a rule."""
    entry = describe_one("SELF_APPROVAL_IS_RESOLVED_AT_ENROLMENT")
    assert entry is not None  # the tuple above always holds this id
    return {
        "rule": vocab.SELF_APPROVAL_RULE_TEXT,
        "chosen": entry["chosen"],
        "jev_audit_id": entry["jev_audit_id"],
        "jev_confidence": entry["jev_confidence"],
        "sourced": list(entry["sourced"]),
        "why": entry["why_one_rule_satisfies_both"],
        "rejected": entry["rejected_because"],
    }
