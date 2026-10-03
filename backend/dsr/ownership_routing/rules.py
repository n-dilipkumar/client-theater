"""Routing rules, and the chain that cannot fail to fall through.

Two researched ways of deciding who a booking goes to, kept apart because the
research keeps them apart:

* A **CRM Ownership** rule in a Concierge router "can check whether a rep from
  Team A owns the Lead, Contact **or** Account, and route to that rep". The rule
  names a team; it matches when the resolved owner is in that team.
* A **Without Ownership** rule reads "CRM values or Data Field values", so it
  matches on a field of the guest's CRM record or on a Data Field the form
  collected.

Both are rules in a chain, and the chain ends in a **catch-all**. That is the
property this module exists to keep: "Admin adds ``Routing Rule`` / ``Catch All``
nodes", and a chain that can simply run out is a prospect who reaches the end of
the scheduler and books nothing. :func:`require_catch_all` refuses the
declaration instead, so ``unroutable`` is not a state a well-formed chain can
reach at runtime.

The researched guardrail lives here too, because it is a property of the chain's
nodes: "you should not use this node in **Ownership** paths. If you have any Assign
To nodes in Ownership-related paths, this could prevent your Router from being
published."
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from dsr.ownership_routing.errors import (
    ForbiddenNodeOnOwnershipPath,
    RoutingRuleError,
    RulesDoNotFallThrough,
)
from dsr.ownership_routing.vocabulary import (
    ASSIGN_TO_NODES,
    CATCH_ALL,
    FORBIDDEN_ON_OWNERSHIP_PATH,
    UPDATE_OWNERSHIP_NODE,
    require_rule_kind,
)

#: A rule that matches on team membership. Named after the research's own words:
#: "checks whether one of the reps from Team A owns the Lead, Contact, or Account
#: object associated with the prospect".
TEAM_RULE = "crm_ownership"

#: A rule that matches on a CRM value or a Data Field value.
VALUE_RULE = "without_ownership"

#: The terminal node. It matches everything and routes to whoever it names.
CATCH_ALL_RULE = CATCH_ALL


def normalise_rules(raw: Any, *, ownership_path: bool = True) -> list[dict[str, Any]]:
    """Validate a declared rule chain into the shape the evaluator consumes.

    ``ownership_path`` defaults to True because that is what this workflow builds:
    an Ownership link is an ownership path, and the researched warning is about
    ownership paths specifically. A chain built for another path can pass False and
    may then carry an ``update_ownership`` node.

    Three refusals happen here rather than at run time, and all three are about a
    chain that could not work:

    * an unknown rule kind, so a typo does not become a rule that never matches;
    * a chain with no catch-all, because it could end without naming a host;
    * a forbidden node on an ownership path, because the research says it "could
      prevent your Router from being published".
    """
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raise RoutingRuleError("rules must be a list of rule objects")

    rules: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise RoutingRuleError(f"rule {index} must be an object, got {type(entry).__name__}")
        kind = str(entry.get("kind") or "").strip()
        if kind.casefold() == CATCH_ALL:
            if rules and rules[-1]["kind"] == CATCH_ALL_RULE:
                raise RoutingRuleError("only one catch-all may terminate a chain")
            rules.append(
                {
                    "kind": CATCH_ALL_RULE,
                    "name": str(entry.get("name") or "Catch all"),
                    "owner_id": str(entry.get("owner_id") or "").strip(),
                    "position": len(rules),
                }
            )
            continue
        try:
            resolved_kind = require_rule_kind(kind)
        except ValueError as exc:
            raise RoutingRuleError(str(exc)) from exc
        rule: dict[str, Any] = {
            "kind": resolved_kind,
            "name": str(entry.get("name") or f"Rule {index + 1}"),
            "position": len(rules),
        }
        if resolved_kind == TEAM_RULE:
            rule["team"] = str(entry.get("team") or "").strip()
            if not rule["team"]:
                raise RoutingRuleError(
                    "a CRM Ownership rule needs a team; the research's example checks whether a rep "
                    "from a named team owns the record"
                )
        else:
            field = str(entry.get("field") or "").strip()
            if not field:
                raise RoutingRuleError("a Without Ownership rule needs a field to read")
            rule["field"] = field
            rule["equals"] = entry.get("equals")
            rule["source"] = str(entry.get("source") or "crm").strip().casefold() or "crm"
            # Carried through because a value rule may name where the prospect goes
            # rather than only whether to send them. Dropping it here made every
            # such rule silently keep the resolved owner - the rule matched, the
            # page said it matched, and the prospect still reached the CRM owner.
            if str(entry.get("owner_id") or "").strip():
                rule["owner_id"] = str(entry["owner_id"]).strip()
            if rule["source"] not in ("crm", "data_field"):
                raise RoutingRuleError(
                    "a Without Ownership rule reads a 'crm' value or a 'data_field' value; "
                    f"{rule['source']!r} is neither"
                )
        rules.append(rule)

    require_catch_all(rules, ownership_path=ownership_path)
    return rules


def require_catch_all(rules: Sequence[Mapping[str, Any]], *, ownership_path: bool = True) -> None:
    """Refuse a chain that does not end in a catch-all naming a host.

    The catch-all has to be **last** as well as present. A chain whose catch-all is
    in the middle is a chain where every later rule is dead, and a dead rule is a
    configuration mistake a reader would not notice from the payload.
    """
    if not rules:
        raise RulesDoNotFallThrough(
            "a routing chain needs at least one rule; the researched flow is built from "
            "Routing Rule and Catch All nodes"
        )
    last = rules[-1]
    if str(last.get("kind") or "") != CATCH_ALL_RULE:
        raise RulesDoNotFallThrough(
            "a routing chain must end in a catch-all naming a host, or a prospect who matches "
            f"no rule reaches the end of the scheduler and books nothing; last rule is "
            f"{last.get('kind')!r}"
        )
    if not str(last.get("owner_id") or "").strip():
        raise RulesDoNotFallThrough(
            "the catch-all must name an owner; a catch-all that routes nowhere is not a catch-all"
        )


def check_nodes(nodes: Any, *, ownership_path: bool = True) -> list[str]:
    """Refuse the nodes the research says must not appear on an ownership path.

    "You should not use this node in **Ownership** paths. If you have any Assign To
    nodes in Ownership-related paths, this could prevent your Router from being
    published."

    Both halves are enforced: the ``Update Ownership`` node itself, and the
    ``Assign To`` family named in the same sentence. The second half is the one
    that matters, because it is the half the sentence explains the consequence of -
    a router that will not publish fails at ship time, not at configuration time,
    which is exactly the failure a reviewer would rather meet here.
    """
    if nodes is None:
        nodes = []
    if not isinstance(nodes, (list, tuple)):
        raise RoutingRuleError("nodes must be a list of node names")
    names = [str(node).strip().casefold().replace("-", "_").replace(" ", "_") for node in nodes]
    if not ownership_path:
        return names
    offenders = [name for name in names if name in FORBIDDEN_ON_OWNERSHIP_PATH]
    if offenders:
        raise ForbiddenNodeOnOwnershipPath(
            f"{', '.join(offenders)} cannot sit on an Ownership path: the research says Assign To "
            "nodes in Ownership-related paths could prevent the Router from being published"
        )
    return names


def nodes_warnings(nodes: Iterable[str], *, ownership_path: bool = True) -> list[dict[str, str]]:
    """The same check as advice, for a page that shows a draft before saving.

    A preview that refuses is less useful than one that says which node is a
    problem, so the page asks for the advisory form and the save path asks for the
    enforcing one.
    """
    warnings: list[dict[str, str]] = []
    if not ownership_path:
        return warnings
    for name in nodes:
        cleaned = str(name).strip().casefold().replace("-", "_").replace(" ", "_")
        if cleaned in FORBIDDEN_ON_OWNERSHIP_PATH:
            warnings.append(
                {
                    "node": cleaned,
                    "severity": "refused",
                    "why": "the research forbids Assign To / Update Ownership on an Ownership path",
                }
            )
    return warnings


def evaluate(
    rules: Sequence[Mapping[str, Any]],
    *,
    resolution: Mapping[str, Any],
    teams: Mapping[str, Iterable[str]],
    guest_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the chain against one resolution and say which rule matched.

    ``teams`` maps a team name to the owner ids in it, which is the research's rule
    shape: "checks whether one of the reps from Team A owns the Lead, Contact or
    Account, and route to that rep". The check is on the *resolved* owner, because
    that is who the booking is going to - a rule that matched on the record's owner
    field without checking the team would route to whoever owns it, which is the
    Ownership link's job and not this rule's.

    The catch-all always matches, so the return is a match by construction rather
    than a lucky one; ``unroutable`` is only reachable through a chain that
    :func:`require_catch_all` would have refused.
    """
    fields = dict(guest_fields or {})
    owner_id = str(resolution.get("owner_id") or "").strip()
    considered: list[dict[str, Any]] = []

    # A chain of only catch-alls carries no decision, so it is not a decision: the
    # Ownership link's own behaviour already resolved an owner, and treating a bare
    # fallback as a rule would send every prospect to the deal desk and make the
    # link's name a lie. So the resolved owner stands unless it is empty, and the
    # catch-all is reached only when there is nobody to route to.
    if all(str(rule.get("kind") or "") == CATCH_ALL_RULE for rule in rules):
        fallback = rules[-1]
        if owner_id:
            return {
                "outcome": "resolved",
                "rule": None,
                "owner_id": owner_id,
                "considered": [
                    {
                        "kind": "ownership",
                        "name": "The CRM record's owner",
                        "matched": True,
                        "why": f"resolved {resolution.get('matched_object_type')} owner "
                        f"{owner_id} at booking time",
                    }
                ],
                "resolution": dict(resolution),
                "catch_all_available": True,
            }
        considered.append(
            {
                "kind": CATCH_ALL_RULE,
                "name": fallback.get("name"),
                "matched": True,
                "owner_id": str(fallback.get("owner_id") or ""),
                "why": "nothing resolved an owner, so the chain fell through to its catch-all",
            }
        )
        return {
            "outcome": "catch_all",
            "rule": dict(considered[-1]),
            "owner_id": str(fallback.get("owner_id") or ""),
            "considered": considered,
            "resolution": dict(resolution),
            "catch_all_available": True,
        }

    for rule in rules:
        kind = str(rule.get("kind") or "")
        entry: dict[str, Any] = {
            "kind": kind,
            "name": rule.get("name"),
            "matched": False,
            "why": "",
        }
        if kind == TEAM_RULE:
            team = str(rule.get("team") or "")
            members = {str(member) for member in (teams.get(team) or ())}
            if owner_id and owner_id in members:
                entry["matched"] = True
                entry["why"] = (
                    f"{owner_id} is on team {team!r}, which owns the {resolution.get('matched_object_type')}"
                )
                considered.append(entry)
                return {
                    "outcome": "resolved",
                    "rule": dict(entry),
                    # A CRM Ownership rule "route[s] to that rep" - the rep who owns
                    # the record. It does not redirect the prospect elsewhere, so the
                    # owner the chain routes to is the owner that was resolved.
                    "owner_id": owner_id,
                    "considered": considered,
                    "resolution": dict(resolution),
                    "catch_all_available": True,
                }
            named = str(resolution.get("named_owner_id") or "")
            if not owner_id and named:
                entry["why"] = (
                    f"the record names owner {named}, but no rep in this workspace answers to it"
                )
            elif owner_id:
                entry["why"] = f"the resolved owner {owner_id} is not on team {team!r}"
            else:
                entry["why"] = f"nothing resolved an owner, so team {team!r} cannot match"
        elif kind == VALUE_RULE:
            field = str(rule.get("field") or "")
            source = str(rule.get("source") or "crm")
            haystack = fields if source == "data_field" else (resolution.get("record") or {})
            actual = haystack.get(field)
            expected = rule.get("equals")
            if source == "data_field" and field not in fields:
                entry["why"] = f"no Data Field named {field!r} was collected"
            elif _same(actual, expected):
                entry["matched"] = True
                entry["why"] = f"{field} == {expected!r}"
                # A value rule may name the owner to send the prospect to, or leave
                # it out and keep whoever the CRM already resolved. The second is
                # the common case: the rule is there to *exclude* a prospect from
                # falling through, not to reassign them.
                entry["owner_id"] = str(rule.get("owner_id") or owner_id)
                considered.append(entry)
                return {
                    "outcome": "resolved",
                    "rule": dict(entry),
                    "owner_id": entry["owner_id"],
                    "considered": considered,
                    "resolution": dict(resolution),
                    "catch_all_available": True,
                }
            else:
                entry["why"] = f"{field} is {actual!r}, not {expected!r}"
        elif kind == CATCH_ALL_RULE:
            # Only reachable when nothing above matched, because a matching rule
            # returns on the spot. So `outcome` is always `catch_all` here, and the
            # distinction the page draws - "the CRM owner took this" against "the
            # deal desk took this because nobody did" - comes from *which* branch
            # answered rather than from re-reading the considered list.
            entry["matched"] = True
            entry["owner_id"] = str(rule.get("owner_id") or "")
            entry["why"] = "no earlier rule matched, so the chain fell through to its catch-all"
            considered.append(entry)
            return {
                "outcome": "catch_all",
                "rule": dict(entry),
                "owner_id": entry["owner_id"],
                "considered": considered,
                "resolution": dict(resolution),
                "catch_all_available": True,
            }
        else:  # pragma: no cover - normalise_rules refuses these first
            entry["why"] = f"unknown rule kind {kind!r}"
        considered.append(entry)

    return {
        "outcome": "unroutable",
        "rule": None,
        "owner_id": "",
        "considered": considered,
        "resolution": dict(resolution),
    }


def _same(actual: Any, expected: Any) -> bool:
    """Compare a rule's expected value with a record's, tolerating case.

    Email addresses and team slugs are compared case-insensitively because a rule
    authored as ``Enterprise`` should match a record carrying ``enterprise``; every
    other comparison is by equality, including a boolean compared against the
    string ``"true"``, which is a data problem worth surfacing rather than papering
    over.
    """
    if actual is None or expected is None:
        return actual is None and expected is None
    if isinstance(actual, bool) or isinstance(expected, bool):
        return bool(actual) == bool(expected) and isinstance(actual, bool) == isinstance(
            expected, bool
        )
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return str(actual).strip().casefold() == str(expected).strip().casefold()


def node_names_for(nodes: Iterable[str]) -> list[str]:
    """Canonical names for a node list, so a page and a validator agree."""
    return [str(node).strip().casefold().replace("-", "_").replace(" ", "_") for node in nodes]


def forbidden_nodes() -> tuple[str, ...]:
    """The nodes refused on an ownership path, as the endpoint serves them."""
    return FORBIDDEN_ON_OWNERSHIP_PATH


__all__ = [
    "CATCH_ALL_RULE",
    "FORBIDDEN_ON_OWNERSHIP_PATH",
    "TEAM_RULE",
    "UPDATE_OWNERSHIP_NODE",
    "VALUE_RULE",
    "ASSIGN_TO_NODES",
    "check_nodes",
    "evaluate",
    "forbidden_nodes",
    "node_names_for",
    "nodes_warnings",
    "normalise_rules",
    "require_catch_all",
]
