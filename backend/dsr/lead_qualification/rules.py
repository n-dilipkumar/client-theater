"""The pure rules: how a lead is matched to a rule, and how a route id is derived.

Nothing in this module reads or writes anything. It takes plain dictionaries in
and returns plain dictionaries out, which is what lets the whole qualification
be tested without a database, and what makes the "no session consumed" guarantee
structural rather than a promise: :func:`route_id_for` is a hash of its inputs,
so there is nothing for a session to be stored in.

The matching order is the research's: a Concierge router is a Trigger node
followed by ``Routing Rule`` nodes and a ``Catch All``, and "Each router must end
with a 'Catch All' path". The first rule whose every condition holds wins, and a
rule after the first match is never evaluated - which is what lets a catch-all be
trusted as the floor.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from dsr.lead_qualification.vocabulary import (
    CRM_OBJECTS,
    DEFAULT_TENANT,
    INTERVAL_FIELD,
    OPERATORS,
    RULE_KINDS,
    RULE_SOURCES,
)

#: A catch-all matches whatever reaches it, so it carries no condition.
CATCH_ALL = "catch_all"

#: A rule with no conditions would match every lead before the rules meant for the
#: lead, so one is refused rather than silently widened to "always true".
MIN_CONDITIONS = 1

#: The most conditions one rule may carry. A rule is a screen a person reads in
#: the flow builder; past this it is a program, and this workflow is not one.
MAX_CONDITIONS = 5


def canonical(value: Any) -> str:
    """A stable string for any JSON value, so a hash does not depend on key order."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def route_id_for(router_slug: str, payload: Mapping[str, Any]) -> str:
    """The researched ``routeId``, derived rather than stored.

    The research says "The ``routeId`` is returned but no slot list is computed
    and no session is consumed". A stored id would be a session, so this is a
    digest of the router and the lead payload: the same lead through the same
    router always answers with the same id, and two calls in a row prove nothing
    was kept because nothing can be.

    Shaped like the research's sample, ``9413f879-...``, because the id travels in
    a URL that a Chili Piper page reads.
    """
    digest = hashlib.sha256(
        canonical([str(router_slug), dict(payload)]).encode("utf-8")
    ).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def routing_link_for(tenant: str, router_slug: str, route_id: str) -> str:
    """The researched ``routingLink``: "a routingLink to redirect the lead to".

    Built the way the research's sample is built, from the tenant host, the
    ``concierge-router`` path, the slug and the route id. It is returned and not
    followed: this workflow never opens a scheduler.
    """
    host = str(tenant or "").strip() or DEFAULT_TENANT
    if "://" not in host:
        host = f"https://{host}"
    return f"{host.rstrip('/')}/concierge-router/{router_slug}/routing/{route_id}"


def dotted_get(source: Mapping[str, Any] | None, path: str) -> tuple[bool, Any]:
    """Read ``a.b.c`` out of a mapping. Returns ``(found, value)``.

    ``found`` is separate from the value because a field can be present and hold
    ``None``, and ``exists`` has to tell those apart.
    """
    current: Any = source or {}
    for part in str(path).split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    """A mapping, or None. Used where an optional object field is read."""
    return value if isinstance(value, Mapping) else None


def _as_number(value: Any) -> float | None:
    """A number for the ordered comparisons, or None when it is not one.

    Booleans are excluded on purpose: ``True`` is not a lead count, and letting
    it compare as 1 would silently match a rule about size.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def compare(operator: str, actual: Any, expected: Any) -> bool:
    """One comparison, on the researched operators plus four this build adds.

    Text comparison is case-insensitive and trimmed, because a webform value and
    a CRM value disagree about capitalisation far more often than they disagree
    about the answer.
    """
    if operator == "exists":
        return actual is not None
    if actual is None:
        return False

    if operator in {"gt", "gte", "lt", "lte"}:
        left, right = _as_number(actual), _as_number(expected)
        if left is None or right is None:
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[operator]

    left = str(actual).strip().lower()
    if operator == "equals":
        return left == _text(expected)
    if operator == "not_equals":
        return left != _text(expected)
    if operator == "contains":
        return _text(expected) in left
    if operator == "in":
        candidates = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return left in {_text(item) for item in candidates}
    return False


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


@dataclass
class Condition:
    """One condition inside a rule, read from the router's own JSON."""

    kind: str
    source: str
    field: str
    operator: str
    expected: Any = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> Condition:
        kind = str(raw.get("kind") or "").strip()
        source = str(raw.get("source") or "").strip()
        return cls(
            kind=kind,
            source=source,
            field=str(raw.get("field") or "").strip(),
            operator=str(raw.get("operator") or "equals").strip(),
            expected=raw.get("value"),
        )

    def evaluate(self, form: Mapping[str, Any], crm: Mapping[str, Any]) -> dict[str, Any]:
        """Read the field out of the right source and compare it.

        A ``crm_field`` condition reads the CRM object the caller passed in, not
        the store: this workflow is given a lead payload, and the caller is the
        one that already knows the CRM. That keeps the workflow free of a CRM
        connection and keeps "availability is not queried" honest.
        """
        if self.kind == "crm_field":
            found, actual = dotted_get(crm.get(self.source), self.field)
        else:
            found, actual = dotted_get(form, self.field)
        return {
            "kind": self.kind,
            "source": self.source,
            "field": self.field,
            "operator": self.operator,
            "expected": self.expected,
            "found": found,
            "actual": actual,
            "matched": found and compare(self.operator, actual, self.expected),
        }


@dataclass
class Rule:
    """One node in the router's chain, either a condition rule or the catch-all."""

    index: int
    name: str
    conditions: list[Condition] = field(default_factory=list)
    assign_user_id: str = ""
    scheduling_allowed: bool = True
    crm_writeback: Mapping[str, Any] | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any], index: int) -> Rule:
        conditions = [
            Condition.parse(entry)
            for entry in raw.get("conditions") or []
            if isinstance(entry, Mapping)
        ]
        return cls(
            index=index,
            name=str(raw.get("name") or f"rule {index + 1}").strip(),
            conditions=conditions,
            assign_user_id=str(raw.get("assign_user_id") or "").strip(),
            scheduling_allowed=bool(raw.get("scheduling_allowed", True)),
            crm_writeback=_mapping_or_none(raw.get("crm_writeback")),
        )

    @property
    def is_catch_all(self) -> bool:
        """True when the rule carries no conditions, so it matches whatever reaches it."""
        return not self.conditions

    def evaluate(self, form: Mapping[str, Any], crm: Mapping[str, Any]) -> dict[str, Any]:
        """Every condition, and whether the whole rule holds."""
        outcomes = [condition.evaluate(form, crm) for condition in self.conditions]
        return {
            "index": self.index,
            "name": self.name,
            "matched": all(outcome["matched"] for outcome in outcomes),
            "conditions": outcomes,
        }


@dataclass
class Match:
    """What the chain decided, and how it got there."""

    rule: Rule | None
    rule_index: int
    fallthrough: bool
    conditions: list[dict[str, Any]]
    evaluated: int

    @property
    def matched(self) -> bool:
        return self.rule is not None


def parse_rules(raw: Sequence[Mapping[str, Any]] | None) -> list[Rule]:
    """Read the router's stored rule list. A malformed entry is skipped, not fatal.

    Skipping keeps a partially bad chain usable, and
    :func:`validate_router` is what refuses to *save* one, so a stored chain is
    already one the validator accepted.
    """
    return [
        Rule.parse(entry, index)
        for index, entry in enumerate(raw or [])
        if isinstance(entry, Mapping)
    ]


def evaluate(rules: Sequence[Rule], form: Mapping[str, Any], crm: Mapping[str, Any]) -> Match:
    """Walk the chain once and stop at the first rule that holds.

    Nothing after the first match is evaluated, which is the property that makes
    the catch-all a floor rather than a suggestion.
    """
    evaluated = 0
    last: dict[str, Any] | None = None
    for rule in rules:
        outcome = rule.evaluate(form, crm)
        evaluated += 1
        last = outcome
        if outcome["matched"]:
            return Match(
                rule=rule,
                rule_index=rule.index,
                fallthrough=False,
                conditions=outcome["conditions"],
                evaluated=evaluated,
            )
    return Match(
        rule=None,
        rule_index=-1,
        fallthrough=True,
        conditions=last["conditions"] if last else [],
        evaluated=evaluated,
    )


def validate_router(spec: Mapping[str, Any]) -> list[str]:
    """Every problem with a router declaration, as sentences. Empty means valid.

    Returned as a list rather than raised so the same function serves the save
    route (which refuses) and the preview route (which only advises).
    """
    problems: list[str] = []
    if not str(spec.get("router_slug") or "").strip():
        problems.append("router_slug is required; it is the researched path segment.")
    if not str(spec.get("name") or "").strip():
        problems.append("name is required; a router with no name cannot be listed.")

    raw_rules = spec.get("rules")
    if not isinstance(raw_rules, (list, tuple)) or not raw_rules:
        problems.append("rules is required; a router with no rules routes no lead.")
        return problems

    kinds: list[str] = []
    for position, entry in enumerate(raw_rules):
        if not isinstance(entry, Mapping):
            problems.append(f"rule {position + 1} is not an object.")
            continue
        kinds.append(str(entry.get("kind") or "").strip())
        problems.extend(_validate_rule(entry, position))

    declared_catch_all = [entry for entry in kinds if entry == CATCH_ALL]
    if not declared_catch_all:
        problems.append(
            "The chain must end with a catch_all: each router must end with a "
            "'Catch All' path so every inbound lead is acknowledged."
        )
    elif len(declared_catch_all) > 1:
        problems.append("Only one catch_all is allowed; the chain's floor is one node.")

    catch_all_positions = [index for index, kind in enumerate(kinds) if kind == CATCH_ALL]
    if catch_all_positions and catch_all_positions[0] != len(kinds) - 1:
        problems.append(
            "The catch_all must be the last rule. A rule after the floor can never be reached."
        )

    unknown = sorted({kind for kind in kinds if kind and kind not in RULE_KINDS})
    if unknown:
        problems.append(
            "Unknown rule kind(s): "
            + ", ".join(unknown)
            + f". This workflow reads {', '.join(RULE_KINDS)}."
        )
    return problems


def _validate_rule(entry: Mapping[str, Any], position: int) -> list[str]:
    """One rule's own problems."""
    where = f"rule {position + 1}"
    kind = str(entry.get("kind") or "").strip()
    problems: list[str] = []

    if kind == CATCH_ALL:
        if entry.get("conditions"):
            problems.append(f"{where} is a catch_all and must not carry conditions.")
        return problems

    conditions = entry.get("conditions")
    if not isinstance(conditions, (list, tuple)) or not conditions:
        problems.append(
            f"{where} has no conditions, so it would match every lead before the rules meant for the lead."
        )
    elif len(conditions) > MAX_CONDITIONS:
        problems.append(
            f"{where} carries {len(conditions)} conditions; the limit is {MAX_CONDITIONS}."
        )

    for offset, condition in enumerate(conditions or []):
        label = f"{where} condition {offset + 1}"
        if not isinstance(condition, Mapping):
            problems.append(f"{label} is not an object.")
            continue
        condition_kind = str(condition.get("kind") or "").strip()
        source = str(condition.get("source") or "").strip()
        field_name = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "equals").strip()

        if condition_kind not in RULE_KINDS or condition_kind == CATCH_ALL:
            problems.append(f"{label} has unknown kind '{condition_kind}'.")
        if source not in RULE_SOURCES:
            problems.append(
                f"{label} reads source '{source}', which is not readable. "
                f"Use 'form' for a Data Field, or one of: {', '.join(CRM_OBJECTS)}."
            )
        if not field_name:
            problems.append(f"{label} names no field.")
        if operator not in OPERATORS:
            problems.append(f"{label} has unknown operator '{operator}'.")

    assign = str(entry.get("assign_user_id") or "").strip()
    if kind and not assign:
        problems.append(
            f"{where} names no assign_user_id, so a lead that matches it would have "
            "no proposed owner."
        )
    writeback = entry.get("crm_writeback")
    if writeback is not None and not isinstance(writeback, Mapping):
        problems.append(f"{where} has a crm_writeback that is not an object.")
    return problems


def refuses_interval(payload: Mapping[str, Any]) -> bool:
    """Does this body carry an ``interval``, the field that switches workflow?

    Checked at the top level only. A nested field called ``interval`` is part of
    a lead's own data, and treating it as the switch would refuse a lead for
    saying so in a form.
    """
    return INTERVAL_FIELD in payload
