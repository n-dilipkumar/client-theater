"""Every judgement call WF-089 made, with the alternative it rejected.

The specification's own instruction to an implementer is that one "must derive it and record
the derivation, not assume it". This module is that record.

Each entry names the open question, the evidence that left it open, the options, the one this
build took, and - the part that matters - what the rejected options would have cost. A
derivation with no rejected alternative recorded is a guess wearing a derivation's clothes,
and a reviewer cannot tell the two apart.

The HTTP layer serves this table at ``GET /api/wf-089/decisions`` so the record is readable by
whoever reviews the workflow, rather than buried in a docstring that nobody opens.
``GET /api/wf-089/decisions/{id}`` returns one.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "PRODUCT_MODEL_DYNAMICS_OVER_HUBSPOT": {
        "question": (
            "The research quotes two products that disagree about multi-currency. HubSpot price "
            "books are single-currency so 'multi-currency is modelled as multiple price books'. "
            "Dynamics uses one transactioncurrency table, a price list per currency, and stores "
            "every money value twice. Which model does this codebase implement?"
        ),
        "left_open_by": (
            "The research records both without resolving them. It lists 'HubSpot - price books "
            "are single-currency' as extensibility and 'Dynamics - custom currency records "
            "(CurrencyType: Custom)' as the other model, and it asks the implementer to 'Pick "
            "one model for this codebase and say which you picked.'"
        ),
        "options": {
            "dynamics_dual_currency_rows": (
                "One transactioncurrency table, a price list per currency, a price row per "
                "product per price list, and every money figure stored twice."
            ),
            "hubspot_multiple_price_books": (
                "A price book per currency and conversion computed at read time, with no stored "
                "base figure and no pricing error codes."
            ),
            "single_multi_currency_price_row": (
                "One price row per product holding a map of currency code to price, so one price "
                "list serves every currency."
            ),
            "rates_only_no_stored_base": (
                "The Dynamics header and rate model, but only the transaction figure stored."
            ),
        },
        "chosen": "dynamics_dual_currency_rows",
        "rejected_because": (
            "The HubSpot model was rejected on the acceptance criterion rather than on taste. "
            "Code 34 is 'Invalid Price Level Currency', which is only expressible when a price "
            "list's currency can differ from the header's, and a single-currency price book makes "
            "that impossible by construction. Choosing it would have deleted the refusal the "
            "specification requires instead of implementing it. The single multi-currency price "
            "row was rejected for the same reason and additionally contradicts the sourced "
            "sentence 'Click the Currencies dropdown menu and select a currency'. Storing only "
            "the transaction figure was rejected because the specification requires the _Base "
            "figure to be stored: the read-only exchangerate and *_Base columns 'hold the "
            "converted base-currency view for reporting'. A base figure computed per read would "
            "move an issued quote's reported total when a currency record was re-stamped, which "
            "is the property the stored rate exists to prevent."
        ),
        "jev_audit_id": "jev-20261004T205203-28472-23034",
        "jev_confidence": 1.0,
    },
    "AUTHORITATIVE_IS_THE_TRANSACTION_FIGURE": {
        "question": (
            "Every money value is stored twice, once in the transaction currency and once in a "
            "_Base column. Which one is authoritative for a given total?"
        ),
        "left_open_by": (
            "The issue states it as an open decision: 'Decide which one is authoritative for a "
            "given total and record it.'"
        ),
        "options": {
            "transaction_currency": (
                "The transaction figure is the truth. Every _Base figure is derived from it by "
                "the rate stamped on the quote."
            ),
            "base_currency": (
                "The _Base figure is the truth, because the base currency is the one the "
                "organisation reports in."
            ),
            "both_authoritative": (
                "Both are truth and a caller may write either, with the other recomputed."
            ),
        },
        "chosen": "transaction_currency",
        "rejected_because": (
            "The base figure as the authority contradicts the research's order of operations: "
            "totals 'are computed in the transaction currency' and only then 'the read-only "
            "exchangerate and *_Base columns ... hold the converted base-currency view for "
            "reporting'. A reporting view that is the source of truth is not a view. Accepting "
            "both as writable was rejected because a caller that could write either figure "
            "could write two totals that disagree, and the disagreement would surface as a "
            "quote whose total does not match the sum of its lines."
        ),
    },
    "RATE_IS_RESOLVED_NOT_SUPPLIED": {
        "question": (
            "'ExchangeRate' is read-only in the entity reference and writable in the "
            "extensibility note. Which behaviour does this build implement, and how is a custom "
            "rate set?"
        ),
        "left_open_by": (
            "The issue states it as an open decision: 'ExchangeRate is read-only in one path and "
            "writable in the other. Record which behaviour you implement and how a custom rate "
            "is set.'"
        ),
        "options": {
            "resolved_from_currency_record": (
                "A pricing run never takes a rate from the caller. The rate comes from the "
                "transaction currency's record, and only a record whose CurrencyType is Custom "
                "may carry a caller-stamped rate."
            ),
            "always_writable": (
                "Any quote may carry any rate the caller supplies, in the spirit of 'exchangerate "
                "is writable'."
            ),
            "read_only_always": (
                "The rate is always the platform's and a Custom currency record changes nothing."
            ),
        },
        "chosen": "resolved_from_currency_record",
        "rejected_because": (
            "Always writable was rejected because it makes the audit log unable to explain a "
            "total: a caller could price a quote at any rate with no record of where the rate "
            "came from, which is precisely the guarantee the two stored figures exist to "
            "provide. Read-only-always was rejected because it contradicts the research's own "
            "extensibility sentence, 'exchangerate is writable so a custom rate can be stamped', "
            "and it would leave a deployment with no way to record a negotiated rate. Resolving "
            "from the record reconciles the two sourced statements: the field is read-only on "
            "the quote and writable on the currency record, and the Custom currency type is "
            "what makes that writable."
        ),
    },
    "REFUSAL_IS_AN_OUTCOME_NOT_AN_ERROR": {
        "question": (
            "On a wrong-currency combination 'the platform refuses to price and sets "
            "pricingerrorcode'. Is that a successful response carrying a refusal, or an HTTP "
            "error?"
        ),
        "left_open_by": (
            "The issue states it as an open decision: 'The refusal path is a first-class "
            "outcome, not an error. On a wrong-currency combination the platform refuses to "
            "price and sets pricingerrorcode. Two codes are named and you should treat them as "
            "separate.'"
        ),
        "options": {
            "outcome_on_200": (
                "The pricing run answers 200 with outcome refused and the named code, and "
                "writes an audit row exactly as a successful run does."
            ),
            "http_422": (
                "The run answers 422 and the caller reads the code out of the error body."
            ),
            "http_409": (
                "The run answers 409 because the quote's configuration conflicts with the price "
                "list."
            ),
        },
        "chosen": "outcome_on_200",
        "rejected_because": (
            "422 was rejected because the pricing run is not a failed request. It ran, it "
            "examined the price list currency and every line item, and it produced a definite "
            "answer. 409 was rejected for the same reason and because a conflict with the state "
            "of the quote is a different failure with a different remedy, one this workflow "
            "returns as CurrencyChangeRefused. Both would also have made a caller unable to "
            "distinguish a misconfigured quote from a broken endpoint, and a retry loop would "
            "hammer a route that is behaving correctly."
        ),
        "jev_audit_id": "jev-20261004T205203-28472-23034",
    },
    "CURRENCY_CHANGE_REFUSED_WHILE_LINES_EXIST": {
        "question": (
            "'You can't change the currency of the base record (in this case, an quote), unless "
            "you remove all the line items associated with the record.' How does the API express "
            "that?"
        ),
        "left_open_by": (
            "The issue states it as an open decision: 'Decide how your API expresses that "
            "constraint.'"
        ),
        "options": {
            "refuse_with_409": (
                "A PATCH naming a different currency on a quote with line items is refused with "
                "409, and the body names the line count and the remedy."
            ),
            "clear_lines_and_reprice": (
                "The change is accepted, every line item is deleted, and the quote is re-priced."
            ),
            "allow_with_repricing": (
                "The change is accepted and every unit price is re-resolved in the new currency."
            ),
        },
        "chosen": "refuse_with_409",
        "rejected_because": (
            "Clearing the lines and re-pricing was rejected because it is destructive and "
            "silent: a caller who meant to change the currency would lose the quote's contents "
            "without being told, and the deletion would be the only record of what happened. "
            "Re-pricing in place was rejected because it is the mismatch the platform exists to "
            "prevent. Every line item's price was resolved from a price row in the old currency, "
            "so re-resolving against a new currency's price list produces a different quote "
            "under the same line items, and if the new price list lacks a product the result is "
            "code 38 with the original lines still attached. Refusing matches the evidence's own "
            "condition, which names removing the line items as the precondition rather than as "
            "something the platform does for you."
        ),
    },
    "PRICE_LIST_CURRENCY_MUST_EQUAL_HEADER_CURRENCY": {
        "question": (
            "The evidence states 'Your base record and all its line items must use the same "
            "currency'. Is the price list's currency checked at stamping time, at pricing time, "
            "or both?"
        ),
        "left_open_by": (
            "The issue states it as an open decision and notes that the user flow says 'the "
            "price list's currency must match' at selection while the refusal path is described "
            "as happening during pricing."
        ),
        "options": {
            "refused_at_pricing": (
                "The mismatch is checked when the quote is priced, and the run answers outcome "
                "refused with code 34. Stamping a quote with a mismatched price list is allowed."
            ),
            "refused_at_stamp": (
                "Stamping a quote with a mismatched price list is refused with a 400."
            ),
            "checked_at_both": (
                "Both: the stamp is refused, and a pricing run on a quote stamped before the rule "
                "existed still refuses with code 34."
            ),
        },
        "chosen": "refused_at_pricing",
        "rejected_because": (
            "Refusing at the stamp was rejected because the code exists to describe a state a "
            "quote can be in, and if the stamp refuses then code 34 can never be observed. The "
            "research describes the platform refusing and setting pricingerrorcode, which is a "
            "statement about pricing. Checking at both was rejected because it makes the "
            "mismatch unreachable as a priced state and duplicates one rule in two places, and "
            "one of the two would then be the one a test exercises. So the mismatch is allowed "
            "at stamping and refused at pricing, which is the only shape in which both "
            "sentences in the research are true at once."
        ),
    },
    "RATE_IS_AN_EVENT_FETCHED_ON_THE_FOUR_TRIGGERS": {
        "question": (
            "The rate source is 'the RetrieveExchangeRate message/event on the Currency table "
            '(RetrieveExchangeRateRequest, "Event: True")\'. When is a rate fetched, and when is '
            "one cached?"
        ),
        "left_open_by": (
            "The issue states it as an open decision: 'Record when you fetch and when you cache a "
            "rate.' The research names the event and separately names 'Currency/exchange-rate-"
            "driven recalculation on record open, create, update, and on product add/update/"
            "delete' without tying the two together."
        ),
        "options": {
            "event_on_each_trigger_then_stamp": (
                "Each of the four triggers raises the rate event and stamps the answer onto the "
                "quote, so a quote is priced against the rate in force when it was priced."
            ),
            "poll_on_read": (
                "The base figure is recomputed from the current rate every time the quote is read."
            ),
            "fetch_once_at_create": (
                "The rate is fetched once when the quote is created and never again."
            ),
        },
        "chosen": "event_on_each_trigger_then_stamp",
        "rejected_because": (
            "Polling on read was rejected because the stored base figure would then not be the "
            "stored figure, and the whole reason for storing it is that a signed quote's reported "
            "total does not move. Fetching once at create was rejected because the research names "
            "four triggers, not one, and a quote whose line items change after creation would "
            "carry a base figure computed at a rate that no longer has anything to do with the "
            "lines being totalled. Raising the event on each trigger and stamping it reconciles "
            "the two sourced sentences: the rate is always current at the moment the totals are "
            "computed, and never moves afterwards."
        ),
    },
    "UNIT_PRICE_COMES_ONLY_FROM_THE_PRICE_ROW": {
        "question": (
            "The research says unit prices 'resolve from the pricelevelproduct row in that "
            "currency'. May a line item also carry its own unit price?"
        ),
        "left_open_by": (
            "Not stated in the research. The question arises because a line item is arbitrary "
            "JSON in this product, so a caller can put a price on it without anything refusing."
        ),
        "options": {
            "price_row_only": (
                "The unit price is read from the price row and a payload unit_price is refused "
                "with a message naming the field."
            ),
            "payload_wins": (
                "A payload unit_price overrides the price row, which makes a negotiated price "
                "possible."
            ),
            "payload_only_when_no_row": (
                "A payload unit_price is used only when the price list has no row for the product."
            ),
        },
        "chosen": "price_row_only",
        "rejected_because": (
            "Payload-wins was rejected because it creates a second price source the audit log "
            "cannot explain: the quote's total would not match the price list a reader can see, "
            "and nothing in the record would say why. Payload-as-fallback was rejected for the "
            "same reason and additionally collapses code 38 into code 34, because a product with "
            "no price row and a product priced in another currency would become the same silent "
            "case. Refusing the field keeps one price source, which is the sourced rule, and "
            "returns the name of the field so a caller learns it rather than having its own "
            "value echoed back."
        ),
    },
    "THIS_WORKFLOW_READS_QUOTE_AND_PRICE_ROWS_AS_DATA": {
        "question": (
            "WF-086 provisions the quote and its line items and WF-087 provisions the price "
            "lists and price rows. Neither is implemented. Does this workflow wait for them, or "
            "read them as data?"
        ),
        "left_open_by": (
            "The issue names both as dependencies and adds that the issue numbers for WF-086 and "
            "WF-087 are not yet known. Nothing resolves what this workflow does in the meantime."
        ),
        "options": {
            "read_as_data": (
                "Read the quote, line items, price lists and price rows from collections, expose "
                "routes that create them, and never import another feature's module."
            ),
            "wait_for_dependencies": ("Ship nothing until WF-086 and WF-087 land."),
            "duplicate_the_upstream_records": (
                "Define its own quote and price list shapes and treat the upstream records as a "
                "separate thing."
            ),
        },
        "chosen": "read_as_data",
        "rejected_because": (
            "Waiting was rejected because it leaves the workflow unimplemented and unverifiable "
            "for as long as the other shard takes, and the issue is explicit that it is "
            "self-sufficient. Duplicating the upstream records was rejected because it produces "
            "two quote shapes, so a quote written by WF-086 and a quote written by this workflow "
            "would be different objects with the same name, and a caller would not know which "
            "one a pricing run had read. Reading them as data and creating them through this "
            "workflow's own routes means one shape, one collection pair, and no import edge: a "
            "feature must not import another feature."
        ),
    },
    "MONEY_IS_DECIMAL_QUANTISED_TO_THE_CURRENCY": {
        "question": ("How is a money figure computed and rounded?"),
        "left_open_by": (
            "Not stated in the research. It names CurrencyPrecision on the currency record and "
            "names the money columns, and says nothing about the arithmetic between them."
        ),
        "options": {
            "decimal_half_up": (
                "Decimal arithmetic, rounded half-up to the currency's own CurrencyPrecision."
            ),
            "float_round_half_even": (
                "Binary floating point with the language's default rounding."
            ),
            "integer_minor_units": ("Store each amount as an integer number of minor units."),
        },
        "chosen": "decimal_half_up",
        "rejected_because": (
            "Float was rejected because 0.1 plus 0.2 is 0.30000000000000004, so a three-line "
            "quote would carry an amount no currency could be paid in, and the base figure "
            "derived from it would carry the same error one step removed. Integer minor units "
            "were rejected because the precision belongs to the currency and a currency can be "
            "re-stamped with a different CurrencyPrecision, so an integer figure would have to "
            "be rescaled on every such change and the stored value would stop being the value "
            "that was priced. Decimal with half-up rounding is named in the code so a change to "
            "the convention is visible rather than inherited."
        ),
    },
}


def describe() -> list[dict[str, Any]]:
    """Every decision, in insertion order."""

    return [{"id": key, **dict(value)} for key, value in DECISIONS.items()]


def count() -> int:
    return len(DECISIONS)


def describe_one(decision_id: str) -> dict[str, Any] | None:
    """One decision by id, or None."""

    entry = DECISIONS.get(decision_id)
    if entry is None:
        return None
    return {"id": decision_id, **dict(entry)}


def jev_audit_ids() -> list[str]:
    """Every Jev audit id this workflow's decisions rest on."""

    found: list[str] = []
    for entry in DECISIONS.values():
        audit_id = entry.get("jev_audit_id")
        if audit_id and audit_id not in found:
            found.append(str(audit_id))
    return found
