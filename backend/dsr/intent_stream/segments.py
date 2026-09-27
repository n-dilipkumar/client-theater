"""Saved Segments: the conditions a Webhook workflow sorts leads with.

Sourced: "Add the conditions you want to be applied to the workflow to sort out
which leads you want your Workflow to send based on saved Segments from your
account." So a Segment is a first-class, reusable, named thing this product
owns, and a workflow's conditions are a selection of them.

What a Segment rule is
----------------------
The research does not enumerate a Segment's fields, and this product does not
either. A rule is a **dotted JSON path into the company lead's own ``data``**
plus an operator and a value, which is the same shape the store's ``find()``
already resolves through the dynamic index. That is a deliberate consequence of
the schema-flexibility rule: a team that adds ``firmographics.hiringSignal`` to
a lead can put it in a Segment the same day, with no migration and no
coordination with anyone. A published field list would be a coordination
requirement wearing a schema.

Why evaluation is in Python and not in the index
------------------------------------------------
``find()`` can only test equality. This grammar needs ``contains``, ``in``,
``exists`` and the ordering comparisons, and - the real reason - a rule that
matched nothing has to be *reportable*: :func:`evaluate` returns one verdict per
rule with the value it actually saw, so an operator can tell "this Segment does
not match" from "this Segment is broken". A predicate that silently failed would
look exactly like a Segment nobody is in.

A missing path is a value, not an absence
-----------------------------------------
``resolve()`` returns a sentinel for a path the lead does not carry, and every
operator has a defined answer for it. That matters because leads are arbitrary
JSON: ``employees.gte 1000`` against a lead with no ``employees`` field is the
common case on a first visit, not an edge case, and it must not read as a crash.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.intent_stream.errors import SegmentError
from dsr.intent_stream.vocabulary import (
    DEFAULT_RULE_MATCH,
    MATCH_ALL,
    MATCH_MODES,
)

#: Returned by :func:`resolve` for a path the payload does not carry. Distinct
#: from ``None``, which is a value a lead can genuinely hold, and from ``""``,
#: which is a value a lead can genuinely hold too.
MISSING = object()

#: The rule grammar. Published through ``GET /vocabulary`` so a rule editor can
#: grey out what this package will refuse rather than letting an operator
#: discover it at send time.
OPERATORS = (
    "eq",
    "ne",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
    "not_exists",
)

#: Operators that take no value, so a rule carrying one is refused rather than
#: quietly ignoring it. A rule with a value the operator does not use is a typo
#: that would otherwise read as "matched nothing".
UNARY_OPERATORS = ("exists", "not_exists")

#: Operators whose value is compared numerically. A rule that says
#: ``employees gte "1000"`` against a lead holding the number 1000 is a silent
#: miss, so a non-numeric value for one of these is refused at save time.
ORDERING_OPERATORS = ("gt", "gte", "lt", "lte")

#: A single path segment may be a plain key or an array index, so a rule can say
#: ``pagesViewed.0`` or ``staff.2.name`` without a special syntax.
MAX_PATH_DEPTH = 8
MAX_RULES = 32
MAX_VALUE_LENGTH = 200
MAX_VALUES = 100


def resolve(payload: Any, path: str) -> Any:
    """The value at a dotted ``path`` in ``payload``, or :data:`MISSING`.

    Walks mappings by key and sequences by numeric index, so ``a.b.0.c``
    reaches into a list without a separate grammar.
    """
    current = payload
    for segment in str(path).split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return MISSING
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            try:
                index = int(segment)
            except ValueError:
                return MISSING
            if index < 0 or index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def _as_number(value: Any) -> float | None:
    """A comparable number, or ``None`` for anything that is not one.

    ``bool`` is excluded on purpose. In Python ``True == 1``, so without this a
    rule of ``views gte 1`` would match a lead whose ``views`` is ``true``,
    which is the kind of thing that makes a segment look wrong for months.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _text(value: Any) -> str:
    """A stable text form for comparison and for reporting.

    Lists and mappings are joined rather than stringified, so a rule of
    ``tags contains enterprise`` matches a list value as well as a string one -
    a lead whose ``tags`` is a JSON array is an ordinary thing to have.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_text(child)}" for key, child in sorted(value.items()))
    return str(value)


def _haystack(value: Any) -> list[str]:
    """Every text form a ``contains`` comparison should look at."""
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value]
    if isinstance(value, Mapping):
        return [_text(value)]
    return [_text(value)]


def _present(value: Any) -> bool:
    """Whether a resolved value counts as populated, for ``exists``.

    ``0`` and ``False`` are populated. An empty string, an empty list and a
    missing key are not: the question ``exists`` asks is "does the lead carry
    this", and a lead that carries an empty ``title`` does not carry a title.
    """
    if value is MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _membership(expected: Any, actual: Any) -> bool:
    """``in`` / ``not_in`` over a scalar or over a list-valued field."""
    if isinstance(actual, (list, tuple)):
        return any(_compare_scalar("eq", item, candidate) for item in actual for candidate in _candidates(expected))
    return any(_compare_scalar("eq", actual, candidate) for candidate in _candidates(expected))


def _candidates(expected: Any) -> list[Any]:
    """``value`` may be a scalar or a list; a list means "any of these"."""
    if isinstance(expected, (list, tuple)):
        return list(expected)
    return [expected]


def _compare_scalar(operator: str, actual: Any, expected: Any) -> bool:
    """One operator, one pair of values, no missing-path handling."""
    if operator == "eq":
        return _loose_equal(actual, expected)
    if operator == "ne":
        return not _loose_equal(actual, expected)
    if operator == "in":
        return _membership(expected, actual)
    if operator == "not_in":
        return not _membership(expected, actual)
    if operator == "contains":
        needle = _text(expected).casefold()
        return bool(needle) and any(needle in hay.casefold() for hay in _haystack(actual))
    if operator == "not_contains":
        needle = _text(expected).casefold()
        return not (bool(needle) and any(needle in hay.casefold() for hay in _haystack(actual)))
    if operator in ORDERING_OPERATORS:
        left, right = _as_number(actual), _as_number(expected)
        if left is None or right is None:
            return False
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        return left <= right
    if operator == "exists":
        return _present(actual)
    if operator == "not_exists":
        return not _present(actual)
    return False


def _loose_equal(actual: Any, expected: Any) -> bool:
    """Equality that does not punish a number arriving as a string.

    A lead's ``employees`` may be the number 850 or the string "850" depending
    on which system filled it in, and a Segment that stops matching because of
    that is a Segment nobody trusts. Strings are compared as numbers when both
    sides look numeric, and case-insensitively otherwise.
    """
    left, right = _as_number(actual), _as_number(expected)
    if left is not None and right is not None:
        return left == right
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected or actual == expected and type(actual) is type(expected)
    return _text(actual).strip().casefold() == _text(expected).strip().casefold()


def describe_rule(rule: Mapping[str, Any]) -> str:
    """A one-line, human-readable rendering of one rule, for the rule editor."""
    operator = str(rule.get("operator") or "eq")
    path = str(rule.get("path") or "")
    if operator in UNARY_OPERATORS:
        return f"{path} {operator}"
    value = rule.get("value")
    if isinstance(value, (list, tuple)):
        rendered = ", ".join(_text(item) for item in value)
        return f"{path} {operator} [{rendered}]"
    return f"{path} {operator} {_text(value)}"


def describe_segment(data: Mapping[str, Any]) -> str:
    """A one-line rendering of a Segment, e.g. ``Enterprise OR >1000 staff``."""
    rules = [rule for rule in (data.get("rules") or []) if isinstance(rule, Mapping)]
    if not rules:
        return f"{data.get('name')} (no rules: matches every lead)"
    joiner = " AND " if str(data.get("match") or DEFAULT_RULE_MATCH) == MATCH_ALL else " OR "
    return joiner.join(describe_rule(rule) for rule in rules)


def require_rules(rules: Any) -> list[dict[str, Any]]:
    """Validate and normalise a Segment's rules, or refuse.

    Refused here rather than at send time, for the reason the module docstring
    gives: a Segment that cannot be evaluated must say so while somebody is
    looking at it, not on the first company visit.
    """
    if rules is None:
        raise SegmentError(
            "a Segment needs its rules; pass rules: [{path, operator, value}]",
            remediation='For example rules: [{"path": "employees", "operator": "gte", "value": 1000}].',
        )
    if not isinstance(rules, (list, tuple)):
        raise SegmentError(
            f"rules must be a list, not {type(rules).__name__}",
            remediation="Pass a list of {path, operator, value} objects.",
        )
    if not rules:
        # A Segment with no rules matches every lead, which is a real and useful
        # thing ("everyone in the account") but must be said out loud, because
        # it is also what an empty form produces.
        return []
    if len(rules) > MAX_RULES:
        raise SegmentError(
            f"a Segment may hold at most {MAX_RULES} rules, got {len(rules)}",
            remediation=f"Split it, or narrow the paths. The cap is {MAX_RULES}.",
        )

    normalised: list[dict[str, Any]] = []
    for position, rule in enumerate(rules):
        where = f"rule {position + 1}"
        if not isinstance(rule, Mapping):
            raise SegmentError(f"{where} must be an object, not {type(rule).__name__}")
        path = str(rule.get("path") or "").strip()
        if not path:
            raise SegmentError(
                f"{where} has no path",
                remediation="A path is a dotted JSON path into the lead's data, e.g. employees or firmographics.hiringSignal.",
            )
        if len(path.split(".")) > MAX_PATH_DEPTH:
            raise SegmentError(
                f"{where} path {path!r} is deeper than {MAX_PATH_DEPTH} segments",
                remediation="Flatten the lead's data, or reach the nested value from a shallower path.",
            )
        operator = str(rule.get("operator") or "eq").strip()
        if operator not in OPERATORS:
            raise SegmentError(
                f"{where} uses operator {operator!r}, which is not in the grammar",
                remediation=f"Use one of: {', '.join(OPERATORS)}.",
            )
        entry: dict[str, Any] = {"path": path, "operator": operator}

        if operator in UNARY_OPERATORS:
            if rule.get("value") not in (None, "", []):
                raise SegmentError(
                    f"{where} is {operator}, which takes no value",
                    remediation="Remove the value, or use eq/ne instead.",
                )
        else:
            if "value" not in rule:
                raise SegmentError(
                    f"{where} uses {operator} but has no value",
                    remediation=f"Add a value, or use exists / not_exists.",
                )
            value = rule.get("value")
            if isinstance(value, (list, tuple)):
                if len(value) > MAX_VALUES:
                    raise SegmentError(
                        f"{where} lists {len(value)} values, more than the {MAX_VALUES} allowed"
                    )
                if operator not in ("in", "not_in"):
                    raise SegmentError(
                        f"{where} is {operator}, which compares against one value, not a list",
                        remediation="Use in / not_in for a list, or one rule per value.",
                    )
                entry["value"] = [_bounded(item, where) for item in value]
            else:
                entry["value"] = _bounded(value, where)
            if operator in ORDERING_OPERATORS and _as_number(entry["value"]) is None:
                raise SegmentError(
                    f"{where} is {operator} with the non-numeric value {entry['value']!r}",
                    remediation="Use gt/gte/lt/lte with a number, or eq/ne with text.",
                )
        normalised.append(entry)
    return normalised


def _bounded(value: Any, where: str) -> Any:
    """A rule value, with text length capped so a rule cannot smuggle a payload."""
    if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
        raise SegmentError(
            f"{where} has a {len(value)}-character value, over the {MAX_VALUE_LENGTH} allowed",
            remediation="Shorten it, or split the Segment.",
        )
    if isinstance(value, (list, tuple, dict)):
        raise SegmentError(
            f"{where} nests a {type(value).__name__} inside a rule value",
            remediation="A rule value is a scalar or a flat list of scalars.",
        )
    return value


def require_match(match: Any, *, default: str = DEFAULT_RULE_MATCH) -> str:
    """Validate an ``any``/``all`` combination, defaulting as documented.

    ``default`` is passed in rather than guessed from the field name: a Segment's
    rules and a workflow's Segments are the same grammar at two levels, and each
    level documents its own default.
    """
    if match is None or match == "":
        return default
    text = str(match).strip().casefold()
    if text not in MATCH_MODES:
        raise SegmentError(
            f"match must be 'any' or 'all', not {match!r}",
            remediation="Use 'any' to send a lead matching at least one Segment, 'all' for every Segment.",
        )
    return text


def evaluate_rule(rule: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """One rule's verdict, with everything needed to explain it.

    ``matched`` is the answer, ``actual`` is what the lead actually carried (or
    the string ``"<missing>"``), and ``reason`` says which branch produced it.
    Without those three, "your Segment matched nothing" is not an actionable
    answer and the operator goes looking for a bug in their own systems.
    """
    operator = str(rule.get("operator") or "eq")
    path = str(rule.get("path") or "")
    actual = resolve(payload, path)
    missing = actual is MISSING
    expected = rule.get("value")
    matched = False if missing and operator not in UNARY_OPERATORS else _compare_scalar(operator, actual, expected)

    if missing:
        reason = "the lead does not carry this path"
    elif operator in ORDERING_OPERATORS and _as_number(actual) is None:
        reason = "the value is not a number, so it cannot be ordered"
    elif operator == "exists":
        reason = "the value is populated" if matched else "the value is empty or absent"
    elif matched:
        reason = "the comparison holds"
    else:
        reason = "the comparison does not hold"
    return {
        "path": path,
        "operator": operator,
        "value": expected,
        "actual": "<missing>" if missing else _text(actual),
        "missing": missing,
        "matched": bool(matched),
        "reason": reason,
    }


def evaluate_segment(data: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """One Segment's verdict: the rules, each one, and the combined answer."""
    rules = [rule for rule in (data.get("rules") or []) if isinstance(rule, Mapping)]
    verdicts = [evaluate_rule(rule, payload) for rule in rules]
    match = str(data.get("match") or DEFAULT_RULE_MATCH)

    if not verdicts:
        # No rules matches everything. Deliberate, and reported as matched with
        # the reason spelled out so a reader is not left guessing whether an
        # empty Segment is a bug.
        matched = True
        reason = "this Segment has no rules, so it matches every company"
    elif match == MATCH_ALL:
        matched = all(verdict["matched"] for verdict in verdicts)
        reason = "every rule holds" if matched else "at least one rule does not hold"
    else:
        matched = any(verdict["matched"] for verdict in verdicts)
        reason = "at least one rule holds" if matched else "no rule holds"

    return {
        "segmentId": data.get("id"),
        "name": data.get("name"),
        "match": match,
        "matched": bool(matched),
        "reason": reason,
        "summary": describe_segment(data),
        "rules": verdicts,
    }


def evaluate_conditions(
    segments: Sequence[Mapping[str, Any]],
    payload: Mapping[str, Any],
    *,
    match: str = DEFAULT_RULE_MATCH,
) -> dict[str, Any]:
    """A workflow's conditions: one verdict per Segment, then the combined answer.

    The combined answer is what decides whether anything is sent. ``matchedIds``
    is what the delivery record carries, so a payload that arrives downstream can
    be traced back to the Segment that selected it - which is the question
    somebody always ends up asking about a lead that turned up unexpectedly.
    """
    verdicts = [evaluate_segment(segment, payload) for segment in segments]
    if not verdicts:
        return {
            "match": match,
            "matched": False,
            "reason": "the workflow has no Segments in its conditions, so nothing can match it",
            "matchedIds": [],
            "segments": [],
        }
    if match == MATCH_ALL:
        matched = all(verdict["matched"] for verdict in verdicts)
        reason = "every Segment matched" if matched else "at least one Segment did not match"
    else:
        matched = any(verdict["matched"] for verdict in verdicts)
        reason = "at least one Segment matched" if matched else "no Segment matched"
    return {
        "match": match,
        "matched": bool(matched),
        "reason": reason,
        "matchedIds": [v["segmentId"] for v in verdicts if v["matched"] and v["segmentId"]],
        "segments": verdicts,
    }
