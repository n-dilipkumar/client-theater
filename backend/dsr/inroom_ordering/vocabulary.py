"""WF-129: the in-room pricing, quote and signable order form.

This module holds the words the workflow speaks: its collections, its statuses,
its signature modes, its refusal codes and the small set of rules the research
leaves open. Nothing here touches the store or HTTP.

What this workflow owns, and what it only reads
-----------------------------------------------

The quote and its line items are WF-086's. The product catalogue and its price
books are WF-087's. Their names are imported rather than retyped, so a rename
lands in one place:

* :data:`dsr.quote_authoring.vocabulary.QUOTE_COLLECTION` is the quote header
  this workflow renders inside the room.
* :data:`dsr.quote_authoring.vocabulary.LINE_ITEM_COLLECTION` is the quote's own
  line items, which are the source of the order form's itemised document.
* :data:`dsr.quote_authoring.vocabulary.DEAL_COLLECTION` is where the executed
  order form is filed back.
* :mod:`dsr.price_books.vocabulary` carries the billing frequencies the line
  item model names.

The order form itself is this workflow's own record. WF-101 also converts an
accepted quote into an order, and that is a different record for a different
purpose: WF-101 freezes a price onto a sales order, while this workflow renders
a *buyer-editable, signable* document inside the room and captures the
signature there. The two collections must not be shared.

The one sentence the package exists to hold is the user flow's::

    Seller generates a price quote or pulls one from CPQ -> it renders inside
    the room as an itemised, buyer-editable line-item document -> the buyer
    accepts and signs in the room, without a redirect -> the executed order
    form is filed back on the deal -> MAP tasks and close dates advance.

Three parts of that sentence have no surface here and are recorded under
:func:`not_implemented`: there is no e-signature provider, no MAP the repository
owns, and no PDF/DOCX renderer. Each is reported as a stand-in rather than
silently absent.
"""

from __future__ import annotations

from typing import Any

from dsr.price_books.vocabulary import (
    BILLING_FREQUENCIES,
    BILLING_ONE_TIME,
    BILLING_RECURRING,
    PRICE_BOOKS as PRICE_BOOK_COLLECTION,
    PRODUCTS as PRODUCT_COLLECTION,
)
from dsr.quote_authoring.vocabulary import (
    DEAL_COLLECTION,
    DISCOUNT_TYPES,
    LINE_ITEM_COLLECTION as QUOTE_LINE_ITEM_COLLECTION,
    QUOTE_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-129"

#: The feature id. It matches the backend feature id and the frontend
#: descriptor id, so the host and the sidebar agree on one name.
FEATURE_ID = "wf-129-inroom-order-form"

#: The router prefix. Ticket-derived, so it cannot collide with the
#: feature-shaped prefixes several workflows already share.
PREFIX = "/api/wf-129"

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The order form header: the itemised document rendered inside the room.
ORDER_FORM_COLLECTION = "wf129_order_form"

#: One line on the order form. Its record id is its own, never the quote line's:
#: the order form is a document the buyer edits, so it is independent of the
#: quote from the moment its lines are cloned.
ORDER_FORM_LINE_COLLECTION = "wf129_order_form_line"

#: A redline: one proposed edit to one line, with who proposed it and whether it
#: was accepted. This is the "real-time redlining and mutual revisions" the
#: research names.
REVISION_COLLECTION = "wf129_order_form_revision"

#: One captured signature. A stand-in for the e-sign provider the research
#: names, recorded as a signature event rather than claimed as a hosted
#: signature.
SIGNATURE_COLLECTION = "wf129_order_form_signature"

#: The audit certificate issued beside a signature. The research names it and
#: fixes no field, so its contents are derived in
#: :func:`dsr.inroom_ordering.rules.certificate_contents`.
CERTIFICATE_COLLECTION = "wf129_audit_certificate"

#: The signed artefact: the frozen document model the signature binds to, with
#: its hash. No PDF/DOCX renderer exists here, so this holds the document model
#: and names the formats that were not produced.
ARTEFACT_COLLECTION = "wf129_signed_artefact"

#: Every collection this workflow writes. A test asserts the set, so a
#: collection added without being recorded here fails rather than slips through.
OWNED_COLLECTIONS: tuple[str, ...] = (
    ORDER_FORM_COLLECTION,
    ORDER_FORM_LINE_COLLECTION,
    REVISION_COLLECTION,
    SIGNATURE_COLLECTION,
    CERTIFICATE_COLLECTION,
    ARTEFACT_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

#: Rendered in the room and still editable. The buyer may redline a line and the
#: seller may accept or reject the proposal.
STATUS_DRAFT = "draft"

#: The buyer accepted the form. Its lines are frozen and it is waiting on a
#: signature, still inside the room.
STATUS_ACCEPTED = "accepted"

#: The signature was captured. The signed artefact and the audit certificate
#: exist beside it.
STATUS_SIGNED = "signed"

#: The executed order form was filed back on the deal.
STATUS_FILED = "filed"

ORDER_FORM_STATUSES: tuple[str, ...] = (
    STATUS_DRAFT,
    STATUS_ACCEPTED,
    STATUS_SIGNED,
    STATUS_FILED,
)

#: The statuses in which the buyer may still change a line.
EDITABLE_STATUSES: tuple[str, ...] = (STATUS_DRAFT,)

# --------------------------------------------------------------------------- #
# Signature capture
# --------------------------------------------------------------------------- #
# The research names three capture modes -- "typed/drawn/typed-photo" -- and no
# rule that selects one. The derived rule is in
# :func:`dsr.inroom_ordering.rules.validate_signature`: the buyer's input
# selects the mode, and a mode whose own artefact is absent is refused rather
# than downgraded to a default. A typed-only submission can therefore never be
# recorded as drawn.

#: A name typed as text.
MODE_TYPED = "typed"

#: A signature drawn as strokes.
MODE_DRAWN = "drawn"

#: A typed name plus an uploaded photo of the wet signature.
MODE_TYPED_PHOTO = "typed_photo"

SIGNATURE_MODES: tuple[str, ...] = (MODE_TYPED, MODE_DRAWN, MODE_TYPED_PHOTO)

#: The payload key each mode requires. Read by :func:`validate_signature`.
SIGNATURE_MODE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    MODE_TYPED: ("text",),
    MODE_DRAWN: ("strokes",),
    MODE_TYPED_PHOTO: ("text", "image"),
}

#: The modes accepted as synonyms for the researched spelling "typed-photo".
SIGNATURE_MODE_SYNONYMS: dict[str, str] = {
    "typed": MODE_TYPED,
    "type": MODE_TYPED,
    "drawn": MODE_DRAWN,
    "draw": MODE_DRAWN,
    "typed_photo": MODE_TYPED_PHOTO,
    "typed-photo": MODE_TYPED_PHOTO,
    "typedphoto": MODE_TYPED_PHOTO,
    "photo": MODE_TYPED_PHOTO,
}

# --------------------------------------------------------------------------- #
# Revision states
# --------------------------------------------------------------------------- #

REVISION_PROPOSED = "proposed"
REVISION_ACCEPTED = "accepted"
REVISION_REJECTED = "rejected"

REVISION_STATES: tuple[str, ...] = (
    REVISION_PROPOSED,
    REVISION_ACCEPTED,
    REVISION_REJECTED,
)

#: The line fields a redline may propose a change to. Anything outside this set
#: is refused, because applying it would need a rule nobody wrote.
REVISION_FIELDS: tuple[str, ...] = (
    "quantity",
    "unit_price",
    "discount_type",
    "discount_value",
    "tax_rate",
    "name",
    "billing_frequency",
)

#: Who proposed a redline. Both directions are named because the research says
#: "mutual revisions".
PROPOSED_BY_BUYER = "buyer"
PROPOSED_BY_SELLER = "seller"

PROPOSERS: tuple[str, ...] = (PROPOSED_BY_BUYER, PROPOSED_BY_SELLER)

# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
# The data flow names "PDF/DOCX rendering". No renderer exists in this
# repository, so the order form stores its document model and reports the
# formats it did not produce. The set of formats it names as missing is served
# by GET /vocabulary so a reviewer reads the gap rather than infers it.

#: The only format this workflow can produce: its own JSON document model.
RENDER_FORMAT_JSON = "json"

#: The formats the research names and this repository cannot produce.
RENDER_FORMATS_UNAVAILABLE: tuple[str, ...] = ("pdf", "docx")

# --------------------------------------------------------------------------- #
# Filing
# --------------------------------------------------------------------------- #

#: The signature is captured in the room, so nothing redirects the buyer.
SIGNED_IN_ROOM = True

#: The MAP is not a surface this repository owns. Filing records the step as
#: skipped rather than inventing a MAP writer.
MAP_ADVANCED = False

#: Payment is out of scope. The research hedges with "Stripe for payment if
#: in-flow" and this build decides it is not in-flow: an order form is signed
#: here, and taking money is a separate flow with its own rules.
PAYMENT_IN_SCOPE = False

# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
# Keyed by a stable token rather than by an English sentence, so the page can
# branch on the token and the server can be reworded without breaking it.

#: Every refusal, with the status it answers.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "quote_not_found": (404, "No quote matches that reference."),
    "not_an_order_form": (404, "No such order form."),
    "not_an_order_form_line": (404, "No such order form line."),
    "not_a_revision": (404, "No such redline proposal."),
    "order_form_needs_a_quote": (
        422,
        "An order form is created from a quote. Name a quote that exists.",
    ),
    "order_form_needs_a_name": (422, "Give the order form a name."),
    "order_form_needs_a_room": (
        422,
        "An order form is signed inside a room. Name the room.",
    ),
    "line_needs_a_name": (422, "Give the line a name."),
    "quantity_is_negative": (422, "A quantity cannot be negative."),
    "price_is_negative": (422, "A price cannot be negative."),
    "tax_rate_out_of_range": (422, "A tax rate is a percentage between 0 and 100."),
    "discount_out_of_range": (422, "A percentage discount cannot exceed 100."),
    "unknown_discount_type": (422, "A discount is a percentage or a currency amount."),
    "form_not_editable": (
        409,
        "The order form is frozen. A line can change only before the buyer accepts it.",
    ),
    "form_not_awaiting_signature": (
        409,
        "The buyer must accept the order form before it can be signed.",
    ),
    "form_already_signed": (409, "This order form is already signed."),
    "form_already_filed": (409, "This order form is already filed on the deal."),
    "form_not_signed": (409, "Sign the order form before filing it on the deal."),
    "signature_needs_a_signer": (422, "Record who signed: a name and an email."),
    "unknown_signature_mode": (422, "Sign by typing, drawing or uploading a photo."),
    "signature_payload_incomplete": (
        422,
        "The signature is missing the artefact its mode needs.",
    ),
    "revision_needs_a_line": (422, "Name the line this redline proposes to change."),
    "revision_field_unknown": (422, "That field cannot be redlined."),
    "revision_needs_a_value": (422, "Give the proposed value."),
    "revision_conflict": (
        409,
        "A redline on this line is unresolved. Resolve it before proposing another.",
    ),
    "revision_not_open": (409, "That redline has already been resolved."),
    "unknown_proposer": (422, "A redline is proposed by the buyer or the seller."),
}

# --------------------------------------------------------------------------- #
# Field descriptions, served by GET /vocabulary
# --------------------------------------------------------------------------- #

ORDER_FORM_FIELDS: dict[str, str] = {
    "title": "The order form name a buyer sees.",
    "quote_id": "The quote this order form was rendered from.",
    "deal_id": "The deal the executed order form is filed back on.",
    "room_id": "The room it is signed inside.",
    "currency": "The currency every figure is stated in.",
    "buyer": "The legal entity that accepts and signs.",
    "signer": "The person who signs, with a name and an email.",
    "status": "draft, accepted, signed or filed.",
    "version": "Bumped every time a redline is accepted.",
    "buyer_editable": "True while a buyer may still redline a line.",
    "totals": "Subtotal, discount, tax, total and the split by billing frequency.",
    "signed_in_room": "The signature was captured inside the room, with no redirect.",
}

LINE_FIELDS: dict[str, str] = {
    "name": "The line's description.",
    "sku": "The product's stock keeping unit, when it comes from the catalogue.",
    "quantity": "How many units.",
    "unit_price": "The price of one unit.",
    "billing_frequency": "one_time or recurring.",
    "discount_type": "percentage or currency.",
    "discount_value": "The discount amount, read according to the type.",
    "tax_rate": "The tax percentage applied to the net amount.",
    "amounts": "The computed subtotal, discount, net, tax and total for the line.",
    "source_quote_line_item_id": "The quote line this line was cloned from.",
}

REVISION_FIELDS_DESCRIBED: dict[str, str] = {
    "line_id": "The line the proposal changes.",
    "field": "The line field the proposal changes.",
    "proposed_value": "The value being proposed.",
    "proposed_by": "buyer or seller.",
    "state": "proposed, accepted or rejected.",
    "version": "The order form version this proposal was accepted into.",
}

FIELD_CATALOGUE: dict[str, dict[str, str]] = {
    "order_form": ORDER_FORM_FIELDS,
    "line": LINE_FIELDS,
    "revision": REVISION_FIELDS_DESCRIBED,
}


class _Missing:
    """Why an item the research names is not a surface here."""

    def __init__(self, item: str, source: str, decision: str) -> None:
        self.item = item
        self.source = source
        self.decision = decision

    def as_dict(self) -> dict[str, str]:
        return {"item": self.item, "source": self.source, "decision": self.decision}


def not_implemented() -> list[dict[str, str]]:
    """What the research names and this repository does not build, and why.

    Recorded rather than silently absent, because a reader who cannot tell
    "we decided not to" from "we forgot to" has to open the source, and that is
    a cost paid every time the question is asked.
    """
    return [
        _Missing(
            "E-signature provider",
            "The research names DocuSign, Documenso and OpenSign as the signing "
            "service. DocuSign Rooms' own product pages are 404, so that evidence "
            "is unsourced.",
            "No e-sign provider is reached. A signature is recorded as this "
            "workflow's own event with its audit certificate, and the certificate "
            "states that no third-party provider verified it. The seed prints this "
            "as a non-success state.",
        ).as_dict(),
        _Missing(
            "PDF/DOCX rendering",
            "The data flow names 'PDF/DOCX rendering' between the document model "
            "and the signature.",
            "No renderer exists here. The order form stores its JSON document model, "
            "hashes it, and reports pdf and docx as unavailable rather than "
            "pretending a file was produced.",
        ).as_dict(),
        _Missing(
            "Mutual action plan",
            "The user flow ends 'MAP tasks and close dates advance'.",
            "This repository owns no MAP surface. Filing records the close date onto "
            "the deal and reports the MAP step as skipped, rather than inventing a "
            "MAP writer.",
        ).as_dict(),
        _Missing(
            "Payment in flow",
            "The research hedges with 'Stripe for payment if in-flow'.",
            "Payment is out of scope: an order form is signed here and taking money "
            "is a separate flow. The seed prints a non-success state for it.",
        ).as_dict(),
    ]


def vocabulary() -> dict[str, Any]:
    """Everything the page needs to label this workflow, and nothing it guesses."""
    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "prefix": PREFIX,
        "statuses": list(ORDER_FORM_STATUSES),
        "editable_statuses": list(EDITABLE_STATUSES),
        "signature_modes": list(SIGNATURE_MODES),
        "signature_mode_requirements": {
            mode: list(keys) for mode, keys in SIGNATURE_MODE_REQUIREMENTS.items()
        },
        "revision_states": list(REVISION_STATES),
        "revision_fields": list(REVISION_FIELDS),
        "proposers": list(PROPOSERS),
        "billing_frequencies": list(BILLING_FREQUENCIES),
        "discount_types": list(DISCOUNT_TYPES),
        "render_format": RENDER_FORMAT_JSON,
        "render_formats_unavailable": list(RENDER_FORMATS_UNAVAILABLE),
        "signed_in_room": SIGNED_IN_ROOM,
        "map_advanced": MAP_ADVANCED,
        "payment_in_scope": PAYMENT_IN_SCOPE,
        "collections": {
            "order_form": ORDER_FORM_COLLECTION,
            "order_form_line": ORDER_FORM_LINE_COLLECTION,
            "revision": REVISION_COLLECTION,
            "signature": SIGNATURE_COLLECTION,
            "certificate": CERTIFICATE_COLLECTION,
            "artefact": ARTEFACT_COLLECTION,
            "quote": QUOTE_COLLECTION,
            "quote_line_item": QUOTE_LINE_ITEM_COLLECTION,
            "deal": DEAL_COLLECTION,
            "product": PRODUCT_COLLECTION,
            "price_book": PRICE_BOOK_COLLECTION,
        },
        "fields": {name: dict(fields) for name, fields in FIELD_CATALOGUE.items()},
        "refusal_reasons": {code: message for code, (_status, message) in ERROR_CODES.items()},
        "billing_one_time": BILLING_ONE_TIME,
        "billing_recurring": BILLING_RECURRING,
        "not_implemented": not_implemented(),
    }
