"""The pricing arithmetic, and nothing else.

Pure functions over plain values. No store, no HTTP, no clock of its own. That is
what makes every figure here testable without a database, and it is why this file
can be read on its own to answer "what does a quantity of twelve cost".

Where the arithmetic comes from
-------------------------------

The research names three pricing methods and quotes one sentence for each, and
quotes no formula. Each formula below is derived from its own sentence, and the
derivation was put to Jev as a choice and selected at confidence 1.00, audit
``jev-20261013T111921-7676-61571``:

* ``volume_based`` -- "the entire line item is priced based on the total quantity
  bought". Every unit is priced at the one rate the total quantity reaches, so
  the line is quantity times that rate.
* ``graduated`` -- "units are priced per tier". Each unit is priced at the rate
  of the range its own position falls in, so the line is one figure per range.
* ``stair_step`` -- "one flat fee is charged based on the quantity range". The
  range selects a fee and the fee is charged once, not multiplied by quantity.

Volume-based and graduated differ only in whether the units inside a range pay
that range's rate or the highest rate reached. Stair-step differs from both
because its figure does not scale with quantity at all. Each quoted sentence
describes exactly one of the three.

Tier maximums
-------------

The research says "per tier enter minimum units (maximum auto-computes; infinity
is the maximum value and creates an open-ended final tier)" and does not say how
the maximum is computed. The rule chosen here: a tier's maximum is the next
tier's minimum minus one, and the final tier has no maximum. Same decision,
confidence 1.00, audit ``jev-20261013T111921-7676-61571``.

The maximum is derived and never stored as a second authored number. Two authored
numbers can disagree, and when they do the range a quantity falls in is
ambiguous: a buyer who orders 24 is charged at one price or another depending on
which of two numbers is trusted. Deriving it means the two cannot disagree.

The corollary is that a quantity below the first tier's minimum prices at the
first tier's rate. A quantity of one on a table whose first tier starts at ten
has to cost something, and charging nothing because no range matched would be a
silent zero.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from dsr.price_books import vocabulary as vocab
from dsr.price_books.errors import refuse

#: Two decimal places. Money in a quote is money to the cent, and a total that
#: does not equal the sum of its printed lines is a bug a buyer finds.
_CENTS = Decimal("0.01")


def as_number(value: Any, default: float = 0.0) -> float:
    """Read a number out of arbitrary JSON, or fall back.

    A product is assembled from a typed form, a CRM mirror and a seeded row, so a
    field arrives as an int, a float or a string a person typed. Anything that is
    not a finite number reads as the default rather than raising, because a
    malformed optional field must not make the whole catalogue unreadable.

    A boolean is not a number here even though Python says ``True == 1``. A
    product whose active flag was read into a price field would price at one unit
    of currency, and the response would look correct.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else default
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return default
        try:
            number = float(Decimal(text))
        except (InvalidOperation, ValueError):
            return default
        return number if math.isfinite(number) else default
    return default


def money(value: Any) -> float:
    """Round a figure to cents, half up.

    ``Decimal`` rather than ``round`` because ``round`` uses banker's rounding, so
    two identical lines can total differently depending on the order they were
    summed in. A column a buyer adds up by hand has to equal the total.
    """
    try:
        decimal = Decimal(str(as_number(value)))
    except (InvalidOperation, ValueError):
        decimal = Decimal("0")
    return float(decimal.quantize(_CENTS, rounding=ROUND_HALF_UP))


def require_money(value: Any, field: str) -> float:
    """Read a price that must be a real, finite, non-negative number.

    ``as_number`` is the forgiving reader for display. This is the strict one for
    input, and the difference is why a product cannot be saved with a price of
    "-5" and discovered later that it undercharges every quote it appears on.
    """
    if isinstance(value, bool):
        raise refuse("price_is_not_a_number", **{field: "A price must be a number."})
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            raise refuse("price_is_not_a_number", **{field: "Give the price a number."})
        try:
            number = float(Decimal(text))
        except (InvalidOperation, ValueError):
            raise refuse("price_is_not_a_number", **{field: "That is not a number."}) from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise refuse("price_is_not_a_number", **{field: "A price must be a number."})

    if not math.isfinite(number):
        raise refuse("price_is_not_finite", **{field: "A price must be a finite number."})
    if number < 0:
        raise refuse("price_is_negative", **{field: "A price cannot be negative."})
    return money(number)


def require_quantity(value: Any, field: str = "quantity") -> float:
    """Read a quantity that must be a positive number of units."""
    number = as_number(value, float("nan"))
    if not math.isfinite(number) or number <= 0:
        raise refuse("quantity_must_be_positive", **{field: "A quantity is 1 unit or more."})
    return number


def normalise_pricing_method(value: Any, field: str = "pricing_method") -> str:
    """Read a pricing method under any of its spellings.

    The research writes "volume-based", "graduated" and "stair-step" with
    hyphens. The values this workflow stores use underscores, because a value
    with a hyphen in it has to be quoted in every filter and every JSON path.
    """
    text = str(value or "").strip().lower()
    resolved = vocab.PRICING_METHOD_SYNONYMS.get(text)
    if resolved is None:
        raise refuse(
            "unknown_pricing_method",
            **{
                field: (
                    "Choose flat, volume-based, graduated or stair-step. "
                    f"'{value}' is not one of them."
                )
            },
        )
    return resolved


def normalise_billing(value: Any, field: str = "billing_frequency") -> str:
    """Read a billing frequency under any of its spellings.

    The research names the field "billing frequency" and PandaDoc names the
    values ``one_time`` and ``recurring``. A CRM mirror may say "monthly", which
    means the same thing here.
    """
    text = str(value or "").strip().lower().replace(" ", "_")
    resolved = vocab.BILLING_SYNONYMS.get(text)
    if resolved is None:
        return vocab.BILLING_ONE_TIME
    return resolved


def normalise_currency(value: Any, field: str = "currency") -> str:
    """Read a currency as a three-letter upper-case code.

    Upper-cased rather than lower-cased-and-accepted so ``usd`` and ``USD`` are
    one currency in the price map rather than two entries that both read as
    absent. The research puts the currency in a dropdown per price book and in a
    field per account currency on a product, so it is a closed list in the UI and
    an open one here: a code this product has never seen is still stored.
    """
    text = str(value or "").strip().upper()
    if len(text) != 3 or not text.isalpha():
        raise refuse(
            "currency_is_not_a_code",
            **{field: "Use a three-letter currency code, such as USD or EUR."},
        )
    return text


def is_tiered(method: Any) -> bool:
    """Whether a pricing method reads a tier table at all.

    Flat does not, which is why a flat product may be bulk-imported and a tiered
    one may not: the research says "tier priced products cannot be imported".
    """
    text = str(method or "").strip().lower()
    return vocab.PRICING_METHOD_SYNONYMS.get(text, text) in vocab.TIERED_PRICING_METHODS


def _is_row(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def read_tier_table(payload: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The raw tier rows on a product or a price-book row, under any spelling.

    Accepts the key spellings in ``TIER_TABLE_KEYS`` and leaves every row as the
    caller wrote it. Normalisation is :func:`tiers`' job, so a caller that only
    wants to know whether a tier table is present does not pay for validating it.
    """
    if not _is_row(payload):
        return []
    for key in vocab.TIER_TABLE_KEYS:
        raw = payload.get(key)
        if _is_list(raw):
            return [row for row in raw if _is_row(row)]
    return []


def tiers(raw: Any, field: str = "tiers") -> list[dict[str, Any]]:
    """Normalise a tier table: validate every row, sort it, derive every maximum.

    Returns a list of ``{min_qty, max_qty, unit_price, fee, method}`` where
    ``max_qty`` is ``None`` on the final tier. That ``None`` is the "open-ended
    final tier" the research calls infinity, stored as an absent maximum rather
    than as a large number: a sentinel like 999999999 reads as a real ceiling to
    anyone who exports the row.

    Raises :class:`~dsr.price_books.errors.CatalogueRefusal` rather than
    repairing a bad table. A tier table with a repeated minimum, a minimum of
    zero or a tier with no price has no honest reading, and quietly dropping the
    bad row would price a buyer's order against a table they never agreed to.
    """
    rows = [row for row in raw if _is_row(row)] if _is_list(raw) else []
    if not rows:
        return []

    parsed: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        where = f"{field}[{position}]"

        threshold: Any = None
        for key in vocab.MIN_QUANTITY_KEYS:
            if row.get(key) is not None:
                threshold = row[key]
                break
        if threshold is None:
            raise refuse(
                "tier_needs_a_minimum",
                **{
                    where: "Give the tier a minimum quantity. It is the only number the author types."
                },
            )

        raw_minimum = as_number(threshold, float("nan"))
        if not math.isfinite(raw_minimum) or float(raw_minimum) != int(raw_minimum):
            raise refuse(
                "tier_minimum_must_be_whole",
                **{where: "A tier minimum is a whole number of units."},
            )
        minimum = int(raw_minimum)
        if minimum < 1:
            raise refuse(
                "tier_minimum_must_be_positive",
                **{where: "A tier minimum is 1 unit or more."},
            )

        price: Any = None
        for key in vocab.UNIT_PRICE_KEYS:
            if row.get(key) is not None:
                price = row[key]
                break
        fee: Any = None
        for key in vocab.TIER_FEE_KEYS:
            if row.get(key) is not None:
                fee = row[key]
                break
        if price is None and fee is None:
            raise refuse(
                "tier_needs_a_price",
                **{where: "Give the tier a unit price, or a fee for stair-step pricing."},
            )

        # A fee is only recorded when the author named one. Stair-step reads a
        # tier's unit price as its fee when it has to (see _fee_for), so copying
        # the rate into a fee field on every row would state the same number twice
        # and let the two drift apart on the next edit.
        explicit_fee = any(row.get(key) is not None for key in ("fee", "flat_fee"))
        parsed.append(
            {
                "min_qty": minimum,
                "unit_price": money(price) if price is not None else None,
                "fee": money(fee) if (explicit_fee and fee is not None) else None,
            }
        )

    parsed.sort(key=lambda row: row["min_qty"])
    for position in range(1, len(parsed)):
        if parsed[position]["min_qty"] == parsed[position - 1]["min_qty"]:
            raise refuse(
                "tier_minimums_must_rise",
                **{
                    f"{field}[{position}].min_qty": (
                        f"Two tiers start at {parsed[position]['min_qty']} units. "
                        "Give each tier its own minimum."
                    )
                },
            )

    # The maximum is derived, never authored. See the module docstring.
    for position, row in enumerate(parsed):
        following = parsed[position + 1] if position + 1 < len(parsed) else None
        row["max_qty"] = (following["min_qty"] - 1) if following else None
    return parsed


def select_tier(table: Sequence[Mapping[str, Any]], quantity: Any) -> dict[str, Any] | None:
    """The range a quantity falls in: the highest one it reaches.

    The table must already be sorted by ``min_qty``, which :func:`tiers` does.
    A quantity below the first minimum returns the first tier rather than ``None``,
    which is the corollary of deriving the maximum: a quantity of one against a
    table starting at ten has to cost the first tier's price and not nothing.
    """
    rows = [row for row in table if _is_row(row)]
    if not rows:
        return None
    amount = as_number(quantity, 0.0)
    chosen = rows[0]
    for row in rows:
        if amount >= as_number(row.get("min_qty"), 0.0):
            chosen = row
        else:
            break
    return dict(chosen)


def tier_label(tier: Mapping[str, Any] | None) -> str | None:
    """The range as a person reads it, for a response and a page.

    "1 to 9", "10 to 24", "25 or more". The open-ended last range is written in
    words rather than with a symbol so it survives a cp1252 console, a JSON
    export and a screen reader.
    """
    if not tier:
        return None
    low = int(as_number(tier.get("min_qty"), 0))
    high = tier.get("max_qty")
    if high is None:
        return f"{low} or more"
    return f"{low} to {int(as_number(high, low))}"


def price_line(
    method: Any,
    table: Sequence[Mapping[str, Any]],
    quantity: Any,
) -> dict[str, Any]:
    """Price one line under one method, and say how the figure was reached.

    Returns the total, the unit price, the reached range and the per-range
    breakdown. The breakdown is there because the three methods differ only in
    how the quantity is split, and a caller that cannot see the split cannot
    check the arithmetic against the tier table a person authored.

    A stair-step total is the reached range's fee, so its unit price is that fee
    divided by the quantity: it is an average, not a rate anybody can charge.
    ``unit_is_average`` says so, so a caller never prints it as a per-unit price.
    """
    resolved = normalise_pricing_method(method, "pricing_method")
    amount = require_quantity(quantity)
    rows = [row for row in table if _is_row(row)]
    reached = select_tier(rows, amount)

    if resolved == vocab.METHOD_STAIR_STEP:
        fee = _fee_for(reached)
        return {
            "pricing_method": resolved,
            "quantity": amount,
            "unit_price": money(fee / amount) if amount else 0.0,
            "unit_is_average": True,
            "total": money(fee),
            "tier_label": tier_label(reached),
            "breakdown": (
                [{"tier_label": tier_label(reached), "quantity": amount, "amount": money(fee)}]
                if reached
                else []
            ),
        }

    if resolved == vocab.METHOD_GRADUATED:
        splits = _split_graded(rows, amount)
        total = sum(as_number(part["amount"], 0.0) for part in splits)
        return {
            "pricing_method": resolved,
            "quantity": amount,
            "unit_price": money(total / amount) if amount else 0.0,
            "unit_is_average": True,
            "total": money(total),
            "tier_label": tier_label(reached),
            "breakdown": splits,
        }

    unit = _unit_for(reached)
    total = money(unit * amount)
    return {
        "pricing_method": resolved,
        "quantity": amount,
        "unit_price": money(unit),
        "unit_is_average": False,
        "total": total,
        "tier_label": tier_label(reached),
        "breakdown": (
            [{"tier_label": tier_label(reached), "quantity": amount, "amount": total}]
            if reached
            else []
        ),
    }


def _unit_for(tier: Mapping[str, Any] | None) -> float:
    """The unit price of a range, for the two methods that scale by quantity."""
    if not tier:
        return 0.0
    for key in vocab.UNIT_PRICE_KEYS:
        if tier.get(key) is not None:
            return as_number(tier[key], 0.0)
    return as_number(tier.get("fee"), 0.0)


def _fee_for(tier: Mapping[str, Any] | None) -> float:
    """The flat fee of a range, for stair-step."""
    if not tier:
        return 0.0
    for key in vocab.TIER_FEE_KEYS:
        if tier.get(key) is not None:
            return as_number(tier[key], 0.0)
    return 0.0


def _split_graded(rows: Sequence[Mapping[str, Any]], amount: float) -> list[dict[str, Any]]:
    """Split a quantity across the ranges it passes through, one figure each.

    The last range takes whatever units are left, so a quantity that runs past
    the final minimum is charged at the final range's rate with no remainder. A
    quantity below the first minimum goes entirely to the first range, which is
    the same rule :func:`select_tier` uses for the other two methods.
    """
    if not rows:
        return []
    remaining = amount
    parts: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        if remaining <= 0:
            break
        low = as_number(row.get("min_qty"), 0.0)
        if position + 1 < len(rows):
            high = as_number(row.get("max_qty"), 0.0)
            taken = min(remaining, max(0.0, high - low + 1))
        else:
            taken = remaining
        if taken <= 0:
            continue
        unit = _unit_for(row)
        parts.append(
            {
                "tier_label": tier_label(row),
                "quantity": taken,
                "unit_price": money(unit),
                "amount": money(unit * taken),
            }
        )
        remaining -= taken
    return parts


def price_map(product: Mapping[str, Any] | None) -> dict[str, float]:
    """The product's flat prices, keyed by currency.

    The research's flat branch is "enter price in each account currency field",
    so a product holds one price per currency rather than one price. A product
    that names a bare ``unit_price`` with no currency is read under
    :data:`DEFAULT_CURRENCY`, so a catalogue written before currencies were
    modelled still prices.

    A ``prices`` map wins over a bare ``unit_price`` because it is the shape the
    research describes, and a row carrying both has said the same thing twice
    with the per-currency figure as the more specific of the two.
    """
    prices: dict[str, float] = {}
    if not _is_row(product):
        return prices
    for key in vocab.PRICE_MAP_KEYS:
        raw = product.get(key)
        if _is_row(raw):
            for code, value in raw.items():
                text = str(code).strip().upper()
                if text:
                    prices[text] = money(value)
    if prices:
        return prices
    single: Any = None
    for key in vocab.PRODUCT_PRICE_KEYS:
        if product.get(key) is not None:
            single = product[key]
            break
    if single is not None:
        code = str(product.get(vocab.CURRENCY_FIELD) or DEFAULT_CURRENCY).strip().upper()
        prices[code or DEFAULT_CURRENCY] = money(single)
    return prices


#: The currency a product price with no currency of its own is read under. A
#: placeholder rather than a rule: the research puts the currency in a per-book
#: dropdown and a per-account field, so a deployment always has one, and this
#: only covers a row written without it.
DEFAULT_CURRENCY = "USD"


def price_in_currency(product: Mapping[str, Any] | None, currency: Any) -> tuple[float, str]:
    """A product's flat price in one currency, plus where it was read from.

    Returns ``(0.0, "missing")`` when the product carries no price in that
    currency. The caller decides what that means. The research says a price book
    holds one currency and a product carries a price per account currency, and
    does not say what a book sees for a product priced in another currency, so the
    rule chosen here is recorded in :func:`effective_unit_price`.
    """
    wanted = str(currency or "").strip().upper()
    prices = price_map(product)
    if wanted and wanted in prices:
        return money(prices[wanted]), "price_map"
    if len(prices) == 1 and not wanted:
        only = next(iter(prices.items()))
        return money(only[1]), "price_map"
    return 0.0, "missing"


def book_item_price(item: Mapping[str, Any] | None, currency: Any) -> float | None:
    """The price a price-book row overrides with, or ``None`` when it overrides none.

    ``None`` and ``0.00`` are different answers and the distinction matters: a row
    that says nothing leaves the product's own price in force, and a row that
    says zero prices the product at nothing. Reading both as zero would let a
    half-filled row silently zero a product.
    """
    if not _is_row(item):
        return None
    wanted = str(currency or "").strip().upper()
    if wanted:
        for key in vocab.PRICE_MAP_KEYS:
            prices = item.get(key)
            if _is_row(prices) and wanted in prices:
                return money(prices[wanted])
    else:
        prices = price_map(item)
        if len(prices) == 1:
            return money(next(iter(prices.values())))
        if prices:
            return None
    for key in vocab.PRODUCT_PRICE_KEYS:
        if item.get(key) is not None:
            return money(item[key])
    return None


def book_currencies(book: Mapping[str, Any] | None) -> list[str]:
    """The currencies a price book supports, in order, without duplicates.

    The vendor's own API reference calls this ``supportedCurrencies`` and types it
    as an array, while the knowledge-base article this workflow's research quotes
    describes a single-currency dropdown. Both spellings are read, so a mirror of
    either shape resolves. ``vocabulary.CURRENCIES`` records the contradiction.

    Order is preserved rather than sorted, because a page lists a book the way an
    administrator entered its currencies and re-sorting that list on every read
    makes the page disagree with what they typed.
    """
    if not _is_row(book):
        return []
    raw: Any = None
    for key in vocab.BOOK_CURRENCY_LIST_KEYS:
        if book.get(key):
            raw = book[key]
            break
    if raw is None:
        return []
    candidates = list(raw) if _is_list(raw) else [raw]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        code = str(candidate or "").strip().upper()
        if code and code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def normalise_book_currencies(value: Any, field: str = "supported_currencies") -> list[str]:
    """Read the currency list a price book is saved with, validated.

    Every code goes through :func:`normalise_currency`, so a code that is not
    three letters is refused at save time rather than making the whole book
    unpriceable later.
    """
    candidates = list(value) if _is_list(value) else ([] if value in (None, "") else [value])
    codes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        code = normalise_currency(candidate, field)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    if not codes:
        raise refuse(
            "price_book_needs_a_currency",
            **{
                field: "Choose at least one currency. A price book names the currencies it prices in."
            },
        )
    return codes


def missing_currencies(
    book: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Which supported currencies each row has no price in.

    The vendor documents the rule twice: the validate endpoint checks that "every
    product has a price for each of the book's supported currencies", and the
    error ``PRICE_BOOK_PRODUCTS_MISSING_PRICES`` blocks activation with a 400 when
    they do not. A row missing one is not priced at zero and not priced at the
    product's price in another currency; it is not priced in that currency at
    all, which is why the book cannot go live yet.

    Returns ``{product_id: [currency, ...]}`` and omits every row that is
    complete, so a caller checks ``if missing`` rather than counting.

    Accepts either a bare row payload or a whole store record, because the two
    callers differ: the engine has raw records from ``find`` while a test passes
    the payload it wrote. Unwrapping here rather than at each call site is what
    stops one caller silently reporting every row as missing a price in every
    currency.
    """
    wanted = book_currencies(book)
    gaps: dict[str, list[str]] = {}
    for row in rows:
        if not _is_row(row):
            continue
        payload = row.get("data") if _is_row(row.get("data")) else row
        product_id = str(payload.get("product_id") or row.get("id") or "")
        if not product_id:
            continue
        missing = [code for code in wanted if book_item_price(payload, code) is None]
        if missing:
            gaps[product_id] = missing
    return gaps


def effective_unit_price(
    product: Mapping[str, Any] | None,
    quantity: Any,
    *,
    price_book: Mapping[str, Any] | None = None,
    price_book_item: Mapping[str, Any] | None = None,
    currency: Any = "",
) -> dict[str, Any]:
    """The unit price a quote line pays, and every step that produced it.

    The order is the data flow's order: "at quote-line creation the tier table is
    evaluated against quantity to select the effective unit price; the price-book
    layer then overrides the product default price for the specific buyer segment
    or region". So the product's own tiers are evaluated first and the price-book
    row, when there is an active one, replaces the figure.

    Precedence, chosen at confidence 0.94, audit ``jev-20261013T111944-8144-84487``,
    and confirmed by the vendor: "The source product is never modified by price
    book operations" and "override a product's default price within a price
    book" (developers.hubspot.com/docs/api-reference/latest/revenue/price-books/
    guide, knowledge.hubspot.com/products/use-price-books).

    1. An active price-book row with a price or a tier table of its own wins.
    2. Otherwise the product tier table is evaluated against the quantity.
    3. Otherwise the product flat price in the wanted currency is used.
    4. Otherwise there is no price and ``source`` is ``missing``.

    A price-book row that is inactive is skipped rather than refused: the row is a
    record of what somebody typed, and the price a buyer sees should not depend
    on a half-filled override.

    No currency is ever converted. The vendor copies a price per currency into the
    book and never mentions an exchange rate, and a converted price would be a
    number this product cannot explain to a buyer.
    """
    amount = require_quantity(quantity)
    wanted = str(currency or "").strip().upper()

    book = dict(price_book_item) if _is_row(price_book_item) else {}
    supported = book_currencies(price_book)
    if not wanted:
        wanted = str(book.get(vocab.CURRENCY_FIELD) or "").strip().upper()
    if not wanted and supported:
        wanted = supported[0]
    if not wanted:
        # A row or a product that carries exactly one price and no currency of
        # its own still names its currency in the key of that price. Reading it
        # from there means the response reports the currency the figure is
        # actually in rather than an empty string beside a number.
        for holder in (book, product):
            prices = price_map(holder)
            if len(prices) == 1:
                wanted = next(iter(prices))
                break
    resolved_currency = wanted

    book_is_active = str(book.get("state") or vocab.BOOK_ITEM_ACTIVE) == vocab.BOOK_ITEM_ACTIVE
    book_price = book_item_price(book, resolved_currency) if book_is_active else None
    book_tiers = tiers(read_tier_table(book), "price_book_item.tiers") if book_is_active else []
    book_method = str(book.get("pricing_method") or "") if book else ""

    product_table = tiers(read_tier_table(product), "tiers")
    product_method = normalise_pricing_method(
        (product or {}).get("pricing_method")
        or (vocab.METHOD_GRADUATED if product_table else vocab.METHOD_FLAT),
        "pricing_method",
    )

    if book_price is not None or book_tiers:
        method = normalise_pricing_method(
            book_method or (vocab.METHOD_GRADUATED if book_tiers else vocab.METHOD_FLAT),
            "price_book_item.pricing_method",
        )
        if book_tiers:
            priced = price_line(method, book_tiers, amount)
            return _answer(priced, vocab.SOURCE_PRICE_BOOK, resolved_currency, priced["tier_label"])
        # A row with a price and no tier table of its own replaces the product
        # pricing outright rather than combining with it. Combining would leave
        # two unit prices for one quantity, and the seller's override would be
        # the one that had to be explained away.
        priced = {
            "pricing_method": vocab.METHOD_FLAT,
            "quantity": amount,
            "unit_price": money(book_price or 0.0),
            "unit_is_average": False,
            "total": money(as_number(book_price, 0.0) * amount),
            "breakdown": [],
        }
        return _answer(priced, vocab.SOURCE_PRICE_BOOK, resolved_currency, None)

    if product_table:
        priced = price_line(product_method, product_table, amount)
        return _answer(
            priced,
            vocab.SOURCE_TIER,
            resolved_currency
            or str((product or {}).get(vocab.CURRENCY_FIELD) or "").strip().upper(),
            priced["tier_label"],
        )

    price, _origin = price_in_currency(product, resolved_currency)
    if price:
        priced = {
            "pricing_method": vocab.METHOD_FLAT,
            "quantity": amount,
            "unit_price": money(price),
            "unit_is_average": False,
            "total": money(price * amount),
            "tier_label": None,
            "breakdown": [],
        }
        return _answer(priced, vocab.SOURCE_PRODUCT_DEFAULT, resolved_currency, None)

    return _answer(
        {
            "pricing_method": vocab.METHOD_FLAT,
            "quantity": amount,
            "unit_price": 0.0,
            "unit_is_average": False,
            "total": 0.0,
            "tier_label": None,
            "breakdown": [],
        },
        vocab.SOURCE_MISSING,
        resolved_currency,
        None,
    )


def _answer(
    priced: Mapping[str, Any], source: str, currency: str, label: str | None
) -> dict[str, Any]:
    """The shape every pricing answer takes, so a caller reads one set of keys."""
    answer = dict(priced)
    answer["source"] = source
    answer["currency"] = currency
    answer["tier_label"] = label
    return answer


def is_bundle(product: Mapping[str, Any] | None) -> bool:
    """Whether a product is a bundle.

    The vendor refuses a bundle outright: "Bundle products can't be added to a
    price book", error ``BUNDLE_PRODUCTS_NOT_ALLOWED`` (developers.hubspot.com/
    docs/api-reference/latest/revenue/price-books/guide). So the answer is a
    refusal rather than a special price, and this workflow does not price a
    bundle by adding up its members, because the research says nothing about how.
    """
    if not _is_row(product):
        return False
    text = str(product.get("product_type") or vocab.PRODUCT_TYPE_REGULAR).strip().lower()
    return text == vocab.PRODUCT_TYPE_BUNDLE


def is_importable(product: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Whether a product may be bulk-imported, and the sentence when it may not.

    The research states the rule outright: "tier priced products can't be
    imported", and "import to bulk-import flat-rate products". So the refusal is
    on the method, not on the presence of tiers: a flat product with a stray tier
    row imports, and a graduated product with no tiers does not, because it is
    the method that makes the export ambiguous.
    """
    if not _is_row(product):
        return False, "There is no product to import."
    method = str(product.get("pricing_method") or "").strip().lower()
    resolved = vocab.PRICING_METHOD_SYNONYMS.get(
        method, vocab.METHOD_FLAT if not method else method
    )
    if resolved in vocab.TIERED_PRICING_METHODS:
        return (
            False,
            "Tier priced products cannot be imported. Import them one at a time, "
            "or import a flat priced copy and set the tiers afterwards.",
        )
    return True, ""


def summary_row(product: Mapping[str, Any] | None) -> dict[str, Any]:
    """One product as a list row shows it.

    Every field a page lists is computed here rather than in the page, so two
    pages listing the catalogue cannot disagree about a product's method, its
    range count or whether it may be imported.
    """
    if not _is_row(product):
        return {}
    method = str(product.get("pricing_method") or vocab.METHOD_FLAT).strip().lower()
    resolved = vocab.PRICING_METHOD_SYNONYMS.get(method, method)
    table = read_tier_table(product)
    importable, _why = is_importable(product)
    prices = price_map(product)
    return {
        "tier_count": len(table),
        "tiers": table,
        "is_tiered": resolved in vocab.TIERED_PRICING_METHODS,
        "pricing_method": resolved,
        "is_importable": importable,
        "currencies": sorted(prices),
        "prices": prices,
        "currency": str(product.get(vocab.CURRENCY_FIELD) or "").strip().upper(),
        "active": product.get("active", True) is not False,
        "status": vocab.ACTIVE if product.get("active", True) is not False else vocab.RETIRED,
    }
