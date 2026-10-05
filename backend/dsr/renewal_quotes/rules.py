"""The rules WF-100 enforces, made executable.

Every function here is pure. It reads a mapping and returns a value or raises one of the three
errors in :mod:`dsr.renewal_quotes.errors`. Nothing here touches the clock, the store, or the
framework, which is what lets a domain test exercise a rule without a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from dsr.renewal_quotes import vocabulary as vocab
from dsr.renewal_quotes.errors import RenewalConflict, RenewalRefusal

#: Re-exported so the engine has one place to read the record field names from.
CHAIN_PREDECESSOR = vocab.CHAIN_PREDECESSOR
CHAIN_SUCCESSOR = vocab.CHAIN_SUCCESSOR
CHAIN_QUOTE = vocab.CHAIN_QUOTE
FINALISED_FLAG = vocab.FINALISED_FLAG

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant, in UTC, for a record field."""

    return (moment or utcnow()).astimezone(timezone.utc).isoformat()


def coerce_date(value: Any, field: str) -> date | None:
    """Read a date from an ISO string or a ``date``. 400 with the field named otherwise."""

    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise RenewalRefusal(
            f"{field} must be an ISO date string.",
            {field: f"Got {type(value).__name__}. Send YYYY-MM-DD."},
        )
    text = value.strip()
    # A date-only string is the researched input: the research says the seller sets the
    # change effective date "via the date picker", which sends a date and not an instant.
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:
        raise RenewalRefusal(
            f"{field} must be an ISO date.",
            {field: f"{value!r} is not an ISO date. Send YYYY-MM-DD. ({exc})"},
        ) from exc


def coerce_positive_int(value: Any, field: str, *, minimum: int = 1) -> int | None:
    """A non-negative or positive integer, or None. A boolean is not an integer here."""

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise RenewalRefusal(f"{field} must be a number.", {field: "It arrived as a boolean."})
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RenewalRefusal(
            f"{field} must be a whole number.", {field: f"{value!r} is not a number."}
        ) from exc
    if number < minimum:
        raise RenewalRefusal(
            f"{field} must be at least {minimum}.",
            {field: f"Got {number}."},
        )
    return number


def coerce_bool(value: Any, field: str, *, default: bool | None = None) -> bool | None:
    """A real boolean, or None. A string is refused rather than guessed at."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise RenewalRefusal(f"{field} must be true or false.", {field: f"Got {value!r}."})


# --------------------------------------------------------------------------- #
# Vocabulary normalisation
# --------------------------------------------------------------------------- #


def normalise_state(value: Any, *, allow: Sequence[str] = vocab.QUOTE_STATES) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text not in allow:
        raise RenewalRefusal(
            "Unknown quote state.",
            {"state": f"{value!r} is not one of {', '.join(allow)}."},
        )
    return text


def normalise_effective_date_mode(value: Any) -> str:
    text = _slug(value)
    # The researched labels are "On agreement", "Custom date", "Delayed start" and "Months".
    # A seller who read the vendor documentation sends the label, not the key.
    aliases = {
        "on_agreement": "on_agreement",
        "onagreement": "on_agreement",
        "custom_date": "custom_date",
        "date": "custom_date",
        "delayed_start": "delayed_start",
        "delayed_start_days": "delayed_start",
        "months": "months",
    }
    key = aliases.get(text)
    if key is None:
        raise RenewalRefusal(
            "Unknown change effective date mode.",
            {
                "effective_date_mode": (
                    f"{value!r} is not one of {', '.join(vocab.EFFECTIVE_DATE_MODES)}."
                )
            },
        )
    return key


def normalise_deal_selection_method(value: Any) -> str:
    text = _slug(value)
    aliases = {
        "new_deal_default_stage": "new_deal_default_stage",
        "new_deal": "new_deal_default_stage",
        "existing_deal": "existing_deal",
    }
    key = aliases.get(text)
    if key is None:
        raise RenewalRefusal(
            "Unknown deal selection method.",
            {
                "deal_selection_method": (
                    f"{value!r} is not one of {', '.join(vocab.DEAL_SELECTION_METHODS)}."
                )
            },
        )
    return key


def _slug(value: Any) -> str:
    """Lowercase, with every run of separator characters collapsed to one underscore.

    The research writes the label as "Contracts: all associated", which has a colon and a
    space next to each other. Replacing each in turn would leave two underscores, so the
    replacement collapses runs rather than substituting character by character.
    """

    text = str(value or "").strip().lower()
    for separator in (":", "-", " "):
        text = text.replace(separator, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def normalise_contract_target(value: Any) -> str:
    text = _slug(value)
    aliases = {
        "one_contract": "one_contract",
        "all_associated": "all_associated",
        "contracts_all_associated": "all_associated",
    }
    key = aliases.get(text)
    if key is None:
        raise RenewalRefusal(
            "Unknown contract target.",
            {"contract_target": (f"{value!r} is not one of {', '.join(vocab.CONTRACT_TARGETS)}.")},
        )
    return key


def normalise_name(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RenewalRefusal(f"{field} is required.", {field: "It was empty."})
    if len(text) > 200:
        raise RenewalRefusal(
            f"{field} is too long.", {field: f"At most 200 characters. Got {len(text)}."}
        )
    return text


def read_path(payload: Mapping[str, Any] | None, path: str) -> Any:
    """Read a dotted path out of a mapping, returning None when any step is missing."""

    current: Any = payload or {}
    for step in path.split("."):
        if not isinstance(current, Mapping) or step not in current:
            return None
        current = current[step]
    return current


# --------------------------------------------------------------------------- #
# Contract rules
# --------------------------------------------------------------------------- #


def term_label(contract: Mapping[str, Any]) -> str:
    """The term length label, which is Evergreen or a plain length.

    The evidence is explicit that this is a label and not a type: "If all line items are set
    to Automatically renew until canceled, the term length is marked as Evergreen." So the
    label is derived from the line items on every read and changing a line item's renewal
    setting changes the label with no separate write.
    """

    line_items = contract.get("line_items") or []
    if not isinstance(line_items, Sequence) or not line_items:
        return str(contract.get("term_length") or "unset")
    renewals = {
        str(item.get(vocab.EVERGREEN_RENEWAL_FIELD) or "")
        for item in line_items
        if isinstance(item, Mapping)
    }
    if renewals == {vocab.EVERGREEN_RENEWAL_VALUE}:
        return vocab.EVERGREEN_LABEL
    return str(contract.get("term_length") or "unset")


def contract_end_date(contract: Mapping[str, Any]) -> date | None:
    return coerce_date(contract.get("end_date"), "end_date")


def contract_is_renewable(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Whether this contract can be renewed now, with the reason when it cannot.

    A contract is renewable when it has an end date and has not already been finalised into a
    renewal. The second half is the researched chain: a contract whose renewal was finalised
    cannot be renewed again by hand, because the new contract is the renewal.
    """

    end = contract_end_date(contract)
    if end is None:
        return {
            "renewable": False,
            "reason": "The contract carries no end date, so there is no term to renew.",
            "field": "end_date",
        }
    if contract.get(FINALISED_FLAG):
        return {
            "renewable": False,
            "reason": (
                "This contract has already been renewed. Renew the contract the acceptance "
                "created instead."
            ),
            "field": FINALISED_FLAG,
        }
    return {"renewable": True, "reason": "", "field": ""}


def require_renewable(contract: Mapping[str, Any]) -> str:
    """The contract id, or a 409 naming the way out."""

    state = contract_is_renewable(contract)
    if state["renewable"]:
        return str(contract.get("id"))
    raise RenewalConflict(
        f"This contract cannot be renewed. {state['reason']}",
        remedy=(
            "Renew a contract that has an end date and has not been renewed yet, or open the "
            "contract the last acceptance created."
        ),
        evidence=vocab.EVIDENCE["renewal_creates_contract"],
    )


def renewal_date(contract: Mapping[str, Any], quote: Mapping[str, Any] | None) -> dict[str, Any]:
    """The contract's renewal date, by whichever branch of the rule applies.

    Both branches are implemented. A contract with no finalised renewal reports the date the
    contract ends. A contract whose renewal quote was accepted reports the effective date of
    that quote.

    The accepted quote's resolved date is preferred over its stored mode. An On agreement quote
    has no stored date until the day of agreement, so reading only the stored field would put
    the accepted quote on the not-finalised branch and report the old end date.
    """

    if quote and quote.get("state") == "accepted":
        resolved = coerce_optional_date(read_path(quote, "effective_date.resolved_on"))
        stored = coerce_optional_date(read_path(quote, "effective_date.on"))
        effective = resolved or stored
        return {
            "renewal_date": effective.isoformat() if effective else None,
            "branch": "if_finalised",
            "rule": vocab.RENEWAL_DATE_RULE_IF_FINALISED,
            "source_quote_id": quote.get("id"),
        }
    end = contract_end_date(contract)
    return {
        "renewal_date": end.isoformat() if end else None,
        "branch": "if_not_finalised",
        "rule": vocab.RENEWAL_DATE_RULE_IF_NOT_FINALISED,
        "source_quote_id": None,
    }


def alert_due_date(contract: Mapping[str, Any], quote: Mapping[str, Any] | None) -> dict[str, Any]:
    """When the renewal alert appears, from the renewal date and a day offset.

    The research says the alert appears "before the renewal date using a configured day
    offset" and gives no default and no bound, so an offset is an integer of any size and 0
    days means the alert appears on the renewal date itself.
    """

    state = renewal_date(contract, quote)
    if not state["renewal_date"]:
        return {
            "due": None,
            "offset_days": vocab.ALERT_OFFSET_DEFAULT,
            "note": vocab.ALERT_OFFSET_NOTE,
        }
    base = date.fromisoformat(str(state["renewal_date"]))
    offset = coerce_positive_int(contract.get("alert_offset_days"), "alert_offset_days", minimum=0)
    if offset is None:
        offset = vocab.ALERT_OFFSET_DEFAULT
    return {
        "due": (base - timedelta(days=offset)).isoformat(),
        "renewal_date": state["renewal_date"],
        "branch": state["branch"],
        "offset_days": offset,
        "note": vocab.ALERT_OFFSET_NOTE,
    }


# --------------------------------------------------------------------------- #
# The effective date rule
# --------------------------------------------------------------------------- #


def effective_date(quote: Mapping[str, Any], *, accepted_on: date | None = None) -> dict[str, Any]:
    """Resolve the researched Summary module's change effective date.

    The research lists four modes: "On agreement / Custom Date / Delayed start days /
    months". Each resolves to a date from the same quote record, and the mode that answered is
    reported so a reader can tell a delayed start from a custom date that happens to be the
    same date.
    """

    stored = quote.get("effective_date") or {}
    mode = normalise_effective_date_mode(stored.get("mode") or "on_agreement")
    if mode == "on_agreement":
        resolved = accepted_on
    elif mode == "custom_date":
        resolved = coerce_date(stored.get("on"), "effective_date.on")
    elif mode == "delayed_start":
        days = coerce_positive_int(stored.get("delay_days"), "effective_date.delay_days", minimum=0)
        base = accepted_on or coerce_date(stored.get("agreed_on"), "effective_date.agreed_on")
        resolved = (
            (base + timedelta(days=days)) if (base is not None and days is not None) else None
        )
    else:
        months = coerce_positive_int(
            stored.get("delay_months"), "effective_date.delay_months", minimum=0
        )
        base = accepted_on or coerce_date(stored.get("agreed_on"), "effective_date.agreed_on")
        resolved = _add_months(base, months) if (base is not None and months is not None) else None
    return {
        "mode": mode,
        "label": vocab.EFFECTIVE_DATE_MODE_LABELS[mode],
        "documented": vocab.EFFECTIVE_DATE_MODE_DOCUMENTED[mode],
        "on": resolved.isoformat() if resolved else None,
        "resolved": resolved is not None,
    }


def coerce_optional_date(value: Any) -> date | None:
    """A date, or None when the value is absent or unparseable.

    Never raises, because every caller uses it on a field the caller did not write and a rule
    that refused to read its own optional field would turn a missing date into a 400.
    """

    if value in (None, ""):
        return None
    try:
        return coerce_date(value, "date")
    except RenewalRefusal:
        return None


def _add_months(base: date, months: int | None) -> date | None:
    if months is None:
        return None
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(
        base.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return date(year, month, day)


def require_resolved_effective_date(resolved: Mapping[str, Any]) -> str:
    """The resolved effective date, or a 409 naming the mode that could not resolve."""

    if resolved.get("resolved") and resolved.get("on"):
        return str(resolved["on"])
    mode = str(resolved.get("mode"))
    if mode == "on_agreement":
        raise RenewalConflict(
            "The change effective date has no date because the quote has not been accepted.",
            remedy="Accept the quote, then read the renewal date again.",
            evidence=vocab.RENEWAL_DATE_RULE_IF_FINALISED,
        )
    raise RenewalConflict(
        "The change effective date did not resolve.",
        remedy=(
            "Set the value this mode needs. A custom date needs a date. A delayed start needs a "
            "day count or a month count."
        ),
        evidence=vocab.EFFECTIVE_DATE_MODE_DOCUMENTED.get(mode, ""),
    )


def prorated_charges(
    quote: Mapping[str, Any], *, from_date: date | None, to_date: date | None
) -> dict[str, Any]:
    """The researched proration rule, or the reason it did not apply.

    The research describes a checkbox the seller can optionally clear: "Prorate charges and
    credits for the remaining billing period". Cleared means the remaining period carries no
    prorated charge and no prorated credit.
    """

    enabled = coerce_bool(quote.get("prorate"), "prorate", default=False)
    if not enabled:
        return {
            "prorated": False,
            "enabled": False,
            "reason": (
                "The seller cleared the proration checkbox, so the remaining billing period "
                "carries no prorated charge and no prorated credit."
            ),
        }
    if from_date is None or to_date is None:
        return {
            "prorated": False,
            "enabled": True,
            "reason": "Proration needs both a contract start and an end date to prorate against.",
        }
    span = (to_date - from_date).days
    if span <= 0:
        return {
            "prorated": False,
            "enabled": True,
            "reason": "The contract has no positive length, so there is no period to prorate.",
        }
    return {"prorated": True, "enabled": True, "period_days": span, "reason": ""}


def sum_amounts(values: Iterable[Any]) -> float:
    total = 0.0
    for value in values:
        if isinstance(value, bool) or value in (None, ""):
            continue
        try:
            total += float(value)
        except (TypeError, ValueError):
            continue
    return round(total, 2)
