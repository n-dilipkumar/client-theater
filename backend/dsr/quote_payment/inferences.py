"""Every judgement call WF-096 made, with the alternative it rejected.

The specification leaves several joints open, and the ticket's own "to build" note says so:
the Connected CPQ single-contract path is out of scope, and the research enumerates neither the
billing frequencies, the effective-date semantics, the net payment terms, nor the invoice
horizon. A derivation with no rejected alternative is a guess wearing a derivation's clothes,
so every entry below names at least two options and says which one was taken and what the
rejection would have cost.

Each entry carries the Jev audit id that chose it. The audit trail is
``orchestration/decisions/jev-audit.jsonl``; this table is the readable form, served at
``GET /api/wf-096/decisions`` so whoever reviews the feature can see the record without
opening the source.

Where a value is *stated* rather than decided — ``hs_payment_status = PENDING`` on publish, the
five payment methods, the three acceptance methods, the three-tax-ID cap, the strict minimum
charge, the ten-day invoice lead — this table is deliberately silent, because those are the
research's words and not this build's judgement.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_NEW_PACKAGE_QUOTE_PAYMENT": {
        "question": "Which domain package should hold WF-096's rules?",
        "left_open_by": (
            "The brief names no package, and no package on main held clickwrap-acceptance or "
            "in-quote-payment vocabulary."
        ),
        "options": {
            "new_package_quote_payment": (
                "Create backend/dsr/quote_payment/ holding vocabulary, rules, inferences and "
                "engine, with its own wf096_ collection names."
            ),
            "extend_quoting_proposals": (
                "Append WF-096 modules to backend/dsr/quoting_proposals, which already reads "
                "wf086_quote and owns proposals."
            ),
            "extend_quote_acceptance": (
                "Append WF-096 modules to backend/dsr/quote_acceptance, which already owns the "
                "acceptance methods and the quote's acceptance status."
            ),
            "extend_quote_authoring": (
                "Append WF-096 modules to backend/dsr/quote_authoring, which provisions "
                "wf086_quote and computes the totals."
            ),
        },
        "chosen": "new_package_quote_payment",
        "rejected_because": (
            "quote_acceptance is an in-flight sibling branch (WF-095), so extending it would "
            "collide with an unmerged branch on its own package initializer — the exact "
            "collision this repository already suffered. quoting_proposals owns rendering a "
            "proposal from a template and quote_authoring owns authoring the quote's amounts; "
            "neither owns taking money. A new package gives the publish configuration, the "
            "acceptance, the charge, the invoice and the subscription exactly one owner."
        ),
        "audit_id": "jev-20261005T121005-24396-05029",
        "confidence": 0.89,
    },
    "DERIVED_PAYMENT_IS_A_SEPARATE_BUYER_STEP": {
        "question": "Should the click-to-accept and the in-quote payment be one step or two?",
        "left_open_by": (
            "The research's buyer step reads 'clicks Accept (click-to-accept) -> optionally Set "
            "up payment at the top of the quote; they can close and revisit the quote later to "
            "set up payment.' It describes two acts and does not say whether one request "
            "performs both."
        ),
        "options": {
            "two_separate_steps": (
                "Accept records the clickwrap acceptance and creates the first invoice; a "
                "separate payment step records the charge, and the buyer may set up payment at "
                "the top of the quote or revisit later."
            ),
            "one_combined_step": (
                "Accept and charge in one request; a declined charge fails the whole acceptance."
            ),
            "accept_then_optional_charge_flag": (
                "One accept endpoint with an optional pay_now flag defaulting to true."
            ),
        },
        "chosen": "two_separate_steps",
        "rejected_because": (
            "The minimum charge is a processor outcome that can decline, and acceptance is "
            "irreversible. Combining the two would let a declined payment undo an acceptance the "
            "buyer already gave, and would make the 'close and revisit to set up payment' the "
            "research describes impossible to express. A pay_now flag hides the same coupling "
            "behind a default, so the two acts are two routes."
        ),
        "audit_id": "jev-20261005T121005-24396-05341",
        "confidence": 0.92,
    },
    "DERIVED_DECLINED_CHARGE_IS_AN_OUTCOME": {
        "question": "How should a charge below the minimum be answered?",
        "left_open_by": (
            "The research states the constraint — 'the total amount due must be more than $0.50' "
            "— and not whether a processor decline is an HTTP error or a recorded outcome."
        ),
        "options": {
            "recorded_declined_outcome": (
                "Write a wf096_charge row with outcome=declined and the named reason, answer 200, "
                "and leave the quote accepted."
            ),
            "hard_400_refusal": ("Answer 400, write no charge row, and leave the quote accepted."),
            "both_400_and_row": ("Answer 400 and also write a declined charge row."),
        },
        "chosen": "recorded_declined_outcome",
        "rejected_because": (
            "A processor decline is a definite result, not a malformed request, which is exactly "
            "the reasoning WF-095 used to make a refused signature a 200 outcome. A 400 would "
            "make the buyer page treat a legitimate decline as a client bug and would leave the "
            "board unable to show that payment was attempted. Writing a row *and* answering 400 "
            "is the worst of both: a caller would retry a request the store already recorded."
        ),
        "audit_id": "jev-20261005T121102-5456-62628",
        "confidence": 1.0,
    },
    "DERIVED_PAYMENT_TYPE_IS_DERIVED_NOT_CHOSEN": {
        "question": "How is hs_payment_type (HUBSPOT vs BYO_STRIPE) decided?",
        "left_open_by": (
            "The research says the two processors 'are swappable per account, and "
            "hs_payment_type is set automatically', and never says what the seller's payload "
            "may carry."
        ),
        "options": {
            "derive_from_connected_account": (
                "Derive it: a payload naming a connected Stripe account gets BYO_STRIPE, "
                "otherwise HUBSPOT. A seller-supplied value is ignored."
            ),
            "seller_chooses": ("Accept hs_payment_type from the seller's publish payload."),
            "always_hubspot": ("Always write HUBSPOT in this build."),
        },
        "chosen": "derive_from_connected_account",
        "rejected_because": (
            "Honouring a seller-supplied value would make the property seller-chosen and "
            "contradict 'set automatically'. Always writing HUBSPOT would make the account's own "
            "Stripe connection invisible. Deriving from the account's connected-Stripe marker "
            "keeps the property automatic while still representing both processors."
        ),
        "audit_id": "jev-20261005T121005-24396-05959",
        "confidence": 0.88,
    },
    "DERIVED_SHIP_SUBSCRIPTIONS_AND_INVOICES": {
        "question": "Which post-acceptance creations does this build ship?",
        "left_open_by": (
            "The data flow names a contract and an order in Connected CPQ and subscriptions and "
            "invoices otherwise, and the issue marks the Connected CPQ path out of scope without "
            "saying what the shipped path creates per line."
        ),
        "options": {
            "subscriptions_and_invoices": (
                "Create a subscription per recurring line plus the immediate first invoice and "
                "the scheduled later invoices."
            ),
            "connected_cpq_contract_and_order": (
                "Create a single contract plus order for the Connected CPQ path."
            ),
            "both_paths": ("Ship both, selected by a flag."),
        },
        "chosen": "subscriptions_and_invoices",
        "rejected_because": (
            "The issue takes the Connected CPQ contract-and-order path out of scope, and the "
            "research makes billing frequency per line item, so one contract for a quote that "
            "mixes a one-time line with a monthly line would lose the distinction the scheduling "
            "rule exists for. Shipping both would add a second code path to prove nothing the "
            "subscription path does not already prove."
        ),
        "audit_id": "jev-20261005T121006-24396-06270",
        "confidence": 1.0,
    },
    "DERIVED_TAX_IS_PER_LINE_RATE_PLUS_TOGGLE": {
        "question": "How is tax modelled when this workflow does not own quote creation?",
        "left_open_by": (
            "The research says automated sales tax 'will be calculated when the quote is "
            "created', and that data sources include 'automated sales tax or per-line-item tax "
            "rates'. Quote creation is WF-086's, so this workflow only carries the result."
        ),
        "options": {
            "per_line_tax_rate_plus_toggle": (
                "Carry an automated-sales-tax switch on the setup and compute tax from each "
                "line's own tax_rate, which WF-086 already stores."
            ),
            "flat_quote_tax_rate": ("A single quote-level tax rate applied to every line."),
            "omit_tax": ("Store no tax and invoice the net amount only."),
        },
        "chosen": "per_line_tax_rate_plus_toggle",
        "rejected_because": (
            "A flat rate would overwrite the per-line rates the research names and the quote "
            "already carries, so a mixed-rate quote would be taxed at one rate and the invoice "
            "would disagree with the quote. Omitting tax would invoice less than the buyer owes. "
            "Reading each line's stored rate keeps the invoice equal to the quote's own total."
        ),
        "audit_id": "jev-20261005T121006-24396-06597",
        "confidence": 0.76,
    },
    "DERIVED_INVOICE_HORIZON_IS_A_DECLARED_CONSTANT": {
        "question": "How is the horizon for scheduled future invoices decided?",
        "left_open_by": (
            "The research names no horizon; it says only that subsequent invoices follow the "
            "billing schedule, ten days before their invoice dates."
        ),
        "options": {
            "declared_bounded_constant": (
                "Declare SUBSEQUENT_INVOICE_PERIODS in the vocabulary and schedule that many "
                "periods per recurring line."
            ),
            "derive_from_term_end": ("Derive the horizon from a subscription term end date."),
            "schedule_all_periods": ("Schedule every period until a notional far horizon."),
        },
        "chosen": "declared_bounded_constant",
        "rejected_because": (
            "Deriving from a term end needs a field the research does not name and this build "
            "does not store, so the derivation would be imaginary. Scheduling every period "
            "floods the board and the store with rows that answer no question a seller asks. A "
            "declared constant is reviewable and bounded, and the vocabulary endpoint reports "
            "it so a reader sees the number rather than inferring it."
        ),
        "audit_id": "jev-20261005T121102-5456-62932",
        "confidence": 1.0,
    },
    "DERIVED_ACCEPT_PATCHES_TWO_QUOTE_PROPERTIES": {
        "question": "Does the accept action patch wf086_quote, or keep acceptance in this "
        "workflow's own tables?",
        "left_open_by": (
            "The data flow says the accept action 'writes hs_clickwrap_accepted_by and flips "
            "hs_status to ACCEPTED', which names a write to the quote's own record; the "
            "plugin contract prefers feature-owned collections."
        ),
        "options": {
            "patch_two_named_properties": (
                "Patch only hs_status=ACCEPTED and hs_clickwrap_accepted_by, the two properties "
                "the data flow names, and never touch an amount or a line item."
            ),
            "own_tables_only": ("Write only to wf096_acceptance and leave wf086_quote untouched."),
            "patch_all_payment_properties": (
                "Patch the acceptance fields and every payment property on the quote."
            ),
        },
        "chosen": "patch_two_named_properties",
        "rejected_because": (
            "The researched data flow names those two properties on the quote, and a downstream "
            "reader (WF-099's contract creation, WF-094's hosted link) looks for them there, so "
            "keeping them only in wf096_acceptance would leave that reader blind. Patching every "
            "payment property widens the write surface onto a record this workflow does not own "
            "and risks a line item or an amount changing as a side effect. Two named properties, "
            "pinned by a test against the exact key set, is the narrow reading."
        ),
        "audit_id": "jev-20261005T121007-24396-07246",
        "confidence": 0.99,
    },
    "DERIVED_BILLING_FREQUENCIES": {
        "question": "Which billing frequencies can a line item carry?",
        "left_open_by": (
            "The research makes billing frequency per line item and enumerates no values."
        ),
        "options": {
            "five_common_frequencies": ("one_time, monthly, quarterly, semi_annual, annual."),
            "monthly_and_annual_only": "monthly and annual.",
            "free_text_frequency": "Accept any string the seller types.",
        },
        "chosen": "five_common_frequencies",
        "rejected_because": (
            "A free string cannot drive an invoice schedule, because the scheduler needs a "
            "bounded set to compute the next date from. Monthly and annual alone would not "
            "represent the quarterly and six-monthly billing the research's ramped-pricing note "
            "implies. Five frequencies cover the shapes a quote can take and each one has a "
            "known month count."
        ),
        "audit_id": "jev-20261005T121121-23432-81082",
        "confidence": 1.0,
    },
    "DERIVED_EFFECTIVE_DATE_RESOLVES_TO_CONCRETE_DATES": {
        "question": "What do the four effective-date modes resolve to?",
        "left_open_by": (
            "The research names the four modes and does not say how each becomes a date or when "
            "it resolves."
        ),
        "options": {
            "resolved_to_concrete_dates": (
                "on_agreement resolves to the acceptance day, custom_date to the given ISO date, "
                "and the two delays to the agreement day plus a whole number of days or months."
            ),
            "store_mode_only": "Store the chosen mode and never resolve it to a date.",
            "resolve_only_on_billing": "Resolve only when the first invoice is generated.",
        },
        "chosen": "resolved_to_concrete_dates",
        "rejected_because": (
            "The billing start and the invoice schedule both need a concrete date, so storing "
            "only the mode pushes the resolution to every reader and lets two readers disagree. "
            "Resolving only at billing time leaves the quote's own effective date unreadable "
            "until money is due. Resolving at publish once gives every reader the same date."
        ),
        "audit_id": "jev-20261005T121121-23432-81382",
        "confidence": 1.0,
    },
    "DERIVED_NET_PAYMENT_TERMS_VOCABULARY": {
        "question": "What vocabulary does hs_net_payment_terms use?",
        "left_open_by": ("The data flow names hs_net_payment_terms and gives no vocabulary."),
        "options": {
            "declared_calendar": (
                "Declare NET_0, NET_15, NET_30, NET_45, NET_60 with NET_30 default."
            ),
            "integer_days": "Accept any integer number of days.",
            "omit_the_property": "Do not write hs_net_payment_terms at all.",
        },
        "chosen": "declared_calendar",
        "rejected_because": (
            "An integer number of days is a value no test can pin and no page can label, which "
            "is the defect the vocabulary endpoint exists to prevent. Omitting the property "
            "would drop a field the data flow names on the publish step. A small declared "
            "calendar keeps the property present and every value reviewable."
        ),
        "audit_id": "jev-20261005T121121-23432-81692",
        "confidence": 1.0,
    },
    "DERIVED_ANONYMOUS_BUYER_IS_NAMED": {
        "question": "What does hs_clickwrap_accepted_by carry when the seller chose Do not "
        "specify?",
        "left_open_by": (
            "The research lets the seller choose 'Do not specify' and says 'a contact doesn't "
            "need to be added to the quote', while the data flow requires the property to be "
            "written."
        ),
        "options": {
            "name_the_anonymous_buyer": (
                "Write a named placeholder such as 'Buyer (no contact specified)'."
            ),
            "require_a_contact": "Refuse the accept until a contact is supplied.",
            "leave_it_empty": "Write an empty value.",
        },
        "chosen": "name_the_anonymous_buyer",
        "rejected_because": (
            "Requiring a contact contradicts the research, which exists precisely so a purchase "
            "order can be accepted without one. An empty value leaves the acceptance row unable "
            "to name its party, which is the property's whole purpose. A named placeholder keeps "
            "the write honest and keeps the accept available exactly when the research says it is."
        ),
        "audit_id": "jev-20261005T121129-7600-89781",
        "confidence": 0.99,
    },
    "DERIVED_PAYMENT_STATUS_STAYS_PENDING": {
        "question": "Does hs_payment_status move after the charge is recorded?",
        "left_open_by": (
            "The data flow writes hs_payment_status = PENDING on publish and no later step in "
            "the research moves it. This build moves no money, so 'paid' would be a claim it "
            "cannot support."
        ),
        "options": {
            "stays_pending": (
                "Leave hs_payment_status at PENDING; the charge row carries the outcome."
            ),
            "flip_to_paid": "Set hs_payment_status to PAID when a charge is recorded.",
            "invent_a_status": "Introduce a CHARGE_RECORDED status.",
        },
        "chosen": "stays_pending",
        "rejected_because": (
            "This build records a charge intent and takes no money, so writing PAID would be a "
            "false statement about a payment. Inventing a status puts a value on a vendor "
            "property that no HubSpot reader understands. Keeping PENDING and recording the "
            "outcome on this workflow's own charge row states exactly what happened."
        ),
        "audit_id": None,
        "confidence": None,
    },
    "DERIVED_CONNECTED_CPQ_IS_OUT_OF_SCOPE": {
        "question": "Is the Connected CPQ single-contract-and-order path built here?",
        "left_open_by": (
            "The data flow names both the Connected CPQ path and the subscription/invoice path."
        ),
        "options": {
            "out_of_scope": (
                "Leave the Connected CPQ contract-and-order path to the issue's out-of-scope "
                "clause and ship the subscription and invoice path."
            ),
            "build_it": "Build the Connected CPQ path too.",
        },
        "chosen": "out_of_scope",
        "rejected_because": (
            "The issue states the Connected CPQ path is out of scope for this ticket. Building "
            "it would duplicate the contract workflow WF-099 owns and add a second creation path "
            "with no ticket behind it."
        ),
        "audit_id": None,
        "confidence": None,
    },
}


def decision_list() -> list[dict[str, Any]]:
    """Every decision, in the order they were made, for the /decisions route."""

    return [dict(value) for value in DECISIONS.values()]
