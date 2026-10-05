"""Every judgement call WF-100 made, with the alternative it rejected.

The specification says a workflow must "derive it and record the derivation, not assume it".
This module is that record, and ``GET /decisions`` serves it so a reviewer reads the decision
rather than the code.
"""

from __future__ import annotations

from typing import Any

#: Each entry is one decision. ``id`` is stable because ``GET /decisions/{id}`` serves it and
#: a test asserts the count, so a decision may be added but an id may not be renamed.
_INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "wf100-acceptance-signal",
        "question": "Who accepts a renewal quote, and how does the room learn that it happened?",
        "chosen": "An explicit room action records the acceptance",
        "left_open_by": (
            "The research says 'When a renewal quote is accepted, a new contract is created and "
            "automatically associated with the previous contract.' It never says who accepts or "
            "how the room learns that it happened."
        ),
        "rejected_because": (
            "Inferring acceptance from a status write would give a status field a hidden side "
            "effect that creates two records. Splitting acceptance and finalisation into two "
            "steps would create a half-finished renewal state that the research does not "
            "describe."
        ),
        "cost_of_the_choice": (
            "The buyer-facing accept click is not modelled as a separate actor with its own "
            "identity. The acceptance is attributed to whoever calls the route. This matches how "
            "the rest of this product treats acceptance, and it keeps the audit row naming a "
            "route the app actually serves."
        ),
        "sourced": False,
        "jev_audit_id": "jev-20261004T231639-22752-99672",
        "jev_verdict": "pass",
    },
    {
        "id": "wf100-direct-renewal",
        "question": "Did this build the direct contract renewal that bypasses the quote?",
        "chosen": "No. The quote path was built.",
        "left_open_by": (
            "The research says 'Renewals can be created directly from a contract (BETA) or by "
            "using renewal quotes.' It names both and does not require both."
        ),
        "rejected_because": (
            "The bypass creates a new contract with no quote record, so it shares no code path "
            "with the quote workflow. Building it would have doubled the surface without adding "
            "a researched rule, and the research marks it BETA."
        ),
        "cost_of_the_choice": (
            "A seller enrolled in the direct renew beta cannot renew through this room. The "
            "bypass is recorded here and in the vocabulary so its absence is a stated decision "
            "rather than a gap a reviewer has to find."
        ),
        "sourced": False,
    },
    {
        "id": "wf100-workflow-action-shape",
        "question": "How is the Create renewal quote from contract workflow action modelled?",
        "chosen": "As a room action backed by a stored workflow definition",
        "left_open_by": (
            "The research says the action is 'a deal-based workflow action (workflows tool, not "
            "a documented REST endpoint on the pages read)'."
        ),
        "rejected_because": (
            "There is no vendor endpoint to mirror. Inventing an endpoint shaped like one would "
            "have made the room look like it integrates with a vendor it does not call."
        ),
        "cost_of_the_choice": (
            "The workflow runs inside this product rather than on a vendor's scheduler. There "
            "is no vendor enrolment state to poll."
        ),
        "sourced": False,
    },
    {
        "id": "wf100-template-ownership",
        "question": "Which workflow owns the renewal and change quote template collection?",
        "chosen": "This workflow owns it",
        "left_open_by": (
            "The research names a renewal and change quote template as an input and does not say "
            "which workflow creates it."
        ),
        "rejected_because": (
            "No pending ticket in this repository provisions a template, so reading one from "
            "another workflow's collection would couple two workflows over a guess."
        ),
        "cost_of_the_choice": (
            "If a later workflow provisions templates it must write into this collection. The "
            "collection name is in the vocabulary so that workflow can find it without reading "
            "this module."
        ),
        "sourced": False,
    },
    {
        "id": "wf100-re-enroll",
        "question": "What does the Re-enroll switch on a renewal workflow do?",
        "chosen": "Re-enrolling starts a fresh cycle against the newly created contract",
        "left_open_by": (
            "The research names a Re-enroll switch on the renewal workflow and never describes "
            "its behaviour."
        ),
        "rejected_because": (
            "Leaving it inert would make the switch a field a seller can set and a room that "
            "ignores, which is the one behaviour that cannot be reviewed."
        ),
        "cost_of_the_choice": (
            "A workflow with the switch on follows the renewal chain onto the new contract. A "
            "workflow with it off stays enrolled on the contract it was enrolled on. Both "
            "behaviours are implemented so a reviewer can see both."
        ),
        "sourced": False,
    },
    {
        "id": "wf100-evergreen-as-label",
        "question": "Is Evergreen a contract type, a state, or a label?",
        "chosen": "A derived label on the term length",
        "left_open_by": (
            "The research says 'If all line items are set to Automatically renew until "
            "canceled, the term length is marked as Evergreen.'"
        ),
        "rejected_because": (
            "A separate type or state would need a migration and a typed column, and the "
            "evidence describes a label applied to an existing field."
        ),
        "cost_of_the_choice": (
            "The label is computed from line items on every read, so changing a line item's "
            "renewal setting changes the label with no separate write."
        ),
        "sourced": True,
    },
    {
        "id": "wf100-renewal-date-branches",
        "question": "Which branch of the renewal date rule is implemented?",
        "chosen": "Both",
        "left_open_by": ("The research states the rule as a conditional and gives both branches."),
        "rejected_because": (
            "Implementing one branch would leave the other unreachable, and a reviewer could "
            "not tell whether that was a decision or an omission."
        ),
        "cost_of_the_choice": (
            "Neither branch is a special case. A contract with no accepted renewal reports its "
            "own end date. A contract with an accepted renewal reports that quote's effective "
            "date."
        ),
        "sourced": True,
    },
    {
        "id": "wf100-proration",
        "question": "How is the proration flag stored when it is cleared?",
        "chosen": "As an explicit false, never as a missing field",
        "left_open_by": (
            "The research describes a checkbox the seller can optionally clear, and does not "
            "describe the stored state when it is cleared."
        ),
        "rejected_because": (
            "Storing the absence of the field would make a cleared checkbox and a template that "
            "never had one indistinguishable."
        ),
        "cost_of_the_choice": (
            "Every quote record carries the flag. A reader cannot tell whether proration was "
            "cleared or was never offered, because both are false, and both mean the same thing."
        ),
        "sourced": False,
    },
    {
        "id": "wf100-deal-creation-timing",
        "question": "When is the renewal deal created?",
        "chosen": "At acceptance, in the same call that creates the new contract",
        "left_open_by": (
            "The research says a deal is created to track the renewal opportunity and that deal "
            "creation can be fully automatic. It does not say whether the deal exists before "
            "the quote is accepted."
        ),
        "rejected_because": (
            "Creating the deal at quote time would put a live opportunity on the board for a "
            "renewal the buyer may never accept, and no researched rule would explain how to "
            "close it."
        ),
        "cost_of_the_choice": (
            "Before acceptance there is no deal for the renewal. The quote carries the deal "
            "pipeline and stage the seller chose, so the intent is visible on the quote even "
            "though the deal does not exist yet."
        ),
        "sourced": False,
    },
    {
        "id": "wf100-read-only-source",
        "question": "Does this workflow write quotes into the shared quote collection?",
        "chosen": "No. It owns its own renewal quote collection",
        "left_open_by": (
            "The research describes a renewal quote as a quote object. This product has a "
            "separate workflow that owns quotes."
        ),
        "rejected_because": (
            "Writing into another workflow's collection would make two workflows write one "
            "collection, which is how a hundred parallel agents collide."
        ),
        "cost_of_the_choice": (
            "A renewal quote is not visible in the other workflow's quote list. It is readable "
            "through this workflow's own routes and the vendor-shaped quote collection is "
            "recorded on each record for anything that needs to align."
        ),
        "sourced": False,
    },
)


def count() -> int:
    return len(_INFERENCES)


def describe() -> list[dict[str, Any]]:
    return [dict(inference) for inference in _INFERENCES]


def describe_one(inference_id: str) -> dict[str, Any] | None:
    for inference in _INFERENCES:
        if inference["id"] == inference_id:
            return dict(inference)
    return None
