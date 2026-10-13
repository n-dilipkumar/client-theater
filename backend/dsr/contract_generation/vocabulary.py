"""The words WF-099 speaks, and the evidence each one came from.

This package turns an accepted quote into a contract. It is the fourteenth
research workflow in the quoting and proposals domain, and its specification is
``docs/research/digital-sales-room-workflows/wf/WF-099.md``, quoted in full in
GitHub issue 195.

The rule this package exists to hold is in one sentence of the research:
"Contracts inherit line items, terms, and other details from the associated
quote." That is a copy, not a reference. The contract is the "centralized source
of truth for committed revenue", so it must not change when the quote it came
from changes. Everything in :mod:`dsr.contract_generation.rules` follows from
that one word: inherit.

What this package owns, and what it only reads
----------------------------------------------

``contract`` is a collection WF-100 already names. WF-100 renews a contract and
reads exactly ``name``, ``seller``, ``buyer``, ``currency``, ``line_items``,
``term_length``, ``start_date`` and ``end_date`` off it, plus the chain fields it
writes itself. This workflow therefore writes a contract in that shape, on the
Jev decision recorded at audit ``jev-20261013T150335-12320-15946``
(``adopt_contract_collection`` at confidence 0.92): a second writer of one
collection, coordinated by the shape WF-100 already reads. The rejected
alternative was a private ``wf099_contract`` collection that WF-100 could not
renew, which would have made this record something other than the source of
truth the research says it is. Collections are not shared files: the plugin
contract governs which files a branch may edit, and it says nothing about which
record collection a workflow may write.

The quote and its line items are WF-086's and are read as data. Their names are
not re-declared here: this module imports them from
:mod:`dsr.quote_authoring.vocabulary` so a rename lands in one place.

Acceptance is a WF-099 ledger, on the Jev decision recorded at audit
``jev-20261013T150332-8132-12677`` (``own_acceptance_ledger`` at confidence
1.00). The rejected alternatives were reading WF-095's or WF-096's private
collection (a coupling WF-100 explicitly refused for its own dependency) and
trusting a bare ``hs_status`` string (which any generic record write could
flip). ``wf099_acceptance`` is written only by this workflow's acceptance route,
and a contract is generated only from a row in it, so an arbitrary write of
``hs_status`` cannot produce a contract.
"""

from __future__ import annotations

from typing import Any

from dsr.quote_authoring.vocabulary import (
    LINE_ITEM_COLLECTION as QUOTE_LINE_ITEM_COLLECTION,
    QUOTE_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The contract itself. WF-100 already reads this collection by this exact name
#: and lists it in its owned collections. This workflow writes the shape WF-100
#: reads, so a contract created from an accepted quote is renewable by WF-100
#: with no adapter. See the module docstring for the Jev decision.
CONTRACT_COLLECTION = "contract"

#: Orders. The research's scenario matrix creates an order "associated with the
#: contract" only when the seller is in the Connected CPQ, Billing and Payments
#: beta. It is this workflow's own collection because no other workflow names an
#: order record.
ORDER_COLLECTION = "wf099_order"

#: The contract-generation setting: ``Settings -> Data Management -> Objects ->
#: Contracts -> Create contracts from accepted quotes``, plus the beta flag and
#: the renewal-alert values the same settings page carries.
SETTING_COLLECTION = "wf099_contract_setting"

#: The acceptance ledger. Written only by this workflow's acceptance route. A
#: contract is generated only from a row here, which is what closes the
#: arbitrary-write hole.
ACCEPTANCE_COLLECTION = "wf099_acceptance"

#: One row per generation attempt, so a reviewer can see why a contract was or
#: was not created: the flow, the reason, the contract id and the order id.
GENERATION_COLLECTION = "wf099_generation"

#: The two collections this workflow reads and does not own. Their names are
#: imported from WF-086 rather than retyped.
READ_ONLY_COLLECTIONS = (QUOTE_COLLECTION, QUOTE_LINE_ITEM_COLLECTION)

#: Every collection this workflow writes.
OWNED_COLLECTIONS = (
    CONTRACT_COLLECTION,
    ORDER_COLLECTION,
    SETTING_COLLECTION,
    ACCEPTANCE_COLLECTION,
    GENERATION_COLLECTION,
)

# --------------------------------------------------------------------------- #
# The setting
# --------------------------------------------------------------------------- #

#: "Set whether contracts are automatically created from accepted quotes." The
#: single switch the user flow turns on.
FIELD_CREATE_FROM_ACCEPTED_QUOTES = "create_contracts_from_accepted_quotes"

#: Whether the seller is "Enrolled in the Connected CPQ, Billing, and Payments
#: beta". The scenario matrix's first branch depends on it.
FIELD_CONNECTED_CPQ = "connected_cpq"

#: The "Direct Create, Edit, and Renew HubSpot Contracts" beta, which "allows
#: contracts without a quote". Enables the standalone contract route.
FIELD_DIRECT_CONTRACTS = "direct_contracts_enabled"

#: "Setup > Deal creation": whether the renewal action creates a deal, and in
#: which pipeline and stage. Recorded because the settings page carries it.
FIELD_DEAL_CREATION = "deal_creation_enabled"
FIELD_DEAL_PIPELINE = "deal_pipeline"
FIELD_DEAL_STAGE = "deal_stage"

#: "Renewals tab -> Renewal is approaching alert". Recorded, not shipped. Its
#: three documented values are below.
FIELD_RENEWAL_ALERT_MODE = "renewal_alert_mode"
FIELD_RENEWAL_ALERT_DAYS = "renewal_alert_days"
RENEWAL_ALERT_MODES = ("days", "custom", "off")
DEFAULT_RENEWAL_ALERT_DAYS = 30

#: The default setting. Off, because the research describes the switch as a
#: seller turning contract creation on, and a product that silently mints
#: contracts is the worse default of the two.
DEFAULT_SETTING: dict[str, Any] = {
    FIELD_CREATE_FROM_ACCEPTED_QUOTES: False,
    FIELD_CONNECTED_CPQ: False,
    FIELD_DIRECT_CONTRACTS: False,
    FIELD_DEAL_CREATION: False,
    FIELD_DEAL_PIPELINE: None,
    FIELD_DEAL_STAGE: None,
    FIELD_RENEWAL_ALERT_MODE: "off",
    FIELD_RENEWAL_ALERT_DAYS: DEFAULT_RENEWAL_ALERT_DAYS,
}

#: Settings keys this workflow accepts on a write. Anything else is ignored so a
#: team adding a field to the record never needs this list updated.
SETTING_FIELDS = tuple(DEFAULT_SETTING)

# --------------------------------------------------------------------------- #
# The effective start date
# --------------------------------------------------------------------------- #
#
# The research names four quote properties and a four-value enumeration:
# ``hs_contract_effective_start_date``, ``hs_contract_effective_start_date_type``,
# ``hs_contract_effective_start_delayed_by_days``,
# ``hs_contract_effective_start_delayed_by_months``. Two modes need one extra
# field and two need none, which is exactly why each is implemented and tested.

FIELD_EFFECTIVE_START_DATE = "hs_contract_effective_start_date"
FIELD_EFFECTIVE_START_DATE_TYPE = "hs_contract_effective_start_date_type"
FIELD_EFFECTIVE_START_DELAYED_DAYS = "hs_contract_effective_start_delayed_by_days"
FIELD_EFFECTIVE_START_DELAYED_MONTHS = "hs_contract_effective_start_delayed_by_months"

MODE_ON_AGREEMENT = "ON_AGREEMENT"
MODE_CUSTOM = "CUSTOM"
MODE_DELAYED_BY_DAYS = "DELAYED_BY_DAYS"
MODE_DELAYED_BY_MONTHS = "DELAYED_BY_MONTHS"

EFFECTIVE_START_DATE_MODES = (
    MODE_ON_AGREEMENT,
    MODE_CUSTOM,
    MODE_DELAYED_BY_DAYS,
    MODE_DELAYED_BY_MONTHS,
)

#: The extra field each mode needs, in the order a form would ask for it. Two
#: modes carry an empty tuple, and that is a fact worth testing rather than a
#: detail: "two of them need an extra field and two do not".
MODE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    MODE_ON_AGREEMENT: (),
    MODE_CUSTOM: (FIELD_EFFECTIVE_START_DATE,),
    MODE_DELAYED_BY_DAYS: (FIELD_EFFECTIVE_START_DELAYED_DAYS,),
    MODE_DELAYED_BY_MONTHS: (FIELD_EFFECTIVE_START_DELAYED_MONTHS,),
}

DEFAULT_EFFECTIVE_START_DATE_MODE = MODE_ON_AGREEMENT

#: The *extra* field each mode needs beyond the base start date. Two modes need
#: one and two do not: ON_AGREEMENT resolves from the agreement date and CUSTOM
#: from the base ``hs_contract_effective_start_date``, so neither needs an extra
#: field, while each delayed mode needs its own offset. That is the "two of them
#: need an extra field and two do not" the issue names.
MODE_EXTRA_FIELDS: dict[str, tuple[str, ...]] = {
    MODE_ON_AGREEMENT: (),
    MODE_CUSTOM: (),
    MODE_DELAYED_BY_DAYS: (FIELD_EFFECTIVE_START_DELAYED_DAYS,),
    MODE_DELAYED_BY_MONTHS: (FIELD_EFFECTIVE_START_DELAYED_MONTHS,),
}

# --------------------------------------------------------------------------- #
# The acceptance signal
# --------------------------------------------------------------------------- #
#
# "The state will automatically be updated to ACCEPTED when the buyer accepts
# the quote." The platform writes ``hs_status`` and the accepting party, and the
# field names are WF-096's so the two workflows agree on the shared quote record.

FIELD_HS_STATUS = "hs_status"
QUOTE_STATUS_ACCEPTED = "ACCEPTED"
FIELD_HS_CLICKWRAP_ACCEPTED_BY = "hs_clickwrap_accepted_by"

#: The quote states WF-086 writes. A contract needs a published quote, because
#: publishing is the step that froze the line items and wrote the total.
QUOTE_STATUS_PUBLISHED = "published"

#: The acceptance methods a buyer may use, copied from the vendor's
#: ``hs_acceptance_method``. Kept as a tuple here rather than imported from
#: WF-095 because this workflow records which method the platform reported, and
#: those words are the platform's, not one feature's.
ACCEPTANCE_METHOD_ESIGNATURE = "esignature"
ACCEPTANCE_METHOD_CLICKWRAP = "clickwrap"
ACCEPTANCE_METHOD_PRINT_AND_SIGN = "print_and_sign"
ACCEPTANCE_METHODS = (
    ACCEPTANCE_METHOD_ESIGNATURE,
    ACCEPTANCE_METHOD_CLICKWRAP,
    ACCEPTANCE_METHOD_PRINT_AND_SIGN,
)

# --------------------------------------------------------------------------- #
# Billing: a second, independent switch
# --------------------------------------------------------------------------- #
#
# "Billing-enabled contracts and quotes create subscriptions, which are used to
# automatically invoice buyers and collect payments." So a subscription does not
# depend on the contract switch. This workflow records the billing intent; the
# subscription itself is WF-096's collection and is not written here.

FIELD_HS_BILLING_ENABLED = "hs_billing_enabled"
FIELD_HS_PAYMENT_ENABLED = "hs_payment_enabled"

BILLING_SUBSCRIPTION = "subscription"
BILLING_NONE = "none"

# --------------------------------------------------------------------------- #
# Flow: what the scenario matrix decides
# --------------------------------------------------------------------------- #

#: Setting on, enrolled in the beta: a contract and an order.
FLOW_CONTRACT_AND_ORDER = "contract_and_order"

#: Setting on, not in the beta: a contract only.
FLOW_CONTRACT_ONLY = "contract_only"

#: Setting off: no contract.
FLOW_NONE = "none"

FLOWS = (FLOW_CONTRACT_AND_ORDER, FLOW_CONTRACT_ONLY, FLOW_NONE)

# --------------------------------------------------------------------------- #
# The legacy exclusion
# --------------------------------------------------------------------------- #
#
# "accepted legacy quotes don't create contracts." The research defines legacy
# nowhere, so this workflow chooses a rule and records it. A quote is legacy when
# it carries an explicit marker. That is the Jev decision recorded at audit
# ``jev-20261013T150339-2452-19263`` (``explicit_marker`` at confidence 1.00);
# the rejected alternative was inferring legacy from missing modern fields, which
# would misclassify any modern quote that simply omitted one.

LEGACY_FLAG = "legacy_quote"
LEGACY_QUOTE_TYPES = ("legacy", "legacy_quote")
LEGACY_ACCEPTANCE_CHANNEL = "legacy"
FIELD_QUOTE_TYPE = "quote_type"
FIELD_ACCEPTED_VIA = "accepted_via"

#: Why the rule is what it is, carried to every response so a reader can tell a
#: decision from an omission.
LEGACY_RULE = (
    "The research states that accepted legacy quotes create no contract and "
    "defines legacy nowhere. This workflow treats a quote as legacy only when it "
    "carries an explicit marker: legacy_quote is true, or quote_type is legacy, or "
    "accepted_via is legacy. It does not infer legacy from age or from a missing "
    "field, because a modern quote that omitted a field would then be excluded "
    "silently."
)

# --------------------------------------------------------------------------- #
# Refusal reasons
# --------------------------------------------------------------------------- #

REASON_QUOTE_NOT_FOUND = "quote_not_found"
REASON_CONTRACT_NOT_FOUND = "contract_not_found"
REASON_QUOTE_NOT_PUBLISHED = "quote_not_published"
REASON_ACCEPTANCE_NOT_RECORDED = "acceptance_not_recorded"
REASON_LEGACY_QUOTE = "legacy_quote_excluded"
REASON_SETTING_OFF = "contract_creation_disabled"
REASON_DIRECT_CONTRACTS_DISABLED = "direct_contracts_disabled"
REASON_UNKNOWN_EFFECTIVE_START_MODE = "unknown_effective_start_mode"
REASON_MISSING_EFFECTIVE_START_FIELD = "missing_effective_start_field"
REASON_ALREADY_GENERATED = "contract_already_exists"


def refusal_reasons() -> dict[str, str]:
    """Every refusal token, with the sentence the page shows for it."""

    return {
        REASON_QUOTE_NOT_FOUND: "No quote record matches that reference.",
        REASON_CONTRACT_NOT_FOUND: "No contract record matches that reference.",
        REASON_QUOTE_NOT_PUBLISHED: (
            "A contract is created only from a published quote. Publish the quote first."
        ),
        REASON_ACCEPTANCE_NOT_RECORDED: (
            "No acceptance has been recorded for this quote through the acceptance "
            "route, so no contract is created."
        ),
        REASON_LEGACY_QUOTE: (
            "This is a legacy quote. Accepted legacy quotes do not create contracts."
        ),
        REASON_SETTING_OFF: (
            "Contracts are not created from accepted quotes while the setting is off."
        ),
        REASON_DIRECT_CONTRACTS_DISABLED: (
            "Standalone contracts need the Direct Create, Edit, and Renew beta."
        ),
        REASON_UNKNOWN_EFFECTIVE_START_MODE: "That is not an effective start date mode.",
        REASON_MISSING_EFFECTIVE_START_FIELD: (
            "This effective start date mode needs an extra field."
        ),
        REASON_ALREADY_GENERATED: "This quote already produced a contract.",
    }


# --------------------------------------------------------------------------- #
# Evidence, quoted from the research
# --------------------------------------------------------------------------- #

EVIDENCE: dict[str, str] = {
    "central_source": (
        "Contracts are the centralized source of truth for committed revenue in HubSpot."
    ),
    "inherit": (
        "Set whether contracts are automatically created from accepted quotes. "
        "Contracts inherit line items, terms, and other details from the associated quote."
    ),
    "setting_and_beta": (
        "Setting on. Enrolled in the Connected CPQ, Billing, and Payments beta. -> "
        "Contracts are created when a quote is accepted. An order is also created, and "
        "associated with the contract."
    ),
    "legacy": "accepted legacy quotes don't create contracts.",
    "accepted_state": (
        "The state will automatically be updated to ACCEPTED when the buyer accepts the quote."
    ),
    "billing_independent": (
        "Billing-enabled contracts and quotes create subscriptions, which are used to "
        "automatically invoice buyers and collect payments."
    ),
    "standalone_beta": (
        "The Direct Create, Edit, and Renew HubSpot Contracts beta allows contracts "
        "without a quote."
    ),
    "renewal_alerts_separate": ("Renewal alerts and renewal workflows are separately scheduled."),
    "effective_start_properties": (
        "properties hs_contract_effective_start_date, "
        "hs_contract_effective_start_date_type, "
        "hs_contract_effective_start_delayed_by_days, "
        "hs_contract_effective_start_delayed_by_months are set on the quote"
    ),
    "effective_start_values": "ON_AGREEMENT | CUSTOM | DELAYED_BY_DAYS | DELAYED_BY_MONTHS",
}


def not_implemented() -> list[dict[str, str]]:
    """What this workflow deliberately does not build, and why."""

    return [
        {
            "item": "Renewal alerts and renewal workflows",
            "source": EVIDENCE["renewal_alerts_separate"],
            "decision": (
                "The renewal alert banner, the days/custom/off alert offset and the "
                "renewal workflow with its relative-date trigger belong to WF-100, which "
                "owns renewals. This workflow records the alert settings the Contracts "
                "settings page carries so the page is honest, and ships no alert or "
                "workflow engine."
            ),
        },
        {
            "item": "Invoice and subscription creation",
            "source": EVIDENCE["billing_independent"],
            "decision": (
                "A subscription and the invoices it drives are WF-096's collections. This "
                "workflow reports the billing flow the quote describes (a subscription is "
                "expected when hs_billing_enabled or hs_payment_enabled is set, "
                "independently of the contract switch) and writes no invoice or "
                "subscription record."
            ),
        },
        {
            "item": "Talking to the vendor CRM",
            "source": (
                "Quote endpoints GET/PATCH /crm/objects/{version}/quotes/{quoteId}; "
                "contracts and orders via the generic CRM objects API."
            ),
            "decision": (
                "This product has no outbound vendor call. The workflow writes to its own "
                "store, and the vendor endpoints are recorded as the shape the research "
                "describes rather than called."
            ),
        },
    ]


def vocabulary() -> dict[str, Any]:
    """Everything the page needs to label this workflow, and nothing it must guess."""

    return {
        "ticket": "WF-099",
        "collections": {
            "contract": CONTRACT_COLLECTION,
            "order": ORDER_COLLECTION,
            "setting": SETTING_COLLECTION,
            "acceptance": ACCEPTANCE_COLLECTION,
            "generation": GENERATION_COLLECTION,
            "quote": QUOTE_COLLECTION,
            "quote_line_item": QUOTE_LINE_ITEM_COLLECTION,
        },
        "flows": list(FLOWS),
        "flow_labels": {
            FLOW_CONTRACT_AND_ORDER: "Contract and order",
            FLOW_CONTRACT_ONLY: "Contract only",
            FLOW_NONE: "No contract",
        },
        "effective_start_date_modes": list(EFFECTIVE_START_DATE_MODES),
        "effective_start_date_mode_labels": {
            MODE_ON_AGREEMENT: "On agreement",
            MODE_CUSTOM: "Custom date",
            MODE_DELAYED_BY_DAYS: "Delayed by days",
            MODE_DELAYED_BY_MONTHS: "Delayed by months",
        },
        "mode_required_fields": {k: list(v) for k, v in MODE_REQUIRED_FIELDS.items()},
        "mode_extra_fields": {k: list(v) for k, v in MODE_EXTRA_FIELDS.items()},
        "default_effective_start_date_mode": DEFAULT_EFFECTIVE_START_DATE_MODE,
        "acceptance_methods": list(ACCEPTANCE_METHODS),
        "quote_status_accepted": QUOTE_STATUS_ACCEPTED,
        "quote_status_published": QUOTE_STATUS_PUBLISHED,
        "renewal_alert_modes": list(RENEWAL_ALERT_MODES),
        "default_setting": dict(DEFAULT_SETTING),
        "setting_fields": list(SETTING_FIELDS),
        "billing_flows": [BILLING_SUBSCRIPTION, BILLING_NONE],
        "legacy_rule": LEGACY_RULE,
        "refusal_reasons": refusal_reasons(),
        "evidence": dict(EVIDENCE),
        "not_implemented": not_implemented(),
    }
