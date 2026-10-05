"""WF-092: every judgement call this workflow makes, in one inspectable place.

The research for WF-092 pins the *surface* tightly. It gives a branch on one property
with a worked example, two documented caps, three approver requirements, the exact
sequential rule, the auto-approval valve, the two end states, and the sentence that
the approval workflow cannot be duplicated. What it does not do is decide the joints
those statements leave open, and the joints are where a build has to choose something.

Those decisions are collected here rather than left as comments in function bodies,
because a judgement call in a comment is one nobody re-reads and a wrong one becomes
product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to change
  it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-092/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

One entry is not an inference but a boundary, and is listed for that reason:
``NOT_BUILT`` records what this build deliberately does not do, because a feature
whose page does not show its own edges overstates itself.
"""

from __future__ import annotations

from typing import Any

from dsr.quoting_proposals import approval_chain_vocabulary as vocab

#: The sentences from the research that govern the surface below.
SOURCED_QUOTES: tuple[str, ...] = (
    "Use sequential approvals ranked by priority. You can add up to five sequences, "
    "and ten approvers per sequence.",
    "Set Branch 1 to greater than 5,000. This would be the start of an advanced "
    "approval workflow for any quotes above $5,000.",
    "Start quote approval flow: if a quote approval step hasn't been added above this "
    "action, quotes will be auto-approved.",
    "You can't duplicate the workflow or create a new workflow to use for quote "
    "approvals.",
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "domain-package-extends-quoting-proposals",
        "topic": "where the domain rules live",
        "basis": (
            "Issue 157 names backend/dsr/quote_approvals/ as a path this feature owns, and "
            "no such package exists on main. dsr/quoting_proposals/ is the package that "
            "already holds the quoting domain, and it holds a second workflow in that "
            "domain already: WF-098 added quote_expiry_rules.py, "
            "quote_expiry_vocabulary.py, quote_expiry_engine.py and "
            "quote_expiry_inferences.py beside WF-093's rules.py and vocabulary.py."
        ),
        "chosen": "extend_quoting_proposals",
        "value": (
            "Four modules inside dsr/quoting_proposals/, named approval_chain_*.py, "
            "following the quote_expiry_* precedent."
        ),
        "rejected": "create backend/dsr/quote_approvals/ as a second package for the quoting domain",
        "rejection_cost": (
            "Two packages would hold the same domain's vocabulary and would each have to "
            "carry its own copy of the quote collection names, so a team adding a quote "
            "field would have to know which package to edit."
        ),
        "decision_audit": "jev-20261005T064651-5128-11936, selected at confidence 0.97",
        "change_it": "move the four approval_chain_* modules and update the import in the feature module",
    },
    {
        "id": "branch-engine-is-owned-here",
        "topic": "whether to reuse the existing crm_workflows criteria evaluator",
        "basis": (
            "The spec's apis_hit field says the workflow is created using the workflows "
            "tool, and data_sources lists the workflows engine. dsr/crm_workflows/ exists "
            "and is a real evaluator, but it is contact-based: its five filter families "
            "are activity families, and it has no notion of an approval step, a priority "
            "rank, or a chained approver decision."
        ),
        "chosen": "own_branch_engine",
        "value": (
            "A branch predicate inside approval_chain_rules.py that reads one quote "
            "property through a dotted path and compares it to a threshold."
        ),
        "rejected": "reuse dsr/crm_workflows.criteria.evaluate as the branch engine",
        "rejection_cost": (
            "That evaluator's input is a DSR activity event and its refinements are "
            "date, file name, link URL and activity text. A quote amount is none of "
            "those, so reusing it means widening an existing feature's vocabulary to "
            "carry a quote field, which is the shared-file coupling the feature contract "
            "exists to prevent."
        ),
        "decision_audit": "jev-20261005T064605-28048-65888, selected at confidence 0.80",
        "change_it": "replace evaluate_branch in approval_chain_rules.py",
    },
    {
        "id": "DERIVED_THRESHOLD_IS_THE_HUBSPOT_FIGURE",
        "topic": "which amount starts an approval chain",
        "basis": (
            "Both figures are sourced and they disagree. HubSpot: 'Set Branch 1 to "
            "greater than 5,000.' NetSuite: 'condition via Visual Builder (Total "
            "greater than or equal to 3000.00)'. The room needs one number."
        ),
        "chosen": "hubspot_5000",
        "value": f"{vocab.DEFAULT_BRANCH_THRESHOLD} with a greater_than operator",
        "rejected": "the NetSuite figure of 3000 with a greater_than_or_equal operator",
        "rejection_cost": (
            "Two vendors approve different shares of the same quote, so a quote at 4000 "
            "would be approved in one product and blocked in the other. The HubSpot "
            "figure is also quoted as an approval branch, which is what this workflow is, "
            "whereas the NetSuite condition is a general workflow trigger."
        ),
        "change_it": "set DEFAULT_BRANCH_THRESHOLD and the branch operator in approval_chain_vocabulary.py",
    },
    {
        "id": "DERIVED_UNVERIFIABLE_IS_NOT_A_REFUSAL",
        "topic": "what a branch does when the quote carries no amount",
        "basis": (
            "The research says the branch reads one property or action output. It does not "
            "say what happens when the property is absent, which is a real case because "
            "payloads are open JSON and a team may not have written the field."
        ),
        "chosen": "third_outcome_unverifiable",
        "value": (
            "evaluate_branch returns outcome 'unverifiable' with a reason, and the "
            "workflow treats it as not qualifying rather than as a rejection."
        ),
        "rejected": "treat an absent amount as zero and let the comparison decide",
        "rejection_cost": (
            "A missing field would read as a quote of 0 and would silently skip "
            "approval. Folding it into 'not qualified' for the same quote and a real zero "
            "would make a bug indistinguishable from a correct decision, which is the "
            "failure the third outcome exists to prevent."
        ),
        "change_it": "fold 'unverifiable' into OUTCOME_NOT_QUALIFIED in evaluate_branches",
    },
    {
        "id": "DERIVED_AUTO_APPROVE_IS_THE_SAFE_DEFAULT",
        "topic": "a quote that matched a branch but collected no approval step",
        "basis": (
            "The valve is sourced and explicit: 'if a quote approval step hasn't been "
            "added above this action, quotes will be auto-approved.'"
        ),
        "chosen": "approve",
        "value": "auto_approve writes state approved and publishable true",
        "rejected": "block the quote and report a configuration error",
        "rejection_cost": (
            "A misconfigured branch would stop every quote above the threshold from "
            "closing. The research chose the other behaviour for exactly that reason, and "
            "a quote with no approver has nobody who could ever decide it."
        ),
        "change_it": "auto_approve in approval_chain_rules.py",
    },
    {
        "id": "DERIVED_ABSTAIN_IS_A_THIRD_DECISION",
        "topic": "a decision that neither approves nor rejects",
        "basis": (
            "The evidence names two outcomes, as NetSuite Approve and Reject buttons. A "
            "chain with several priorities needs a way for an approver to stand still "
            "without ending the chain."
        ),
        "chosen": "add_abstained",
        "value": "DECISIONS_ALLOWED carries approved, rejected and abstained",
        "rejected": "model only approved and rejected, and treat an absent decision as the only way to stand still",
        "rejection_cost": (
            "An approver who recuses has no row, so the audit trail loses the fact that "
            "they were asked and declined to act. Abstaining records that row without "
            "advancing a sequential level, which is what the researched rule counts."
        ),
        "change_it": "DECISIONS_ALLOWED in approval_chain_vocabulary.py",
    },
    {
        "id": "DERIVED_DATA_VARIABLE_SYNTAX",
        "topic": "how the message to an approver embeds a quote property",
        "basis": (
            "The Message to approver field 'can embed quote properties via Choose data "
            "variable'. The vendor offers a picker. A page needs a syntax a person can "
            "type and a server can resolve."
        ),
        "chosen": "double_brace_dotted_path",
        "value": "{{quote.property}} resolved against the quote record, with a dotted path for nested values",
        "rejected": "positional tokens such as {{1}} or a server-side handle",
        "rejection_cost": (
            "A positional token cannot be read by a person editing the message, and a "
            "server-side handle cannot be rendered without a second lookup the message "
            "author does not see. A dotted path also reuses the same resolution the "
            "branch uses, so a renamed field fails in one place."
        ),
        "change_it": "TEMPLATE_OPEN, TEMPLATE_CLOSE and render_message",
    },
    {
        "id": "DERIVED_RENDERED_MESSAGE_NAMES_MISSING_FIELDS",
        "topic": "a template variable the quote does not carry",
        "basis": (
            "Nothing in the research covers it. A template is stored once and rendered "
            "against every quote that enrols, and the quotes differ."
        ),
        "chosen": "leave_the_token_and_name_it",
        "value": "render_message leaves {{quote.missing}} in place and lists the path under missing",
        "rejected": "render an unresolved variable as an empty string",
        "rejection_cost": (
            "A message reading 'quote  is over your limit' is indistinguishable from a "
            "bug in the number. Naming the field makes it debuggable, which is the whole "
            "reason the third outcome exists elsewhere in this package."
        ),
        "change_it": "the _replace closure in render_message",
    },
    {
        "id": "DERIVED_RE_ENROLMENT_RESETS_THE_CHAIN",
        "topic": "the re-enrolment state machine",
        "basis": (
            "The research gives the switch and its purpose. 'Re-enrollment can be "
            "toggled for quotes that need re-approval after edits.' It does not describe "
            "the transitions."
        ),
        "chosen": "reset_to_priority_one_and_count_the_run",
        "value": (
            "re_enrol returns the chain to pending_approval at priority 1, clears the "
            "decisions and increments a run counter, keeping the prior run's decision "
            "rows"
        ),
        "rejected": "keep the existing decisions and only move the state back to pending",
        "rejection_cost": (
            "Every level already reads approved, so a re-approval pass would complete "
            "instantly without asking anybody. The run counter is what preserves the "
            "history the audit log needs."
        ),
        "change_it": "re_enrol in approval_chain_rules.py",
    },
    {
        "id": "NOT_BUILT_TEAMS_SLACK_CHAT",
        "topic": "notification channels the research names but this build does not deliver",
        "basis": (
            "'Notifications fire automatically on each step' to bell, email, Teams, "
            "Slack and Google Chat. The product has a confirmed delivery surface for the "
            "first two only."
        ),
        "chosen": "build_bell_and_email",
        "value": "every notification is stored with its channel, and only bell and email resolve",
        "rejected": "store all five channels and report them as sent",
        "rejection_cost": (
            "A notification recorded as sent on a channel with no delivery surface is a "
            "false record in the audit log, which is the one thing the product promises "
            "is complete."
        ),
        "change_it": "DELIVERY_CHANNELS_BUILT and CHANNELS_RESEARCHED_BUT_NOT_BUILT",
    },
    {
        "id": "SHARED_APPROVAL_SHAPE_WITH_WF091",
        "topic": "the approval record shape WF-091 also describes",
        "basis": (
            "Issue 157 states that WF-091, 'Route a discounted quote for standard "
            "approval', covers the single-level case, and that both documents describe "
            "the same Settings > Objects > Quotes > Approvals tab. WF-091 is not on main."
        ),
        "chosen": "own_the_shape_and_publish_the_names",
        "value": (
            "These four collection names and the chain state vocabulary are the shared "
            "shape. WF-091 should read them rather than declare its own."
        ),
        "rejected": "wait for WF-091 to land and adopt whatever it chose",
        "rejection_cost": (
            "Waiting makes this ticket depend on a workflow that has not been built, and "
            "if WF-091 then diverges, two features hold two approval shapes in one "
            "product. Declaring the names here and serving them at /vocabulary gives the "
            "later feature a single thing to agree with."
        ),
        "change_it": "the collection names in approval_chain_vocabulary.py",
    },
)

NOT_BUILT: tuple[dict[str, Any], ...] = (
    {
        "id": "no-delivery-for-three-channels",
        "what_is_not_built": "Sending a notification on Teams, Slack or Google Chat.",
        "why": (
            "The research names the channels. This product has no credential store, no "
            "outbound connector and no confirmed delivery surface for any of the three, "
            "so a send would be a recorded intent rather than a delivery."
        ),
        "instead": "the notification row carries the channel, so a later connector delivers what is already recorded",
    },
    {
        "id": "quote-authoring-is-wf086",
        "what_is_not_built": "Creating a quote, its line items or its totals.",
        "why": (
            "WF-086 provisions the authored quote. This workflow reads wf086_quote as "
            "data and evaluates it, because the branch engine's question is which quote "
            "qualifies and not how a quote is written."
        ),
        "instead": "the workflow evaluates any quote record and works without a provisioned one",
    },
    {
        "id": "publishing-is-wf094",
        "what_is_not_built": "Publishing the approved quote to a public URL.",
        "why": (
            "'The quote becomes publishable' is a flag this workflow writes. WF-094 owns "
            "the publish and the hosted link."
        ),
        "instead": "final_state returns publishable true alongside the status, and the caller decides what to do with it",
    },
)


def describe() -> dict[str, Any]:
    """The judgement calls, served so a reviewer reads the list instead of the diff."""
    return {
        "sourced_quotes": list(SOURCED_QUOTES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "not_built": [dict(entry) for entry in NOT_BUILT],
    }