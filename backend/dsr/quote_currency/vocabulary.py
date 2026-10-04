"""Every researched term WF-089 enforces against, with the evidence it came from.

This is the researched specification for WF-089 made executable, and the only place a
constant named after the specification lives. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-089.md``, quoted in full in issue 146.
Every value below is either quoted from that document or derived from a quote by a
derivation recorded in :mod:`dsr.quote_currency.inferences`. Nothing here is a house
opinion.

The two vendor models, and which one this build implements
----------------------------------------------------------

The research quotes two products and they disagree about multi-currency.

**HubSpot.** "Click the **Currencies** dropdown menu and select a currency", so a price
book is single-currency and "multi-currency is modelled as multiple price books".

**Dynamics 365 Sales.** One ``transactioncurrency`` table holds every currency the
organisation transacts in, each row carrying ``ISOCurrencyCode``, ``CurrencySymbol``,
``CurrencyPrecision`` and ``ExchangeRate``. A ``pricelevel`` is a price list, a
``pricelevelproduct`` is one price row on that list, and every money field on a quote is
stored twice: once in the transaction currency and once in a ``_Base`` column that the
record's ``exchangerate`` drives.

:data:`PRODUCT_MODEL` records the choice: this build implements the **Dynamics** model,
because it is the one that names a refusal. The specification's acceptance behaviour is
two ``PricingErrorCode`` values, and ``34 Invalid Price Level Currency`` is only
expressible when a price list's currency can differ from the header's. A HubSpot-shaped
model makes that impossible by construction, so it could not carry the refusal the
specification requires. The HubSpot reading survives in one place that matters: a price
list is single-currency, so multi-currency is modelled as several price lists.

Which figure is authoritative
-----------------------------

The issue asks this build to decide it and to record the decision. :data:`AUTHORITATIVE`
says the **transaction-currency figure is authoritative** and every ``_Base`` figure is
derived from it by the stamped rate. The reason is the order the evidence gives: totals
"are computed in the transaction currency", and only then "the read-only ``exchangerate``
and ``*_Base`` columns ... hold the converted base-currency view **for reporting**". A
derived column that is written first and read back as truth inverts that, and a re-stamped
rate would then silently rewrite what a signed quote says it cost.

Which direction the rate runs
-----------------------------

``ExchangeRate`` "is used to convert all money fields in the record from the local currency
to the system's default currency", so one rate is *base per one unit of transaction
currency*. A EUR quote under USD 1.08 has a rate of 1.08: multiplying a EUR total by 1.08
gives the USD figure. :func:`dsr.quote_currency.rules.to_base` is the only place that
multiplication happens.

Read-only, with one documented exception
----------------------------------------

``ExchangeRate`` is read-only in the entity reference and writable in the extensibility
note: "``exchangerate`` is writable so a custom rate can be stamped", alongside "custom
currency records (``CurrencyType: Custom``)". Those two are reconciled rather than
averaged. :data:`RATE_POLICY` says a rate is resolved from the currency record and is
never supplied by the caller on a pricing run; the one exception is a currency record whose
``currency_type`` is :data:`CURRENCY_TYPE_CUSTOM`, which exists precisely so a custom rate
can be stamped. So "writable" means "a deployment may maintain its own rate on its own
currency record", and a rate on a Standard record is refused rather than silently accepted.

When a rate is fetched and when it is cached
--------------------------------------------

The specification names the mechanism: "the ``RetrieveExchangeRate`` message/event on the
Currency table (``RetrieveExchangeRateRequest``, "Event: True")". An event is raised; it is
not polled. :data:`RECALCULATION_TRIGGERS` lists the four triggers the specification names
("record open, create, update, and on product add/update/delete"), and a rate is read at
each of them and **stamped onto the quote**, so the quote is priced against the rate that
was in force when it was priced. The base view of a signed quote therefore does not move
under the reader's feet when a currency record is re-stamped, which is the same property
that makes an issued document a record rather than a view.

The refusal codes are outcomes, not exceptions
----------------------------------------------

:data:`PRICING_ERROR_CODES` names both codes the research names, with the specification's
own wording beside each. They are returned as ``outcome: refused`` on a normal 200, and the
refusal writes an audit row exactly as a success does. The reasoning is in
:mod:`dsr.quote_currency.errors`.

Sources
-------

* https://learn.microsoft.com/en-us/dynamics365/developer/reference/entities/quote
* https://learn.microsoft.com/en-us/power-apps/developer/data-platform/reference/entities/transactioncurrency
* https://learn.microsoft.com/en-us/dynamics365/sales/create-edit-quote-sales
* https://learn.microsoft.com/en-us/dynamics365/sales/price-calculation-opportunity-quote-order-invoice-records
* https://knowledge.hubspot.com/products/use-price-books
* https://knowledge.hubspot.com/quotes/create-and-send-quote
* https://developers.hubspot.com/docs/api-reference/latest/crm/objects/quotes/guide
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced with the ticket, because every feature shares one ``records`` table and
# ``find()`` matches on collection before it matches on anything else.

CURRENCY_COLLECTION = "wf089_transaction_currency"
PRICE_LIST_COLLECTION = "wf089_price_list"
PRICE_ITEM_COLLECTION = "wf089_price_item"
QUOTE_COLLECTION = "wf089_quote"
QUOTE_LINE_COLLECTION = "wf089_quote_line"
PRICING_COLLECTION = "wf089_quote_pricing"
RATE_READ_COLLECTION = "wf089_exchange_rate_read"

ALL_COLLECTIONS: tuple[str, ...] = (
    CURRENCY_COLLECTION,
    PRICE_LIST_COLLECTION,
    PRICE_ITEM_COLLECTION,
    QUOTE_COLLECTION,
    QUOTE_LINE_COLLECTION,
    PRICING_COLLECTION,
    RATE_READ_COLLECTION,
)

#: The payload-side twin of the envelope's ``room_id``.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the record
#: *envelope*, so the store strips it out of ``data`` before the dynamic index is built. A
#: record that stored its room there would be unfilterable by ``find()``, and a "filter by
#: room" that silently returns nothing is the kind of defect that ships. The envelope still
#: carries ``room_id``; every response projects this key back to it.
ROOM_REF = "room_ref"


# --------------------------------------------------------------------------- #
# The product model this build implements
# --------------------------------------------------------------------------- #

#: Which of the two researched product models this codebase implements, and why.
#:
#: Read the module docstring for the argument. The short form: the Dynamics model is the
#: one that can express a refusal, because a price list's currency can differ from the
#: header's currency in it, and ``PricingErrorCode 34`` is exactly that case. The HubSpot
#: single-currency price book is honoured as the rule that a price list carries one
#: currency.
PRODUCT_MODEL = "dynamics_dual_currency_rows"

PRODUCT_MODEL_REJECTED = {
    "hubspot_multiple_price_books": (
        "Rejected. A single-currency price book per currency cannot raise '34 Invalid Price "
        "Level Currency' because no price list can hold the wrong currency. Choosing it would "
        "have removed the refusal the specification requires rather than implemented it."
    ),
    "single_multi_currency_price_row": (
        "Rejected. One product row holding a map of currency to price would let a price list "
        "serve every currency, so the price list currency always matched and code 34 became "
        "unreachable. It also contradicts the sourced HubSpot sentence that a price book is "
        "single-currency."
    ),
    "rates_only_no_stored_base": (
        "Rejected. The specification requires the _Base figure to be stored: the read-only "
        "exchangerate and *_Base columns 'hold the converted base-currency view for reporting'. "
        "Computing it per read would mean an issued quote's reported total changes when a "
        "currency record is re-stamped, which is the property the stored rate exists to stop."
    ),
}

#: Which stored figure a caller may treat as the truth for a total.
AUTHORITATIVE = "transaction_currency"

AUTHORITATIVE_REASON = (
    "Totals are computed in the transaction currency and the *_Base columns hold the converted "
    "view for reporting, so the transaction figure is the authoritative one and every base "
    "figure is derived from it by the rate stamped on the quote."
)


# --------------------------------------------------------------------------- #
# Currency records
# --------------------------------------------------------------------------- #

#: The organisation's own currency. One per deployment, and its ISO code is what every
#: ``_Base`` figure is expressed in. Derived from the research's "organization.basecurrencyid"
#: and from "the system's default currency" in the ExchangeRate definition.
BASE_CURRENCY_FIELD = "is_base_currency"

#: A currency record created by the platform's own rate feed.
CURRENCY_TYPE_STANDARD = "Standard"

#: A currency record a deployment maintains itself. This is the record the extensibility note
#: points at: "custom currency records (CurrencyType: Custom)" and "exchangerate is writable so
#: a custom rate can be stamped". Only this type accepts a caller-supplied rate.
CURRENCY_TYPE_CUSTOM = "Custom"

CURRENCY_TYPES: tuple[str, ...] = (CURRENCY_TYPE_STANDARD, CURRENCY_TYPE_CUSTOM)

CURRENCY_TYPE_LABELS: dict[str, str] = {
    CURRENCY_TYPE_STANDARD: "Standard: the platform maintains the rate.",
    CURRENCY_TYPE_CUSTOM: "Custom: this deployment stamps its own rate on the record.",
}

#: The fields the research names on a ``transactioncurrency`` record, spelled as the research
#: spells them so a caller reading the API can match it against the vendor documentation.
CURRENCY_FIELDS: dict[str, str] = {
    "iso_code": "ISOCurrencyCode",
    "currency_symbol": "CurrencySymbol",
    "currency_precision": "CurrencyPrecision",
    "exchange_rate": "ExchangeRate",
    "currency_type": "CurrencyType",
    "name": "Name",
}

#: The precision bound. ``CurrencyPrecision`` is a Dataverse whole number of decimal places,
#: and a currency with more than six is not a currency any of the sources describe. Derived,
#: and the derivation is recorded in :mod:`dsr.quote_currency.inferences`.
CURRENCY_PRECISION_MIN = 0
CURRENCY_PRECISION_MAX = 6

DEFAULT_CURRENCY_PRECISION = 2

#: The base currency's rate against itself. A quote in the organisation's own currency has
#: nothing to convert, and it still gets a stamped rate so every quote carries one.
IDENTITY_RATE = 1.0


# --------------------------------------------------------------------------- #
# The rate policy
# --------------------------------------------------------------------------- #

RATE_POLICY = "resolved_from_currency_record"

#: The three answers to "where did this rate come from", in the order this build prefers them.
RATE_SOURCE_IDENTITY = "identity"
RATE_SOURCE_CUSTOM = "custom_stamped"
RATE_SOURCE_STANDARD = "standard_record"
RATE_SOURCES: tuple[str, ...] = (
    RATE_SOURCE_IDENTITY,
    RATE_SOURCE_CUSTOM,
    RATE_SOURCE_STANDARD,
)

#: The event the research names for fetching a rate, verbatim, so the mechanism is on the
#: record rather than implied by a comment.
RATE_EVENT = "RetrieveExchangeRate"

RATE_EVENT_NOTE = (
    "The rate source is an event on the Currency table (RetrieveExchangeRateRequest, "
    '"Event: True"), not a poll. This workflow raises that event on each recalculation trigger '
    "and stamps the answer onto the quote."
)


# --------------------------------------------------------------------------- #
# Price lists
# --------------------------------------------------------------------------- #

#: A price list carries exactly one currency. This is the HubSpot sentence that survives the
#: choice of the Dynamics model: "Click the Currencies dropdown menu and select a currency".
PRICE_LIST_SINGLE_CURRENCY = True

PRICE_LIST_SINGLE_CURRENCY_REASON = (
    "A price list carries one currency, so a price book per currency is how a deployment prices "
    "the same product in two currencies. That is the HubSpot sentence 'Click the Currencies "
    "dropdown menu and select a currency' and it is kept because it is the rule that makes code "
    "34 reachable: two currencies means two price lists, and a caller can pick the wrong one."
)


# --------------------------------------------------------------------------- #
# Money fields
# --------------------------------------------------------------------------- #

#: The totals the research names, in its own spelling. Each is stored twice: once in the
#: transaction currency, once in the base currency under the ``_base`` suffix.
TRANSACTION_TOTALS: tuple[str, ...] = (
    "totallineitemamount",
    "totaldiscountamount",
    "totaltax",
    "freightamount",
    "totalamount",
)

BASE_SUFFIX = "_base"

#: The ``_Base`` twin of each transaction total, derived rather than named by hand so the two
#: lists cannot drift.
BASE_TOTALS: tuple[str, ...] = tuple(f"{name}{BASE_SUFFIX}" for name in TRANSACTION_TOTALS)

#: The rate field on the quote header, read-only, spelling the research's spelling.
RATE_FIELD = "exchangerate"

#: The rate the last pricing run stamped, kept beside the read-only field so a reader can see
#: what the figures were computed from rather than trusting that they agree.
STAMPED_RATE_FIELD = "stamped_exchangerate"

#: The transaction currency on the quote header, spelling the research's field name.
TRANSACTION_CURRENCY_FIELD = "transactioncurrencyid"

PRICE_LIST_FIELD = "pricelevelid"


# --------------------------------------------------------------------------- #
# Pricing outcomes and the two refusal codes
# --------------------------------------------------------------------------- #

OUTCOME_PRICED = "priced"
OUTCOME_REFUSED = "refused"
PRICING_OUTCOMES: tuple[str, ...] = (OUTCOME_PRICED, OUTCOME_REFUSED)

#: ``PricingErrorCode`` option 34, with the specification's own wording.
PRICING_ERROR_INVALID_PRICE_LEVEL_CURRENCY = "34"

#: ``PricingErrorCode`` option 38, with the specification's own wording.
PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET = "38"

PRICING_ERROR_CODES: dict[str, str] = {
    PRICING_ERROR_INVALID_PRICE_LEVEL_CURRENCY: "Invalid Price Level Currency",
    PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET: (
        "Transaction currency is not set for the product price list item"
    ),
}

#: Which code belongs to which failure, so a caller can branch on one string and the mapping
#: cannot be written twice in two places.
CODE_PRICE_LIST_CURRENCY_MISMATCH = PRICING_ERROR_INVALID_PRICE_LEVEL_CURRENCY
CODE_LINE_ITEM_HAS_NO_PRICE = PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET

REFUSAL_IS_OUTCOME_NOT_ERROR = (
    "A wrong-currency combination is a recorded outcome, not a request error. The pricing run "
    "happened, produced no totals, and set pricingerrorcode. It answers 200 and writes an audit "
    "row exactly as a successful run does, so 'the platform refuses to price' is visible "
    "without turning a correct refusal into a server fault."
)


# --------------------------------------------------------------------------- #
# Recalculation triggers
# --------------------------------------------------------------------------- #

#: The four triggers the research names: "Currency/exchange-rate-driven recalculation on record
#: open, create, update, and on product add/update/delete".
TRIGGER_OPEN = "record_open"
TRIGGER_CREATE = "record_create"
TRIGGER_UPDATE = "record_update"
TRIGGER_PRODUCT_ADD = "product_add"
TRIGGER_PRODUCT_UPDATE = "product_update"
TRIGGER_PRODUCT_DELETE = "product_delete"

RECALCULATION_TRIGGERS: tuple[str, ...] = (
    TRIGGER_OPEN,
    TRIGGER_CREATE,
    TRIGGER_UPDATE,
    TRIGGER_PRODUCT_ADD,
    TRIGGER_PRODUCT_UPDATE,
    TRIGGER_PRODUCT_DELETE,
)

RECALCULATION_TRIGGER_LABELS: dict[str, str] = {
    TRIGGER_OPEN: "The quote was opened.",
    TRIGGER_CREATE: "The quote was created.",
    TRIGGER_UPDATE: "The quote was updated.",
    TRIGGER_PRODUCT_ADD: "A product was added to the quote.",
    TRIGGER_PRODUCT_UPDATE: "A product on the quote was updated.",
    TRIGGER_PRODUCT_DELETE: "A product was removed from the quote.",
}


# --------------------------------------------------------------------------- #
# The currency-in-place constraint
# --------------------------------------------------------------------------- #

CURRENCY_CHANGE_REQUIRES_NO_LINES = (
    "You can't change the currency of the base record (in this case, an quote), unless you "
    "remove all the line items associated with the record."
)

CURRENCY_CHANGE_REFUSAL_NOTE = (
    "The currency is refused rather than re-priced. Re-stamping the currency would leave every "
    "line item priced in the old currency under a new header, which is the mismatch the "
    "platform exists to prevent."
)


# --------------------------------------------------------------------------- #
# Fields this workflow reads from the records other workflows provision
# --------------------------------------------------------------------------- #

#: WF-086 provisions the quote and its line items; WF-087 provisions the price list and the
#: price rows. Neither is implemented yet, so this workflow reads both as data and names the
#: fields it looks for. It never imports either workflow's module.
DEPENDENCY_QUOTE_COLLECTION = QUOTE_COLLECTION
DEPENDENCY_LINE_COLLECTION = QUOTE_LINE_COLLECTION
DEPENDENCY_PRICE_LIST_COLLECTION = PRICE_LIST_COLLECTION
DEPENDENCY_PRICE_ITEM_COLLECTION = PRICE_ITEM_COLLECTION

LINE_FIELDS: dict[str, str] = {
    "product_code": "The product the line prices.",
    "quantity": "The number of units priced.",
    "unit_price": "The unit price resolved from the price item, in the transaction currency.",
    "discount_amount": "The discount on this line, in the transaction currency.",
    "tax_amount": "The tax on this line, in the transaction currency.",
}

#: The dependencies the issue names, recorded as the issue records them. WF-086 and WF-087
#: are unimplemented, so each is listed with what this build does instead of waiting.
DEPENDENCIES: tuple[dict[str, Any], ...] = (
    {
        "ticket": "WF-086",
        "name": "Author a quote from a deal or opportunity",
        "provisions": "The quote header and its quotedetail line items.",
        "status": "not implemented",
        "this_workflow_does": (
            "Reads the quote and its line items from its own collections as data, and exposes "
            "routes that create them, so the workflow is demonstrable and testable now. When "
            "WF-086 lands it writes into the same two collections and this workflow prices what "
            "it finds."
        ),
    },
    {
        "ticket": "WF-087",
        "name": "Curate a product and price-book catalogue with tiered pricing",
        "provisions": "The price lists (pricelevel) and price rows (pricelevelproduct).",
        "status": "not implemented",
        "this_workflow_does": (
            "Reads price lists and price rows from its own collections as data and exposes "
            "routes that create them. A price row is keyed by product and price list, so a "
            "product priced in two currencies needs two price lists, which is the multi-currency "
            "rule the research states."
        ),
    },
)


def describe() -> dict[str, Any]:
    """The vocabulary as plain data, so ``GET /vocabulary`` serves one definition."""

    return {
        "product_model": PRODUCT_MODEL,
        "product_model_rejected": dict(PRODUCT_MODEL_REJECTED),
        "authoritative": AUTHORITATIVE,
        "authoritative_reason": AUTHORITATIVE_REASON,
        "currency_fields": dict(CURRENCY_FIELDS),
        "currency_types": list(CURRENCY_TYPES),
        "currency_type_labels": dict(CURRENCY_TYPE_LABELS),
        "base_currency_field": BASE_CURRENCY_FIELD,
        "currency_precision": {
            "min": CURRENCY_PRECISION_MIN,
            "max": CURRENCY_PRECISION_MAX,
            "default": DEFAULT_CURRENCY_PRECISION,
            "unit": "decimal places",
        },
        "rate_policy": RATE_POLICY,
        "rate_event": RATE_EVENT,
        "rate_event_note": RATE_EVENT_NOTE,
        "rate_sources": list(RATE_SOURCES),
        "identity_rate": IDENTITY_RATE,
        "price_list_single_currency": PRICE_LIST_SINGLE_CURRENCY,
        "price_list_single_currency_reason": PRICE_LIST_SINGLE_CURRENCY_REASON,
        "transaction_totals": list(TRANSACTION_TOTALS),
        "base_totals": list(BASE_TOTALS),
        "rate_field": RATE_FIELD,
        "stamped_rate_field": STAMPED_RATE_FIELD,
        "transaction_currency_field": TRANSACTION_CURRENCY_FIELD,
        "price_list_field": PRICE_LIST_FIELD,
        "pricing_outcomes": list(PRICING_OUTCOMES),
        "pricing_error_codes": dict(PRICING_ERROR_CODES),
        "refusal_is_outcome": REFUSAL_IS_OUTCOME_NOT_ERROR,
        "recalculation_triggers": list(RECALCULATION_TRIGGERS),
        "recalculation_trigger_labels": dict(RECALCULATION_TRIGGER_LABELS),
        "currency_change_requires_no_lines": CURRENCY_CHANGE_REQUIRES_NO_LINES,
        "currency_change_refusal_note": CURRENCY_CHANGE_REFUSAL_NOTE,
        "line_fields": dict(LINE_FIELDS),
        "collections": list(ALL_COLLECTIONS),
        "dependencies": [dict(entry) for entry in DEPENDENCIES],
        "sources": [
            "https://learn.microsoft.com/en-us/dynamics365/developer/reference/entities/quote",
            "https://learn.microsoft.com/en-us/power-apps/developer/"
            "reference/entities/transactioncurrency",
            "https://learn.microsoft.com/en-us/dynamics365/sales/create-edit-quote-sales",
            "https://learn.microsoft.com/en-us/dynamics365/sales/"
            "price-calculation-opportunity-quote-order-invoice-records",
            "https://knowledge.hubspot.com/products/use-price-books",
            "https://knowledge.hubspot.com/quotes/create-and-send-quotes",
            "https://developers.hubspot.com/docs/api-reference/latest/crm/objects/quotes/guide",
        ],
    }
