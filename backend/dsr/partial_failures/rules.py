"""The stored configuration record: the two rule families a deployment owns.

Everything this workflow decides about a vendor error is a record, not a column
and not a function body, for the reason the research's extensibility note gives:
"The room's error model is the extension point - a connector maps vendor codes
into ``{retryable, field, code, message, docLink}``. A deployment can add a rule
... without changing the transport." A team that needs "route records missing
``email`` to a manual-review queue instead of retrying" ships a record, and no
migration, no redeploy, and no coordination with anyone.

There are two rule families and they are deliberately different shapes:

``routing``
    The researched extension point. A rule tests a normalised error and sets
    ``retryable``. This is what "without changing the transport" means in
    practice: the classifier consults the routing rules *before* its own table,
    so a deployment's rule wins.

``preflight``
    The rules :mod:`dsr.partial_failures.validation` checks a proposed batch
    against, so a write the room can refuse never reaches the CRM at all.

And one number, ``max_attempts``, which bounds the researched automatic drain -
see :mod:`dsr.partial_failures.retry`.

Two things are refused rather than tolerated, for the same reason as everywhere
else in this codebase: an unknown key and an unknown rule kind. Both produce a
rule that looks deliberate and does nothing, and a validation rule that does
nothing looks exactly like a validation rule that passes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.partial_failures import validation
from dsr.partial_failures.errors import InvalidRule
from dsr.partial_failures.normalise import OVERRIDE_MATCH_KEYS, OVERRIDE_THEN_KEYS
from dsr.partial_failures.retry import MAX_ATTEMPTS

#: The keys the whole record carries.
RULE_KEYS: tuple[str, ...] = ("max_attempts", "routing", "preflight")

#: The keys a routing rule carries.
ROUTING_RULE_KEYS: frozenset[str] = frozenset({"id", "when", "then", "basis"})

#: The whole record as this build ships it.
DEFAULT_RULES: dict[str, Any] = {
    "max_attempts": MAX_ATTEMPTS,
    "routing": [],
    "preflight": validation.defaults(),
}


# --------------------------------------------------------------------------- #
# Routing rules
# --------------------------------------------------------------------------- #


def validate_routing_rule(rule: Any) -> dict[str, Any]:
    """Check one routing rule, or say precisely what is wrong with it.

    ``basis`` is required, and that is the unusual part. The built-in
    classification table in :mod:`dsr.partial_failures.normalise` requires a basis
    on every entry for the same reason: a rule that changes whether a class is
    retried, with no statement of why, is a guess with a boolean attached, and it
    is exactly the kind of thing that survives a year in a production config.
    """
    if not isinstance(rule, Mapping):
        raise InvalidRule(f"a routing rule must be a JSON object; got {type(rule).__name__}")

    unknown = sorted(set(rule) - ROUTING_RULE_KEYS)
    if unknown:
        raise InvalidRule(
            f"unknown routing rule key(s) {', '.join(unknown)}; a rule carries "
            f"{', '.join(sorted(ROUTING_RULE_KEYS))}"
        )

    identifier = str(rule.get("id") or "").strip()
    if not identifier:
        raise InvalidRule("every routing rule needs an id; it is how a deployment refers to it")

    when = rule.get("when")
    if not isinstance(when, Mapping) or not when:
        raise InvalidRule(
            f"routing rule {identifier}: when must name at least one thing to match on, out of "
            f"{', '.join(OVERRIDE_MATCH_KEYS)}. A rule that matches everything is a way to turn "
            "the classification off by accident."
        )
    unknown_when = sorted(set(when) - set(OVERRIDE_MATCH_KEYS))
    if unknown_when:
        raise InvalidRule(
            f"routing rule {identifier}: unknown when key(s) {', '.join(unknown_when)}; a rule can "
            f"test {', '.join(OVERRIDE_MATCH_KEYS)}"
        )
    for key, value in when.items():
        if value in (None, "", []):
            raise InvalidRule(
                f"routing rule {identifier}: when.{key} is empty. A condition that matches "
                "everything is not the narrowing you meant."
            )

    then = rule.get("then")
    if not isinstance(then, Mapping) or not then:
        raise InvalidRule(
            f"routing rule {identifier}: then must set one of {', '.join(OVERRIDE_THEN_KEYS)}"
        )
    unknown_then = sorted(set(then) - set(OVERRIDE_THEN_KEYS))
    if unknown_then:
        raise InvalidRule(
            f"routing rule {identifier}: unknown then key(s) {', '.join(unknown_then)}; the "
            f"disposition follows from retryable, so there is no second knob to set"
        )
    if "retryable" not in then:
        raise InvalidRule(f"routing rule {identifier}: then.retryable is required")
    if not isinstance(then["retryable"], bool):
        raise InvalidRule(
            f"routing rule {identifier}: then.retryable must be true or false; got "
            f"{then['retryable']!r}"
        )

    basis = str(rule.get("basis") or "").strip()
    if not basis:
        raise InvalidRule(
            f"routing rule {identifier}: basis is required. A rule that changes whether a class "
            "is retried, with no statement of why, is a guess with a boolean attached."
        )

    return {"id": identifier, "when": dict(when), "then": dict(then), "basis": basis}


def validate_routing(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        raise InvalidRule(f"routing must be a list of rules; got {type(rules).__name__}")
    checked = [validate_routing_rule(rule) for rule in rules]
    _require_unique_ids(checked, "routing")
    return checked


# --------------------------------------------------------------------------- #
# The whole record
# --------------------------------------------------------------------------- #


def validate(rules: Mapping[str, Any]) -> dict[str, Any]:
    """Check a complete rules mapping, or say precisely what is wrong with it."""
    if not isinstance(rules, Mapping):
        raise InvalidRule(f"the rules must be a JSON object; got {type(rules).__name__}")

    unknown = sorted(set(rules) - set(RULE_KEYS))
    if unknown:
        raise InvalidRule(
            f"unknown rule(s) {', '.join(unknown)}; this workflow configures {', '.join(RULE_KEYS)}"
        )

    for key in RULE_KEYS:
        if key not in rules:
            raise InvalidRule(f"{key} is required in a complete rules mapping")

    attempts = rules["max_attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise InvalidRule(f"max_attempts must be a whole number; got {attempts!r}")
    if attempts < 1:
        raise InvalidRule(
            f"max_attempts must be at least 1; a bound of {attempts} would stop the researched "
            "automatic drain from ever running"
        )

    return {
        "max_attempts": attempts,
        "routing": validate_routing(rules["routing"]),
        "preflight": validation.validate_rules(rules["preflight"]),
    }


def merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay a partial patch on the rules in force and validate the result.

    A patch to ``routing`` or ``preflight`` merges key by key on the rule id, so
    retuning one rule does not silently reset another - and so replacing the
    shipped Dataverse length limit is a patch that names the same id rather than a
    second rule that shadows the first.
    """
    if not isinstance(patch, Mapping):
        raise InvalidRule(f"the rules patch must be a JSON object; got {type(patch).__name__}")

    unknown = sorted(set(patch) - set(RULE_KEYS))
    if unknown:
        raise InvalidRule(
            f"unknown rule(s) {', '.join(unknown)}; this workflow configures {', '.join(RULE_KEYS)}"
        )

    current = validate(base)
    merged: dict[str, Any] = {
        "max_attempts": current["max_attempts"],
        "routing": [dict(rule) for rule in current["routing"]],
        "preflight": [dict(rule) for rule in current["preflight"]],
    }

    if "max_attempts" in patch:
        merged["max_attempts"] = patch["max_attempts"]
    if "routing" in patch:
        merged["routing"] = _merge_by_id(
            merged["routing"], patch["routing"], validate_routing_rule, "routing"
        )
    if "preflight" in patch:
        merged["preflight"] = validation.merge_rules(merged["preflight"], patch["preflight"])

    return validate(merged)


def effective(record: Mapping[str, Any] | None) -> tuple[dict[str, Any], str]:
    """The rules in force, and whether they came from the defaults or a record.

    A stored override that has become invalid - someone typed it, or a future
    version narrowed the schema - falls back to the defaults rather than raising
    in the middle of a Sync log read. The stored copy is still served as
    ``stored`` so the mismatch is visible instead of being papered over.
    """
    if not record:
        return validate(DEFAULT_RULES), "defaults"
    try:
        merged = merge(DEFAULT_RULES, {k: v for k, v in record.items() if k in RULE_KEYS})
    except InvalidRule:
        return validate(DEFAULT_RULES), "defaults"
    return merged, "override"


def describe(rules: Mapping[str, Any], origin: str) -> dict[str, Any]:
    """The rules as the API serves them, next to where they came from."""
    from dsr.partial_failures.vocabulary import RULES_COLLECTION, RULES_RECORD_ID

    return {
        "rules": dict(rules),
        "source": origin,
        "record_id": RULES_RECORD_ID,
        "collection": RULES_COLLECTION,
        "rule_kinds": list(validation.PREFLIGHT_KINDS),
        "when_keys": list(OVERRIDE_MATCH_KEYS),
        "then_keys": list(OVERRIDE_THEN_KEYS),
    }


def _merge_by_id(
    current: Sequence[Mapping[str, Any]],
    patch: Any,
    check: Any,
    what: str,
) -> list[dict[str, Any]]:
    if not isinstance(patch, Sequence) or isinstance(patch, (str, bytes)):
        raise InvalidRule(f"{what} must be a list of rules; got {type(patch).__name__}")
    merged: dict[str, dict[str, Any]] = {str(rule["id"]): dict(rule) for rule in current}
    for entry in patch:
        rule = check(entry)
        merged[rule["id"]] = rule
    checked = list(merged.values())
    _require_unique_ids(checked, what)
    return checked


def _require_unique_ids(rules: Sequence[Mapping[str, Any]], what: str) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule["id"] in seen:
            raise InvalidRule(
                f"two {what} rules share the id {rule['id']!r}; ids are how a deployment replaces "
                "one rule, so they have to be unique"
            )
        seen.add(rule["id"])


__all__ = [
    "RULE_KEYS",
    "ROUTING_RULE_KEYS",
    "DEFAULT_RULES",
    "validate_routing_rule",
    "validate_routing",
    "validate",
    "merge",
    "effective",
    "describe",
]
