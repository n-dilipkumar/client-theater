"""The rules: the publish configuration, the amount due, the charge and the invoice schedule.

Everything here is a rule and nothing here is a framework. Each function reads plain values
and returns plain values, or raises one of the four errors in
:mod:`dsr.quote_payment.errors`. Nothing here knows about HTTP, SQLite, a request or the
application module, which is what makes every rule testable without a server and makes the
HTTP layer in ``dsr.features.wf096_accept_a_quote_without_a_signature_and_take`` a thin
translation of what happens here.

The rules the research fixes exactly
-------------------------------------

**Online payments need a compatible acceptance method.** "online payments require an
acceptance method of *E-signature* or *Accept without signature*. *Print and sign* isn't a
valid acceptance method with online payments." :func:`validate_publish` refuses
``print_and_sign`` when billing or payments are on, and names the method that would work.

**Billing forces whole-number quantities.** "if the quote offers online payments or the
*Enable billing* switch is on, line item quantities must be whole numbers."
:func:`assert_whole_quantities` refuses a fractional quantity and names the offending lines,
before anything is written, so a quote never enters billing with a quantity that cannot be
charged.

**The minimum charge is strict and per currency.** "the total amount due must be more than
$0.50, or the equivalent minimum of the settlement currency."
:func:`charge_outcome` returns ``declined`` for an amount equal to or below the minimum, so a
processor's refusal is a recorded outcome rather than a malformed request.

**The first invoice ignores its own schedule.** "The first invoice is also generated and sent
to the buyer immediately after quote acceptance, regardless of its scheduled invoice date or
due date." :func:`first_invoice` is dated today and sent today; only the later invoices are
scheduled, and they are sent ten days before their invoice dates:
"Subsequent invoices are generated and sent according to the contract's billing schedule, 10
days before their invoice dates."

**Acceptance is irreversible.** "Quotes can't be voided or deleted after they have been
accepted, e-signed, or marked as signed." :func:`assert_can_void` refuses both, with the same
reason code, because the remedy is the same: do not attempt it.

The rules the research leaves open, and where the derivation is recorded
-----------------------------------------------------------------------

The four effective-date modes, the billing frequencies, the invoice horizon and the net
payment term calendar are not enumerated by the research. Each value is declared in
:mod:`dsr.quote_payment.vocabulary` and the choice is recorded in
:mod:`dsr.quote_payment.inferences`, so a reader can see which parts are sourced and which are
this build's own.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from dsr.quote_payment import vocabulary as vocab
from dsr.quote_payment.errors import PaymentRefused, StateConflict

_CENTS = Decimal("0.01")


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def truthy(value: Any, default: bool = False) -> bool:
    """Read a boolean out of arbitrary JSON.

    A switch arrives as a JSON boolean, the string ``"true"`` a form posted, or an absent key.
    An absent key gets the caller's default rather than ``False``, because the default is the
    whole point of declaring one.
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on", "y", "t"):
        return True
    if text in ("false", "0", "no", "off", "n", "f", ""):
        return False
    return default


def as_number(value: Any, default: float = 0.0) -> float:
    """Read a finite number out of arbitrary JSON, or fall back.

    ``Decimal`` is handled in its own branch rather than coerced through ``float`` first,
    because a price a person typed as a string parses through :class:`Decimal` and must not
    fall through to the default. This is the defect WF-086's pricing module records: a string
    unit price that silently became zero.
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

    ``Decimal`` rather than ``round()`` because binary floats cannot express half-up exactly:
    ``round(2.675, 2)`` is 2.67, and a cent lost per line is a pound lost on a hundred-line
    quote.
    """

    try:
        decimal = Decimal(str(as_number(value)))
    except (InvalidOperation, ValueError):
        decimal = Decimal("0")
    return float(decimal.quantize(_CENTS, rounding=ROUND_HALF_UP))


def as_date(value: Any) -> date | None:
    """Parse a ``YYYY-MM-DD`` date, or a full ISO instant, or ``None``.

    A quote's dates arrive from a form as ``YYYY-MM-DD`` and from the store as a full instant.
    Both resolve; anything else is ``None`` and the caller decides what that means.
    """

    text = as_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def add_months(anchor: date, months: int) -> date:
    """``anchor`` shifted by whole months, clamped to the last valid day.

    The 31st of January plus one month is the 28th or 29th of February, not the 3rd of March,
    because a billing date that skips a month is a date a buyer will dispute.
    """

    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    last_day = _days_in_month(year, month)
    return date(year, month, min(anchor.day, last_day))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


# --------------------------------------------------------------------------- #
# The amount due
# --------------------------------------------------------------------------- #


def line_amounts(line: Mapping[str, Any]) -> dict[str, float]:
    """Every figure one line contributes, at that line's own rounding.

    ``discount_value`` is read against ``discount_type``: a percentage is a share of the line
    subtotal, a currency amount is taken off it directly. A percentage above 100 is clamped to
    100 rather than producing a negative line, because a negative line is a figure no buyer can
    be shown. ``quantity`` defaults to ``1.0`` and ``unit_price`` to ``0.0``, matching WF-086,
    so the same line answered by either engine prices identically.
    """

    quantity = as_number(line.get("quantity"), 1.0)
    unit_price = as_number(line.get("unit_price"), 0.0)
    subtotal = money(quantity * unit_price)

    discount_type = as_text(line.get("discount_type"), vocab.DISCOUNT_PERCENTAGE)
    raw_discount = as_number(line.get("discount_value"), 0.0)
    if discount_type == vocab.DISCOUNT_CURRENCY:
        discount = money(raw_discount)
        if subtotal >= 0:
            discount = min(discount, subtotal)
    else:
        percent = min(max(raw_discount, 0.0), 100.0)
        discount = money(subtotal * percent / 100.0)

    net = money(subtotal - discount)
    tax_rate = min(max(as_number(line.get("tax_rate"), 0.0), 0.0), 100.0)
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


def amount_due(lines: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The quote's amount due: the sum of the lines' own rounded figures.

    Lines are summed as their own rounded figures rather than one running float, so a buyer
    who adds the printed column gets the printed total. ``total`` is the amount due the
    minimum-charge rule and the first invoice both read.
    """

    subtotal = 0.0
    discount = 0.0
    tax = 0.0
    count = 0
    for line in lines or []:
        figures = line_amounts(line)
        subtotal += figures["subtotal"]
        discount += figures["discount"]
        tax += figures["tax"]
        count += 1
    subtotal = money(subtotal)
    discount = money(discount)
    tax = money(tax)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "tax": tax,
        "total": money(subtotal - discount + tax),
        "line_count": count,
    }


# --------------------------------------------------------------------------- #
# Quantities
# --------------------------------------------------------------------------- #


def is_whole_quantity(value: Any) -> bool:
    """Whether a quantity is a whole number.

    Read as a float and checked with ``is_integer`` rather than tested against a string, so
    ``3``, ``3.0`` and ``"3"`` are all whole and ``1.5`` is not. ``True`` is not a quantity.
    """

    if isinstance(value, bool) or value is None or value == "":
        return True
    number = as_number(value, default=float("nan"))
    if math.isnan(number):
        return False
    return float(number).is_integer()


def assert_whole_quantities(
    lines: Sequence[Mapping[str, Any]],
    *,
    billing_enabled: bool,
    payment_enabled: bool,
) -> None:
    """Refuse a fractional quantity while billing or online payments are on.

    Does nothing when both switches are off, because a quote without billing may quote half a
    day or a proportional licence and the rule does not reach it.
    """

    if not (billing_enabled or payment_enabled):
        return
    offenders = [
        as_text(line.get("name")) or as_text(line.get("id")) or f"line {index + 1}"
        for index, line in enumerate(lines or [])
        if not is_whole_quantity(line.get("quantity"))
    ]
    if not offenders:
        return
    names = ", ".join(offenders[:3])
    raise PaymentRefused(
        "Billing and online payments need whole-number quantities.",
        {
            "quantity": (
                f"Set a whole-number quantity on {names}. {vocab.WHOLE_NUMBER_QUANTITY_QUOTE}"
            )
        },
        reason=vocab.REASON_FRACTIONAL_QUANTITY_WITH_BILLING,
    )


# --------------------------------------------------------------------------- #
# Acceptance methods
# --------------------------------------------------------------------------- #


def normalise_acceptance_method(value: Any) -> str:
    """One of the three researched methods, or a refusal naming the value.

    Both the data flow's snake_case token and the UI's label are accepted, because both are in
    the research and a caller reading either should not have to translate.
    """

    text = as_text(value)
    if not text:
        return vocab.ACCEPTANCE_CLICKWRAP
    normalised = text.strip().lower().replace(" ", "_").replace("-", "_")
    if normalised in vocab.ACCEPTANCE_METHODS:
        return normalised
    alias = vocab.ACCEPTANCE_METHOD_ALIASES.get(normalised)
    if alias:
        return alias
    raise PaymentRefused(
        "That acceptance method is not one I recognise.",
        {
            "acceptance_method": (
                f"Use one of: {', '.join(vocab.ACCEPTANCE_METHODS)}. "
                f"{vocab.ONLINE_PAYMENT_ACCEPTANCE_QUOTE}"
            )
        },
        reason=vocab.REASON_UNKNOWN_ACCEPTANCE_METHOD,
    )


def assert_acceptance_method(method: str, *, online_payments: bool) -> str:
    """Refuse *Print and sign* on a quote that offers online payments."""

    if online_payments and method == vocab.ACCEPTANCE_PRINT_AND_SIGN:
        raise PaymentRefused(
            "Print and sign cannot be used on a quote with online payments.",
            {"acceptance_method": vocab.ONLINE_PAYMENT_ACCEPTANCE_QUOTE},
            reason=vocab.REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS,
        )
    return method


def assert_clickwrap(method: str) -> str:
    """Refuse anything but *Accept without signature* on this workflow's accept route.

    An e-signature quote is a real quote, but collecting that acceptance is WF-095's path, so
    the refusal names it rather than pretending the method is unknown.
    """

    if method == vocab.ACCEPTANCE_CLICKWRAP:
        return method
    if method == vocab.ACCEPTANCE_ESIGNATURE:
        raise PaymentRefused(
            "This quote is published for e-signature, not for acceptance without a signature.",
            {"acceptance_method": vocab.CLICKWRAP_QUOTE},
            reason=vocab.REASON_E_SIGNATURE_BELONGS_TO_WF095,
        )
    raise PaymentRefused(
        "Print and sign cannot be accepted online.",
        {"acceptance_method": vocab.ONLINE_PAYMENT_ACCEPTANCE_QUOTE},
        reason=vocab.REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS,
    )


# --------------------------------------------------------------------------- #
# Payment methods and type
# --------------------------------------------------------------------------- #


def normalise_payment_method(value: Any) -> str:
    """One of the five researched payment methods, or a refusal naming the value."""

    text = as_text(value)
    normalised = text.upper().replace(" ", "_").replace("-", "_")
    if normalised in vocab.PAYMENT_METHODS:
        return normalised
    alias = vocab.PAYMENT_METHOD_ALIASES.get(normalised)
    if alias:
        return alias
    raise PaymentRefused(
        "That payment method is not one this product can charge.",
        {"payment_method": (f"Use one of: {', '.join(vocab.PAYMENT_METHODS)}.")},
        reason=vocab.REASON_UNKNOWN_PAYMENT_METHOD,
    )


def validate_payment_methods(value: Any) -> tuple[str, ...]:
    """The allowed payment methods, normalised, de-duplicated and in the caller's order."""

    if value is None or value == "" or value == []:
        return tuple(vocab.DEFAULT_PAYMENT_METHODS)
    if isinstance(value, (str, bytes)):
        candidates: Sequence[Any] = [value]
    elif isinstance(value, Sequence):
        candidates = list(value)
    else:
        candidates = [value]

    methods: list[str] = []
    for candidate in candidates:
        method = normalise_payment_method(candidate)
        if method not in methods:
            methods.append(method)
    if not methods:
        raise PaymentRefused(
            "Choose at least one payment method.",
            {"allowed_payment_methods": "Choose at least one of the five methods."},
            reason=vocab.REASON_NO_PAYMENT_METHODS,
        )
    return tuple(methods)


def payment_method_allowed(method: str, allowed: Sequence[str]) -> bool:
    """Whether a method is among the quote's allowed methods."""

    return method in tuple(allowed or ())


def derive_payment_type(payload: Mapping[str, Any]) -> str:
    """HubSpot payments or the account's own Stripe, derived and never chosen.

    The research says ``hs_payment_type`` "is set automatically" and that the two processors
    "are swappable per account". The account is described by the presence of a connected
    Stripe account on the payload; a payload that names none is a HubSpot-payments account.
    A seller-supplied ``hs_payment_type`` is deliberately ignored, because honouring it would
    make the property seller-chosen and contradict the evidence.
    """

    for key in vocab.CONNECTED_STRIPE_KEYS:
        if as_text(payload.get(key)):
            return vocab.PAYMENT_TYPE_BYO_STRIPE
    return vocab.PAYMENT_TYPE_HUBSPOT


# --------------------------------------------------------------------------- #
# Billing frequency
# --------------------------------------------------------------------------- #


def validate_billing_frequency(value: Any) -> str:
    """One of the five derived frequencies, or a refusal naming the value.

    An absent value is ``one_time``: a line billed once is the shape a quote without billing
    already has, so silence must not invent a subscription.
    """

    text = as_text(value)
    if not text:
        return vocab.BILLING_ONE_TIME
    normalised = text.strip().lower().replace(" ", "_")
    if normalised in vocab.BILLING_FREQUENCIES:
        return normalised
    alias = vocab.BILLING_FREQUENCY_ALIASES.get(normalised)
    if alias:
        return alias
    raise PaymentRefused(
        "That billing frequency is not one this product recognises.",
        {"billing_frequency": f"Use one of: {', '.join(vocab.BILLING_FREQUENCIES)}."},
        reason=vocab.REASON_UNKNOWN_BILLING_FREQUENCY,
    )


def line_billing_frequency(line: Mapping[str, Any], default: str) -> str:
    """A line's frequency, falling back to the quote-level default."""

    raw = line.get("billing_frequency") or line.get("frequency")
    if as_text(raw):
        return validate_billing_frequency(raw)
    return default


def classify_lines(
    lines: Sequence[Mapping[str, Any]], default_frequency: str = vocab.BILLING_ONE_TIME
) -> dict[str, list[dict[str, Any]]]:
    """Split the lines into recurring and one-time, with each row's frequency and figures.

    The research makes billing frequency per line item, so the split is per line and not per
    quote, and a quote may hold both kinds at once ("which supports ramped pricing").
    """

    recurring: list[dict[str, Any]] = []
    one_time: list[dict[str, Any]] = []
    for line in lines or []:
        frequency = line_billing_frequency(line, default_frequency)
        row = {
            "line_id": as_text(line.get("id")),
            "name": as_text(line.get("name")),
            "frequency": frequency,
            "amounts": line_amounts(line),
            "billing_start": as_text(line.get("billing_start")),
            "position": as_number(line.get("position"), 0.0),
        }
        if frequency == vocab.BILLING_ONE_TIME:
            one_time.append(row)
        else:
            recurring.append(row)
    return {"recurring": recurring, "one_time": one_time}


# --------------------------------------------------------------------------- #
# Effective date
# --------------------------------------------------------------------------- #


def validate_effective_date(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The quote's effective-date configuration, normalised, or a refusal.

    The four modes are the research's: "On agreement / Custom Date / Delayed start (days) /
    Delayed start (months)". ``custom_date`` needs a parsable date and the two delays need a
    non-negative whole number; a mode that cannot be resolved is refused here rather than
    silently falling back to on-agreement, because a quote that starts on the wrong day is a
    quote that bills on the wrong day.
    """

    raw_mode = payload.get("effective_date_mode")
    if not as_text(raw_mode) and as_text(payload.get("effective_date")):
        # A bare `effective_date` with no mode means the custom-date mode.
        raw_mode = vocab.EFFECTIVE_CUSTOM_DATE
    mode = as_text(raw_mode, vocab.DEFAULT_EFFECTIVE_DATE_MODE).strip().lower().replace(" ", "_")
    if mode not in vocab.EFFECTIVE_DATE_MODES:
        raise PaymentRefused(
            "That effective-date mode is not one of the four.",
            {"effective_date_mode": (f"Use one of: {', '.join(vocab.EFFECTIVE_DATE_MODES)}.")},
            reason=vocab.REASON_INVALID_EFFECTIVE_DATE,
        )

    config: dict[str, Any] = {
        "mode": mode,
        "custom_date": None,
        "delay_days": 0,
        "delay_months": 0,
    }
    if mode == vocab.EFFECTIVE_CUSTOM_DATE:
        parsed = as_date(payload.get("effective_date") or payload.get("custom_date"))
        if parsed is None:
            raise PaymentRefused(
                "A custom effective date needs a date.",
                {"effective_date": "Use YYYY-MM-DD."},
                reason=vocab.REASON_INVALID_EFFECTIVE_DATE,
            )
        config["custom_date"] = parsed.isoformat()
    elif mode == vocab.EFFECTIVE_DELAYED_DAYS:
        days = as_number(payload.get("effective_delay_days"), -1.0)
        if days < 0 or not float(days).is_integer():
            raise PaymentRefused(
                "A delayed start needs a whole, non-negative number of days.",
                {"effective_delay_days": "Use 0 or more whole days."},
                reason=vocab.REASON_INVALID_EFFECTIVE_DATE,
            )
        config["delay_days"] = int(days)
    elif mode == vocab.EFFECTIVE_DELAYED_MONTHS:
        months = as_number(payload.get("effective_delay_months"), -1.0)
        if months < 0 or not float(months).is_integer():
            raise PaymentRefused(
                "A delayed start needs a whole, non-negative number of months.",
                {"effective_delay_months": "Use 0 or more whole months."},
                reason=vocab.REASON_INVALID_EFFECTIVE_DATE,
            )
        config["delay_months"] = int(months)
    return config


def effective_date_on(config: Mapping[str, Any], agreement_date: date) -> str:
    """Resolve an effective-date configuration against the day the agreement was reached."""

    mode = as_text(config.get("mode"), vocab.DEFAULT_EFFECTIVE_DATE_MODE)
    if mode == vocab.EFFECTIVE_CUSTOM_DATE:
        parsed = as_date(config.get("custom_date")) or agreement_date
        return parsed.isoformat()
    if mode == vocab.EFFECTIVE_DELAYED_DAYS:
        return (agreement_date + timedelta(days=int(config.get("delay_days") or 0))).isoformat()
    if mode == vocab.EFFECTIVE_DELAYED_MONTHS:
        return add_months(agreement_date, int(config.get("delay_months") or 0)).isoformat()
    return agreement_date.isoformat()


def line_billing_start(
    line: Mapping[str, Any], config: Mapping[str, Any], agreement_date: date
) -> str:
    """A line's billing start: its own explicit date, its own mode, or the quote's mode.

    The research makes the start date modifiable per line item, so an explicit per-line
    ``billing_start`` wins; failing that a per-line mode is resolved against the agreement
    date; failing both the quote-level configuration applies.
    """

    explicit = as_text(line.get("billing_start") or line.get("billing_start_date"))
    if explicit:
        parsed = as_date(explicit)
        if parsed is None:
            raise PaymentRefused(
                "That billing start date cannot be read.",
                {"billing_start": "Use YYYY-MM-DD."},
                reason=vocab.REASON_INVALID_EFFECTIVE_DATE,
            )
        return parsed.isoformat()
    if as_text(line.get("effective_date_mode")) or as_text(line.get("effective_date")):
        return effective_date_on(validate_effective_date(line), agreement_date)
    return effective_date_on(config, agreement_date)


# --------------------------------------------------------------------------- #
# Collection process and net terms
# --------------------------------------------------------------------------- #


def validate_collection_process(value: Any) -> str:
    """The one researched collection process, ``AUTO_PAYMENTS``."""

    text = as_text(value, vocab.COLLECTION_PROCESS_AUTO_PAYMENTS).upper().replace(" ", "_")
    if text not in vocab.COLLECTION_PROCESSES:
        raise PaymentRefused(
            "That collection process is not one this product recognises.",
            {"hs_collection_process": f"Use one of: {', '.join(vocab.COLLECTION_PROCESSES)}."},
            reason=vocab.REASON_UNKNOWN_COLLECTION_PROCESS,
        )
    return text


def validate_net_payment_terms(value: Any) -> str:
    """A net payment term from the product's own calendar."""

    text = as_text(value, vocab.DEFAULT_NET_PAYMENT_TERMS).upper().replace(" ", "_")
    if text not in vocab.NET_PAYMENT_TERMS:
        raise PaymentRefused(
            "That net payment term is not one this product recognises.",
            {"hs_net_payment_terms": f"Use one of: {', '.join(vocab.NET_PAYMENT_TERMS)}."},
            reason=vocab.REASON_UNKNOWN_NET_PAYMENT_TERMS,
        )
    return text


# --------------------------------------------------------------------------- #
# The minimum charge
# --------------------------------------------------------------------------- #


def minimum_charge_for(currency: str | None) -> float:
    """The strict minimum for a currency, defaulting to the researched USD figure."""

    code = as_text(currency, vocab.DEFAULT_CURRENCY).upper()
    return money(vocab.MINIMUM_CHARGE_BY_CURRENCY.get(code, vocab.MINIMUM_CHARGE_USD))


def meets_minimum_charge(total: Any, currency: str | None = None) -> bool:
    """Whether an amount is *more than* the minimum. Exactly the minimum is not enough."""

    return as_number(total) > minimum_charge_for(currency)


def charge_outcome(total: Any, currency: str | None = None) -> dict[str, Any]:
    """The processor's outcome for an amount: recorded, or declined below the minimum."""

    floor = minimum_charge_for(currency)
    amount = money(total)
    if amount > floor:
        return {
            "outcome": vocab.OUTCOME_RECORDED,
            "reason": None,
            "minimum": floor,
            "amount": amount,
        }
    return {
        "outcome": vocab.OUTCOME_DECLINED,
        "reason": vocab.REASON_AMOUNT_DUE_BELOW_MINIMUM,
        "minimum": floor,
        "amount": amount,
        "detail": (
            f"The total amount due is {amount:.2f}, which does not exceed the "
            f"{floor:.2f} minimum. {vocab.MINIMUM_CHARGE_QUOTE}"
        ),
    }


# --------------------------------------------------------------------------- #
# Tax IDs
# --------------------------------------------------------------------------- #


def normalise_tax_id(value: Any) -> str:
    """A non-empty tax identifier, or a refusal."""

    text = as_text(value)
    if not text:
        raise PaymentRefused(
            "A tax ID needs a value.",
            {"value": "Enter the tax ID."},
            reason=vocab.REASON_TAX_ID_EMPTY,
        )
    return text


def assert_tax_id_capacity(existing_count: int, *, limit: int = vocab.TAX_ID_LIMIT) -> None:
    """Refuse a tax ID beyond the researched cap of three."""

    if existing_count >= limit:
        raise PaymentRefused(
            f"A quote can carry at most {limit} tax IDs.",
            {"value": vocab.TAX_ID_QUOTE},
            reason=vocab.REASON_TAX_ID_LIMIT_REACHED,
        )


# --------------------------------------------------------------------------- #
# State gates
# --------------------------------------------------------------------------- #


def assert_can_accept(existing: Mapping[str, Any] | None, *, quote_id: str | None = None) -> None:
    """A quote may be accepted once."""

    if existing:
        raise StateConflict(
            vocab.REASON_QUOTE_ALREADY_ACCEPTED,
            "This quote has already been accepted.",
            quote_id=quote_id,
        )


def assert_can_void(accepted: bool, *, quote_id: str | None = None) -> None:
    """Refuse a void or a delete once the quote is accepted."""

    if accepted:
        raise StateConflict(
            vocab.REASON_IRREVERSIBLE_AFTER_ACCEPTANCE,
            vocab.IRREVERSIBLE_QUOTE,
            quote_id=quote_id,
        )


def assert_can_pay(accepted: bool, *, quote_id: str | None = None) -> None:
    """Refuse a charge before the quote is accepted."""

    if not accepted:
        raise StateConflict(
            vocab.REASON_PAYMENT_REQUIRES_ACCEPTANCE,
            "Set up payment after the buyer accepts the quote.",
            quote_id=quote_id,
        )


def assert_no_charge(existing: list[Any], *, quote_id: str | None = None) -> None:
    """A quote carries at most one charge in this build."""

    if existing:
        raise StateConflict(
            vocab.REASON_CHARGE_ALREADY_RECORDED,
            "A charge has already been recorded against this quote.",
            quote_id=quote_id,
        )


# --------------------------------------------------------------------------- #
# The publish configuration
# --------------------------------------------------------------------------- #


def validate_publish(
    payload: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    *,
    currency: str | None = None,
) -> dict[str, Any]:
    """Validate and normalise the publish-time payment configuration.

    Validation is gathered rather than short-circuited, so a seller who sent two bad values
    sees both. When any value is refused, one :class:`PaymentRefused` carries the field-keyed
    map and nothing has been written.

    The order the rules are checked in is the order the research's user flow names them:
    the acceptance method against online payments first, then the payment methods, then the
    whole-number quantities, then the effective date. The first two configure what the buyer
    will see; the quantity rule is what makes the amounts chargeable at all.
    """

    errors: dict[str, str] = {}
    reason: str | None = None

    # This workflow's publish step exists to turn billing and online payments on, so both are
    # always true. The researched publish tuple writes ``hs_billing_enabled = true`` and
    # ``hs_payment_enabled = true`` unconditionally, and a payload that asked for one of them
    # off would provision a payment setup whose own properties contradict the research. The
    # consequence is deliberate and testable: the whole-number quantity rule always applies and
    # *Print and sign* is always refused, because both switches are on.
    billing_enabled = True
    payment_enabled = True
    online_payments = True

    method: str | None = None
    try:
        method = normalise_acceptance_method(
            payload.get("acceptance_method") or payload.get("hs_acceptance_method")
        )
        method = assert_acceptance_method(method, online_payments=online_payments)
    except PaymentRefused as refusal:
        errors.update(refusal.errors)
        reason = reason or refusal.reason

    methods: tuple[str, ...] = ()
    try:
        methods = validate_payment_methods(
            payload.get("allowed_payment_methods") or payload.get("hs_allowed_payment_methods")
        )
    except PaymentRefused as refusal:
        errors.update(refusal.errors)
        reason = reason or refusal.reason

    default_frequency = vocab.BILLING_ONE_TIME
    try:
        default_frequency = validate_billing_frequency(payload.get("billing_frequency"))
    except PaymentRefused as refusal:
        errors.update(refusal.errors)
        reason = reason or refusal.reason

    effective: dict[str, Any] = {}
    try:
        effective = validate_effective_date(payload)
    except PaymentRefused as refusal:
        errors.update(refusal.errors)
        reason = reason or refusal.reason

    try:
        collection_process = validate_collection_process(payload.get("hs_collection_process"))
        net_terms = validate_net_payment_terms(payload.get("hs_net_payment_terms"))
    except PaymentRefused as refusal:
        errors.update(refusal.errors)
        reason = reason or refusal.reason
        collection_process = vocab.COLLECTION_PROCESS_AUTO_PAYMENTS
        net_terms = vocab.DEFAULT_NET_PAYMENT_TERMS

    try:
        assert_whole_quantities(
            lines, billing_enabled=billing_enabled, payment_enabled=payment_enabled
        )
    except PaymentRefused as refusal:
        errors.update(refusal.errors)
        reason = reason or refusal.reason

    if not lines:
        errors["line_items"] = (
            "Publish a quote with at least one line item. " + vocab.MINIMUM_CHARGE_QUOTE
        )
        reason = reason or vocab.REASON_QUOTE_IS_EMPTY

    if errors:
        raise PaymentRefused(
            "The payment configuration is not one I can accept.",
            errors,
            reason=reason,
        )

    return {
        "acceptance_method": method or vocab.ACCEPTANCE_CLICKWRAP,
        "billing_enabled": billing_enabled,
        "payment_enabled": payment_enabled,
        "online_payments": online_payments,
        "payment_type": derive_payment_type(payload),
        "allowed_payment_methods": list(methods),
        "collect_billing_address": truthy(payload.get("collect_billing_address"), True),
        "collect_shipping_address": truthy(payload.get("collect_shipping_address"), False),
        "collection_process": collection_process,
        "net_payment_terms": net_terms,
        "billing_frequency": default_frequency,
        "effective_date": effective,
        "currency": as_text(currency, vocab.DEFAULT_CURRENCY).upper(),
        "minimum_charge": minimum_charge_for(currency),
        "store_payment_method": truthy(payload.get(vocab.FIELD_STORE_PAYMENT_METHOD), False),
        "automated_sales_tax": truthy(payload.get(vocab.FIELD_AUTOMATED_SALES_TAX), False),
        "payment_status": vocab.PAYMENT_STATUS_PENDING,
    }


# --------------------------------------------------------------------------- #
# Invoices and subscriptions
# --------------------------------------------------------------------------- #


def first_invoice(
    lines: Sequence[Mapping[str, Any]],
    *,
    currency: str,
    today: date,
) -> dict[str, Any]:
    """The first invoice, sent today whatever its schedule says.

    Its amount is the whole amount due, because the first invoice covers the order the buyer
    accepted: the one-time lines plus the first period of each recurring line.
    """

    due = amount_due(lines)
    return {
        "kind": vocab.INVOICE_FIRST,
        "status": vocab.INVOICE_STATUS_SENT,
        "amount": due["total"],
        "subtotal": due["subtotal"],
        "discount": due["discount"],
        "tax": due["tax"],
        "currency": currency,
        "invoice_date": today.isoformat(),
        "send_on": today.isoformat(),
        "rule": vocab.FIRST_INVOICE_QUOTE,
        "line_item_ids": [as_text(line.get("id")) for line in lines if as_text(line.get("id"))],
    }


def next_invoice_dates(
    frequency: str,
    start: date,
    *,
    today: date,
    count: int = vocab.SUBSEQUENT_INVOICE_PERIODS,
) -> list[dict[str, str]]:
    """The next ``count`` invoice dates for a recurring line, each with its send date.

    A period whose invoice date is today or earlier is skipped, because the first invoice
    already covers the present. Each send date is ten days before its invoice date, the lead
    the research names.
    """

    months = vocab.BILLING_PERIOD_MONTHS.get(frequency, 0)
    if months <= 0 or count <= 0:
        return []
    schedule: list[dict[str, str]] = []
    period = 0
    while len(schedule) < count and period < count + 120:
        period += 1
        invoice_date = add_months(start, months * period)
        if invoice_date <= today:
            continue
        schedule.append(
            {
                "invoice_date": invoice_date.isoformat(),
                "send_on": (invoice_date - timedelta(days=vocab.INVOICE_LEAD_DAYS)).isoformat(),
            }
        )
    return schedule


def recurring_invoice_amount(row: Mapping[str, Any]) -> float:
    """The amount one recurring invoice charges: the line's own total for one period."""

    amounts = row.get("amounts")
    if isinstance(amounts, Mapping):
        return money(amounts.get("total"))
    return money(row.get("total"))
