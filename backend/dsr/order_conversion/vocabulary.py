"""The words WF-101 speaks, and the evidence each one came from.

This package turns an accepted and activated quote into a sales order, and it
stamps a price lock on the order header. Its specification is
``docs/research/digital-sales-room-workflows/wf/WF-101.md``, quoted in full in
GitHub issue 192.

The one sentence the package exists to hold is the data flow's::

    Accepted + activated Quote row (+ its QuoteDetail lines) ->
    ConvertQuoteToSalesOrder clones price list, currency, products,
    addresses, shipping and totals onto a new salesorder row (+
    salesorderdetail lines) -> ispricelocked is stamped on the order
    header -> subsequent price-list edits either apply to the order
    (current pricing) or are ignored (locked).

Everything in :mod:`dsr.order_conversion.rules` follows from that: the order is
a copy, the lock is a flag on the copy, and the copy reprices only when the
lock is off.

What this package owns, and what it only reads
----------------------------------------------

The quote and its line items are WF-086's. Their names are imported from
:mod:`dsr.quote_authoring.vocabulary` rather than retyped, so a rename lands in
one place. The product catalogue and its price books are WF-087's, imported from
:mod:`dsr.price_books.vocabulary`. Acceptance is read as a marker on the quote,
never as a second writer of another workflow's acceptance ledger: WF-095,
WF-096 and WF-099 each own their own ledger, and reading a private collection
across a workflow boundary is the coupling WF-100 explicitly refused for its own
dependency.

The order is this workflow's own record. WF-099 writes a ``wf099_order`` when
the seller is in its Connected CPQ beta, and that is a different record for a
different purpose (a contract's companion order), so this workflow does not
write it. Collections are not shared files: the plugin contract governs which
files a branch may edit and says nothing about which collection a workflow owns.
"""

from __future__ import annotations

from typing import Any

from dsr.price_books.vocabulary import (
    PRICE_BOOK_ITEMS as PRICE_BOOK_ITEM_COLLECTION,
    PRICE_BOOKS as PRICE_BOOK_COLLECTION,
    PRODUCTS as PRODUCT_COLLECTION,
)
from dsr.quote_authoring.vocabulary import (
    LINE_ITEM_COLLECTION as QUOTE_LINE_ITEM_COLLECTION,
    QUOTE_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-101"

#: The feature id matches this backend feature id and the frontend descriptor id.
FEATURE_ID = "wf-101-quote-to-order"

#: The router prefix. Ticket-derived, so it cannot collide with a feature-shaped
#: prefix by construction.
PREFIX = "/api/wf-101"

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The sales order header. ``salesorder`` in the research, namespaced with the
#: ticket because every feature shares one ``records`` table.
ORDER_COLLECTION = "wf101_sales_order"

#: One order line. ``salesorderdetail`` in the research. Its record id is its
#: own, never the quote line's: the order and the quote are independent records
#: from the moment the clone is written.
ORDER_LINE_COLLECTION = "wf101_sales_order_line"

#: The organisation settings: the two toggles named by the research plus the
#: Order table column default the price-lock rule reads.
SETTING_COLLECTION = "wf101_order_setting"

#: One row per conversion attempt, whether it produced an order or was refused.
#: A reviewer can see why an order was or was not created without replaying
#: every quote.
CONVERSION_COLLECTION = "wf101_order_conversion"

#: One row per fulfilment close. ``orderclose`` in the research.
ORDER_CLOSE_COLLECTION = "wf101_order_close"

#: Every collection this workflow writes.
OWNED_COLLECTIONS = (
    ORDER_COLLECTION,
    ORDER_LINE_COLLECTION,
    SETTING_COLLECTION,
    CONVERSION_COLLECTION,
    ORDER_CLOSE_COLLECTION,
)

#: The collections this workflow reads and does not own, spelled from the
#: packages that own them so a rename lands in one place.
READ_ONLY_COLLECTIONS = (
    QUOTE_COLLECTION,
    QUOTE_LINE_ITEM_COLLECTION,
    PRODUCT_COLLECTION,
    PRICE_BOOK_COLLECTION,
    PRICE_BOOK_ITEM_COLLECTION,
)

# --------------------------------------------------------------------------- #
# The clone list
# --------------------------------------------------------------------------- #
#
# The data flow names the things the conversion copies, and the issue says to
# copy all seven. They are enumerated here so a test asserts the count and a
# response can report which ones landed.

CLONE_PRICE_LIST = "price_list"
CLONE_CURRENCY = "currency"
CLONE_PRODUCTS = "products"
CLONE_LINE_ITEMS = "line_items"
CLONE_ADDRESSES = "addresses"
CLONE_SHIPPING = "shipping"
CLONE_TOTALS = "totals"

CLONE_COMPONENTS: tuple[str, ...] = (
    CLONE_PRICE_LIST,
    CLONE_CURRENCY,
    CLONE_PRODUCTS,
    CLONE_LINE_ITEMS,
    CLONE_ADDRESSES,
    CLONE_SHIPPING,
    CLONE_TOTALS,
)

# --------------------------------------------------------------------------- #
# Order status
# --------------------------------------------------------------------------- #

ORDER_STATUS_OPEN = "open"
ORDER_STATUS_FULFILLED = "fulfilled"
ORDER_STATUS_CANCELLED = "cancelled"

ORDER_STATUSES: tuple[str, ...] = (
    ORDER_STATUS_OPEN,
    ORDER_STATUS_FULFILLED,
    ORDER_STATUS_CANCELLED,
)

#: "Order/invoice cannot be updated once closed or fulfilled." Both are the
#: same refusal, and both statuses raise it.
CLOSED_ORDER_STATUSES: tuple[str, ...] = (ORDER_STATUS_FULFILLED, ORDER_STATUS_CANCELLED)

ORDER_STATUS_LABELS: dict[str, str] = {
    ORDER_STATUS_OPEN: "Open",
    ORDER_STATUS_FULFILLED: "Fulfilled",
    ORDER_STATUS_CANCELLED: "Cancelled",
}

# --------------------------------------------------------------------------- #
# Pricing mode and the price lock
# --------------------------------------------------------------------------- #

#: "Lock Pricing (sets Prices Locked = Yes; Price Per Unit becomes
#: non-editable; write-in products can no longer be added; changing the price
#: list does not reprice existing lines)".
PRICING_MODE_LOCKED = "lock_pricing"

#: "Use Current Pricing (Prices Locked = No; Price Per Unit editable; prices
#: auto-update to the price list when the record is opened or edited; overridden
#: line prices are unaffected)."
PRICING_MODE_CURRENT = "use_current_pricing"

PRICING_MODES: tuple[str, ...] = (PRICING_MODE_LOCKED, PRICING_MODE_CURRENT)

PRICING_MODE_LABELS: dict[str, str] = {
    PRICING_MODE_LOCKED: "Lock Pricing",
    PRICING_MODE_CURRENT: "Use Current Pricing",
}

#: The order header field. The research names the column ``ispricelocked`` and
#: the seller-facing field "Prices Locked"; this is the stored column name.
FIELD_IS_PRICE_LOCKED = "ispricelocked"

#: The seller-facing spelling, carried on every response so a page reads it the
#: way the vendor labels it. One stored field, two names, never two values.
FIELD_PRICES_LOCKED = "prices_locked"

# --------------------------------------------------------------------------- #
# The two organisation settings
# --------------------------------------------------------------------------- #

#: ``enable-creation-of-orders-from-quotes``. "In Sales Professional this is
#: only available if the admin has enabled creation of orders from quotes."
FIELD_CREATE_ORDERS = "enable_creation_of_orders_from_quotes"

#: ``enable-sales-order-processing-integration``. The flag the price-lock
#: default branches on.
FIELD_SALES_ORDER_PROCESSING = "enable_sales_order_processing_integration"

#: The default value specified for the ``ispricelocked`` column in the Order
#: table. Read only when the sales order processing integration is enabled.
FIELD_ISPRICELOCKED_DEFAULT = "ispricelocked_default"

#: The setting as it stands before an administrator changes anything. Creation
#: is off because the research describes an admin turning it on, and a product
#: that mints orders silently is the worse default. The integration is off, so
#: the price-lock rule takes its first branch (Yes). The table default is No,
#: which the research names as the documented default "if unchanged".
DEFAULT_SETTING: dict[str, Any] = {
    FIELD_CREATE_ORDERS: False,
    FIELD_SALES_ORDER_PROCESSING: False,
    FIELD_ISPRICELOCKED_DEFAULT: False,
}

SETTING_FIELDS: tuple[str, ...] = tuple(DEFAULT_SETTING)

# --------------------------------------------------------------------------- #
# What "accepted and activated" means
# --------------------------------------------------------------------------- #
#
# The research's input state is "Accepted + activated Quote row" and it defines
# neither word here. WF-086's own vocabulary supplies the reading: a quote is
# *activated* when it is frozen, which WF-086 spells ``published`` ("its line
# items are frozen and its totals have been written"), and it is *accepted* when
# an acceptance marker is present. Both readings are recorded in
# :mod:`dsr.order_conversion.inferences`.

#: The statuses that mean a quote is frozen and therefore activated. WF-086
#: writes ``published``; the two longer spellings are accepted so a mirror of a
#: Dynamics quote, whose state is literally Active, reads without translation.
QUOTE_STATUS_PUBLISHED = "published"
ACTIVATED_QUOTE_STATUSES: tuple[str, ...] = ("published", "active", "activated")

#: Boolean or timestamp fields that mean a quote is activated.
ACTIVATION_FLAGS: tuple[str, ...] = ("activated", "is_active")

#: The shared acceptance field WF-096 and WF-099 also write.
FIELD_HS_STATUS = "hs_status"
HS_STATUS_ACCEPTED = "ACCEPTED"

#: Markers that mean a quote is accepted. ``hs_status`` is the shared one; the
#: rest cover a quote accepted by a workflow that recorded its own marker.
ACCEPTANCE_STATUSES: tuple[str, ...] = ("accepted",)
ACCEPTANCE_FLAGS: tuple[str, ...] = ("accepted", "accepted_at", "accepted_by")

# --------------------------------------------------------------------------- #
# Refusal reasons
# --------------------------------------------------------------------------- #

REASON_QUOTE_NOT_FOUND = "quote_not_found"
REASON_ORDER_NOT_FOUND = "order_not_found"
REASON_QUOTE_NOT_ACTIVATED = "quote_not_activated"
REASON_QUOTE_NOT_ACCEPTED = "quote_not_accepted"
REASON_CREATION_DISABLED = "order_creation_disabled"
REASON_ALREADY_CONVERTED = "quote_already_converted"
REASON_ORDER_CLOSED = "order_closed"
REASON_PRICES_LOCKED = "prices_locked"
REASON_WRITE_IN_NOT_ALLOWED = "write_in_product_not_allowed"
REASON_UNKNOWN_PRICING_MODE = "unknown_pricing_mode"
REASON_LINE_NOT_FOUND = "order_line_not_found"


def refusal_reasons() -> dict[str, str]:
    """Every refusal token, with the sentence the page shows for it."""

    return {
        REASON_QUOTE_NOT_FOUND: "No quote record matches that reference.",
        REASON_ORDER_NOT_FOUND: "No order record matches that reference.",
        REASON_QUOTE_NOT_ACTIVATED: (
            "A quote is converted only after it is activated. Activate the quote first."
        ),
        REASON_QUOTE_NOT_ACCEPTED: (
            "A quote is converted only after the customer accepts it. Record the acceptance first."
        ),
        REASON_CREATION_DISABLED: (
            "Orders from quotes are turned off. An administrator must enable creation "
            "of orders from quotes."
        ),
        REASON_ALREADY_CONVERTED: "This quote already produced an order.",
        REASON_ORDER_CLOSED: ("An order that is closed or fulfilled cannot be updated."),
        REASON_PRICES_LOCKED: (
            "Prices are locked, so this price-list change does not apply to the order."
        ),
        REASON_WRITE_IN_NOT_ALLOWED: (
            "A write-in product is addable only while Prices Locked is No."
        ),
        REASON_UNKNOWN_PRICING_MODE: "That is not a pricing mode.",
        REASON_LINE_NOT_FOUND: "No order line matches that reference.",
    }


# --------------------------------------------------------------------------- #
# Evidence, quoted from the research
# --------------------------------------------------------------------------- #

EVIDENCE: dict[str, str] = {
    "clone": (
        "The order is created and all products from the quote are added to the order. "
        "The price list and currency are also copied from the quote to the order."
    ),
    "lock_default": (
        "If the sales order processing integration is disabled, the Prices Locked "
        "field is set to Yes. If it is enabled, the field is set to the default value "
        "specified for the ispricelocked column in the Order table."
    ),
    "lock_pricing": (
        "Lock Pricing sets Prices Locked to Yes. The Price Per Unit field is locked "
        "for all the products in the order, and changing the price list does not "
        "reprice existing lines."
    ),
    "use_current_pricing": (
        "Use Current Pricing sets Prices Locked to No. When you open or edit the "
        "order, the price of the line items is automatically updated to reflect any "
        "changes in the price list."
    ),
    "override_survives": (
        "If the price of a line item has been overridden in the order or invoice, it "
        "is unaffected by any changes to the price list."
    ),
    "write_in_gate": (
        "Write-in product prices are overridden, so a write-in product is only "
        "addable when Prices Locked is No."
    ),
    "activated_is_read_only": (
        "Activating a quote makes it read-only so your version matches the customer's version."
    ),
    "closed_is_final": ("An order or invoice cannot be updated once it is closed or fulfilled."),
    "get_products": (
        "Get Products copies products from an opportunity onto the order. The related "
        "opportunity can be kept open or closed; if it is closed, the final revenue "
        "amount is recorded."
    ),
    "convert_message": (
        "ConvertQuoteToSalesOrder clones the quote onto a new salesorder row and its "
        "QuoteDetail lines onto salesorderdetail lines."
    ),
}


def not_implemented() -> list[dict[str, str]]:
    """What this workflow deliberately does not build, and why."""

    return [
        {
            "item": "Winning and closing the quote (WinQuote, CloseQuote, WinOpportunity)",
            "source": (
                "The APIs touched name WinQuote, CloseQuote with Lost, Canceled and "
                "Revised, and WinOpportunity."
            ),
            "decision": (
                "Acceptance is this workflow's input, not its action. WF-095, WF-096 "
                "and WF-099 own the paths that accept a quote, and this workflow reads "
                "the accepted state they produce. It does not win, reopen or close a "
                "quote, because a second writer of a quote's acceptance is the coupling "
                "the acceptance workflows each refused for their own dependency."
            ),
        },
        {
            "item": "Invoicing (invoice, invoicedetail)",
            "source": (
                "The data flow names invoice and invoicedetail as created for billing "
                "after fulfilment."
            ),
            "decision": (
                "Billing is a separate workflow with its own quote, its own payment "
                "state and its own ledger. This workflow records order fulfilment as "
                "the order close and writes no invoice, because an invoice raised here "
                "would duplicate the billing workflow's own record."
            ),
        },
        {
            "item": "RetrieveExchangeRate and Rollup",
            "source": (
                "The APIs touched name RetrieveExchangeRate for currency resolution and "
                "Rollup for totals."
            ),
            "decision": (
                "This product makes no outbound vendor call. Currency is copied from "
                "the quote verbatim, and nothing is converted at an exchange rate: a "
                "converted price is a number this product could not later explain. "
                "Rollup is arithmetic this workflow already performs over its own lines."
            ),
        },
        {
            "item": "A Dynamics client",
            "source": "POST /salesorders, GET /salesorders, PATCH /salesorders({id}).",
            "decision": (
                "The three endpoints are modelled as this workflow's own REST routes "
                "over its own store. The product is not a Dynamics client and calls no "
                "vendor."
            ),
        },
    ]


def vocabulary() -> dict[str, Any]:
    """Everything the page needs to label this workflow, and nothing it must guess."""

    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "prefix": PREFIX,
        "collections": {
            "order": ORDER_COLLECTION,
            "order_line": ORDER_LINE_COLLECTION,
            "setting": SETTING_COLLECTION,
            "conversion": CONVERSION_COLLECTION,
            "order_close": ORDER_CLOSE_COLLECTION,
            "quote": QUOTE_COLLECTION,
            "quote_line_item": QUOTE_LINE_ITEM_COLLECTION,
            "product": PRODUCT_COLLECTION,
            "price_book": PRICE_BOOK_COLLECTION,
            "price_book_item": PRICE_BOOK_ITEM_COLLECTION,
        },
        "clone_components": list(CLONE_COMPONENTS),
        "clone_component_labels": {
            CLONE_PRICE_LIST: "Price list",
            CLONE_CURRENCY: "Currency",
            CLONE_PRODUCTS: "Products",
            CLONE_LINE_ITEMS: "Order lines",
            CLONE_ADDRESSES: "Addresses",
            CLONE_SHIPPING: "Shipping",
            CLONE_TOTALS: "Totals",
        },
        "order_statuses": list(ORDER_STATUSES),
        "order_status_labels": dict(ORDER_STATUS_LABELS),
        "closed_order_statuses": list(CLOSED_ORDER_STATUSES),
        "pricing_modes": list(PRICING_MODES),
        "pricing_mode_labels": dict(PRICING_MODE_LABELS),
        "field_is_price_locked": FIELD_IS_PRICE_LOCKED,
        "field_prices_locked": FIELD_PRICES_LOCKED,
        "activated_quote_statuses": list(ACTIVATED_QUOTE_STATUSES),
        "activation_flags": list(ACTIVATION_FLAGS),
        "acceptance_statuses": list(ACCEPTANCE_STATUSES),
        "acceptance_flags": list(ACCEPTANCE_FLAGS),
        "hs_status_accepted": HS_STATUS_ACCEPTED,
        "setting_fields": list(SETTING_FIELDS),
        "default_setting": dict(DEFAULT_SETTING),
        "refusal_reasons": refusal_reasons(),
        "evidence": dict(EVIDENCE),
        "not_implemented": not_implemented(),
    }
