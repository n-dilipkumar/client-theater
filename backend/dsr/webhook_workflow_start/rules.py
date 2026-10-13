"""WF-114: the pure rules - parse, resolve, compare, bind. No storage, no HTTP.

Every function here is total over the JSON the research's payload example
implies: a payload may be an object, an array, a string, a number or null, and the
parse step records whatever arrived instead of rejecting it. The research
publishes one payload example and no schema, so this build refuses a payload for
exactly one reason: a mapping row the team declared requires a key the payload
did not carry.

Three rules with a reason each
------------------------------
* **The payload shape is unvalidated.** :func:`payload_facts` records the shape
  as it arrived and derives a flat map of dotted paths. A body that is not an
  object is wrapped, never dropped. Quoted from the issue: *"One example is
  given and no schema is published. Treat the payload shape as unvalidated."*
* **A mapping binds a path to a variable, and a mapping may carry a condition.**
  :func:`plan_bindings` runs the condition, resolves the path, coerces the value
  to the declared variable's type, and reports three outcomes: bound, declined,
  and required-but-unresolved. A payload whose rows all declined binds nothing
  and is recorded as ``unmapped``.
* **Comparison is by value, not by JSON type.** A GET payload arrives as query
  strings, so ``amount=100`` is the text ``"100"``. :func:`_loose_equal` compares
  two numbers numerically and everything else as text, so the same mapping works
  for a JSON body and a query string.

ASCII only, as everywhere in this workflow.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from dsr.webhook_workflow_start import vocabulary as vocab
from dsr.webhook_workflow_start.errors import InvalidTrigger

__all__ = [
    "canonical_json",
    "coerce_value",
    "fingerprint",
    "flatten",
    "get_path",
    "match_condition",
    "normalise_mappings",
    "payload_facts",
    "plan_bindings",
    "shape_of",
    "test_mode_requested",
]

_MISSING = object()


# --------------------------------------------------------------------------- #
# Any shape parses
# --------------------------------------------------------------------------- #


def shape_of(value: Any) -> str:
    """The shape a payload arrived in, named rather than inferred."""
    if value is None:
        return "empty"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "scalar"


def canonical_json(value: Any) -> str:
    """A stable serialisation, so a fingerprint of equal payloads is equal."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint(value: Any) -> str:
    """The dedupe key for one payload: sha256 of its canonical JSON.

    Equal bytes are not required and equal objects are. An external service that
    retries re-serialises, so keying on the object rather than the bytes is what
    makes a retry recognisable.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def get_path(value: Any, path: str) -> Any:
    """Read a dotted path out of arbitrary JSON. ``_MISSING`` when absent."""
    if not path:
        return _MISSING
    node: Any = value
    for segment in str(path).split("."):
        if isinstance(node, Mapping):
            if segment not in node:
                return _MISSING
            node = node[segment]
        elif isinstance(node, (list, tuple)):
            if not segment.lstrip("-").isdigit():
                return _MISSING
            try:
                node = node[int(segment)]
            except IndexError:
                return _MISSING
        else:
            return _MISSING
    return node


def is_missing(value: Any) -> bool:
    """Whether :func:`get_path` found nothing. Exposed so a caller need not
    import the sentinel."""
    return value is _MISSING


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every scalar in a payload, keyed by its dotted path.

    A flattening is what the mapping UI offers as the list of keys a team can
    bind, so a nested payload is as usable as a flat one.
    """
    out: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            out.update(flatten(child, f"{prefix}{key}."))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            out.update(flatten(child, f"{prefix}{index}."))
    elif prefix:
        out[prefix[:-1]] = value
    return out


def payload_facts(payload: Any, *, source: str) -> dict[str, Any]:
    """The payload as it arrived, named, shaped and flattened."""
    return {
        "source": source,
        "shape": shape_of(payload),
        "paths": sorted(flatten(payload)) if isinstance(payload, (Mapping, list, tuple)) else [],
        "keys": sorted(str(key) for key in payload) if isinstance(payload, Mapping) else [],
    }


def test_mode_requested(requested: Any) -> bool:
    """Whether a delivery asked to be a validation test rather than a start.

    Step 4 of the user flow is *"Send a test request to validate the
    integration"*, and a test request must not start the workflow. The flag is
    read from a query parameter so a GET payload and a POST body stay untouched.
    """
    if requested is None:
        return False
    if isinstance(requested, bool):
        return requested
    return str(requested).strip().lower() in vocab.TEST_MODE_VALUES


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def _as_number(value: Any) -> float | None:
    """A value as a number, or ``None`` when it is not one.

    A bool is deliberately not a number here, even though Python makes it an
    ``int``: ``status=true`` compared numerically to ``1`` would be a match a
    reader cannot see.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _loose_equal(left: Any, right: Any) -> bool:
    """Compare two values the way a mapping reader would.

    Two numbers compare numerically, so ``100`` and ``"100"`` are equal, which is
    what makes one mapping work for a JSON body and for a query string. Anything
    else compares as trimmed, case-folded text.
    """
    left_number, right_number = _as_number(left), _as_number(right)
    if left_number is not None or right_number is not None:
        return left_number is not None and right_number is not None and left_number == right_number
    return str(left).strip().casefold() == str(right).strip().casefold()


def match_condition(condition: Mapping[str, Any] | None, payload: Any) -> tuple[bool, str]:
    """Whether one mapping row's condition passes. ``(ok, reason)``.

    A row with no condition always passes. A condition that names an operator this
    build does not serve is a mapping a team cannot save, so it is refused when
    the trigger is written rather than silently never matching.
    """
    if not condition:
        return True, ""

    operator = str(condition.get("operator") or vocab.OPERATOR_EXISTS)
    if not vocab.is_known_operator(operator):
        return False, "unknown_operator"

    path = str(condition.get("path") or "")
    found = get_path(payload, path)
    present = found is not _MISSING and found is not None

    if operator == vocab.OPERATOR_EXISTS:
        return (present, "" if present else "not_present")
    if operator == vocab.OPERATOR_MISSING:
        return (not present, "" if not present else "present")

    wanted = condition.get("value")
    if operator == vocab.OPERATOR_EQUALS:
        return (_loose_equal(found, wanted), "" if _loose_equal(found, wanted) else "not_equal")
    if operator == vocab.OPERATOR_NOT_EQUALS:
        ok = not _loose_equal(found, wanted)
        return (ok, "" if ok else "equal")
    if operator == vocab.OPERATOR_CONTAINS:
        ok = not is_missing(found) and str(wanted).casefold() in str(found).casefold()
        return (ok, "" if ok else "does_not_contain")
    if operator == vocab.OPERATOR_IN:
        options = wanted if isinstance(wanted, (list, tuple, set)) else [wanted]
        ok = any(_loose_equal(found, option) for option in options)
        return (ok, "" if ok else "not_one_of")

    # The two ordering operators need numbers on both sides.
    left, right = _as_number(found), _as_number(wanted)
    if left is None or right is None:
        return False, "not_a_number"
    if operator == vocab.OPERATOR_GREATER_THAN:
        return (left > right, "" if left > right else "not_greater_than")
    if operator == vocab.OPERATOR_LESS_THAN:
        return (left < right, "" if left < right else "not_less_than")
    return False, "unknown_operator"


def coerce_value(value: Any, variable_type: str) -> tuple[bool, Any, str]:
    """A payload value as the declared variable's type. ``(ok, value, reason)``.

    A query string carries only text, so ``amount=100`` must become the number
    100 for a number-typed variable. A value that will not convert is declined
    rather than stored as the wrong type.
    """
    if variable_type == vocab.VARIABLE_NUMBER:
        number = _as_number(value)
        if number is None:
            return False, None, "not_a_number"
        return True, int(number) if number.is_integer() else number, ""
    if variable_type == vocab.VARIABLE_BOOLEAN:
        if isinstance(value, bool):
            return True, value, ""
        text = str(value).strip().casefold()
        if text in ("true", "1", "yes", "on"):
            return True, True, ""
        if text in ("false", "0", "no", "off"):
            return True, False, ""
        return False, None, "not_a_boolean"
    if variable_type == vocab.VARIABLE_JSON:
        return True, value, ""
    if isinstance(value, (Mapping, list, tuple)):
        return True, canonical_json(value), ""
    return True, str(value), ""


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #


def normalise_mappings(rows: Any) -> list[dict[str, Any]]:
    """Validate and normalise a trigger's mapping rows. Raises on a bad row.

    Every row is checked when the trigger is written, so a mapping a team cannot
    save is refused once rather than failing silently on every delivery after.
    """
    if rows in (None, ""):
        return []
    if not isinstance(rows, (list, tuple)):
        raise InvalidTrigger(
            "mappings must be a list of rows, one per variable.",
            mappings="must be a list",
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise InvalidTrigger(
                "every mapping row must be an object.", mappings="each row an object"
            )
        variable = str(row.get("variable") or "").strip()
        if not variable:
            raise InvalidTrigger(
                "every mapping row needs the workflow variable it binds.",
                variable="a variable name is required",
            )
        path = str(row.get("path") or "").strip()
        if not path:
            raise InvalidTrigger(
                f"the mapping for {variable!r} needs the payload path it reads.",
                path="a dotted payload path is required",
            )
        when = row.get("when")
        condition: dict[str, Any] | None = None
        if when not in (None, ""):
            if not isinstance(when, Mapping):
                raise InvalidTrigger(
                    f"the condition on {variable!r} must be an object.",
                    when="an object with path and operator",
                )
            operator = str(when.get("operator") or "").strip().lower()
            if not vocab.is_known_operator(operator):
                raise InvalidTrigger(
                    f"operator {operator!r} on {variable!r} is not one of {list(vocab.OPERATOR_IDS)}.",
                    operator=f"must be one of {list(vocab.OPERATOR_IDS)}",
                )
            condition_path = str(when.get("path") or "").strip()
            if not condition_path:
                raise InvalidTrigger(
                    f"the condition on {variable!r} needs a path to compare.",
                    when="a dotted payload path is required",
                )
            if vocab.operator_takes_value(operator) and "value" not in when:
                raise InvalidTrigger(
                    f"operator {operator!r} on {variable!r} needs a value to compare against.",
                    value="a comparison value is required",
                )
            condition = {
                "path": condition_path,
                "operator": operator,
                "value": when.get("value"),
            }
        variable_type = str(row.get("type") or vocab.VARIABLE_TEXT).strip().lower()
        if not vocab.is_known_variable_type(variable_type):
            raise InvalidTrigger(
                f"variable type {variable_type!r} on {variable!r} is not one of "
                f"{list(vocab.VARIABLE_TYPE_IDS)}.",
                type=f"must be one of {list(vocab.VARIABLE_TYPE_IDS)}",
            )
        out.append(
            {
                "variable": variable,
                # A declared mapping needs its path. A team that wants a row to
                # bind only when a key happens to be present marks it optional.
                "required": bool(row.get("required", True)),
                "path": path,
                "type": variable_type,
                "when": condition,
            }
        )
    return out


def plan_bindings(mappings: Any, payload: Any) -> dict[str, Any]:
    """Run every mapping row against one payload.

    Returns ``bindings`` (the rows that bound), ``declined`` (the rows that did
    not, each with the reason) and ``required_unresolved`` (the subset of
    declined rows the trigger declared as required). ``matched`` counts the
    bindings, and a payload with ``matched == 0`` is the state the issue calls
    *"a request that arrived at a saved trigger but matched no mapping"*.
    """
    resolved = normalise_mappings(mappings)
    bindings: list[dict[str, Any]] = []
    declined: list[dict[str, Any]] = []
    required_unresolved: list[dict[str, Any]] = []

    for row in resolved:
        passed, why = match_condition(row.get("when"), payload)
        if not passed:
            # A condition that did not match is a legitimate decline, not a missing
            # required key: the row says "bind only when the payload looks like
            # this", and this payload does not. A payload whose rows all declined
            # binds nothing and is recorded as unmapped rather than refused.
            declined.append(
                {
                    "variable": row["variable"],
                    "path": row["path"],
                    "reason": f"condition_not_met:{why}" if why else "condition_not_met",
                }
            )
            continue

        found = get_path(payload, row["path"])
        if found is _MISSING:
            entry = {"variable": row["variable"], "path": row["path"], "reason": "path_absent"}
            declined.append(entry)
            if row["required"]:
                required_unresolved.append(entry)
            continue

        variable_type = str(row.get("type") or vocab.VARIABLE_TEXT).strip().lower()
        ok, value, reason = coerce_value(found, variable_type)
        if not ok:
            entry = {"variable": row["variable"], "path": row["path"], "reason": reason}
            declined.append(entry)
            if row["required"]:
                required_unresolved.append(entry)
            continue

        bindings.append(
            {
                "variable": row["variable"],
                "path": row["path"],
                "value": value,
                "type": variable_type,
            }
        )

    return {
        "bindings": bindings,
        "declined": declined,
        "required_unresolved": required_unresolved,
        "matched": len(bindings),
        "planned": len(resolved),
    }
