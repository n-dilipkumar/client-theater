"""WF-092: every researched term, quoted from the specification.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-092.md``,
quoted in full in issue 157, and the underlying research is
``docs/research/raw/quoting-proposals.md`` section 7. Every constant below carries
the sentence it came from, because a cap in a governance rule that nobody can trace
to its source is a cap somebody will change one day without knowing why.

Nothing here reads or writes. These are the words, the caps and the evidence.

Where the specification left a joint open, the gap is recorded in
:mod:`dsr.quoting_proposals.approval_chain_inferences` rather than quietly filled in
here, so the difference between what the evidence says and what this build chose stays
readable.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
#
# Ticket-prefixed, which is the uniform convention in this repository and in this
# package. The host has no collection-collision check, so the prefix is what keeps two
# features from quietly sharing one.
#
# WF-091, "Route a discounted quote for standard approval", covers the single-level
# case. It is not on main yet. These four names are therefore the shared approval
# record shape both workflows must agree on, and they are declared here rather than
# invented again in whichever feature lands second. See
# ``SHARED_APPROVAL_SHAPE_WITH_WF091`` in the inferences module.

#: The approval workflow itself. Exactly one exists: "You can't duplicate the
#: workflow or create a new workflow to use for quote approvals." The engine refuses a
#: second one rather than storing two and letting a caller choose.
WORKFLOW = "wf092_quote_approval_workflow"

#: A branch on quote properties. Each qualifying branch pushes an approval step.
#: The researched example is one branch: "Set Branch 1 to greater than 5,000."
BRANCHES = "wf092_approval_branch"

#: One row per approver at one priority. A priority step is a set of rows, because
#: "Sequential approvals require approval by every approver at each priority step" and
#: a step is therefore a group rather than a single ordered field.
APPROVAL_STEPS = "wf092_approval_step"

#: One row per enrollee. Holds the chain state: which priority is active, which
#: approvers have decided, and the final decision when it lands.
ENROLMENTS = "wf092_approval_enrolment"

#: One row per approver decision, and one row per notification sent on a step.
#: Decisions are rows rather than a list on the enrolment because the audit trail the
#: user flow describes is per-approver: "the sales director won't need to approve the
#: quote until the sales manager has completed their approval."
DECISIONS = "wf092_approval_decision"
NOTIFICATIONS = "wf092_approval_notification"

#: WF-086's authored quote, read as data. WF-086 provisions it, its line items and
#: its totals. This workflow evaluates quotes and never creates one.
SOURCE_QUOTES = "wf086_quote"

#: The quote publishability state this workflow writes. WF-094 owns publishing and
#: reads this.
SOURCE_PUBLISHABILITY = "publishable"


# --------------------------------------------------------------------------- #
# The branch engine
# --------------------------------------------------------------------------- #

#: The one property the researched branch reads: "choose the **Quote amount**
#: property".
PROPERTY_QUOTE_AMOUNT = "quote_amount"

#: "Set Branch 1 to greater than 5,000. This would be the start of an advanced
#: approval workflow for any quotes above $5,000."
#:
#: The NetSuite half of the specification offers a different figure. "condition via
#: **Visual Builder** (`Total` greater than or equal to `3000.00`)". Both numbers are
#: sourced, and the room needs one. The HubSpot figure is the primary source for this
#: ticket and it is quoted as an approval *branch*, so 5000 is the room default. See
#: ``DERIVED_THRESHOLD_IS_THE_HUBSPOT_FIGURE`` in the inferences module.
DEFAULT_BRANCH_THRESHOLD = 5000.0

#: The comparison the branch is written with. "greater than 5,000" is a strict
#: comparison, so a quote of exactly 5000 does not qualify. The NetSuite example uses
#: "greater than or equal to", so the operator is stored per branch rather than fixed.
BRANCH_OPERATORS = ("greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "equals")

#: The outcome of a branch that did not qualify. The user flow puts it explicitly:
#: under the **None met** branch add **Start quote approval flow**, "so non-qualifying
#: quotes skip approvals".
OUTCOME_QUALIFIED = "qualified"
OUTCOME_NOT_QUALIFIED = "not_qualified"
OUTCOME_AUTO_APPROVED = "auto_approved"


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #

#: The chain's own states. The final two are the researched end states: "the last
#: decision writes `APPROVED`/`REJECTED`", and in NetSuite the custom **Approval
#: Status** field is "stepped `Pending Approval -> Approved | Rejected`".
STATE_PENDING = "pending_approval"
STATE_IN_REVIEW = "in_review"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
CHAIN_STATES = (STATE_PENDING, STATE_IN_REVIEW, STATE_APPROVED, STATE_REJECTED)

#: The value written to the quote when the chain finishes. "the quote becomes
#: publishable" is a separate sentence from the status write, so both are written.
QUOTE_STATUS_KEY = "approval_status"

#: The three values under *Approvers required*: "select **All approvers**, **Any
#: approvers**, or **Sequential**". All three are built.
REQUIREMENT_ALL = "all"
REQUIREMENT_ANY = "any"
REQUIREMENT_SEQUENTIAL = "sequential"
APPROVER_REQUIREMENTS = (REQUIREMENT_ALL, REQUIREMENT_ANY, REQUIREMENT_SEQUENTIAL)

#: "Sequential approvals require approval by every approver at each priority step."
#: This is the sentence :func:`~dsr.quoting_proposals.approval_chain_rules.advance`
#: exists to enforce. A lower-priority approver is not notified until every approver
#: at the current priority has decided.
SEQUENTIAL_RULE = (
    "Sequential approvals require approval by every approver at each priority step."
)

#: The researched caps. "You can add up to five sequences, and ten approvers per
#: sequence." Both are enforced in validation and tested at the boundary.
MAX_SEQUENCES = 5
MAX_APPROVERS_PER_SEQUENCE = 10

#: What a step is made of: a priority rank, an approver, and the message shown to
#: them. "**Add quote approval step** -> **Message to approver** ... **Approvers**
#: dropdown -> **Priority** (for sequential)".
PRIORITY_KEY = "priority"
APPROVER_KEY = "approver"
MESSAGE_KEY = "message"


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

#: The three decisions an approver can make. The evidence names two outcomes
#: (**Approve** / **Reject** buttons in NetSuite) and the chain needs a way to stand
#: still, so a decision may also be to abstain. Abstaining does not advance a
#: sequential step on its own. See ``DERIVED_ABSTAIN_IS_A_THIRD_DECISION`` in the
#: inferences module.
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_ABSTAINED = "abstained"
DECISIONS_ALLOWED = (DECISION_APPROVED, DECISION_REJECTED, DECISION_ABSTAINED)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

#: "Notifications fire automatically on each step." The research names five channels.
#: This product has a confirmed delivery surface for two of them, so only those two
#: are built and the other three are recorded as not built rather than faked.
#: See ``NOT_BUILT_TEAMS_SLACK_CHAT`` in the inferences module.
CHANNEL_BELL = "bell"
CHANNEL_EMAIL = "email"
DELIVERY_CHANNELS_BUILT = (CHANNEL_BELL, CHANNEL_EMAIL)
CHANNELS_RESEARCHED_BUT_NOT_BUILT = ("teams", "slack", "google_chat")


# --------------------------------------------------------------------------- #
# The message template
# --------------------------------------------------------------------------- #

#: The message to an approver "can embed quote properties via *Choose data
#: variable*". The vendor offers a picker; a page needs a syntax. The derivation is
#: ``{{quote.property}}`` resolved against the quote record, with a dotted path so
#: ``{{quote.line_items.0.sku}}`` resolves too. See
#: ``DERIVED_DATA_VARIABLE_SYNTAX`` in the inferences module.
TEMPLATE_OPEN = "{{"
TEMPLATE_CLOSE = "}}"
TEMPLATE_ROOT = "quote"


# --------------------------------------------------------------------------- #
# The single workflow
# --------------------------------------------------------------------------- #

#: "You can't duplicate the workflow or create a new workflow to use for quote
#: approvals." The engine enforces this, and the workflow id is fixed so a caller
#: cannot sidestep it by passing a second name.
SINGLE_WORKFLOW_ID = "wf092-sequential-quote-approval"

#: The seed scenario the specification gives: "you could set a sales manager as first
#: priority, a sales director as second priority, and a legal representative as third
#: priority."
SEED_PRIORITY_ROLES = ("sales_manager", "sales_director", "legal_representative")

#: The switch. "Optionally enable **Re-enroll** in **Settings**" and "Re-enrollment
#: can be toggled for quotes that need re-approval after edits."
RE_ENROL_KEY = "re_enroll"


def describe() -> dict[str, Any]:
    """The researched surface, served so a page renders it rather than repeating it."""
    return {
        "branch": {
            "property": PROPERTY_QUOTE_AMOUNT,
            "default_threshold": DEFAULT_BRANCH_THRESHOLD,
            "operators": list(BRANCH_OPERATORS),
            "outcomes": [OUTCOME_QUALIFIED, OUTCOME_NOT_QUALIFIED, OUTCOME_AUTO_APPROVED],
        },
        "chain": {
            "states": list(CHAIN_STATES),
            "requirements": list(APPROVER_REQUIREMENTS),
            "sequential_rule": SEQUENTIAL_RULE,
            "caps": {
                "max_sequences": MAX_SEQUENCES,
                "max_approvers_per_sequence": MAX_APPROVERS_PER_SEQUENCE,
            },
        },
        "decisions": list(DECISIONS_ALLOWED),
        "notifications": {
            "built": list(DELIVERY_CHANNELS_BUILT),
            "researched_but_not_built": list(CHANNELS_RESEARCHED_BUT_NOT_BUILT),
        },
        "template": {
            "open": TEMPLATE_OPEN,
            "close": TEMPLATE_CLOSE,
            "root": TEMPLATE_ROOT,
            "example": f"{TEMPLATE_OPEN}{TEMPLATE_ROOT}.{PROPERTY_QUOTE_AMOUNT}{TEMPLATE_CLOSE}",
        },
        "single_workflow_id": SINGLE_WORKFLOW_ID,
    }