"""The rules of the conversion, as pure functions. No store, no HTTP, no clock.

Every function here takes plain values and returns plain values, which is what
makes the price-lock arithmetic, the clone and the refusals testable without a
database. The engine calls these; the routes call the engine.

The quote's own arithmetic is not re-derived here. A line's amounts and a set of
lines' totals come from :mod:`dsr.quote_authoring.pricing`, so an order line and
the quote line it came from cannot disagree about what a discount or a tax rate
does.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from dsr.order_conversion import vocabulary as vocab
from dsr.order_conversion.errors import OrderConflict, OrderError
from dsr.quote_authoring.pricing import (
    as_number as _as_number,
    as_text as _as_text,
    line_amounts,
    money,
    totals_for,
)

#: Where a unit price came from, mirrored from WF-086's vocabulary so an order
#: line reports its provenance with the same words a quote line does.
PRICE_FROM_DEAL = "deal"
PRICE_FROM_CATALOG = "catalog"
PRICE_FROM_MANUAL = "manual"
PRICE_FROM_PRICE_BOOK = "price_book"
PRICE_SOURCES: tuple[str, ...] = (
    PRICE_FROM_DEAL,
    PRICE_FROM_CATALOG,
    PRICE_FROM_MANUAL,
    PRICE_FROM_PRICE_BOOK,
)

#: The open-ended opportunity states. Any of them means the opportunity is
#: closed and its final revenue is a recorded figure.
CLOSED_OPPORTUNITY_STATES: tuple[str, ...] = ("closed", "won", "lost", "complete")


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def as_text(value: Any, default: str = "") -> str:
    return _as_text(value, default)


def as_number(value: Any, default: float = 0.0) -> float:
    return _as_number(value, default)


def as_bool(value: Any, default: bool = False) -> bool:
    """Read a boolean out of arbitrary JSON.

    The two settings arrive from a form as strings ("true", "on", "1") and from
    a seeded row as booleans, so both spellings are read. An unrecognised value
    reads as the default rather than raising, because one odd setting must not
    make the whole page unreadable.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
    return default


def as_int(value: Any, default: int | None = None) -> int | None:
    number = as_number(value, float("nan"))
    if not math.isfinite(number) or float(number) != int(number):
        return default
    return int(number)


# --------------------------------------------------------------------------- #
# The quote's state
# --------------------------------------------------------------------------- #


def is_activated(quote: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Is the quote frozen, and which marker said so.

    See :data:`dsr.order_conversion.inferences.DERIVED_ACTIVATED` for why frozen
    is the reading of activated.
    """
    data = quote or {}
    status = as_text(data.get("status")).lower()
    if status in vocab.ACTIVATED_QUOTE_STATUSES:
        return True, f"status={status}"
    for flag in vocab.ACTIVATION_FLAGS:
        if as_bool(data.get(flag)):
            return True, flag
    return False, ""


def is_accepted(quote: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Is the quote accepted, and which marker said so.

    See :data:`dsr.order_conversion.inferences.DERIVED_ACCEPTED`.
    """
    data = quote or {}
    if as_text(data.get(vocab.FIELD_HS_STATUS)).upper() == vocab.HS_STATUS_ACCEPTED:
        return True, vocab.FIELD_HS_STATUS
    status = as_text(data.get("status")).lower()
    if status in vocab.ACCEPTANCE_STATUSES:
        return True, f"status={status}"
    for flag in vocab.ACCEPTANCE_FLAGS:
        value = data.get(flag)
        if isinstance(value, bool):
            if value:
                return True, flag
        elif as_text(value):
            return True, flag
    return False, ""


def quote_state(quote: Mapping[str, Any] | None) -> dict[str, Any]:
    """The three facts the conversion reads off a quote, and their markers."""

    activated, activated_by = is_activated(quote)
    accepted, accepted_by = is_accepted(quote)
    return {
        "activated": activated,
        "activated_by": activated_by,
        "accepted": accepted,
        "accepted_by": accepted_by,
        "convertible": activated and accepted,
    }


# --------------------------------------------------------------------------- #
# The price-lock default
# --------------------------------------------------------------------------- #


def price_lock_for(setting: Mapping[str, Any] | None) -> dict[str, Any]:
    """The Prices Locked value the two-branch rule produces.

    "If the integration is disabled, the Prices Locked field is set to Yes. If
    it is enabled, the field is set to the default value specified for the
    ispricelocked column in the Order table." The documented column default is
    No if unchanged, which is why the table default is a setting rather than a
    constant.
    """
    data = setting or {}
    integration = as_bool(data.get(vocab.FIELD_SALES_ORDER_PROCESSING))
    table_default = as_bool(data.get(vocab.FIELD_ISPRICELOCKED_DEFAULT))
    if not integration:
        locked = True
        branch = "integration_disabled"
        reason = "The sales order processing integration is off, so Prices Locked is Yes."
    else:
        locked = table_default
        branch = "table_default"
        reason = (
            "The sales order processing integration is on, so Prices Locked takes the "
            "Order table default."
        )
    return {
        "locked": locked,
        vocab.FIELD_PRICES_LOCKED: "Yes" if locked else "No",
        "branch": branch,
        "integration_enabled": integration,
        "table_default": table_default,
        "reason": reason,
        "quote": vocab.EVIDENCE["lock_default"],
    }


def creation_enabled(setting: Mapping[str, Any] | None) -> bool:
    """Is the admin switch that allows orders from quotes on?"""

    return as_bool((setting or {}).get(vocab.FIELD_CREATE_ORDERS))


def conversion_refusal(
    quote: Mapping[str, Any] | None,
    setting: Mapping[str, Any] | None,
    *,
    already_converted: bool = False,
) -> str | None:
    """Why this quote cannot be converted now, or ``None`` when it can.

    The order of the checks is the order a person meets them: does the quote
    exist, is it activated, is it accepted, is the feature switched on, has it
    already been converted. A caller that gets ``None`` may proceed.
    """
    if quote is None:
        return vocab.REASON_QUOTE_NOT_FOUND
    if not creation_enabled(setting):
        return vocab.REASON_CREATION_DISABLED
    activated, _ = is_activated(quote)
    if not activated:
        return vocab.REASON_QUOTE_NOT_ACTIVATED
    accepted, _ = is_accepted(quote)
    if not accepted:
        return vocab.REASON_QUOTE_NOT_ACCEPTED
    if already_converted:
        return vocab.REASON_ALREADY_CONVERTED
    return None


# --------------------------------------------------------------------------- #
# The clone
# --------------------------------------------------------------------------- #


def read_price_list(quote: Mapping[str, Any] | None) -> str:
    """The quote's price list, under any spelling it may carry."""

    data = quote or {}
    for key in ("price_list", "price_book_id", "price_book", "price_list_id", "pricebook_id"):
        value = as_text(data.get(key))
        if value:
            return value
    return ""


def read_currency(quote: Mapping[str, Any] | None) -> str:
    data = quote or {}
    return (
        as_text(data.get("currency"))
        or as_text(data.get("currency_label"))
        or as_text(data.get("transaction_currency"))
    )


def read_addresses(quote: Mapping[str, Any] | None) -> dict[str, Any]:
    """The quote's addresses, on the order header.

    The research names addresses among the cloned content and never fixes a
    shape, so both the single ``address`` a WF-086 quote carries and the split
    billing and shipping pair a Dynamics quote carries are read.
    """

    data = quote or {}
    addresses: dict[str, Any] = {
        "billing": data.get("billing_address") or data.get("address"),
        "shipping": data.get("shipping_address") or data.get("address"),
        "address": data.get("address"),
    }
    return {key: value for key, value in addresses.items() if value not in (None, "")}


def read_shipping(quote: Mapping[str, Any] | None) -> dict[str, Any]:
    data = quote or {}
    return {
        key: data[key]
        for key in ("shipping_method", "shipping_amount", "shipping", "freight_terms")
        if data.get(key) not in (None, "")
    }


def order_line_from_quote(line: Mapping[str, Any], position: int) -> dict[str, Any]:
    """One quote line as an order line.

    A new record with its own id, carrying the quote line's id as provenance.
    ``price_overridden`` is set when the quote priced the line by hand, because
    that is the price the seller typed and it is the price the research says a
    reprice must never touch.
    """

    price_source = as_text(line.get("price_source"), PRICE_FROM_MANUAL) or PRICE_FROM_MANUAL
    overridden = bool(line.get("price_overridden")) or price_source == PRICE_FROM_MANUAL
    shaped = {
        "name": as_text(line.get("name")),
        "sku": as_text(line.get("sku")),
        "product_id": as_text(line.get("product_id")) or None,
        "description": as_text(line.get("description")),
        "quantity": as_number(line.get("quantity"), 1.0),
        "unit_price": money(line.get("unit_price")),
        "discount_type": as_text(line.get("discount_type")) or None,
        "discount_value": as_number(line.get("discount_value")),
        "tax_rate": as_number(line.get("tax_rate")),
        "price_source": price_source,
        "price_overridden": overridden,
        "tier_label": as_text(line.get("tier_label")) or None,
        "source_quote_line_item_id": as_text(line.get("id")) or None,
        "position": position,
    }
    shaped["amounts"] = line_amounts(shaped)
    return shaped


def order_totals(
    lines: Sequence[Mapping[str, Any]],
    schedule: Sequence[Mapping[str, Any]] = (),
    today: str = "",
) -> dict[str, float]:
    """The order's totals, recomputed from its own lines."""

    return totals_for(lines, schedule, today)


def order_payload(
    quote: Mapping[str, Any],
    quote_id: str,
    quote_lines: Sequence[Mapping[str, Any]],
    *,
    price_lock: Mapping[str, Any],
    won_on: str,
    description: str,
    converted_at: str,
    today: str = "",
) -> dict[str, Any]:
    """The order header: the seven clone components, and the price lock.

    See :data:`dsr.order_conversion.inferences.DERIVED_CLONE_COUNT` for how the
    seven components are counted.
    """

    data = quote or {}
    lines = [order_line_from_quote(line, position) for position, line in enumerate(quote_lines)]
    totals = order_totals(lines, data.get("payment_schedule") or [], today)
    name = as_text(data.get("title")) or as_text(data.get("name")) or f"Order from quote {quote_id}"
    price_list = read_price_list(data)
    currency = read_currency(data)
    return {
        "name": name,
        "description": description,
        "source_quote_id": quote_id,
        "source_quote_title": as_text(data.get("title")),
        "deal_id": data.get("deal_id"),
        "account": as_text(data.get("account")),
        "company_name": as_text(data.get("company_name")) or as_text(data.get("account")),
        "owner": as_text(data.get("owner")),
        "price_list": price_list or None,
        "price_book_id": price_list or None,
        "currency": currency or None,
        "currency_label": currency or None,
        "addresses": read_addresses(data),
        "shipping": read_shipping(data),
        "products": [
            {
                "product_id": line.get("product_id"),
                "sku": line.get("sku"),
                "name": line.get("name"),
            }
            for line in quote_lines
        ],
        "totals": totals,
        "total_contract_value": totals["total_contract_value"],
        "payment_schedule": list(data.get("payment_schedule") or []),
        "source_quote_totals": data.get("totals") or {},
        vocab.FIELD_IS_PRICE_LOCKED: bool(price_lock.get("locked")),
        "price_lock_branch": price_lock.get("branch"),
        "price_lock_reason": price_lock.get("reason"),
        "status": vocab.ORDER_STATUS_OPEN,
        "won_on": won_on or None,
        "converted_at": converted_at,
        "cloned_components": list(vocab.CLONE_COMPONENTS),
    }


# --------------------------------------------------------------------------- #
# The order's state
# --------------------------------------------------------------------------- #


def is_closed(order: Mapping[str, Any] | None) -> bool:
    data = order or {}
    if as_bool(data.get("closed")) or as_bool(data.get("is_closed")):
        return True
    return as_text(data.get("status")).lower() in vocab.CLOSED_ORDER_STATUSES


def is_locked(order: Mapping[str, Any] | None) -> bool:
    return as_bool((order or {}).get(vocab.FIELD_IS_PRICE_LOCKED))


def assert_order_open(order: Mapping[str, Any] | None) -> None:
    """Refuse any write to an order that is closed or fulfilled."""

    if is_closed(order):
        raise OrderConflict(
            vocab.REASON_ORDER_CLOSED,
            "An order that is closed or fulfilled cannot be updated.",
        )


def can_add_write_in(order: Mapping[str, Any] | None) -> bool:
    """Is a write-in product addable? Only while the order is open and unlocked."""

    return not is_closed(order) and not is_locked(order)


def assert_write_in_allowed(order: Mapping[str, Any] | None) -> None:
    if is_closed(order):
        raise OrderConflict(
            vocab.REASON_ORDER_CLOSED,
            "An order that is closed or fulfilled cannot be updated.",
        )
    if is_locked(order):
        raise OrderConflict(
            vocab.REASON_WRITE_IN_NOT_ALLOWED,
            "A write-in product is addable only while Prices Locked is No.",
        )


# --------------------------------------------------------------------------- #
# Repricing
# --------------------------------------------------------------------------- #


def repriced_line(line: Mapping[str, Any], priced: Mapping[str, Any]) -> dict[str, Any]:
    """One line with a new unit price, and its amounts recomputed.

    The caller decides whether the price applies. This function only carries a
    resolved price onto the line.
    """

    updated = dict(line)
    updated["unit_price"] = money(priced.get("unit_price"))
    if priced.get("source"):
        updated["price_source"] = as_text(priced.get("source"))
    updated["tier_label"] = priced.get("tier_label")
    updated["amounts"] = line_amounts(updated)
    return updated


def reprice_plan(
    order: Mapping[str, Any] | None,
    lines: Sequence[Mapping[str, Any]],
    prices: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """What a reprice would do, line by line, without writing anything.

    ``prices`` maps an order line id to the resolved price for it. The plan is a
    pure computation so the engine and a test share one definition of "a locked
    order is not repriced" and "an overridden line is never touched".
    """

    locked = is_locked(order)
    closed = is_closed(order)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for line in lines:
        line_id = as_text(line.get("id"))
        if line.get("price_overridden"):
            skipped.append({"line_id": line_id, "reason": "price_overridden"})
            continue
        if closed:
            skipped.append({"line_id": line_id, "reason": vocab.REASON_ORDER_CLOSED})
            continue
        if locked:
            skipped.append({"line_id": line_id, "reason": vocab.REASON_PRICES_LOCKED})
            continue
        priced = prices.get(line_id)
        if not priced or as_text(priced.get("source")) == "missing":
            skipped.append({"line_id": line_id, "reason": "no_price_found"})
            continue
        applied.append(repriced_line(line, priced))
    return {
        "locked": locked,
        "closed": closed,
        "applied": applied,
        "skipped": skipped,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "reason": (
            vocab.REASON_ORDER_CLOSED
            if closed
            else (vocab.REASON_PRICES_LOCKED if locked else None)
        ),
    }


# --------------------------------------------------------------------------- #
# Fulfilment and Get Products
# --------------------------------------------------------------------------- #


def close_payload(
    order: Mapping[str, Any] | None,
    *,
    status: str,
    reason: str,
    closed_at: str,
    invoice_expected: bool = True,
) -> dict[str, Any]:
    """The orderclose record and the header patch for a fulfilment.

    Fulfilment and cancellation are the two closes the research names for an
    order; invoicing is a separate workflow and is recorded as expected rather
    than created here.
    """

    if status not in (vocab.ORDER_STATUS_FULFILLED, vocab.ORDER_STATUS_CANCELLED):
        raise OrderError(
            "An order closes as fulfilled or cancelled.",
            {"status": "Use fulfilled or cancelled."},
        )
    data = order or {}
    return {
        "source_quote_id": as_text(data.get("source_quote_id")) or None,
        "status": status,
        "reason": reason,
        "closed_at": closed_at,
        "invoice_expected": bool(invoice_expected),
        "total": (data.get("totals") or {}).get("total"),
        "patch": {
            "status": status,
            "closed_reason": reason,
            "closed_at": closed_at,
            "invoice_expected": bool(invoice_expected),
        },
    }


def opportunity_is_closed(opportunity: Mapping[str, Any] | None) -> bool:
    data = opportunity or {}
    if as_bool(data.get("closed")) or as_bool(data.get("is_closed")):
        return True
    state = as_text(data.get("state") or data.get("status")).lower()
    return state in CLOSED_OPPORTUNITY_STATES


def final_revenue(opportunity: Mapping[str, Any] | None) -> float | None:
    """The opportunity's final revenue, when it names one."""

    data = opportunity or {}
    for key in ("final_revenue", "revenue", "amount"):
        if data.get(key) not in (None, ""):
            return money(data.get(key))
    return None


def opportunity_lines(opportunity: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The products an opportunity carries onto an order.

    Get Products copies products from an opportunity, and the research never
    fixes where the opportunity keeps them, so both a line item list and a
    product list are read.
    """

    data = opportunity or {}
    raw = data.get("line_items") or data.get("products") or []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def write_in_line(payload: Mapping[str, Any], position: int) -> dict[str, Any]:
    """A write-in product as an order line.

    A write-in has no catalogue behind it, so its price is typed and it is
    marked as overridden: an override survives every reprice, which is the other
    half of the rule that a write-in is only addable while prices are unlocked.
    """

    name = as_text(payload.get("name"))
    if not name:
        raise OrderError("A line needs a name.", {"name": "Enter a name for the line."})
    quantity = as_number(payload.get("quantity"), 1.0)
    if quantity < 0:
        raise OrderError("Quantity cannot be negative.", {"quantity": "Use 0 or more."})
    unit_price = as_number(payload.get("unit_price"), 0.0)
    if unit_price < 0:
        raise OrderError("Unit price cannot be negative.", {"unit_price": "Use 0 or more."})
    shaped = {
        "name": name,
        "sku": as_text(payload.get("sku")),
        "product_id": None,
        "description": as_text(payload.get("description")),
        "quantity": quantity,
        "unit_price": money(unit_price),
        "discount_type": as_text(payload.get("discount_type")) or None,
        "discount_value": as_number(payload.get("discount_value")),
        "tax_rate": as_number(payload.get("tax_rate")),
        "price_source": PRICE_FROM_MANUAL,
        "price_overridden": True,
        "tier_label": None,
        "source_quote_line_item_id": None,
        "position": position,
        "write_in": True,
    }
    shaped["amounts"] = line_amounts(shaped)
    return shaped
