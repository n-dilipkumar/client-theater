"""The pure rules of WF-099. No store, no HTTP, no framework.

Every function here is a decision the research states, expressed so it can be
tested without a database. The engine in
:mod:`dsr.contract_generation.engine` calls these; the routes call the engine.

The one word that shapes the file is *inherit*: "Contracts inherit line items,
terms, and other details from the associated quote." Inheritance is a copy made
once, at creation. :func:`contract_payload` therefore reads the quote and
produces a fresh mapping; nothing holds a live reference to the quote, so a later
edit to the quote cannot change the contract that was created from it.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.contract_generation import vocabulary as vocab
from dsr.contract_generation.errors import ContractError

# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def as_text(value: Any) -> str:
    """A trimmed string, or an empty string for anything absent."""

    if value is None:
        return ""
    return str(value).strip()


def as_number(value: Any) -> float:
    """A float, or 0.0 for anything that is not a number."""

    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def as_int(value: Any) -> int | None:
    """An int, or None when the value is absent or not an integer."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool:
    """A boolean, accepting the spellings a JSON body or a store row may carry."""

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def parse_date(value: Any) -> date | None:
    """A date, or None. Never raises, because every caller reads a field it did
    not write and a rule that refused to read an optional field would turn a
    missing date into a 400."""

    text = as_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def add_months(moment: date, months: int) -> date:
    """``moment`` advanced by whole months, clamping the day to the month end.

    A term of one month from 31 January ends on 28 February, not on 3 March.
    """

    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# --------------------------------------------------------------------------- #
# The quote's state
# --------------------------------------------------------------------------- #


def is_published(quote: Mapping[str, Any]) -> bool:
    """Whether the quote has been published. Publishing froze its line items."""

    return as_text(quote.get("status")).lower() == vocab.QUOTE_STATUS_PUBLISHED


def is_legacy(quote: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the quote is legacy, and which marker made it so.

    See :data:`dsr.contract_generation.vocabulary.LEGACY_RULE`: the research
    defines legacy nowhere, so this workflow accepts only an explicit marker and
    never infers it.
    """

    if as_bool(quote.get(vocab.LEGACY_FLAG)):
        return True, vocab.LEGACY_FLAG
    if as_text(quote.get(vocab.FIELD_QUOTE_TYPE)).lower() in vocab.LEGACY_QUOTE_TYPES:
        return True, vocab.FIELD_QUOTE_TYPE
    if as_text(quote.get(vocab.FIELD_ACCEPTED_VIA)).lower() == vocab.LEGACY_ACCEPTANCE_CHANNEL:
        return True, vocab.FIELD_ACCEPTED_VIA
    return False, ""


# --------------------------------------------------------------------------- #
# The effective start date
# --------------------------------------------------------------------------- #


def resolve_effective_start(
    mode: str, *, accepted_on: date, quote: Mapping[str, Any]
) -> dict[str, Any]:
    """The contract's effective start date, from the quote's four properties.

    ``ON_AGREEMENT`` and ``CUSTOM`` read a date; ``DELAYED_BY_DAYS`` and
    ``DELAYED_BY_MONTHS`` read an offset. A mode whose extra field is absent is a
    malformed request, so the missing field is named rather than defaulted.
    """

    if mode not in vocab.EFFECTIVE_START_DATE_MODES:
        raise ContractError(
            f"Unknown effective start date mode {mode!r}.",
            {
                vocab.FIELD_EFFECTIVE_START_DATE_TYPE: (
                    "Must be one of: " + ", ".join(vocab.EFFECTIVE_START_DATE_MODES)
                )
            },
        )

    missing = {
        field: f"{field} is required for effective start date mode {mode}."
        for field in vocab.MODE_REQUIRED_FIELDS[mode]
        if as_text(quote.get(field)) == ""
    }
    if missing:
        raise ContractError("The effective start date is incomplete.", missing)

    if mode == vocab.MODE_ON_AGREEMENT:
        start: date | None = accepted_on
    elif mode == vocab.MODE_CUSTOM:
        start = parse_date(quote.get(vocab.FIELD_EFFECTIVE_START_DATE))
        if start is None:
            raise ContractError(
                "The custom effective start date is not a date.",
                {vocab.FIELD_EFFECTIVE_START_DATE: "Use an ISO date, for example 2026-04-01."},
            )
    elif mode == vocab.MODE_DELAYED_BY_DAYS:
        days = as_int(quote.get(vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS))
        if days is None or days < 0:
            raise ContractError(
                "The delayed-by-days offset is not a non-negative whole number.",
                {
                    vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS: "Use a whole number of days, 0 or more."
                },
            )
        start = accepted_on + timedelta(days=days)
    else:  # MODE_DELAYED_BY_MONTHS
        months = as_int(quote.get(vocab.FIELD_EFFECTIVE_START_DELAYED_MONTHS))
        if months is None or months < 0:
            raise ContractError(
                "The delayed-by-months offset is not a non-negative whole number.",
                {vocab.FIELD_EFFECTIVE_START_DELAYED_MONTHS: "Use a whole number of months."},
            )
        start = add_months(accepted_on, months)

    return {
        "mode": mode,
        "start_date": start.isoformat(),
        "accepted_on": accepted_on.isoformat(),
    }


# --------------------------------------------------------------------------- #
# Term length
# --------------------------------------------------------------------------- #


def term_length(quote: Mapping[str, Any]) -> int | None:
    """The term in months, or None when the quote names none.

    WF-086 writes no term field, so this reads the two spellings a quote may
    carry and accepts the vendor's own ``term_length`` first.
    """

    for field in ("term_length", "term_months", "term"):
        months = as_int(quote.get(field))
        if months is not None and months > 0:
            return months
    return None


def contract_end_date(start_iso: str, months: int | None) -> str | None:
    """The end date implied by a term, or None when there is no term.

    A contract with no term has no end date. WF-100 reads a missing end date as
    "not renewable", which is the honest outcome for a term that was never set.
    """

    if months is None:
        return None
    start = parse_date(start_iso)
    if start is None:
        return None
    return add_months(start, months).isoformat()


# --------------------------------------------------------------------------- #
# Inheritance
# --------------------------------------------------------------------------- #


def line_amount(line: Mapping[str, Any]) -> float:
    """The line's total, from the WF-086 ``amounts`` block or by arithmetic."""

    amounts = line.get("amounts")
    if isinstance(amounts, Mapping) and amounts.get("total") is not None:
        return round(as_number(amounts.get("total")), 2)
    return round(as_number(line.get("quantity")) * as_number(line.get("unit_price")), 2)


def contract_line_items(quote_lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The line items a contract inherits, in the shape WF-100 reads.

    WF-100 reads ``sku``, ``quantity``, ``amount`` and ``auto_renew`` off a
    contract line. This keeps those and carries the pricing detail too, so a
    reviewer can see where each figure came from. The quote line's own id is
    recorded as ``quote_line_item_id`` rather than reused: the contract's line is
    a new record, which is what inheritance means.
    """

    inherited: list[dict[str, Any]] = []
    for line in quote_lines:
        if not isinstance(line, Mapping):
            continue
        item: dict[str, Any] = {
            "name": as_text(line.get("name")) or None,
            "sku": as_text(line.get("sku")) or None,
            "description": as_text(line.get("description")) or None,
            "quantity": as_number(line.get("quantity")),
            "unit_price": as_number(line.get("unit_price")),
            "amount": line_amount(line),
            "discount_type": line.get("discount_type"),
            "discount_value": as_number(line.get("discount_value")),
            "tax_rate": as_number(line.get("tax_rate")),
            "billing_start": line.get("billing_start"),
            "quote_line_item_id": line.get("id"),
        }
        if line.get("auto_renew") is not None:
            item["auto_renew"] = line.get("auto_renew")
        inherited.append(item)
    return inherited


# --------------------------------------------------------------------------- #
# The scenario matrix
# --------------------------------------------------------------------------- #


def generation_plan(setting: Mapping[str, Any]) -> dict[str, Any]:
    """What acceptance should create, per the scenario matrix.

    Three cases are named in the specification and each is one branch here:

    * setting on and enrolled in Connected CPQ -> a contract and an order;
    * setting on, not in the beta -> a contract only;
    * setting off -> no contract.
    """

    if not as_bool(setting.get(vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES)):
        return {
            "flow": vocab.FLOW_NONE,
            "create_contract": False,
            "create_order": False,
            "reason": vocab.REASON_SETTING_OFF,
        }
    if as_bool(setting.get(vocab.FIELD_CONNECTED_CPQ)):
        return {
            "flow": vocab.FLOW_CONTRACT_AND_ORDER,
            "create_contract": True,
            "create_order": True,
            "reason": "",
        }
    return {
        "flow": vocab.FLOW_CONTRACT_ONLY,
        "create_contract": True,
        "create_order": False,
        "reason": "",
    }


def billing_flow(quote: Mapping[str, Any]) -> str:
    """Whether a subscription is expected, from the billing switch alone.

    This is deliberately independent of the contract setting. The research says
    that with the setting off, billing-enabled contracts and quotes still create
    subscriptions, so "no contract" does not mean "no subscription".
    """

    if as_bool(quote.get(vocab.FIELD_HS_BILLING_ENABLED)) or as_bool(
        quote.get(vocab.FIELD_HS_PAYMENT_ENABLED)
    ):
        return vocab.BILLING_SUBSCRIPTION
    return vocab.BILLING_NONE


# --------------------------------------------------------------------------- #
# The contract payload
# --------------------------------------------------------------------------- #


def contract_name(quote: Mapping[str, Any], quote_id: str) -> str:
    """A display name for the contract."""

    for field in ("title", "deal_name", "name"):
        text = as_text(quote.get(field))
        if text:
            return text
    return f"Contract for {quote_id}"


def contract_payload(
    *,
    quote: Mapping[str, Any],
    quote_id: str,
    quote_lines: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    effective: Mapping[str, Any],
    term: int | None,
) -> dict[str, Any]:
    """The contract a quote produces, as plain JSON for ``records.data``.

    Nothing here is a live reference to the quote. Every commercial value is
    copied, so the contract is the record of what was agreed even after the quote
    is edited, which is what "centralized source of truth" requires.
    """

    start = as_text(effective.get("start_date"))
    totals = quote.get("totals") if isinstance(quote.get("totals"), Mapping) else {}
    return {
        "name": contract_name(quote, quote_id),
        "seller": as_text(quote.get("owner")) or as_text(quote.get("seller")) or None,
        "buyer": (
            as_text(quote.get("account"))
            or as_text(quote.get("company_name"))
            or as_text(quote.get("buyer"))
            or None
        ),
        "currency": (
            as_text(quote.get("currency")) or as_text(quote.get("currency_label")) or None
        ),
        "line_items": contract_line_items(quote_lines),
        "terms": quote.get("terms"),
        "term_length": term,
        "start_date": start,
        "end_date": contract_end_date(start, term),
        "effective_start_date_mode": effective.get("mode"),
        "accepted_on": effective.get("accepted_on"),
        "source_quote_id": quote_id,
        "deal_id": quote.get("deal_id"),
        "totals": dict(totals),
        "contract_flow": plan.get("flow"),
        "order_expected": bool(plan.get("create_order")),
        "billing_flow": billing_flow(quote),
        "status": "active",
    }
