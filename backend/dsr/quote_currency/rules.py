"""The rules: money, the rate, the pricing refusal, and the currency-in-place constraint.

Everything here is a rule, not a framework. It reads a mapping and returns a mapping, raises
one of the four errors in :mod:`dsr.quote_currency.errors`, and knows nothing about HTTP,
SQLite or a request. That is what makes every rule below testable without a server, and it
is why the HTTP layer in ``dsr.features.wf089_quote_in_a_transaction_currency_with_fx`` is
a thin translation of what happens here.

Why Decimal rather than float
----------------------------

A money figure is an amount of a currency. ``0.1 + 0.2`` is ``0.30000000000000004`` in binary
floating point, so a quote that totals three line items would carry a figure no currency
could be paid in, and the ``_Base`` figure derived from it would carry the same error one
step removed. :data:`MONEY` is :class:`~decimal.Decimal` and every money value that enters
or leaves this module is quantised to the currency's own precision first. Float is used only
where the research names a float-shaped field: the exchange rate, which is a ratio and not
an amount.

The direction of the rate
-------------------------

``ExchangeRate`` "is used to convert all money fields in the record from the local currency to
the system's default currency", so the rate is **base per one unit of transaction currency**.
:func:`to_base` is the only place that multiplication happens and it reads in that direction;
:func:`to_transaction` is its inverse and exists so a caller can check the round trip.

Which figure is authoritative
-----------------------------

The transaction-currency figure is. :func:`price_quote` computes those first and derives
every ``_Base`` figure from them by :func:`to_base`, and nothing in this module reads a
``_Base`` value as an input. The reasoning is in
:data:`~dsr.quote_currency.vocabulary.AUTHORITATIVE_REASON`: the research computes totals in
the transaction currency and stores ``*_Base`` "for reporting".

The refusal is an outcome, not an exception
-------------------------------------------

:func:`price_quote` does not raise when the price list currency differs from the header
currency, or when a line item has no price in the transaction currency. It returns a
:func:`Refusal`, carrying one of the two codes the research names. Both refusals are total:
a run that refuses writes no money figure at all, so a caller cannot mistake a partial total
for a priced quote. :func:`Refusal.code_label` returns the specification's own wording.

Why a line item's unit price cannot be supplied by the caller
-------------------------------------------------------------

The research says unit prices "resolve from the ``pricelevelproduct`` row in that currency".
A caller-supplied ``unit_price`` on a line would therefore be a second, unaudited price
source, and a quote whose total disagreed with its price list would have no way to explain
itself. :func:`resolve_unit_price` reads the price row and refuses to take one from the
payload; the rejection is a :class:`~dsr.quote_currency.errors.CurrencyRefusal` naming the
field, so a caller that sends one learns the rule rather than silently getting the value it
sent echoed back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from dsr.quote_currency import vocabulary as vocab
from dsr.quote_currency.errors import CurrencyRefusal

#: Money is decimal. See the module docstring for why float is not used for an amount.
MONEY = Decimal

#: The unit price of nothing, and the discount of nothing. A quoted line always has both.
ZERO = MONEY("0")

#: A rate is a ratio, not an amount, so it keeps the research's float shape and is never
#: quantised to a currency's precision. A rate of 1.0837 is meaningful and rounding it to
#: two places would move every converted figure on the quote.
RATE = float

#: A rate at or below this is refused. A negative rate inverts a sign and a zero rate makes
#: every base figure zero, and neither is a conversion.
MINIMUM_RATE = 1e-9


# --------------------------------------------------------------------------- #
# Parsing and validating
# --------------------------------------------------------------------------- #


def as_money(value: Any, precision: int, field_name: str) -> MONEY:
    """Read one money value and quantise it to the currency's precision.

    ``quantise`` rather than ``round``: the research's currency carries a
    ``CurrencyPrecision``, so the number of decimal places is the currency's own and not this
    module's to choose. Rounding is half-up, which is the commercial convention a quote is
    read under, and half-up is named rather than inherited so a change to it is visible.
    """

    if value is None or value == "":
        return quantise(ZERO, precision)
    if isinstance(value, bool):
        raise CurrencyRefusal(
            f"{field_name} must be a number.",
            {field_name: f"{field_name} must be a number, not a boolean."},
        )
    try:
        amount = MONEY(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CurrencyRefusal(
            f"{field_name} must be a number.",
            {field_name: f"{field_name} must be a number, not {value!r}."},
        ) from exc
    if not amount.is_finite():
        raise CurrencyRefusal(
            f"{field_name} must be a finite amount.",
            {field_name: f"{field_name} must be a finite amount."},
        )
    return quantise(amount, precision)


def quantise(amount: MONEY, precision: int) -> MONEY:
    """Round an amount to a number of decimal places, half-up."""

    return amount.quantize(Decimal(1).scaleb(-int(precision)), rounding=ROUND_HALF_UP)


def as_rate(value: Any, field_name: str = "exchange_rate") -> RATE:
    """Read an exchange rate and refuse anything that is not a usable ratio."""

    if isinstance(value, bool) or value is None or value == "":
        raise CurrencyRefusal(
            f"{field_name} is required.",
            {field_name: f"{field_name} is required."},
        )
    try:
        rate = RATE(value)
    except (TypeError, ValueError) as exc:
        raise CurrencyRefusal(
            f"{field_name} must be a number.",
            {field_name: f"{field_name} must be a number, not {value!r}."},
        ) from exc
    if rate != rate or rate in (float("inf"), float("-inf")):
        raise CurrencyRefusal(
            f"{field_name} must be a finite number.",
            {field_name: f"{field_name} must be a finite number."},
        )
    if rate < MINIMUM_RATE:
        raise CurrencyRefusal(
            f"{field_name} must be greater than zero.",
            {field_name: f"{field_name} must be greater than zero, not {rate}."},
        )
    return rate


def validate_precision(value: Any, field_name: str = "currency_precision") -> int:
    """Read a ``CurrencyPrecision`` and hold it inside the derived bound."""

    if value is None or value == "":
        return vocab.DEFAULT_CURRENCY_PRECISION
    if isinstance(value, bool):
        raise CurrencyRefusal(
            f"{field_name} must be a whole number.",
            {field_name: f"{field_name} must be a whole number."},
        )
    try:
        precision = int(value)
    except (TypeError, ValueError) as exc:
        raise CurrencyRefusal(
            f"{field_name} must be a whole number.",
            {field_name: f"{field_name} must be a whole number, not {value!r}."},
        ) from exc
    if not vocab.CURRENCY_PRECISION_MIN <= precision <= vocab.CURRENCY_PRECISION_MAX:
        raise CurrencyRefusal(
            f"{field_name} must be between {vocab.CURRENCY_PRECISION_MIN} "
            f"and {vocab.CURRENCY_PRECISION_MAX}.",
            {
                field_name: (
                    f"{field_name} must be between {vocab.CURRENCY_PRECISION_MIN} and "
                    f"{vocab.CURRENCY_PRECISION_MAX} decimal places, not {precision}."
                )
            },
        )
    return precision


def normalise_iso_code(value: Any, field_name: str = "iso_code") -> str:
    """Read an ISO currency code and hold it to the three letters the research names.

    ``ISOCurrencyCode`` is a three-letter code in every source, so the length is enforced
    rather than trimmed to fit. Upper-cased because ``usd`` and ``USD`` are one currency and
    two spellings would give a deployment two price lists for one currency.
    """

    code = str(value or "").strip().upper()
    if not code:
        raise CurrencyRefusal(
            "An ISO currency code is required.",
            {field_name: "An ISO currency code is required."},
        )
    if len(code) != 3 or not code.isalpha():
        raise CurrencyRefusal(
            f"{field_name} must be three letters.",
            {field_name: f"{field_name} must be a three-letter ISO code, not {value!r}."},
        )
    return code


def normalise_currency_type(value: Any, field_name: str = "currency_type") -> str:
    """Read a ``CurrencyType`` and hold it to the two the research names."""

    if value is None or value == "":
        return vocab.CURRENCY_TYPE_STANDARD
    candidate = str(value).strip()
    for known in vocab.CURRENCY_TYPES:
        if candidate.lower() == known.lower():
            return known
    raise CurrencyRefusal(
        f"{field_name} must be one of {', '.join(vocab.CURRENCY_TYPES)}.",
        {field_name: f"{field_name} must be one of {', '.join(vocab.CURRENCY_TYPES)}."},
    )


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


def to_base(amount: MONEY, rate: RATE, precision: int) -> MONEY:
    """Convert an amount in the transaction currency into the base currency.

    ``amount * rate``, because the research defines the rate as base per one unit of
    transaction currency: "the exchange rate is used to convert all money fields in the
    record from the local currency to the system's default currency". The multiplication
    happens on :class:`~decimal.Decimal` by converting the rate through its string form, so a
    float rate cannot inject its representation error into a money figure.
    """

    return quantise(amount * MONEY(repr(rate)), precision)


def to_transaction(amount: MONEY, rate: RATE, precision: int) -> MONEY:
    """The inverse of :func:`to_base`, so a caller can check the round trip."""

    return quantise(amount / MONEY(repr(rate)), precision)


# --------------------------------------------------------------------------- #
# The rate, resolved from the currency record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RateResolution:
    """One resolved rate, with where it came from."""

    rate: RATE
    source: str
    iso_code: str
    currency_type: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": self.rate,
            "source": self.source,
            "iso_code": self.iso_code,
            "currency_type": self.currency_type,
            "note": self.note,
        }


def resolve_rate(
    currency: Mapping[str, Any] | None,
    *,
    transaction_iso_code: str | None = None,
    base_iso_code: str | None = None,
) -> RateResolution:
    """Resolve the rate for one quote header from the currency records.

    Three answers, in this order, and each one is a different fact rather than a preference:

    * A quote in the base currency has nothing to convert. It still gets the identity rate so
      every quote carries a rate and no caller has to special-case the base currency.
    * A ``Custom`` currency record carries the rate a deployment stamped on it. That record
      exists for this: "custom currency records (``CurrencyType: Custom``)" and
      "``exchangerate`` is writable so a custom rate can be stamped".
    * A ``Standard`` record carries the platform's rate.

    A quote whose transaction currency has no currency record at all is refused by the caller
    in :mod:`dsr.quote_currency.engine`, because "no record" is a different failure from "a
    record with no rate" and it deserves a different message.
    """

    if currency is None:
        raise CurrencyRefusal(
            "The transaction currency has no currency record.",
            {
                "transactioncurrencyid": (
                    f"no currency record is registered for {transaction_iso_code!r}, so no rate "
                    "can be resolved."
                )
            },
        )
    iso_code = str(currency.get("iso_code") or transaction_iso_code or "").upper()
    currency_type = normalise_currency_type(currency.get("currency_type"))
    base = str(base_iso_code or "").upper()

    if base and iso_code == base:
        return RateResolution(
            rate=vocab.IDENTITY_RATE,
            source=vocab.RATE_SOURCE_IDENTITY,
            iso_code=iso_code,
            currency_type=currency_type,
            note=(
                "The quote is in the organisation base currency, so nothing is converted and "
                "the rate is one."
            ),
        )

    raw_rate = currency.get("exchange_rate")
    if raw_rate in (None, ""):
        raise CurrencyRefusal(
            "The currency record carries no exchange rate.",
            {
                "exchange_rate": (
                    f"currency record {iso_code!r} has no exchange_rate, so no base-currency "
                    "figure can be computed."
                )
            },
        )
    rate = as_rate(raw_rate, "exchange_rate")
    source = (
        vocab.RATE_SOURCE_CUSTOM
        if currency_type == vocab.CURRENCY_TYPE_CUSTOM
        else vocab.RATE_SOURCE_STANDARD
    )
    note = (
        "The rate was stamped on a Custom currency record, which is the one record type the "
        "research says a deployment may write."
        if source == vocab.RATE_SOURCE_CUSTOM
        else "The rate came from the currency record the platform maintains."
    )
    return RateResolution(
        rate=rate, source=source, iso_code=iso_code, currency_type=currency_type, note=note
    )


# --------------------------------------------------------------------------- #
# Unit price resolution
# --------------------------------------------------------------------------- #


def resolve_unit_price(
    line: Mapping[str, Any],
    price_items: Sequence[Mapping[str, Any]],
    *,
    price_list_id: str,
) -> MONEY:
    """The unit price for one line, read from the price row in the transaction currency.

    A caller-supplied ``unit_price`` is refused rather than used. The research says unit
    prices "resolve from the ``pricelevelproduct`` row in that currency", so a price on the
    payload would be a second price source the audit log does not explain.

    When the product is on the price list but the price row names a different currency, the
    research's code 38 applies and the caller turns that into a refusal. This function
    therefore raises :class:`~dsr.quote_currency.errors.CurrencyRefusal` for a missing price
    row too, and the engine catches it to build the refusal rather than letting it reach HTTP.
    """

    if "unit_price" in line and line.get("unit_price") not in (None, ""):
        raise CurrencyRefusal(
            "A line item may not carry its own unit price.",
            {
                "unit_price": (
                    "unit_price is resolved from the price list row in the transaction "
                    "currency, so it is not accepted from the caller."
                )
            },
        )

    product = str(line.get("product_code") or "").strip()
    if not product:
        raise CurrencyRefusal(
            "A line item needs a product code.",
            {"product_code": "A line item needs a product code."},
        )

    for item in price_items:
        if str(item.get("product_code") or "").strip() != product:
            continue
        if str(item.get("price_list_id") or "").strip() != str(price_list_id or "").strip():
            continue
        return as_money(item.get("unit_price"), 6, "unit_price")
    raise CurrencyRefusal(
        f"No price for {product!r} on this price list.",
        {
            "product_code": (
                f"product {product!r} has no price row on price list {price_list_id!r} in the "
                "transaction currency."
            )
        },
    )


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Refusal:
    """A pricing run that produced no totals, with the code that says why."""

    code: str
    detail: str
    subject: str | None = None
    expected: str | None = None
    found: str | None = None
    quote_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        return vocab.OUTCOME_REFUSED

    @property
    def code_label(self) -> str:
        """The specification's own wording for this code, never a paraphrase."""

        return vocab.PRICING_ERROR_CODES.get(self.code, "Unknown pricing error")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "outcome": self.outcome,
            "pricing_error_code": self.code,
            "pricing_error": self.code_label,
            "detail": self.detail,
            "authority": vocab.REFUSAL_IS_OUTCOME_NOT_ERROR,
        }
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.expected is not None:
            payload["expected"] = self.expected
        if self.found is not None:
            payload["found"] = self.found
        if self.quote_id is not None:
            payload["quote_id"] = self.quote_id
        if self.extra:
            payload.update(self.extra)
        return payload


def refuse_price_list_currency(
    *,
    header_iso_code: str,
    price_list_iso_code: str,
    price_list_id: str,
    quote_id: str | None = None,
) -> Refusal:
    """Code 34: the price list is in a different currency from the quote header."""

    return Refusal(
        code=vocab.CODE_PRICE_LIST_CURRENCY_MISMATCH,
        detail=(
            f"The price list is in {price_list_iso_code!r} but the quote is in "
            f"{header_iso_code!r}. The base record and all its line items must use the same "
            "currency."
        ),
        subject=price_list_id,
        expected=header_iso_code,
        found=price_list_iso_code,
        quote_id=quote_id,
    )


def refuse_missing_transaction_currency_price(
    *,
    product_code: str,
    price_list_id: str,
    quote_id: str | None = None,
) -> Refusal:
    """Code 38: a line item's product has no price in the transaction currency."""

    return Refusal(
        code=vocab.CODE_LINE_ITEM_HAS_NO_PRICE,
        detail=(
            f"product {product_code!r} has no price on price list {price_list_id!r} in the "
            "transaction currency, so the quote cannot be priced."
        ),
        subject=product_code,
        expected="a price row for this product in the transaction currency",
        found="no price row",
        quote_id=quote_id,
    )


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PricedLine:
    """One priced line, in the transaction currency and in the base currency."""

    product_code: str
    quantity: MONEY
    unit_price: MONEY
    line_total: MONEY
    discount_amount: MONEY
    tax_amount: MONEY
    line_base: MONEY
    discount_base: MONEY
    tax_base: MONEY

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_code": self.product_code,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "line_total": str(self.line_total),
            "discount_amount": str(self.discount_amount),
            "tax_amount": str(self.tax_amount),
            "line_total_base": str(self.line_base),
            "discount_amount_base": str(self.discount_base),
            "tax_amount_base": str(self.tax_base),
        }


def price_quote(
    lines: Sequence[Mapping[str, Any]],
    price_items: Sequence[Mapping[str, Any]],
    *,
    quote_id: str,
    price_list_id: str,
    price_list_iso_code: str,
    header_iso_code: str,
    base_iso_code: str,
    base_precision: int,
    rate: RATE,
    precision: int,
    discount: MONEY = ZERO,
    tax: MONEY = ZERO,
    freight: MONEY = ZERO,
) -> dict[str, Any]:
    """Price one quote in the transaction currency, then derive the base figures.

    The order is the research's order and it is load-bearing: the transaction-currency totals
    are computed first and every ``_Base`` figure is derived from them. Nothing here reads a
    base figure as an input, which is what makes the transaction figure authoritative.

    Returns either ``{"outcome": "refused", ...}`` carrying one of the two researched codes,
    or ``{"outcome": "priced", "lines": [...], "totals": {...}, "totals_base": {...}}``. There
    is no third shape, so a caller cannot receive a partial total that looks priced.
    """

    if price_list_iso_code and header_iso_code and price_list_iso_code != header_iso_code:
        return refuse_price_list_currency(
            header_iso_code=header_iso_code,
            price_list_iso_code=price_list_iso_code,
            price_list_id=price_list_id,
            quote_id=quote_id,
        ).to_dict()

    priced: list[PricedLine] = []
    for line in lines:
        try:
            unit_price = resolve_unit_price(line, price_items, price_list_id=price_list_id)
        except CurrencyRefusal:
            # Any refusal here means the price row could not be resolved for this line, and
            # that is code 38 by definition: the product has no price in the transaction
            # currency. The specific message is for a caller of the rule directly; the
            # pricing run reports the code and the product.
            product = str(line.get("product_code") or "").strip()
            return refuse_missing_transaction_currency_price(
                product_code=product or "(unnamed)",
                price_list_id=price_list_id,
                quote_id=quote_id,
            ).to_dict()
        quantity = as_money(line.get("quantity", 1), 6, "quantity")
        line_discount = as_money(line.get("discount_amount"), precision, "discount_amount")
        line_tax = as_money(line.get("tax_amount"), precision, "tax_amount")
        gross = quantise(quantity * unit_price, precision)
        net = quantise(gross - line_discount, precision)
        priced.append(
            PricedLine(
                product_code=str(line.get("product_code") or "").strip(),
                quantity=quantity,
                unit_price=quantise(unit_price, precision),
                line_total=net,
                discount_amount=line_discount,
                tax_amount=line_tax,
                line_base=to_base(net, rate, base_precision),
                discount_base=to_base(line_discount, rate, base_precision),
                tax_base=to_base(line_tax, rate, base_precision),
            )
        )

    header_discount = as_money(discount, precision, "discount")
    header_tax = as_money(tax, precision, "tax")
    freight_amount = as_money(freight, precision, "freight_amount")

    line_amount = sum((row.line_total for row in priced), ZERO)
    discount_amount = sum((row.discount_amount for row in priced), ZERO) + header_discount
    tax_amount = sum((row.tax_amount for row in priced), ZERO) + header_tax
    total = quantise(line_amount + tax_amount + freight_amount, precision)

    totals = {
        "totallineitemamount": str(quantise(line_amount, precision)),
        "totaldiscountamount": str(quantise(discount_amount, precision)),
        "totaltax": str(quantise(tax_amount, precision)),
        "freightamount": str(freight_amount),
        "totalamount": str(total),
    }
    totals_base = {
        f"{name}{vocab.BASE_SUFFIX}": str(to_base(MONEY(value), rate, base_precision))
        for name, value in totals.items()
    }
    return {
        "outcome": vocab.OUTCOME_PRICED,
        "pricing_error_code": None,
        "lines": [row.to_dict() for row in priced],
        "totals": totals,
        "totals_base": totals_base,
        "authoritative": vocab.AUTHORITATIVE,
        "authoritative_reason": vocab.AUTHORITATIVE_REASON,
    }
