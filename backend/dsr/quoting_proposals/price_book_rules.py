"""WF-088: the rules a deal's price book is chosen by, made executable.

Pricing, quoting and proposals. Every function here is pure: it takes values and
returns values, reads nothing and writes nothing. The vocabulary is
:mod:`dsr.quoting_proposals.price_book_vocabulary` and the writes are in
:mod:`dsr.quoting_proposals.price_book_engine`.

The rules the rest of the workflow leans on
--------------------------------------------

**One rule matching exactly is what assigns.** "When a deal matches exactly one
assignment rule, that price book is automatically assigned." Quoted whole.
:func:`decide_assignment` requires exactly one candidate to write anything, so the
sourced sentence is the code rather than a paragraph.

**More than one match assigns nothing.** The research gives two behaviours and no
rule for choosing between them, and :func:`decide_assignment` implements the one
place they agree: nothing is written and a person chooses. See
``MULTIPLE_MATCHES_NEED_A_CHOICE`` in the inferences module for the reading and the
alternative that was rejected.

**A rule with no filters matches nothing.** :func:`validate_rule` refuses to save
one, because a rule that matched every deal would put a price book on every deal.

**The property is a dotted JSON path.** "configure deal-property filters". The store
holds arbitrary JSON and a team may name any property, so this module resolves a
path against whatever the record carries. A missing path does not match, which is
what makes a rule that names a property no deal carries a rule that never fires.

**Filters combine with AND by default and can combine with OR.** The source names
both: "configure deal-property filters (`and` / `or` groups)". :func:`matches_filters`
takes the mode from the rule and reports every filter either way, because the
flow's right panel has to "review the matching deals" and a seller who is told "two
rules matched" with no filter report has nothing to review.

**Auto-assignment runs on create and never again.** "Price books are auto-assigned
only when a deal is created. After a price book is auto-assigned, HubSpot won't run
auto-assignment again if the deal or associated company properties used in the
filter are updated". Quoted whole. :func:`decide_assignment` refuses to write twice,
so the second sentence follows from the first rather than being a second rule
somebody has to remember.

**A price book is a reference, not a lookup.** WF-087 provisions the catalogue and
has not shipped. :func:`normalise_book` takes an id or a name and refuses neither,
so a rule saved today works when the catalogue lands. See
``PRICE_BOOKS_ARE_REFERENCED_NOT_RESOLVED``.

**Changing the book removes the previous book's lines.** "If the price book is
changed, any line items associated with the previous price book will be removed."
:func:`lines_for_book` decides which lines those are, and the engine removes exactly
those and names them on the assignment.

**A quote inherits and cannot be set.** "Quotes inherit the price book from the
associated deal. Users can't select a price book when creating a quote; they must
select it on the deal." :func:`evaluate_inheritance` derives it and
:func:`require_set_on_deal` raises rather than returning a boolean, so a caller
cannot forget to check it.

What this module does not decide
--------------------------------

Whether a price book is the right one for a buyer, and whether two rules that both
match should both have been written. It matches properties against configured
filters, resolves the one book a rule names, and decides which of the researched
outcomes a set of candidates produces. It never asserts that a price is right.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from dsr.quoting_proposals import price_book_vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product. That is what makes it
# safe for the feature module to map them: a handler for a shared type such as
# ``ValueError`` would intercept that exception across the whole application.


class PriceBookRefusal(ValueError):
    """A rule, a filter or an override this workflow will not accept.

    Carries a code the feature module maps to a status and a field-keyed map,
    because a page puts each message beside the input that caused it rather than in
    one combined sentence.
    """

    def __init__(
        self, code: str, message: str | None = None, errors: Mapping[str, str] | None = None
    ):
        status, detail = vocab.ERROR_CODES.get(code, (422, code))
        super().__init__(message or detail)
        self.code = code
        self.status = status
        self.detail = message or detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "errors": self.errors,
        }


class PriceBookNotFound(LookupError):
    """No such rule, no such assignment, no such deal.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as
    every other workflow: a feature may only map error types it raises itself.
    """

    code = "unknown_assignment"
    status = 404

    def __init__(self, code: str, label: str, record_id: str) -> None:
        super().__init__(f"{label} {record_id} not found")
        self.code = code
        self.record_id = record_id


def refuse(code: str, message: str | None = None, **errors: str) -> PriceBookRefusal:
    """A refusal of a known code, built at the call site that raised it."""
    return PriceBookRefusal(code, message, errors or None)


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept."""
    value = moment or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def normalise_key(value: Any, field: str = "name") -> str:
    """A lower-cased identifier, checked for shape.

    A rule key is an API-facing name, so it is held to a shape rather than stored as
    anything at all. An empty or punctuated key would make a URL this product has to
    route on ambiguous.
    """
    text = str(value or "").strip().lower().replace(" ", "-")
    if not text:
        raise refuse("rule_needs_a_name", **{field: vocab.ERROR_CODES["rule_needs_a_name"][1]})
    if not _SLUG_RE.match(text):
        raise refuse(
            "rule_needs_a_name",
            f"{text!r} is not a usable rule name.",
            **{
                field: "A rule name uses lower-case letters, digits, a hyphen or an underscore, and is between 2 and 64 characters."
            },
        )
    return text


def normalise_object(value: Any) -> str:
    """The object a filter reads.

    The two the research names are accepted and so is anything else, because
    "Filters can target any object ... and any standard or custom property" is the
    reason the set is not closed by this product. Normalising to a lower-case slug
    keeps a rule written by hand and a rule written by a page the same value.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        raise refuse(
            "unknown_filter_object", **{"object": vocab.ERROR_CODES["unknown_filter_object"][1]}
        )
    return text


def normalise_operator(value: Any) -> str:
    """One known operator, or a refusal naming the set."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.OPERATORS:
        raise refuse("unknown_operator", **{"operator": vocab.ERROR_CODES["unknown_operator"][1]})
    return text


def normalise_match_mode(value: Any) -> str:
    """``all`` or ``any``, or a refusal naming both.

    ``and`` and ``or`` are accepted because the research spells the groups that way,
    and a caller who typed what the source says should not have to know this build's
    spelling.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("and", "all_of"):
        text = vocab.FILTER_MATCH_ALL
    elif text in ("or", "any_of"):
        text = vocab.FILTER_MATCH_ANY
    if text not in vocab.FILTER_MATCH_MODES:
        raise refuse(
            "unknown_match_mode", **{"matchMode": vocab.ERROR_CODES["unknown_match_mode"][1]}
        )
    return text


def normalise_trigger(value: Any) -> str:
    """``create`` or ``update``, or a refusal naming the two."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("deal_created", "on_create", "created"):
        text = vocab.TRIGGER_CREATE
    elif text in ("deal_updated", "on_update", "updated"):
        text = vocab.TRIGGER_UPDATE
    if text not in vocab.TRIGGERS:
        raise refuse("unknown_trigger", **{"trigger": vocab.ERROR_CODES["unknown_trigger"][1]})
    return text


# --------------------------------------------------------------------------- #
# Reading a property
# --------------------------------------------------------------------------- #


def read_path(payload: Any, path: str) -> Any:
    """Read one dotted JSON path, or ``None`` when any step of it is missing.

    ``None`` is a real answer and not an error. The store holds arbitrary JSON, so a
    rule may bind to a path this repository declares nothing about, and the only
    honest result for a path the record does not carry is that the record does not
    carry it.
    """
    current = payload
    for part in str(path or "").split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _as_decimal(value: Any, field: str) -> Decimal:
    """One filter value as a decimal, refusing a boolean.

    A boolean is 1 in Python, and ``True > 0`` is a comparison that runs and means
    nothing, so it is refused rather than read as one.
    """
    if isinstance(value, bool):
        raise refuse(
            "numeric_filter_value_is_not_a_number",
            **{field: f"{field} must be a number. A boolean is not a number."},
        )
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise refuse(
            "numeric_filter_value_is_not_a_number",
            **{field: f"{field} must be a number, not {value!r}."},
        ) from exc


def _as_number(value: Any) -> float | None:
    """A stored property value as a number, or ``None`` when it is not one.

    ``None`` rather than a refusal, because a property may hold text on some deals
    and a number on others and a rule that compares numbers should simply not match
    the text ones. Refusing would make a filter un-saveable because one existing
    record has the wrong type.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_one_of(actual: Any, wanted: set[str]) -> bool:
    """Whether a stored value is, or contains, one of a set of words.

    Two cases, because the store holds arbitrary JSON and both shapes are real. A
    multi-select property is a list, so "is any of" asks whether *any* of its entries is
    wanted. A scalar is itself. Joining a list into one string and comparing it was the
    first implementation and it is wrong: ``["silver", "gold"]`` joined to
    ``"silver gold"`` never equals ``"gold"``, so a seller writing a tag rule would get a
    rule that saves cleanly and never fires.
    """
    if isinstance(actual, (list, tuple)):
        return any(as_text(entry) in wanted for entry in actual)
    return as_text(actual) in wanted


def _any_element(actual: Any, needle: str) -> bool:
    """Whether a stored value contains a word, whole or as one of its entries.

    The same two cases as :func:`_is_one_of`, for ``contains``. On a list, "contains x"
    means one of the entries contains x, not that the joined string does.
    """
    if isinstance(actual, (list, tuple)):
        return any(needle in as_text(entry) for entry in actual)
    return needle in as_text(actual)


def as_text(value: Any) -> str:
    """A value as lower-cased text, for the word operators and for a price book id.

    A nested object reads as its id, then as its name, because an association is stored
    as an object and a filter on one has to resolve. It is lower-cased on the way out,
    like every other branch: an operator that compares a nested object against a saved
    value and a scalar against the same value has to agree on case.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(as_text(entry) for entry in value)
    if isinstance(value, Mapping):
        # Lower-cased like every other branch, so a filter comparing an association
        # against a saved value and one comparing a scalar agree on case.
        return str(value.get("id") or value.get("name") or "").strip().lower()
    return str(value).strip().lower()


# --------------------------------------------------------------------------- #
# Price book references
# --------------------------------------------------------------------------- #


def normalise_book(value: Any, field: str = "price_book") -> dict[str, Any]:
    """A price book reference, as ``{"id", "name"}``.

    Both are optional in the payload and at least one is required, because a rule is
    configured by opening a price book and an override is chosen from a dropdown, and
    a deployment may identify its books by name only. This is the reference-not-a-
    -lookup decision: WF-087 has not shipped, so a book that cannot be resolved
    against a catalogue is still a usable book here.
    """
    if isinstance(value, Mapping):
        raw_id = value.get(vocab.BOOK_ID) or value.get("price_book_id") or value.get("pricelevelid")
        raw_name = value.get(vocab.BOOK_NAME) or value.get("label") or value.get("title")
    elif value in (None, ""):
        raw_id = raw_name = None
    else:
        # A bare string is an id if it looks like one this product minted, and a
        # name otherwise. Guessing is stated rather than hidden: a deployment that
        # names its books with uuids gets ids, and one that names them "Enterprise
        # 2026" gets names, and either way the reference round-trips.
        text = str(value).strip()
        raw_id, raw_name = (text, None) if _SLUG_RE.match(text.lower()) else (None, text)

    identifier = str(raw_id).strip() if raw_id not in (None, "") else ""
    name = str(raw_name).strip() if raw_name not in (None, "") else ""
    if not identifier and not name:
        raise refuse(
            "price_book_needs_an_id_or_a_name",
            **{field: vocab.ERROR_CODES["price_book_needs_an_id_or_a_name"][1]},
        )
    return {vocab.BOOK_ID: identifier or None, vocab.BOOK_NAME: name or None}


def book_identity(book: Any) -> str:
    """One comparable string for a price book: its id if it has one, else its name.

    Two references are the same book when this returns the same string. That is what
    makes "already assigned" and "changed to the same book" decidable without
    resolving either reference against a catalogue.
    """
    if not isinstance(book, Mapping):
        return ""
    identifier = str(book.get(vocab.BOOK_ID) or "").strip()
    if identifier:
        return identifier
    return str(book.get(vocab.BOOK_NAME) or "").strip()


def read_book_field(payload: Mapping[str, Any]) -> tuple[str | None, Any]:
    """The price book on a deal or a quote, and the field it was found in.

    Read through :data:`~dsr.quoting_proposals.price_book_vocabulary.PRICE_BOOK_FIELD_ALIASES`
    and reported with the field that answered, so a page can say where the value came
    from rather than implying this workflow wrote it. The first alias that carries
    something wins, which is why a deal a mirror already prices is read rather than
    overwritten by a rule.
    """
    for field in vocab.PRICE_BOOK_FIELD_ALIASES:
        if field in payload and payload[field] not in (None, "", {}):
            return field, payload[field]
    return None, None


def book_view(value: Any) -> dict[str, Any] | None:
    """A price book reference as a displayable object, or ``None``.

    Returns whatever the record already carried rather than a rebuilt object, so a
    reference this workflow never wrote keeps the fields its writer gave it.
    """
    if value in (None, "", {}):
        return None
    if isinstance(value, Mapping):
        book = dict(value)
        book.setdefault(vocab.BOOK_ID, None)
        book.setdefault(vocab.BOOK_NAME, None)
        return book
    return normalise_book(value, "price_book")


def book_label(book: Any) -> str:
    """The words a card shows for a price book: the name, else the id."""
    view = book_view(book) or {}
    return str(view.get(vocab.BOOK_NAME) or view.get(vocab.BOOK_ID) or vocab.PRICE_BOOK_NONE_LABEL)


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


def validate_filter(payload: Any, index: int = 0) -> dict[str, Any]:
    """One filter, checked and normalised.

    ``property`` is a dotted path and is not checked against a list of properties,
    because the flow picks one from the object's property list at runtime and this
    repository declares no such list. The two operators that need a list are checked
    for a list and the four that compare numbers are checked for a number, because
    those two mistakes make a rule that reads as configured and never fires.
    """
    where = f"filters[{index}]"
    if not isinstance(payload, Mapping):
        raise refuse("filter_needs_a_property", **{where: "Each filter is an object."})

    prop = str(payload.get("property") or "").strip()
    if not prop:
        raise refuse(
            "filter_needs_a_property",
            **{
                "property": f"{where} needs a property. Choose one from the object's property list."
            },
        )

    operator = normalise_operator(payload.get("operator") or vocab.OPERATOR_IS)
    value = payload.get("value")

    if operator in vocab.LIST_OPERATORS:
        if not isinstance(value, (list, tuple)) or not value:
            raise refuse(
                "list_operator_needs_a_list",
                **{"value": f"{where} uses {operator!r}, so it needs a non-empty list of values."},
            )
        values = [entry for entry in (as_text(item) for item in value) if entry]
        if not values:
            raise refuse(
                "list_operator_needs_a_list",
                **{"value": f"{where} uses {operator!r} and every value it was given is empty."},
            )
        return {
            "object": normalise_object(payload.get("object") or vocab.FILTER_OBJECT_DEAL),
            "property": prop,
            "operator": operator,
            "value": values,
        }

    if value in (None, ""):
        raise refuse(
            "filter_needs_a_value",
            **{"value": f"{where} needs a value to compare {prop} with."},
        )

    if operator in vocab.NUMERIC_OPERATORS:
        _as_decimal(value, "value")
        return {
            "object": normalise_object(payload.get("object") or vocab.FILTER_OBJECT_DEAL),
            "property": prop,
            "operator": operator,
            "value": value,
        }

    return {
        "object": normalise_object(payload.get("object") or vocab.FILTER_OBJECT_DEAL),
        "property": prop,
        "operator": operator,
        "value": as_text(value),
    }


def filter_matches(
    one: Mapping[str, Any],
    deal: Mapping[str, Any],
    company: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Whether one filter holds for a deal, and what it read.

    The object the filter names decides the payload it reads: a deal filter reads the
    deal and a company filter reads the company the deal points at. The answer carries
    ``actual`` and ``present``, because the flow's right panel has to "review the
    matching deals" and a report that says "this filter did not match" without saying
    what it looked at is the same as no report.
    """
    target = str(one.get("object") or vocab.FILTER_OBJECT_DEAL)
    prop = str(one.get("property") or "")
    operator = str(one.get("operator") or vocab.OPERATOR_IS)
    expected = one.get("value")

    if target == vocab.FILTER_OBJECT_COMPANY:
        payload: Any = company if isinstance(company, Mapping) else {}
    else:
        payload = deal

    if not payload:
        return {
            "matched": False,
            "object": target,
            "property": prop,
            "operator": operator,
            "expected": expected,
            "actual": None,
            "present": False,
            "reason": f"This deal has no {target.replace('_', ' ')} to read {prop} from.",
        }

    comparison = _compare(operator, read_path(payload, prop), expected)
    # The filter's own identity is folded onto the comparison rather than left beside it.
    # A report row that says only `matched: false` is useless on a panel that has to
    # explain which of a rule's filters failed, and it is this dict that is stored on the
    # assignment, so a comparison without its identity would be stored that way too.
    return {
        **comparison,
        "object": target,
        "property": prop,
        "operator": operator,
        "expected": expected,
    }


def _compare(operator: str, actual: Any, expected: Any) -> dict[str, Any]:
    """One operator applied to one value, and whether the value was there at all."""
    present = actual is not None
    if operator in vocab.NUMERIC_OPERATORS:
        left = _as_number(actual)
        right = _as_decimal(expected, "value")
        if left is None:
            return {"matched": False, "actual": actual, "present": present}
        matched = {
            vocab.OPERATOR_GREATER_THAN: left > float(right),
            vocab.OPERATOR_GREATER_THAN_OR_EQUAL: left >= float(right),
            vocab.OPERATOR_LESS_THAN: left < float(right),
            vocab.OPERATOR_LESS_THAN_OR_EQUAL: left <= float(right),
        }[operator]
        return {"matched": matched, "actual": actual, "present": present}

    if operator == vocab.OPERATOR_IS:
        return {
            "matched": present and as_text(actual) == as_text(expected),
            "actual": actual,
            "present": present,
        }
    if operator == vocab.OPERATOR_IS_NOT:
        return {
            "matched": not present or as_text(actual) != as_text(expected),
            "actual": actual,
            "present": present,
        }
    if operator == vocab.OPERATOR_CONTAINS:
        return {
            "matched": present and _any_element(actual, as_text(expected)),
            "actual": actual,
            "present": present,
        }
    if operator == vocab.OPERATOR_DOES_NOT_CONTAIN:
        return {
            "matched": not present or not _any_element(actual, as_text(expected)),
            "actual": actual,
            "present": present,
        }
    if operator == vocab.OPERATOR_IN:
        wanted = {as_text(entry) for entry in (expected or [])}
        return {
            "matched": present and _is_one_of(actual, wanted),
            "actual": actual,
            "present": present,
        }
    if operator == vocab.OPERATOR_NOT_IN:
        unwanted = {as_text(entry) for entry in (expected or [])}
        return {
            "matched": not present or not _is_one_of(actual, unwanted),
            "actual": actual,
            "present": present,
        }
    # The vocabulary is closed and validate_filter rejects anything else.
    return {"matched": False, "actual": actual, "present": present}  # pragma: no cover


def matches_filters(
    filters: Sequence[Mapping[str, Any]],
    deal: Mapping[str, Any],
    company: Mapping[str, Any] | None = None,
    mode: str = vocab.FILTER_MATCH_ALL,
) -> dict[str, Any]:
    """Whether a rule's filters hold, and the per-filter report.

    ``all`` is the default because the flow's "+ Add filter" builds an AND list and
    because a rule that assigns a price book to a deal on any one of five conditions
    is a rule nobody can reason about. ``any`` is honoured because the research names
    or groups, and a rule that reaches it did so deliberately.

    A rule with no filters returns ``matched: False`` rather than ``True``, because
    :func:`validate_rule` refuses to save such a rule and a rule read from the store
    that has no filter must not match every deal.
    """
    if not filters:
        return {"matched": False, "mode": mode, "filters": [], "matched_count": 0, "total": 0}

    reports = [filter_matches(one, deal, company) for one in filters]
    matched = [report for report in reports if report["matched"]]
    satisfied = len(matched) == len(reports) if mode == vocab.FILTER_MATCH_ALL else bool(matched)
    return {
        "matched": satisfied,
        "mode": mode,
        "filters": reports,
        "matched_count": len(matched),
        "total": len(reports),
    }


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def validate_rule(payload: Any) -> dict[str, Any]:
    """One assignment rule, checked and normalised.

    Every field the flow sets is validated here rather than at assignment time, so a
    seller finds out at save time that a rule names no price book rather than when a
    deal arrives and nothing is assigned. A rule that is only half configured is the
    failure mode the research's own UI prevents by refusing to save.
    """
    if not isinstance(payload, Mapping):
        raise refuse("rule_needs_a_name")

    key = normalise_key(payload.get("key") or payload.get("name"))
    label = str(payload.get("label") or payload.get("name") or key).strip() or key

    raw_filters = payload.get("filters")
    if not isinstance(raw_filters, (list, tuple)) or not raw_filters:
        raise refuse(
            "rule_needs_a_filter", **{"filters": vocab.ERROR_CODES["rule_needs_a_filter"][1]}
        )
    filters = [validate_filter(one, index) for index, one in enumerate(raw_filters)]

    # The default is applied before normalisation, not after: an absent ``matchMode``
    # is the flow's default configuration rather than an unknown mode, and normalising
    # ``None`` would refuse every rule the research says a seller can save.
    match_mode = normalise_match_mode(
        payload.get("matchMode") or payload.get("match_mode") or vocab.FILTER_MATCH_ALL
    )

    # A rule missing its book is refused with the rule-level code rather than the
    # reference-level one ``normalise_book`` raises. Both are true, and the difference
    # is who is being told: "name the price book to set" is right for an override and
    # wrong for a rule, where the sourced sentence is that the rule has nothing to
    # assign. Without this, ``rule_needs_a_price_book`` would be a code in the
    # vocabulary that nothing ever raises.
    try:
        book = normalise_book(payload.get("price_book") or payload.get("priceBook"))
    except PriceBookRefusal as exc:
        raise refuse(
            "rule_needs_a_price_book",
            vocab.ERROR_CODES["rule_needs_a_price_book"][1],
            **exc.errors,
        ) from exc

    return {
        "key": key,
        "label": label,
        vocab.ENABLED: bool(payload.get(vocab.ENABLED, vocab.DEFAULT_ENABLED)),
        vocab.AUTO_ASSIGN: bool(payload.get(vocab.AUTO_ASSIGN, vocab.DEFAULT_AUTO_ASSIGN)),
        "price_book": book,
        "filters": filters,
        "matchMode": match_mode,
    }


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def decide_assignment(
    matched: Sequence[Mapping[str, Any]],
    *,
    current: Any = None,
    trigger: str = vocab.TRIGGER_CREATE,
) -> dict[str, Any]:
    """Which of the researched outcomes a set of matching rules produces.

    Nine outcomes and one function, because the order they are tested in is the rule:

    1. **A non-create trigger assigns nothing.** "Price books are auto-assigned only
       when a deal is created." Tested first, because it is about the moment rather
       than about the rules and it holds whatever they are.
    2. **A deal that already has a book is left alone.** The second half of the same
       sentence: "HubSpot won't run auto-assignment again if the deal or associated
       company properties used in the filter are updated". Reported as its own reason
       so a seller re-running the message on an already-priced deal is told why
       nothing moved rather than being shown the same answer twice.
    3. **Nothing matched.** The manual-only mode, and the honest answer to a seller
       who asked.
    4. **Something matched but is inactive.** The price book's own **Inactive** switch
       is on.
    5. **Something matched and is active but not auto-assigning.** The research's
       test-first mode: "**Assignment rules without auto-assignment** (test first)".
    6. **More than one rule matched.** Nothing is written and the candidates are
       returned, because both sourced sentences about this case agree that a person
       chooses.
    7. **Exactly one rule matched.** The book is written. "When a deal matches exactly
       one assignment rule, that price book is automatically assigned."

    The auto-assign switch is applied before the count, so a test-first rule never
    becomes a candidate. That is what stops a rule somebody is testing from competing
    for a deal that another rule should have priced.

    ``matched`` is one entry per rule with its ``rule`` and its ``report``, the shape
    the engine builds.
    """
    candidates = [
        entry
        for entry in matched
        if entry["rule"].get(vocab.ENABLED, True) and entry["rule"].get(vocab.AUTO_ASSIGN, True)
    ]
    report = {
        "outcome": None,
        "writes": False,
        "price_book": None,
        "rule_id": None,
        "candidates": [
            {
                "rule_id": entry["rule"].get("id"),
                "rule_label": entry["rule"].get("label"),
                "price_book": book_view(entry["rule"].get("price_book")),
                "matched_filters": entry["report"]["filters"],
                "matched_count": entry["report"]["matched_count"],
                "total": entry["report"]["total"],
            }
            for entry in matched
        ],
        "auto_assigning_candidates": len(candidates),
        "matched_rules": len(matched),
        "trigger": trigger,
        "current_price_book": book_view(current),
    }

    if trigger != vocab.TRIGGER_CREATE:
        report["outcome"] = vocab.ASSIGNMENT_NOT_ON_CREATE
        report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_NOT_ON_CREATE]
        return report

    if book_view(current) is not None:
        report["outcome"] = vocab.ASSIGNMENT_ALREADY_ASSIGNED
        report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_ALREADY_ASSIGNED]
        return report

    if not matched:
        report["outcome"] = vocab.ASSIGNMENT_NO_MATCH
        report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_NO_MATCH]
        return report

    if not any(entry["rule"].get(vocab.ENABLED, True) for entry in matched):
        report["outcome"] = vocab.ASSIGNMENT_NO_MATCH_INACTIVE_RULE
        report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[
            vocab.ASSIGNMENT_NO_MATCH_INACTIVE_RULE
        ]
        return report

    if not candidates:
        report["outcome"] = vocab.ASSIGNMENT_NO_AUTO_ASSIGN
        report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_NO_AUTO_ASSIGN]
        return report

    if len(candidates) > 1:
        report["outcome"] = vocab.ASSIGNMENT_NEEDS_CHOICE
        report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_NEEDS_CHOICE]
        return report

    head = candidates[0]
    report["outcome"] = vocab.ASSIGNMENT_ASSIGNED
    report["writes"] = True
    report["explanation"] = vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_ASSIGNED]
    report["price_book"] = book_view(head["rule"].get("price_book"))
    report["rule_id"] = head["rule"].get("id")
    report["rule_label"] = head["rule"].get("label")
    report["matched_filters"] = head["report"]["filters"]
    return report


def workspace_mode(rules: Sequence[Mapping[str, Any]]) -> str:
    """Which of the three sourced modes a set of rules puts a workspace in.

    Read from the rules' own two switches rather than from a setting, because in the
    source each of the three modes is a configuration of rules and none of them is a
    switch. A rule that is saved but inactive does not count, which is what makes
    "turn the price book's Inactive switch off to activate" the difference between the
    first mode and the second.
    """
    active = [rule for rule in rules if rule.get(vocab.ENABLED, True)]
    if not active:
        return vocab.MODE_NO_RULES
    if any(rule.get(vocab.AUTO_ASSIGN, True) for rule in active):
        return vocab.MODE_AUTO_ASSIGN
    return vocab.MODE_TEST_ONLY


# --------------------------------------------------------------------------- #
# Line items and the previous book
# --------------------------------------------------------------------------- #


def lines_for_book(line_items: Sequence[Mapping[str, Any]], book: Any) -> list[dict[str, Any]]:
    """The line items associated with one price book.

    "If the price book is changed, any line items associated with the previous price
    book will be removed." Quoted whole. Two cases count as associated:

    * the line names the book, through any of
      :data:`~dsr.quoting_proposals.price_book_vocabulary.LINE_BOOK_FIELDS`; or
    * the line names no book at all, in which case it belongs to the deal's book
      because the sourced data flow puts the book on the deal header and then scopes
      the line items to it.

    The second case is why the function takes the previous book rather than filtering
    lines on their own field, and it is the reading recorded as
    ``OVERRIDE_REMOVES_THE_PREVIOUS_BOOKS_LINES``. A line that names a *different*
    book is not removed: it is not a line of the previous book, and removing it would
    be a deletion the source does not describe.
    """
    wanted = book_identity(book)
    if not wanted:
        return []
    claimed: list[dict[str, Any]] = []
    for line in line_items:
        named: str | None = None
        for field in vocab.LINE_BOOK_FIELDS:
            if field in line and line[field] not in (None, "", {}):
                named = book_identity(line[field])
                break
        if named is None or named == wanted:
            claimed.append(dict(line))
    return claimed


# --------------------------------------------------------------------------- #
# The quote
# --------------------------------------------------------------------------- #


def evaluate_inheritance(
    quote: Mapping[str, Any],
    deal: Mapping[str, Any] | None,
    *,
    deal_field: str | None = None,
    deal_price_book: Any = None,
    deal_missing: str | None = None,
) -> dict[str, Any]:
    """The price book a quote gets, and where that value came from.

    "Quotes inherit the price book from the associated deal." So the answer is the
    deal's book and nothing else. ``authority`` says whether the deal carried one and
    whether it came from this workflow's own write or from whatever wrote the deal,
    because a quote whose price book was set by a CRM mirror is a different fact from
    one this product priced.

    A quote with no associated deal reports ``authority: none`` rather than raising:
    a quote is allowed to exist before its deal does, and the honest answer is that
    there is nothing to inherit.

    ``reason`` branches on whether the *deal* exists, not on whether a book was found
    on it. Those are different facts and a reader cannot act on the wrong one: a quote
    whose deal is absent, a quote whose deal is present but unpriced, and a quote whose
    deal is named but does not exist all leave ``price_book`` empty, and reporting the
    wrong one of those sends a seller to repair something that is already correct, or
    hides a broken reference nobody would otherwise find. ``deal_missing`` carries the
    id the quote named, and is what separates the third case from the first.
    """
    has_deal = isinstance(deal, Mapping)
    inherited = deal_price_book if has_deal else None
    if inherited is not None:
        reason = vocab.INHERITANCE_REASON_FROM_DEAL
    elif has_deal:
        reason = vocab.INHERITANCE_REASON_DEAL_HAS_NO_BOOK
    elif deal_missing:
        reason = vocab.INHERITANCE_REASON_DEAL_MISSING
    else:
        reason = vocab.INHERITANCE_REASON_NO_DEAL

    return {
        "price_book": book_view(inherited),
        "price_book_label": book_label(inherited),
        "authority": (
            vocab.INHERITANCE_AUTHORITY_DEAL
            if inherited is not None
            else vocab.INHERITANCE_AUTHORITY_NONE
        ),
        "deal_id": deal.get("id") if has_deal else None,
        "deal_field": deal_field,
        "settable_here": False,
        "evidence": vocab.QUOTE_INHERITS_QUOTE,
        "reason": reason,
    }


def require_set_on_deal() -> None:
    """Refuse the attempt to set a price book on a quote.

    "Users can't select a price book when creating a quote; they must select it on the
    deal." A raise rather than a boolean, so the route that would have offered the
    choice cannot ship without the refusal in it.
    """
    raise refuse(
        "quote_price_book_is_set_on_the_deal",
        vocab.QUOTE_INHERITS_QUOTE,
        **{"price_book": vocab.ERROR_CODES["quote_price_book_is_set_on_the_deal"][1]},
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _payload(row: Any) -> dict[str, Any]:
    """One store row's own payload, whether the caller passed the row or the payload.

    The domain rules are pure and know nothing about the store's envelope, so a caller
    may hand them either shape. Accepting both here is what keeps a caller from having
    to remember which one it is holding, which is the mistake this codebase has made
    more than once: a switch read off an envelope is always absent, so a count taken
    that way reports every rule as off.
    """
    if isinstance(row, Mapping):
        data = row.get("data")
        if isinstance(data, Mapping):
            return dict(data)
        return dict(row)
    return {}


def summary_counts(
    rules: Sequence[Mapping[str, Any]], assignments: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The board a reviewer reads first: how much is priced, and what still needs a person."""
    payloads = [_payload(one) for one in assignments]
    by_outcome: dict[str, int] = {name: 0 for name in vocab.ASSIGNMENT_REASONS}
    awaiting_choice = 0
    written = 0
    for payload in payloads:
        outcome = str(payload.get("outcome") or "")
        if outcome in by_outcome:
            by_outcome[outcome] += 1
        if outcome in vocab.WRITING_REASONS and payload.get("price_book"):
            written += 1
        if outcome in vocab.NEEDS_CHOICE_REASONS:
            awaiting_choice += 1
    configured = [_payload(rule) for rule in rules]
    return {
        "rules": len(configured),
        "rules_enabled": len([one for one in configured if one.get(vocab.ENABLED, True)]),
        "rules_auto_assigning": len(
            [
                one
                for one in configured
                if one.get(vocab.ENABLED, True) and one.get(vocab.AUTO_ASSIGN, True)
            ]
        ),
        "assignments": len(payloads),
        "written": written,
        "awaiting_a_choice": awaiting_choice,
        "by_outcome": by_outcome,
        "mode": workspace_mode(configured),
        "reasons": list(vocab.ASSIGNMENT_REASONS),
        "create_only_quote": vocab.CREATE_ONLY_QUOTE,
    }
