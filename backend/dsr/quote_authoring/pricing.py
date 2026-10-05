"""The price arithmetic, and nothing else.

Pure functions over plain values. No store, no HTTP, no clock of its own: a
caller passes ``today`` when it needs the date. That is what makes every figure
here testable without a database, and it is why this file can be read on its
own to answer "what is a total, exactly".

The totals contract was put to Jev and answered
(``tax_on_net``, audit ``jev-20261004T181435-29468-75674``, confidence 0.87).
The short form:

* tax is computed on the **net** line amount, so a discount reduces the tax as
  well as the subtotal. Taxing the gross was the rejected alternative, and it
  overstates the tax a buyer owes whenever a discount is applied.
* the **total contract value** is the total plus the payments dated after today,
  and it is the only figure written back to the deal amount.
* every figure is rounded half up to two decimals, once at the line and once at
  the total, so the sum of the printed lines equals the printed total.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from dsr.quote_authoring.vocabulary import (
    DISCOUNT_CURRENCY,
    DISCOUNT_PERCENTAGE,
)

#: Every money figure this product stores is rounded to this many places.
MONEY_PLACES = Decimal("0.01")

_CENTS = Decimal("0.01")


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def as_number(value: Any, default: float = 0.0) -> float:
    """Read a number out of arbitrary JSON, or fall back.

    A quote is assembled from a deal mirror, a price book and a typed form, so a
    field arrives as an int, a float, a ``Decimal`` or a string a person typed.
    Anything that is not a finite number reads as the default rather than
    raising, because a malformed optional field must not make the whole quote
    unreadable.

    ``Decimal`` is in the numeric branch rather than coerced through ``float``
    first. It was missing, and the consequence was quiet: ``as_number("4200")``
    parsed the string into a ``Decimal``, handed the ``Decimal`` back to this
    function, hit the fall-through, and returned 0.0. A unit price a seller typed
    as a string priced the whole quote at zero.
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else default
    if isinstance(value, str):
        try:
            return as_number(Decimal(value.strip()), default)
        except (InvalidOperation, ValueError, ArithmeticError):
            return default
    return default


def as_text(value: Any, default: str = "") -> str:
    """Read a trimmed string out of arbitrary JSON."""
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def money(value: Any) -> float:
    """Round to two decimals, half up.

    ``Decimal`` rather than ``round()`` because binary floats make half-up
    impossible to state exactly: ``round(2.675, 2)`` is 2.67, and a quote that
    is off by a cent per line is off by a pound on a hundred-line quote.
    """
    try:
        decimal = Decimal(str(as_number(value)))
    except (InvalidOperation, ValueError):
        decimal = Decimal("0")
    return float(decimal.quantize(_CENTS, rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# Tiers
# --------------------------------------------------------------------------- #


def _tier_rows(product: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The price tiers on a catalogue product, lowest threshold first.

    A tier is a mapping with ``min_qty`` and a ``unit_price``. The field names
    are synonyms because WF-087 has not shipped: a catalogue written against a
    different spelling still resolves, and one written against none of them
    simply has no tiers and falls back to its own unit price.
    """
    if not product:
        return []
    raw = product.get("tiers") or product.get("price_tiers") or product.get("quantity_tiers") or []
    rows: list[tuple[float, float]] = []
    for tier in raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []:
        if not isinstance(tier, Mapping):
            continue
        threshold = tier.get("min_qty")
        if threshold is None:
            threshold = tier.get("min_quantity")
        if threshold is None:
            threshold = tier.get("from")
        price = tier.get("unit_price")
        if price is None:
            price = tier.get("price")
        if price is None:
            price = tier.get("amount")
        if threshold is None or price is None:
            continue
        rows.append((as_number(threshold), as_number(price)))
    return [{"min_qty": threshold, "unit_price": price} for threshold, price in sorted(rows)]


def resolve_unit_price(
    product: Mapping[str, Any] | None,
    quantity: float,
) -> tuple[float, str | None, str]:
    """The unit price for a quantity, plus the tier label and where it came from.

    The research says pricing "recalculates automatically whenever a line item
    quantity changes (tier boundaries re-evaluate)", so the tier is a function
    of the quantity and not a price the seller typed once. The highest tier whose
    ``min_qty`` the quantity reaches wins, which is the only ordering under
    which a quantity-based boundary means anything.

    A product with no tiers, or no product at all, resolves to a price of zero
    and the source ``missing``. The caller decides what to do about it, and the
    response says so rather than the arithmetic raising: a catalogue that has
    not been provisioned is WF-087's absence, not a broken quote.
    """
    qty = as_number(quantity)
    if not product:
        return 0.0, None, "missing"

    tiers = _tier_rows(product)
    if tiers:
        chosen = tiers[0]
        for tier in tiers:
            if qty >= tier["min_qty"]:
                chosen = tier
            else:
                break
        return money(chosen["unit_price"]), f"from {chosen['min_qty']:g}", "tier"

    price = product.get("unit_price")
    if price is None:
        price = product.get("price")
    if price is None:
        return 0.0, None, "missing"
    return money(price), "list", "list"


# --------------------------------------------------------------------------- #
# One line
# --------------------------------------------------------------------------- #


def line_amounts(line: Mapping[str, Any]) -> dict[str, float]:
    """Every figure one line contributes, at that line's own rounding.

    ``discount_value`` is read against ``discount_type``: a percentage is a
    share of the line subtotal, a currency amount is taken off it directly. A
    percentage above 100 is clamped to 100 rather than producing a negative
    line, because a negative total is a figure no buyer can be shown.
    """
    quantity = as_number(line.get("quantity"), 0.0)
    unit_price = as_number(line.get("unit_price"), 0.0)
    subtotal = money(quantity * unit_price)

    discount_type = as_text(line.get("discount_type"), DISCOUNT_PERCENTAGE)
    raw_discount = as_number(line.get("discount_value"), 0.0)
    if discount_type == DISCOUNT_CURRENCY:
        discount = money(raw_discount)
        discount = min(discount, subtotal) if subtotal >= 0 else discount
    else:
        percent = min(max(raw_discount, 0.0), 100.0)
        discount = money(subtotal * percent / 100.0)

    net = money(subtotal - discount)
    tax_rate = as_number(line.get("tax_rate"), 0.0)
    tax = money(net * tax_rate / 100.0)
    return {
        "quantity": quantity,
        "unit_price": money(unit_price),
        "subtotal": subtotal,
        "discount": discount,
        "net": net,
        "tax": tax,
        "total": money(net + tax),
    }


# --------------------------------------------------------------------------- #
# The quote
# --------------------------------------------------------------------------- #


def future_payments(schedule: Iterable[Any], today: str) -> float:
    """The sum of scheduled payments dated after ``today``.

    A payment dated today is not a future payment. It falls due today, which is
    the present, and counting it as future would tell a seller the contract
    value includes money the buyer already owes.
    """
    total = 0.0
    for entry in schedule or []:
        if not isinstance(entry, Mapping):
            continue
        due = as_text(entry.get("due_on") or entry.get("due") or entry.get("date"))
        if not due or due <= today:
            continue
        total += as_number(entry.get("amount"))
    return money(total)


def totals_for(
    lines: Sequence[Mapping[str, Any]],
    schedule: Iterable[Any] = (),
    today: str = "",
) -> dict[str, float]:
    """The quote's totals, in the order the research lists them.

    Lines are summed as their own rounded figures rather than as one running
    float. The alternative accumulates the raw products and rounds once at the
    end, which is cheaper and produces a total that does not equal the sum of
    the printed lines. A buyer who adds up the column must get the total.
    """
    subtotal = 0.0
    discount = 0.0
    tax = 0.0
    for line in lines or []:
        amounts = line_amounts(line)
        subtotal += amounts["subtotal"]
        discount += amounts["discount"]
        tax += amounts["tax"]

    subtotal = money(subtotal)
    discount = money(discount)
    tax = money(tax)
    total = money(subtotal - discount + tax)
    future = future_payments(schedule, today)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "tax": tax,
        "total": total,
        "future_payments": future,
        "total_contract_value": money(total + future),
    }
