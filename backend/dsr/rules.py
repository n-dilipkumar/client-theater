"""WF-013: conditional rules that personalise room content.

This module is the domain core for conditional content. It is deliberately pure:
no database, no HTTP, no framework. Storage and transport are the caller's
problem, which is what lets the semantics be tested exhaustively and lets the
same rules be evaluated from a preview, a personalise call, or a background job.

Evidence
--------
Every constant and behaviour below is tagged with the workflow research it comes
from. ``docs/research/digital-sales-room-workflows/wf/WF-013.md`` is the source
document; the design inferences this module commits to are listed in
``WF-013-design.md`` §1 and are marked ``(inference)`` here.

The three behaviours that most often go wrong in an implementation of this
feature, and which the tests pin explicitly:

1. **A condition with no variable is ignored, and the block still appears**
   (S8, fail open). A half-configured rule must never hide buyer content.
2. **An empty value and the string ``"0"`` are both valid match values** (S9).
   Completeness depends only on whether a variable was chosen, never on whether
   a value was typed.
3. **The 11th ``Or`` condition is rejected, not silently dropped** (S7 + D4).
   Silent loss is the failure mode the research warns about at the Saved Block
   boundary, so it must not be repeated here.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

#: The three condition categories, with the modifiers the research lists for
#: each (S3, S4, S5).
TEXT_MODIFIERS: tuple[str, ...] = (
    "is",
    "is_not",
    "contains",
    "does_not_contain",
    "starts_with",
    "ends_with",
    "includes",
    "does_not_include",
)

NUMBER_MODIFIERS: tuple[str, ...] = (
    "equals",
    "does_not_equal",
    "is_more_than",
    "is_less_than",
)

ANY_MODIFIERS: tuple[str, ...] = (
    "has_no_value",
    "has_any_value",
)

MODIFIERS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "text": TEXT_MODIFIERS,
    "number": NUMBER_MODIFIERS,
    "any": ANY_MODIFIERS,
}

#: Every modifier, mapped to the single category it belongs to. Used to infer a
#: category when a condition omits one, so a client can send
#: ``{"variable": "seats", "modifier": "is_more_than", "value": 10}``.
CATEGORY_BY_MODIFIER: dict[str, str] = {
    modifier: category
    for category, modifiers in MODIFIERS_BY_CATEGORY.items()
    for modifier in modifiers
}

JOINERS: tuple[str, ...] = ("and", "or")

#: S7: "You can utilize up to 10 OR conditions for a rule" / "Or conditions are
#: limited to 10 per block, while AND conditions do not have a limit."
MAX_OR_CONDITIONS = 10

#: S10: "All block types can have rules applied to them with the exception of the
#: Accept Block." Normalised, so ``Accept Block`` and ``accept-block`` both hit.
#: A set in code, not a column, so it costs no migration and a team can extend
#: the rule engine without touching storage.
RULE_FORBIDDEN_BLOCK_TYPES: frozenset[str] = frozenset(
    {"accept", "accept_block", "acceptance", "acceptance_block"}
)

class RuleError(ValueError):
    """Raised when a rule cannot be accepted. Carries a machine-readable field."""


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _as_text(value: Any) -> str:
    """Render any supplied value as the text a Text modifier compares against.

    ``None`` becomes the empty string rather than the words ``"None"``, so that
    "is empty" behaves the way a seller expects.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _as_number(value: Any) -> float | None:
    """Coerce to a number, or ``None`` when the value is not numeric.

    A non-numeric CRM value must not raise: it should simply not match (D5), or
    a single bad field would 500 an entire page build.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fold(value: Any, case_sensitive: bool) -> str:
    text = _as_text(value)
    return text if case_sensitive else text.casefold()


def _is_absent(value: Any) -> bool:
    """Blank for Any purposes, treating the string ``"0"`` as a real value."""
    if value is None:
        return True
    return _as_text(value).strip() == ""


# --------------------------------------------------------------------------- #
# Matchers
# --------------------------------------------------------------------------- #

TextMatcher = Callable[[str, str, bool], bool]


def _text_is(observed: str, expected: str, _cs: bool) -> bool:
    return observed == expected


def _text_contains(observed: str, expected: str, _cs: bool) -> bool:
    return expected in observed


#: The research lists four text modifiers (``contains``/``does_not_contain`` and
#: ``includes``/``does_not_include``) that describe two behaviours: substring and
#: its negation. They are implemented as aliases rather than inventing a
#: distinction the source does not draw.
TEXT_MATCHERS: dict[str, TextMatcher] = {
    "is": _text_is,
    "is_not": lambda observed, expected, _cs: observed != expected,
    "contains": _text_contains,
    "does_not_contain": lambda observed, expected, _cs: expected not in observed,
    "starts_with": lambda observed, expected, _cs: observed.startswith(expected),
    "ends_with": lambda observed, expected, _cs: observed.endswith(expected),
    "includes": _text_contains,
    "does_not_include": lambda observed, expected, _cs: expected not in observed,
}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def normalise_block_type(value: Any) -> str:
    """Normalise a block type for comparison against the reserved set."""
    return _as_text(value).strip().casefold().replace(" ", "_").replace("-", "_")


def block_accepts_rules(block: Mapping[str, Any] | None) -> bool:
    """Whether a block may carry a rule (S10).

    The type is read from the block's own open payload, so a team that names its
    block types differently can still be recognised by adding to
    :data:`RULE_FORBIDDEN_BLOCK_TYPES` in code. No column is involved.
    """
    if not block:
        return True
    for key in ("type", "block_type", "kind"):
        if key in block:
            return normalise_block_type(block[key]) not in RULE_FORBIDDEN_BLOCK_TYPES
    return True


def _condition_category(condition: Mapping[str, Any]) -> str | None:
    """Resolve a condition's category, inferring it from the modifier if absent.

    The modifier is compared case-folded, so a client that sends ``IS_MORE_THAN``
    is treated the same as ``is_more_than``; validation lowercases modifiers, and
    an inference that disagreed with it would reject a rule it had just accepted.
    """
    category = condition.get("category")
    if category is None or str(category).strip() == "":
        modifier = condition.get("modifier")
        if not modifier:
            return None
        return CATEGORY_BY_MODIFIER.get(str(modifier).strip().casefold())
    return str(category).strip().casefold()


def validate_rule(
    rule: Any,
    *,
    block: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalise a rule. Raises :class:`RuleError` on rejection.

    The returned dict is safe to store as the block's ``data.rule``. Unknown keys
    on a condition are preserved, because a team adding its own field must not
    need a migration or a change to this function.
    """
    if rule is None:
        return {"join": "and", "conditions": []}
    if not isinstance(rule, Mapping):
        raise RuleError("rule must be an object with 'join' and 'conditions'")

    join = str(rule.get("join") or "and").strip().casefold()
    if join not in JOINERS:
        raise RuleError(f"join must be one of {list(JOINERS)}, got {join!r}")

    raw_conditions = rule.get("conditions") or []
    if not isinstance(raw_conditions, Sequence) or isinstance(raw_conditions, (str, bytes)):
        raise RuleError("conditions must be a list")
    raw_conditions = list(raw_conditions)

    # S7 + D4: the limit is enforced loudly. Silently dropping the 11th condition
    # would hide buyer-facing content for a reason nobody can see.
    if join == "or" and len(raw_conditions) > MAX_OR_CONDITIONS:
        raise RuleError(
            f"a rule may use at most {MAX_OR_CONDITIONS} Or conditions, got {len(raw_conditions)}"
        )

    # S10. Checked before the conditions so the message names the real problem.
    if raw_conditions and not block_accepts_rules(block):
        raise RuleError(
            "an Accept Block cannot have rules; every other block type can"
        )

    conditions: list[dict[str, Any]] = []
    for position, condition in enumerate(raw_conditions):
        conditions.append(_validate_condition(condition, position))

    return {"join": join, "conditions": conditions}


def _validate_condition(condition: Any, position: int) -> dict[str, Any]:
    if not isinstance(condition, Mapping):
        raise RuleError(f"condition {position} must be an object")

    modifier = condition.get("modifier")
    if not modifier or not str(modifier).strip():
        raise RuleError(f"condition {position} is missing a modifier")
    modifier = str(modifier).strip().casefold()

    # Check that the modifier exists at all before checking it against the
    # category, so a typo gets "not a known modifier" rather than being
    # reported as a category mismatch.
    if modifier not in CATEGORY_BY_MODIFIER:
        raise RuleError(
            f"condition {position} has modifier {modifier!r}, which is not a known modifier; "
            f"expected one of {sorted(CATEGORY_BY_MODIFIER)}"
        )

    category = _condition_category(condition)
    if category is None:
        raise RuleError(
            f"condition {position} has modifier {modifier!r} and no category, "
            f"which cannot be resolved"
        )
    if category not in MODIFIERS_BY_CATEGORY:
        raise RuleError(
            f"condition {position} has category {category!r}; "
            f"expected one of {sorted(MODIFIERS_BY_CATEGORY)}"
        )
    if modifier not in MODIFIERS_BY_CATEGORY[category]:
        raise RuleError(
            f"condition {position} uses modifier {modifier!r}, which is not a "
            f"{category} modifier; expected one of {list(MODIFIERS_BY_CATEGORY[category])}"
        )

    # Normalised copy: unknown keys are carried through untouched so a team can
    # add a field of its own without a migration.
    normalised = {key: value for key, value in condition.items() if key != "modifier"}
    normalised["modifier"] = modifier
    normalised["category"] = category
    return normalised


def is_incomplete(condition: Mapping[str, Any]) -> bool:
    """Whether a condition names no variable (S8).

    Note what is deliberately *not* consulted: the value. An empty value and the
    string ``"0"`` are both valid (S9), so a condition with a variable chosen is
    always complete regardless of what was typed in the value box.
    """
    variable = condition.get("variable")
    if variable is None:
        return True
    return not str(variable).strip()


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _match_text(
    modifier: str, observed: Any, expected: Any, case_sensitive: bool
) -> bool:
    matcher = TEXT_MATCHERS[modifier]
    return matcher(_fold(observed, case_sensitive), _fold(expected, case_sensitive), case_sensitive)


def evaluate_condition(
    condition: Mapping[str, Any],
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one condition and return its result **and** its trace.

    The trace is not a debugging nicety: a seller who hides a block by accident
    needs to see which condition fired and what the value actually was.
    """
    modifier = str(condition.get("modifier", "")).strip().casefold()
    category = _condition_category(condition) or "text"
    expected = condition.get("value")

    result: dict[str, Any] = {
        "variable": condition.get("variable"),
        "category": category,
        "modifier": modifier,
        "value": expected,
    }

    if is_incomplete(condition):
        # S8: ignored at personalisation time. Not a match, but not a reason to
        # hide anything either.
        return {**result, "status": "incomplete", "observed": None, "matched": False}

    variable = str(condition["variable"]).strip()
    supplied = variable in variables
    observed = variables.get(variable)

    # S5: the Any category is about presence, so it never needs a value.
    if modifier == "has_no_value":
        return {
            **result,
            "status": "matched" if not supplied or _is_absent(observed) else "unmatched",
            "observed": observed,
            "matched": (not supplied) or _is_absent(observed),
        }
    if modifier == "has_any_value":
        has_value = supplied and not _is_absent(observed)
        return {
            **result,
            "status": "matched" if has_value else "unmatched",
            "observed": observed,
            "matched": has_value,
        }

    # D3: a variable that was never supplied cannot match a value comparison, and
    # this is distinct from being supplied as empty (S9 makes "" a real value).
    if not supplied:
        return {**result, "status": "no_value", "observed": None, "matched": False}

    if category == "number":
        left = _as_number(observed)
        right = _as_number(expected)
        if left is None or right is None:
            # D5: a bad CRM value hides the block; it does not break the build.
            return {**result, "status": "not_numeric", "observed": observed, "matched": False}
        comparisons = {
            "equals": left == right,
            "does_not_equal": left != right,
            "is_more_than": left > right,
            "is_less_than": left < right,
        }
        matched = comparisons[modifier]
        return {**result, "status": "matched" if matched else "unmatched", "observed": observed, "matched": matched}

    case_sensitive = bool(condition.get("case_sensitive", False))  # D1
    matched = _match_text(modifier, observed, expected, case_sensitive)
    return {**result, "status": "matched" if matched else "unmatched", "observed": observed, "matched": matched}


def evaluate_rule(
    rule: Any,
    variables: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a rule against supplied variable values.

    Returns ``shown`` plus a per-condition trace. ``reason`` is one of
    ``no_rule``, ``all_incomplete``, ``matched`` or ``unmatched``.

    S8 fail-open: incomplete conditions are dropped before the join, so they
    neither satisfy nor defeat it. If that leaves nothing to decide on, the block
    is shown.
    """
    supplied = dict(variables or {})
    normalised = validate_rule(rule)

    conditions = list(normalised["conditions"])
    decided = [condition for condition in conditions if not is_incomplete(condition)]

    base = {"join": normalised["join"], "rule": normalised}

    if not conditions:
        return {**base, "shown": True, "reason": "no_rule", "conditions": []}

    trace = [evaluate_condition(condition, supplied) for condition in conditions]

    if not decided:
        # S8: every condition is incomplete, so the block appears.
        return {**base, "shown": True, "reason": "all_incomplete", "conditions": trace}

    outcomes = [entry["matched"] for entry in trace if entry["status"] != "incomplete"]
    if normalised["join"] == "or":
        shown = any(outcomes)
    else:
        shown = all(outcomes)

    return {
        **base,
        "shown": shown,
        "reason": "matched" if shown else "unmatched",
        "conditions": trace,
    }


def evaluate_block(
    block: Mapping[str, Any],
    variables: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one block record's inline rule.

    A block with no rule is always shown, and so is a block that cannot accept
    rules (S10) even if a rule somehow reached storage.
    """
    rule = block.get("rule") if isinstance(block, Mapping) else None
    if not rule or not block_accepts_rules(block):
        return {
            "shown": True,
            "reason": "no_rule",
            "join": "and",
            "rule": None,
            "conditions": [],
        }
    return evaluate_rule(rule, variables)


def personalise_blocks(
    blocks: Sequence[Mapping[str, Any]],
    variables: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate every block and split the result into shown and hidden.

    S13: this is the generation-time decision, made once when variable values are
    supplied, not something re-evaluated per viewer.
    """
    decisions = [
        {
            "block_id": block.get("id"),
            "title": (block.get("data") or {}).get("title") if isinstance(block, Mapping) else None,
            **evaluate_block((block.get("data") or {}) if isinstance(block, Mapping) else {}, variables),
        }
        for block in blocks
    ]
    return {
        "variables": dict(variables or {}),
        "block_count": len(blocks),
        "blocks": decisions,
        "shown": [entry["block_id"] for entry in decisions if entry["shown"]],
        "hidden": [entry["block_id"] for entry in decisions if not entry["shown"]],
    }


def catalog() -> dict[str, Any]:
    """Discovery payload for the rule builder UI.

    Exposed as data rather than hard-coded in the client so a team that adds a
    modifier sees it here without a frontend change.
    """
    return {
        "categories": {
            category: list(modifiers) for category, modifiers in MODIFIERS_BY_CATEGORY.items()
        },
        "joiners": list(JOINERS),
        "limits": {
            "max_or_conditions": MAX_OR_CONDITIONS,
            "max_and_conditions": None,
        },
        "forbidden_block_types": sorted(RULE_FORBIDDEN_BLOCK_TYPES),
        "defaults": {
            "join": "and",
            "category": "text",
            "case_sensitive": False,
        },
        "behaviours": {
            "incomplete_condition": "ignored; the block is shown (fail open)",
            "empty_value_is_valid": True,
            "zero_value_is_valid": True,
            "unsupplied_variable": "does not match, unless the modifier is has_no_value",
            "non_numeric_value": "does not match; it is not an error",
        },
    }
