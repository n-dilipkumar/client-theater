"""Every researched term WF-100 enforces against.

Served from ``GET /vocabulary`` rather than duplicated in the frontend, so the page cannot
drift from the rules that compute it. Every sentence in :data:`EVIDENCE` is quoted from the
research in the ticket, and nothing here is a paraphrase presented as a quotation.
"""

from __future__ import annotations

from typing import Any

#: The researched lifecycle of a renewal quote.
#:
#: The research states the end of the lifecycle ("When a renewal quote is accepted, a new
#: contract is created and automatically associated with the previous contract") and names the
#: entry point ("Create renewal quote from contract"). It names nothing between them. So the
#: states before ``accepted`` are derived, and :data:`DERIVED_NOT_SOURCED` says so rather than
#: presenting the whole chain as sourced.
QUOTE_STATES = ("draft", "shared", "accepted", "superseded")

QUOTE_STATE_LABELS = {
    "draft": "Draft",
    "shared": "Shared with the buyer",
    "accepted": "Accepted by the buyer",
    "superseded": "Superseded by a newer quote",
}

#: The state machine, with what each state means and how it is reached.
QUOTE_STATES_DOCUMENTED = {
    "draft": "Created from a contract. No buyer has seen it.",
    "shared": "The seller shared the quote with the buyer. No state change in the data.",
    "accepted": (
        "The buyer accepted. This is the one transition the research states, and the one "
        "that creates the new contract and the renewal deal."
    ),
    "superseded": (
        "The seller created a newer renewal quote from the same contract. The older one can "
        "no longer be accepted. This state is derived, not sourced."
    ),
}

#: The evidence ends at "accepted". The earlier states are an implementation choice.
DERIVED_NOT_SOURCED = (
    "The research names the entry point and the acceptance outcome. It does not name the "
    "states between them. draft, shared and superseded are this implementation's choice, "
    "recorded rather than presented as sourced. They are derived, not sourced."
)

#: The researched settlement modes for a change effective date, in the researcher's order.
#:
#: The research lists them for the quote editor's Summary module: "Change effective date: On
#: agreement / Custom Date / Delayed start days / months". The wording is kept because a
#: seller reading the vendor documentation will search for exactly these words.
EFFECTIVE_DATE_MODES = ("on_agreement", "custom_date", "delayed_start", "months")

EFFECTIVE_DATE_MODE_LABELS = {
    "on_agreement": "On agreement",
    "custom_date": "Custom date",
    "delayed_start": "Delayed start",
    "months": "Months",
}

EFFECTIVE_DATE_MODE_DOCUMENTED = {
    "on_agreement": "The change takes effect on the day the quote is accepted.",
    "custom_date": "The change takes effect on a date the seller chose with a date picker.",
    "delayed_start": "The change takes effect a set number of days after the day of agreement.",
    "months": "The change takes effect a set number of months after the day of agreement.",
}

#: The researched way a renewal date is decided. Both branches are implemented.
#:
#: The research states the rule as a conditional and both of its branches are quoted, because
#: implementing only one of them would leave the other branch unreachable and a reviewer could
#: not tell whether that was a decision or an omission.
RENEWAL_DATE_RULE_IF_NOT_FINALISED = (
    "If a renewal hasn't been finalized, or a renewal quote isn't added to the contract, the "
    "renewal date will be the date the contract ends."
)
RENEWAL_DATE_RULE_IF_FINALISED = (
    "If a renewal has been finalized or a renewal quote has been accepted by the customer, "
    "the renewal date will be the effective date of the renewal quote."
)
RENEWAL_DATE_RULE = f"{RENEWAL_DATE_RULE_IF_FINALISED} {RENEWAL_DATE_RULE_IF_NOT_FINALISED}"
RENEWAL_DATE_BRANCHES = {
    "if_not_finalised": RENEWAL_DATE_RULE_IF_NOT_FINALISED,
    "if_finalised": RENEWAL_DATE_RULE_IF_FINALISED,
}
RENEWAL_DATE_BRANCH_NOTE = (
    "Both branches are implemented. A contract with no accepted renewal quote reports its own "
    "end date. A contract whose renewal quote was accepted reports the effective date of that "
    "quote."
)

#: The researched term-length label for a contract that renews until cancelled.
#:
#: The research is explicit that this is a label and not a separate type: "If all line items
#: are set to Automatically renew until canceled, the term length is marked as Evergreen."
EVERGREEN_LABEL = "Evergreen"
EVERGREEN_RULE = (
    "If all line items are set to Automatically renew until canceled, the term length is "
    "marked as Evergreen."
)
EVERGREEN_IS_A_LABEL = (
    "Evergreen is a derived label on a term length. It is not a separate contract type and it "
    "is not a separate state. The record keeps its own line items and this workflow computes "
    "the label from them."
)
EVERGREEN_RENEWAL_FIELD = "auto_renew"
EVERGREEN_RENEWAL_VALUE = "until_canceled"

#: The researched deal-selection methods for the renewal workflow action.
DEAL_SELECTION_METHODS = ("new_deal_default_stage", "existing_deal")

DEAL_SELECTION_METHOD_LABELS = {
    "new_deal_default_stage": "New deal using default pipeline and stage",
    "existing_deal": "Existing deal",
}

DEAL_SELECTION_METHOD_DOCUMENTED = {
    "new_deal_default_stage": (
        "The renewal action creates a new deal in the pipeline and stage the seller chose."
    ),
    "existing_deal": (
        "The renewal action attaches the renewal to a deal the seller named. No deal is created."
    ),
}

#: The researched contract scope for a renewal workflow.
CONTRACT_TARGETS = ("one_contract", "all_associated")

CONTRACT_TARGET_LABELS = {
    "one_contract": "One contract",
    "all_associated": "Contracts: all associated",
}

CONTRACT_TARGET_DOCUMENTED = {
    "one_contract": "The renewal action runs against a single contract the seller chose.",
    "all_associated": (
        "The renewal action runs against every contract associated with the deal the workflow "
        "is enrolled on."
    ),
}

#: The researched re-enrol switch. Its behaviour is not described anywhere in the research,
#: so the decision is recorded rather than presented as a sourced fact.
REENROLL_FIELD = "re_enroll"
REENROLL_DECISION = "reset"
REENROLL_DECISION_REASON = (
    "The research names a Re-enroll switch on the renewal workflow and never describes what it "
    "does. This implementation treats it as: when the workflow has produced an accepted "
    "renewal, re-enrolling starts a fresh cycle against the new contract rather than staying "
    "attached to the contract that has just been renewed. A workflow without the switch stays "
    "enrolled on the contract it was enrolled on."
)
REENROLL_NOT_SOURCED = (
    "Re-enroll was named in the research but its behaviour was not described. The behaviour "
    "implemented here is a decision, not a sourced fact."
)

#: The researched alert offset on the contract's Renewals settings.
ALERT_OFFSET_NOTE = (
    "The renewal alert appears on the contract before the renewal date, using a configured day "
    "offset. The research gives no default and no bound, so an offset is an integer of any "
    "size and 0 days means the alert appears on the renewal date itself."
)
ALERT_OFFSET_DEFAULT = 30

#: The researched direct-renewal beta path.
DIRECT_RENEWAL_BETA = True
DIRECT_RENEWAL_NOTE = (
    "Direct contract renewal is marked BETA in the research and is described as bypassing the "
    "quote. This implementation did not build the bypass. It built the quote path, because the "
    "bypass's whole behaviour is that no quote exists, so implementing it would have meant "
    "shipping a second workflow that shares no code with the first. The bypass is recorded as "
    "not built rather than left unmentioned."
)
DIRECT_RENEWAL_REJECTED = (
    "Renew contract directly, which creates a new contract with no quote record at all. It was "
    "rejected for this ticket because it shares no code path with the quote workflow, so "
    "building it would have doubled the surface without adding a researched rule."
)

#: The researched workflow-action boundary.
WORKFLOW_ACTION_NOTE = (
    "Create renewal quote from contract is described in the research as a deal-based workflow "
    "action and explicitly not a documented REST endpoint. There is no vendor endpoint to "
    "mirror, so this workflow models it as a room action: the same engine call the seller makes "
    "by hand, reachable from a stored workflow definition."
)
WORKFLOW_ACTION_REJECTED = (
    "Model it as a call to a HubSpot-style POST /automation/actions endpoint. It was rejected "
    "because the research says no such endpoint was read, so the shape would have been invented."
)

#: The template collection. The research names the template as an input and never says which
#: workflow creates it.
TEMPLATE_COLLECTION = "wf100_renewal_template"
TEMPLATE_OWNERSHIP_NOTE = (
    "The research names a renewal and change quote template as an input to this workflow and "
    "does not say which workflow creates it. No pending ticket in this repository provisions "
    "one, so this workflow owns the collection and writes its own templates. If another "
    "workflow later provisions templates, it writes into this collection and this workflow "
    "reads what it finds."
)
TEMPLATE_REJECTED = (
    "Read templates from the wf086_quote template collection. It was rejected because that "
    "workflow is not a dependency of this one and the research names no relationship between "
    "them, so reading it would couple two workflows over a guess."
)

#: The researched quote template association type.
TEMPLATE_ASSOCIATION_TYPE = 286
TEMPLATE_ASSOCIATION_NOTE = (
    "The research states the renewal quote template id is attached as association type 286 at "
    "creation. That number is carried on the quote record as template_association_type so the "
    "association is recorded rather than implied by the presence of a template id."
)

#: The researched quote creation endpoint, recorded because this workflow does not call it.
VENDOR_QUOTE_ENDPOINT = "POST /crm/objects/{version}/quotes"
VENDOR_QUOTE_NOTE = (
    "The research names this endpoint for a vendor quote object. This product has no vendor "
    "call and no outbound integration for this workflow, so the endpoint is recorded as the "
    "shape the research describes and the room writes to its own store."
)

#: The renewal chain, as record fields. Both ends are written on acceptance, so the chain is a
#: walk in either direction rather than a search over the collection.
CHAIN_PREDECESSOR = "renewed_from_contract_id"
CHAIN_SUCCESSOR = "renewed_into_contract_id"
CHAIN_QUOTE = "renewed_into_quote_id"

#: Stamped on a contract the moment its renewal is accepted.
#:
#: It is separate from :data:`CHAIN_QUOTE` because the chain link is a pointer and this is a
#: fact about the contract. A contract that names an accepted quote has been renewed. A
#: contract that merely has an end date has not.
FINALISED_FLAG = "renewal_finalised"

#: Every quotation in this module is quoted from the research in the ticket.
EVIDENCE = {
    "renewal_creates_contract": (
        "When a renewal quote is accepted, a new contract is created and automatically "
        "associated with the previous contract."
    ),
    "renewal_can_be_direct": (
        "Renewals can be created directly from a contract (BETA) or by using renewal quotes."
    ),
    "workflow_action": "Create renewal quote from contract",
    "workflow_contract_dropdown": "(a recommended option or Contracts: all associated)",
    "renewal_date_when_finalised": RENEWAL_DATE_RULE_IF_FINALISED,
    "renewal_date_when_not_finalised": RENEWAL_DATE_RULE_IF_NOT_FINALISED,
    "evergreen": EVERGREEN_RULE,
}

#: The collections this workflow owns, and the ones it only reads.
OWNED_COLLECTIONS = (
    "contract",
    TEMPLATE_COLLECTION,
    "wf100_renewal_quote",
    "wf100_deal",
    "wf100_renewal_workflow",
)

READ_ONLY_SOURCE = "wf086_quote"


def vocabulary() -> dict[str, Any]:
    """The vocabulary, shaped for a page to render without re-deriving anything."""

    return {
        "quote_states": list(QUOTE_STATES),
        "quote_state_labels": dict(QUOTE_STATE_LABELS),
        "quote_states_documented": dict(QUOTE_STATES_DOCUMENTED),
        "derived_not_sourced": DERIVED_NOT_SOURCED,
        "effective_date_modes": list(EFFECTIVE_DATE_MODES),
        "effective_date_mode_labels": dict(EFFECTIVE_DATE_MODE_LABELS),
        "effective_date_modes_documented": dict(EFFECTIVE_DATE_MODE_DOCUMENTED),
        "renewal_date_rule": RENEWAL_DATE_RULE,
        "renewal_date_branches": dict(RENEWAL_DATE_BRANCHES),
        "renewal_date_branch_note": RENEWAL_DATE_BRANCH_NOTE,
        "evergreen_label": EVERGREEN_LABEL,
        "evergreen_rule": EVERGREEN_RULE,
        "evergreen_is_a_label": EVERGREEN_IS_A_LABEL,
        "deal_selection_methods": list(DEAL_SELECTION_METHODS),
        "deal_selection_method_labels": dict(DEAL_SELECTION_METHOD_LABELS),
        "deal_selection_methods_documented": dict(DEAL_SELECTION_METHOD_DOCUMENTED),
        "contract_targets": list(CONTRACT_TARGETS),
        "contract_target_labels": dict(CONTRACT_TARGET_LABELS),
        "contract_targets_documented": dict(CONTRACT_TARGET_DOCUMENTED),
        "re_enroll_field": REENROLL_FIELD,
        "re_enroll_decision": REENROLL_DECISION,
        "re_enroll_reason": REENROLL_DECISION_REASON,
        "re_enroll_not_sourced": REENROLL_NOT_SOURCED,
        "alert_offset_note": ALERT_OFFSET_NOTE,
        "alert_offset_default": ALERT_OFFSET_DEFAULT,
        "direct_renewal_beta": DIRECT_RENEWAL_BETA,
        "direct_renewal_note": DIRECT_RENEWAL_NOTE,
        "workflow_action_note": WORKFLOW_ACTION_NOTE,
        "template_collection": TEMPLATE_COLLECTION,
        "template_ownership_note": TEMPLATE_OWNERSHIP_NOTE,
        "template_association_type": TEMPLATE_ASSOCIATION_TYPE,
        "template_association_note": TEMPLATE_ASSOCIATION_NOTE,
        "vendor_quote_endpoint": VENDOR_QUOTE_ENDPOINT,
        "vendor_quote_note": VENDOR_QUOTE_NOTE,
        "chain_fields": {
            "predecessor": CHAIN_PREDECESSOR,
            "successor": CHAIN_SUCCESSOR,
            "quote": CHAIN_QUOTE,
            "finalised": FINALISED_FLAG,
        },
        "owned_collections": list(OWNED_COLLECTIONS),
        "read_only_source": READ_ONLY_SOURCE,
        "evidence": dict(EVIDENCE),
    }


def describe() -> dict[str, Any]:
    """Alias of :func:`vocabulary`, for the call site that reads better as ``describe``."""

    return vocabulary()
