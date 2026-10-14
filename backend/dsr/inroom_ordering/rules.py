"""The pure rules of the in-room order form.

Everything here is a function over plain values. Nothing reads the store,
nothing touches HTTP, and nothing raises anything but the two error types the
domain owns. The engine calls these; the tests read them directly.

The derived rules
-----------------

The research names several things and fixes no rule for them. Each derivation is
written down here, beside the function that uses it, and recorded again by
:func:`dsr.inroom_ordering.engine.OrderFormEngine.decisions` so a reviewer reads
the choice rather than infers it.

* **Which signature mode is used.** The research says "typed/drawn/typed-photo"
  and names no selector. The buyer's own input selects the mode, and a mode
  whose artefact is absent is refused rather than downgraded. See
  :func:`validate_signature`.
* **What the audit certificate contains.** The research names it and fixes no
  field. See :func:`certificate_contents`.
* **How a redline conflicts.** The research names "real-time redlining and
  mutual revisions" and no conflict rule. See :func:`revision_conflict`.
* **Which amount is written back.** The research names "auto-update CRM amount"
  and does not choose between the quote total and the contract value. See
  :func:`deal_writeback`.
* **How the close date advances.** See :func:`deal_writeback`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from dsr.inroom_ordering.vocabulary import (
    BILLING_ONE_TIME,
    BILLING_RECURRING,
    REVISION_FIELDS,
    REVISION_PROPOSED,
    SIGNATURE_MODE_SYNONYMS,
    SIGNATURE_MODES,
)
from dsr.quote_authoring.pricing import as_number, as_text, money
from dsr.quote_authoring.vocabulary import (
    DISCOUNT_CURRENCY,
    DISCOUNT_PERCENTAGE,
    DISCOUNT_TYPES,
)

#: What a signature cannot prove without a provider. Written into the
#: certificate so a reader never mistakes this stand-in for a verified signature.
CERTIFICATE_ISSUER = "digital-sales-room-in-room-ordering"
CERTIFICATE_STATEMENT = (
    "The signature was captured in the room by this product. No third-party "
    "e-signature provider verified it."
)


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def as_bool(value: Any, default: bool = False) -> bool:
    """A boolean from the spellings a record may carry."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0", ""):
        return False
    return default


def normalise_billing(value: Any, default: str = BILLING_ONE_TIME) -> str:
    """One of the two billing frequencies the line model names."""
    text = as_text(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in (BILLING_ONE_TIME, BILLING_RECURRING):
        return text
    if text in ("onetime", "once", "one", "one_off"):
        return BILLING_ONE_TIME
    if text in ("sub", "subscription", "monthly", "annual", "yearly", "recurring"):
        return BILLING_RECURRING
    return default


def normalise_signature_mode(value: Any) -> str:
    """The researched spelling of a capture mode, or the input unchanged.

    ``typed-photo`` and ``typed_photo`` are one mode; anything else is returned
    as typed so :func:`validate_signature` refuses it by name rather than
    silently selecting a default.
    """
    text = as_text(value).strip().lower()
    return SIGNATURE_MODE_SYNONYMS.get(text, text)


# --------------------------------------------------------------------------- #
# The line arithmetic and the totals contract
# --------------------------------------------------------------------------- #


def line_amounts(line: Mapping[str, Any]) -> dict[str, float]:
    """The five figures one line carries, rounded half up to two decimals.

    Quantity times unit price is the subtotal. A percentage discount is that
    percentage of the subtotal; a currency discount is taken off as given, never
    below zero. Tax is the rate applied to the net amount, so a discount reduces
    the tax as well.
    """
    quantity = as_number(line.get("quantity"), 1.0)
    unit_price = as_number(line.get("unit_price"), 0.0)
    subtotal = money(quantity * unit_price)

    discount_type = as_text(line.get("discount_type"), DISCOUNT_PERCENTAGE)
    discount_value = as_number(line.get("discount_value"), 0.0)
    if discount_type == DISCOUNT_CURRENCY:
        discount = money(min(max(discount_value, 0.0), subtotal))
    else:
        discount = money(subtotal * max(discount_value, 0.0) / 100.0)

    net = money(subtotal - discount)
    tax = money(net * as_number(line.get("tax_rate"), 0.0) / 100.0)
    return {
        "quantity": quantity,
        "unit_price": unit_price,
        "subtotal": subtotal,
        "discount": discount,
        "net": net,
        "tax": tax,
        "total": money(net + tax),
    }


def totals_for(lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The document's totals, and the same total split by billing frequency.

    The split is here because the research's document model names "billing
    frequency" as a column, and a figure a buyer signs should be readable as
    what recurs and what is charged once.
    """
    subtotal = 0.0
    discount = 0.0
    tax = 0.0
    total = 0.0
    by_billing = {BILLING_ONE_TIME: 0.0, BILLING_RECURRING: 0.0}
    for line in lines:
        amounts = line_amounts(line)
        subtotal += amounts["subtotal"]
        discount += amounts["discount"]
        tax += amounts["tax"]
        total += amounts["total"]
        billing = normalise_billing(line.get("billing_frequency"))
        by_billing[billing] += amounts["total"]
    return {
        "subtotal": money(subtotal),
        "discount": money(discount),
        "tax": money(tax),
        "total": money(total),
        "line_count": len(lines),
        "by_billing_frequency": {
            BILLING_ONE_TIME: money(by_billing[BILLING_ONE_TIME]),
            BILLING_RECURRING: money(by_billing[BILLING_RECURRING]),
        },
        "rounding": "Every figure is rounded half up to two decimals, at the line and at the total.",
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_line(data: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The clean line a record stores, plus a field-keyed map of what is wrong.

    Returned rather than raised so the engine can decide whether an invalid line
    is a refusal or a redline proposal against a field it did not clean.
    """
    errors: dict[str, str] = {}
    name = as_text(data.get("name"))
    if not name:
        errors["name"] = "Give the line a name."

    quantity = as_number(data.get("quantity"), 1.0)
    if quantity < 0:
        errors["quantity"] = "A quantity cannot be negative."

    unit_price = as_number(data.get("unit_price"), 0.0)
    if unit_price < 0:
        errors["unit_price"] = "A price cannot be negative."

    discount_type = as_text(data.get("discount_type"), DISCOUNT_PERCENTAGE)
    if discount_type not in DISCOUNT_TYPES:
        errors["discount_type"] = "A discount is a percentage or a currency amount."

    discount_value = as_number(data.get("discount_value"), 0.0)
    if discount_value < 0:
        errors["discount_value"] = "A discount cannot be negative."
    if discount_type == DISCOUNT_PERCENTAGE and discount_value > 100:
        errors["discount_value"] = "A percentage discount cannot exceed 100."

    tax_rate = as_number(data.get("tax_rate"), 0.0)
    if tax_rate < 0 or tax_rate > 100:
        errors["tax_rate"] = "A tax rate is a percentage between 0 and 100."

    clean = {
        "name": name,
        "sku": as_text(data.get("sku")),
        "description": as_text(data.get("description")),
        "quantity": quantity,
        "unit_price": unit_price,
        "billing_frequency": normalise_billing(data.get("billing_frequency")),
        "discount_type": discount_type,
        "discount_value": discount_value,
        "tax_rate": tax_rate,
        "source_quote_line_item_id": as_text(data.get("source_quote_line_item_id")) or None,
    }
    clean["amounts"] = line_amounts(clean)
    return clean, errors


def validate_signature(mode: Any, payload: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    """The capture mode, and a field-keyed map of what is missing.

    The research names three modes and no selector. The derived rule: the mode
    is the one the buyer asks for, and it is valid only when the payload carries
    the artefact that mode is named for. A mode whose artefact is absent is
    refused, never downgraded, so a typed-only submission cannot be recorded as
    drawn and a drawn submission cannot be recorded without strokes.
    """
    normalised = normalise_signature_mode(mode)
    errors: dict[str, str] = {}
    if normalised not in SIGNATURE_MODES:
        return normalised, {"mode": "Sign by typing, drawing or uploading a photo."}

    if normalised == "typed":
        if not as_text(payload.get("text")):
            errors["text"] = "A typed signature needs a name."
    elif normalised == "drawn":
        strokes = payload.get("strokes")
        if not strokes:
            errors["strokes"] = "A drawn signature needs strokes."
    elif normalised == "typed_photo":
        if not as_text(payload.get("text")):
            errors["text"] = "A typed-photo signature needs a name."
        if not (payload.get("image") or payload.get("photo")):
            errors["image"] = "A typed-photo signature needs an image."
    return normalised, errors


# --------------------------------------------------------------------------- #
# Redlining
# --------------------------------------------------------------------------- #


def revision_conflict(open_revisions: Sequence[Mapping[str, Any]], line_id: str) -> bool:
    """Whether a redline on ``line_id`` is already unresolved.

    The derived conflict rule: at most one proposal may be open on a line at a
    time. Two proposals on one line have no defined order of application, so the
    second is refused until the first is resolved. That is what makes "mutual
    revisions" a sequence of accepted changes rather than a race.

    ``open_revisions`` is the flattened shape the engine and the page read: a
    revision's own fields beside its record id.
    """
    return any(
        str(row.get("line_id")) == str(line_id) and row.get("state") == REVISION_PROPOSED
        for row in open_revisions
    )


def apply_revision(
    line: Mapping[str, Any], field: str, value: Any
) -> tuple[dict[str, Any], dict[str, str]]:
    """The line with one accepted proposal applied, and what the line model says.

    The value is re-validated against the line model, so an accepted redline
    that would make the line invalid is refused by the same rule a direct edit
    is. The whole line is returned so the engine can re-run the totals.
    """
    updated = dict(line)
    updated[field] = value
    return validate_line(updated)


def revision_is_known_field(field: Any) -> bool:
    return as_text(field) in REVISION_FIELDS


# --------------------------------------------------------------------------- #
# The document, its hash and the audit certificate
# --------------------------------------------------------------------------- #


def document_model(
    order_form: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Any],
) -> dict[str, Any]:
    """The itemised document the buyer read, as plain data.

    This is the thing the signature binds to and the hash is taken over. It
    holds no record ids and no timestamps, so two bytes of the same content hash
    the same and the hash cannot be invalidated by an envelope field changing.
    """
    return {
        "title": as_text(order_form.get("title")),
        "quote_id": as_text(order_form.get("quote_id")),
        "room_id": as_text(order_form.get("room_id")),
        "currency": as_text(order_form.get("currency")),
        "buyer": order_form.get("buyer"),
        "version": int(as_number(order_form.get("version"), 1.0)),
        "lines": [
            {
                "name": as_text(line.get("name")),
                "sku": as_text(line.get("sku")),
                "quantity": as_number(line.get("quantity"), 1.0),
                "unit_price": as_number(line.get("unit_price"), 0.0),
                "billing_frequency": normalise_billing(line.get("billing_frequency")),
                "amounts": line_amounts(line),
            }
            for line in lines
        ],
        "totals": {
            "subtotal": as_number(totals.get("subtotal")),
            "discount": as_number(totals.get("discount")),
            "tax": as_number(totals.get("tax")),
            "total": as_number(totals.get("total")),
        },
    }


def document_hash(model: Mapping[str, Any]) -> str:
    """The SHA-256 of the canonical document model.

    Canonical, not pretty: the keys are sorted and the separators are tight, so
    the same content hashes the same on every machine. A pretty-printed hash
    would change if a formatter changed, which would make the certificate claim
    a different document than the one the buyer read.
    """
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def certificate_contents(
    order_form: Mapping[str, Any],
    signature: Mapping[str, Any],
    document_sha256: str,
) -> dict[str, Any]:
    """The audit certificate's contents.

    The research names "signed artefact + audit certificate" and fixes no field.
    The derived contents are: what was signed (the document hash and version),
    who signed it (the signer), how (the capture mode), when, where, and the
    plain statement that no third-party provider verified it. The last field is
    the one that keeps this stand-in honest.
    """
    return {
        "order_form_id": as_text(order_form.get("id")),
        "quote_id": as_text(order_form.get("quote_id")),
        "room_id": as_text(order_form.get("room_id")),
        "signer": {
            "name": as_text((signature.get("signer") or {}).get("name")),
            "email": as_text((signature.get("signer") or {}).get("email")),
        },
        "signature_mode": as_text(signature.get("mode")),
        "signed_at": as_text(signature.get("signed_at")),
        "document_sha256": document_sha256,
        "document_version": int(as_number(order_form.get("version"), 1.0)),
        "currency": as_text(order_form.get("currency")),
        "signed_in_room": True,
        "verified_by_provider": False,
        "issuer": CERTIFICATE_ISSUER,
        "statement": CERTIFICATE_STATEMENT,
    }


# --------------------------------------------------------------------------- #
# Filing the executed order form
# --------------------------------------------------------------------------- #


def deal_writeback(
    order_form: Mapping[str, Any],
    totals: Mapping[str, Any],
    signed_on: str,
    existing_deal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What filing writes onto the deal.

    Two derived rules:

    * **The amount.** The research names "auto-update CRM amount" and does not
      choose between the quote total and the contract value. The signed order
      form's own total wins, because the buyer may have redlined a line before
      signing, so the executed document is the figure both parties agreed to.
      The quote's total contract value is the alternative this rejects.
    * **The close date.** The deal's close date advances to the signature date
      when the deal has no close date, or when the deal's forecast date is
      later than the signature date. A deal already forecast to close earlier
      than the signed date keeps its earlier date, because filing a signature
      does not move a forecast backwards.
    """
    amount = money(as_number(totals.get("total")))
    existing_close = as_text((existing_deal or {}).get("close_date"))
    if not existing_close:
        close_date = signed_on
    else:
        close_date = min(existing_close, signed_on)
    return {
        "amount": amount,
        "amount_source": "signed_order_form_total",
        "close_date": close_date,
        "signed_order_form_id": as_text(order_form.get("id")),
        "order_form_status": "filed",
    }


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def parse_date(value: Any) -> str:
    """An ISO date string from a value, or an empty string.

    Used to validate a caller-supplied date without raising, so the engine
    decides what a bad date means rather than a conversion error.
    """
    text = as_text(value)
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def stamp(now: datetime) -> str:
    """The instant a record stores, seconds precision."""
    return now.isoformat(timespec="seconds")
