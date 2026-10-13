"""The judgement calls WF-101 made, with the alternative each one rejected.

The research leaves several things open. Each is recorded here rather than
resolved silently in a docstring a reviewer has to hunt for, and
``GET /api/wf-101/decisions`` serves them so the page can show its own choices.

These are the readings this build took, not claims the sources force. Where the
research supports one reading better than another, the entry says so and names
the rejected alternative.
"""

from __future__ import annotations

from typing import Any

#: The reading of "activated" this build uses.
DERIVED_ACTIVATED = "a_quote_is_activated_when_it_is_frozen"

#: The reading of "accepted" this build uses.
DERIVED_ACCEPTED = "acceptance_is_a_marker_not_a_ledger"

#: Where fulfilment and invoicing stop.
DERIVED_CLOSE_SCOPE = "fulfilment_here_invoicing_elsewhere"

#: How the seven clone components are counted.
DERIVED_CLONE_COUNT = "seven_components_products_and_lines_apart"

#: How a price-list edit reaches a locked order.
DERIVED_LOCKED_EDIT = "a_locked_edit_is_recorded_not_applied"


def decision_list() -> list[dict[str, Any]]:
    """Every judgement call, with what was chosen and what was rejected."""

    return [
        {
            "id": DERIVED_ACTIVATED,
            "question": (
                "The input state is an accepted and activated quote, and the research "
                "defines activated only by its effect: it makes a quote read-only."
            ),
            "chosen": (
                "A quote is activated when it is frozen: status is published, active or "
                "activated, or an activated or is_active flag is set. This maps the "
                "read-only effect the research names onto WF-086's own frozen status, "
                "published."
            ),
            "rejected": (
                "Treating activation as a separate stored boolean only. A quote that "
                "WF-086 published is already frozen, and requiring a second flag would "
                "report a read-only quote as not activated."
            ),
        },
        {
            "id": DERIVED_ACCEPTED,
            "question": (
                "The research names an accepted quote as the input and does not say "
                "which workflow produces the accepted state."
            ),
            "chosen": (
                "Acceptance is read as a marker on the quote: the shared hs_status of "
                "ACCEPTED, a status of accepted, or an accepted, accepted_at or "
                "accepted_by field. WF-096 and WF-099 write hs_status, so the marker is "
                "the field the accepting workflows already agree on."
            ),
            "rejected": (
                "Reading another workflow's private acceptance ledger. WF-095, WF-096 "
                "and WF-099 each own one, and reading across that boundary is the "
                "coupling WF-100 explicitly refused for its own dependency."
            ),
        },
        {
            "id": DERIVED_CLOSE_SCOPE,
            "question": (
                "The data flow names an order close and the creation of an invoice for "
                "billing, and does not say whether one workflow owns both."
            ),
            "chosen": (
                "Fulfilment and cancellation close the order here, because the "
                "read-only rule ('an order cannot be updated once closed or fulfilled') "
                "is a rule this workflow must enforce on its own order. Invoicing is "
                "not built: it belongs to the billing workflow with its own ledger."
            ),
            "rejected": (
                "Building invoice and invoicedetail records here. An invoice raised by "
                "the quoting workflow would duplicate the billing workflow's own "
                "record, and the two could disagree about what was billed."
            ),
        },
        {
            "id": DERIVED_CLONE_COUNT,
            "question": (
                "The data flow lists price list, currency, products, addresses, "
                "shipping and totals, and the issue says to copy all seven."
            ),
            "chosen": (
                "Seven components: the price list and currency on the header, the "
                "products as the priced line set, the order lines as their own detail "
                "rows, the addresses, the shipping details and the recomputed totals. "
                "Products and order lines are listed apart because the research names "
                "them apart, as salesorder and salesorderdetail."
            ),
            "rejected": (
                "Counting products and order lines as one component, which would "
                "either drop the detail rows or leave the clone list at six."
            ),
        },
        {
            "id": DERIVED_LOCKED_EDIT,
            "question": (
                "The research says a locked order's lines do not reprice, and does not "
                "say whether a price-list edit is refused or ignored."
            ),
            "chosen": (
                "A reprice of a locked order is recorded on the conversion ledger and "
                "applied to nothing. The order keeps its prices, and the ledger says "
                "why, so a reviewer can see that the edit was received rather than "
                "lost."
            ),
            "rejected": (
                "Refusing the edit with a 409. The research's wording is that a locked "
                "order is not repriced, which is a no-op, not an error: a price-list "
                "edit is a legitimate request that a locked order simply does not act "
                "on."
            ),
        },
        {
            "id": "copy_not_reference",
            "question": (
                "The order inherits the quote's commercial content, and the research "
                "does not say whether later quote edits reach the order."
            ),
            "chosen": (
                "The clone is a copy. Each order line is a new record with its own id, "
                "and the quote line's id is kept only as provenance. Editing the quote "
                "afterwards changes nothing on the order."
            ),
            "rejected": (
                "Holding the order as a live view over the quote. A view would make the "
                "order change when its source changed, which is the opposite of what a "
                "price lock means."
            ),
        },
    ]
