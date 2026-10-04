"""The declaration rules: what a router must look like before it can be saved.

Every function here is a refusal that fires when a router is *declared*, never
when a prospect arrives. That placement is the point. The research says three
things about shape, and all three are stated as absolutes:

* "Trigger will **always** be your first node in a Concierge Router."
* "Each router **must** end with a '**Catch All**' path to make sure you define
  the routing and acknowledge all inbound Leads."
* "First node is always ``Trigger``; enables ``Webform is submitted``,
  ``In-app``, and/or ``Router Link``."

A declaration that could never have worked is refused where it was written. The
alternative is discovering it from a lead nobody answered, which is the failure
the Catch All rule exists to prevent.

Nothing here touches the store. These are pure functions over a payload, so the
rules can be tested without a database and cannot write anything themselves.
"""

from __future__ import annotations

from typing import Any

from dsr.concierge_router import vocabulary
from dsr.concierge_router.errors import (
    FieldMappingError,
    RedirectWithoutUrl,
    RulesDoNotFallThrough,
    TriggerActionMissing,
    TriggerMustBeFirst,
    UnknownNodeType,
)

#: The conditions a rule node may test. Two sources, exactly as the research
#: splits them: a CRM Ownership rule reads "Lead/Contact/Account owner against a
#: Team", and a Without Ownership rule reads "CRM values or Data Field values".
CRM_OWNERSHIP_FIELDS: tuple[str, ...] = ("owner_id", "owner_team")
DATA_FIELD_SOURCE = "data_field"
CRM_OBJECT_SOURCE = "crm_object"

CONDITION_SOURCES: tuple[str, ...] = (DATA_FIELD_SOURCE, CRM_OBJECT_SOURCE)

CONDITION_OPERATORS: tuple[str, ...] = ("equals", "not_equals", "contains", "in")


def _refuse_unknown_node(node: dict[str, Any], index: int) -> str:
    try:
        return vocabulary.require_node_type(node.get("type", ""))
    except ValueError as exc:
        raise UnknownNodeType(f"node {index}: {exc}") from exc


def require_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise every node's type, or refuse a type the research does not name.

    Refused by name so a typo becomes a message rather than a rule that never
    fires: "The router's default post-booking nodes fire" names the node set, and
    a node outside it has no researched behaviour at all.
    """
    raw = payload.get("nodes")
    if not isinstance(raw, list) or not raw:
        raise UnknownNodeType("a router needs a non-empty 'nodes' list")
    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(raw):
        if not isinstance(node, dict):
            raise UnknownNodeType(f"node {index} must be an object")
        stated = _refuse_unknown_node(node, index)
        nodes.append({**node, "type": stated})
    return nodes


def require_trigger_first(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the ``Trigger`` node, or refuse a chain that does not start with one.

    "Trigger will **always** be your first node in a Concierge Router, and it
    indicates which action will make your router be triggered." The word is
    *always*, so a chain whose first node is a routing rule has no declared
    trigger and nothing can start it.
    """
    first = nodes[0]
    if first["type"] != vocabulary.TRIGGER:
        raise TriggerMustBeFirst(
            f"a Concierge Router's first node is always {vocabulary.TRIGGER!r}; "
            f"this one starts with {first['type']!r}"
        )
    return first


def require_trigger_action(trigger: dict[str, Any]) -> list[str]:
    """Return the trigger's actions, or refuse a trigger enabled by nothing.

    The node "enables ``Webform is submitted``, ``In-app``, and/or ``Router
    Link``". A trigger naming none of the three is triggered by nothing, which is
    the same defect as having no trigger at all, reached by a different route.
    """
    raw = trigger.get("actions")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise TriggerActionMissing(
            f"the Trigger node must enable at least one of {', '.join(vocabulary.TRIGGER_ACTIONS)}"
        )
    actions: list[str] = []
    for value in raw:
        try:
            action = vocabulary.require_trigger_action(value)
        except ValueError as exc:
            raise TriggerActionMissing(f"trigger action: {exc}") from exc
        if action not in actions:
            actions.append(action)
    return actions


def require_catch_all(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the chain's ``Catch All`` node, or refuse a chain that lacks one.

    "Each router **must** end with a '**Catch All**' path to make sure you define
    the routing and acknowledge all inbound Leads."

    Refusing the declaration is the fix. A chain that can run out is an inbound
    lead who reaches the end of the router and is never acknowledged, which is
    exactly what the rule forbids. Enforcing it here also makes
    :class:`~dsr.concierge_router.errors.NoRuleMatched` unreachable at run time
    for any router that saved, so the catch-all path is a rule and not a default.
    """
    for node in reversed(nodes):
        if node["type"] == vocabulary.CATCH_ALL:
            return node
    raise RulesDoNotFallThrough(
        "each router must end with a 'Catch All' path to make sure you define the "
        "routing and acknowledge all inbound Leads"
    )


def require_field_map(trigger: dict[str, Any]) -> dict[str, str]:
    """Return the ``Trigger`` node's field map, or refuse a map that is not one.

    "Admin maps the webform fields to Chili Piper **Data Fields** in the Trigger
    node (via ``Find Form`` -> ``Map Fields`` automap, or ``Add Mapping`` manual
    mapping)."

    Both sides are folded to snake_case by
    :func:`~dsr.concierge_router.vocabulary.normalise_field_name`, because a
    marketing webform posts whatever HTML ``name`` the page author wrote and the
    research's own map may be hand-written. A map that maps nothing, or that names
    the same Data Field twice, is refused where it is declared: a rule reading
    that Data Field would either never fire or read whichever entry was written
    last.
    """
    raw = trigger.get("field_map")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise FieldMappingError("the Trigger node's field_map must be an object")

    normalised: dict[str, str] = {}
    targets: dict[str, str] = {}
    for form_field, data_field in raw.items():
        folded_field = vocabulary.normalise_field_name(form_field)
        folded_target = vocabulary.normalise_field_name(data_field)
        if not folded_field or not folded_target:
            raise FieldMappingError(
                f"field_map entry {form_field!r} -> {data_field!r} maps to an empty name"
            )
        if folded_target in targets:
            raise FieldMappingError(
                f"the Data Field {folded_target!r} is mapped from both "
                f"{targets[folded_target]!r} and {folded_field!r}; a rule reading it "
                "could only read one"
            )
        targets[folded_target] = folded_field
        normalised[folded_field] = folded_target
    return normalised


def require_node_timers(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check every timer node carries the fields its researched timer needs.

    Two node types carry a timer. The ``Display Calendar`` node's **Time
    Elapsed** timer decides when a session becomes "not scheduled", so it needs a
    duration. ``Redirect To`` "has its own countdown timer before bouncing the
    prospect", so it needs a duration *and* something to bounce to.
    """
    checked: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if node["type"] not in vocabulary.TIMER_NODE_TYPES:
            continue
        minutes = node.get("timer_minutes", vocabulary.DEFAULT_TIMER_MINUTES)
        try:
            duration = int(minutes)
        except (TypeError, ValueError) as exc:
            raise RedirectWithoutUrl(
                f"node {index} ({node['type']}): timer_minutes must be a whole number "
                f"of minutes; got {minutes!r}"
            ) from exc
        if duration <= 0:
            raise RedirectWithoutUrl(
                f"node {index} ({node['type']}): timer_minutes must be positive; got {duration}"
            )
        if node["type"] == vocabulary.REDIRECT_TO and not str(node.get("url") or "").strip():
            raise RedirectWithoutUrl(
                f"node {index} ({node['type']}): a Redirect To countdown needs a url, "
                "because a countdown with nothing to bounce to can only expire into a dead end"
            )
        checked.append({**node, "timer_minutes": duration})
    return checked


def require_conditions(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise every rule node's conditions, or refuse one that cannot be read.

    A rule with no conditions is legal: the research's Catch All is "a path to
    make sure you define the routing and acknowledge all inbound Leads", which is
    exactly a rule that matches everything remaining. A condition that names no
    field is not legal, because it cannot ever be true or false.
    """
    checked: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if node["type"] not in vocabulary.RULE_NODE_TYPES:
            continue
        kind = vocabulary.normalise_rule_kind_for_node(str(node.get("kind", "")))
        raw = node.get("conditions") or []
        if not isinstance(raw, list):
            raise FieldMappingError(f"node {index}: conditions must be a list")
        conditions: list[dict[str, Any]] = []
        for position, condition in enumerate(raw):
            if not isinstance(condition, dict):
                raise FieldMappingError(f"node {index} condition {position} must be an object")
            field = str(condition.get("field") or "").strip()
            if not field:
                raise FieldMappingError(
                    f"node {index} condition {position} names no field, so it can never "
                    "be true or false"
                )
            source = str(condition.get("source") or DATA_FIELD_SOURCE).strip().lower()
            if source not in CONDITION_SOURCES:
                raise FieldMappingError(
                    f"node {index} condition {position}: source must be one of "
                    f"{', '.join(CONDITION_SOURCES)}; got {source!r}"
                )
            operator = str(condition.get("operator") or "equals").strip().lower()
            if operator not in CONDITION_OPERATORS:
                raise FieldMappingError(
                    f"node {index} condition {position}: operator must be one of "
                    f"{', '.join(CONDITION_OPERATORS)}; got {operator!r}"
                )
            conditions.append(
                {
                    "field": vocabulary.normalise_field_name(field),
                    "source": source,
                    "operator": operator,
                    "value": condition.get("value"),
                }
            )
        checked.append({**node, "kind": kind, "conditions": conditions})
    return checked


def require_assignment(node: dict[str, Any]) -> dict[str, Any]:
    """Normalise a ``Display Calendar`` node's assignment and Meeting Types.

    "On a rule match, admin adds a ``Display Calendar`` node choosing **Owner**,
    **Round-Robin**, or **Individual user**, plus the **Meeting Type(s)** to
    offer."

    Both halves are required. A node that names no Meeting Type has nothing to
    offer, so no slot can be rendered in the modal.
    """
    try:
        assignment_type = vocabulary.require_assignment_type(
            str(node.get("assignment", {}).get("type", ""))
            if isinstance(node.get("assignment"), dict)
            else str(node.get("assignment") or "")
        )
    except ValueError as exc:
        raise UnknownNodeType(f"a Display Calendar node's assignment: {exc}") from exc

    meeting_types = node.get("meeting_types") or []
    if isinstance(meeting_types, str):
        meeting_types = [meeting_types]
    if not isinstance(meeting_types, list) or not meeting_types:
        raise UnknownNodeType(
            "a Display Calendar node must name at least one Meeting Type to offer"
        )
    names = [str(name).strip() for name in meeting_types if str(name).strip()]
    if not names:
        raise UnknownNodeType(
            "a Display Calendar node must name at least one Meeting Type to offer"
        )
    return {
        "assignment": {
            "type": assignment_type,
            "policy": str(
                (node.get("assignment") or {}).get("policy", "")
                if isinstance(node.get("assignment"), dict)
                else ""
            ).strip(),
        },
        "meeting_types": names,
    }


def validate_declaration(payload: dict[str, Any]) -> dict[str, Any]:
    """Check a whole router declaration and return it normalised.

    The order matters and is the research's order: the trigger is checked first
    because "Trigger will **always** be your first node", the field map next
    because the rules read it, the catch-all last because it is the rule about
    the end of the chain. Returns the normalised declaration so a caller saves
    what was checked rather than what was sent.

    Each node type is normalised by its own rule, in one pass. Splitting this into
    a stage per node type and reassembling the list is the shape that produced
    four separate list comprehensions fighting over the same indices, and a
    reassembly like that is where a node silently goes missing.
    """
    raw_nodes = require_nodes(payload)

    nodes: list[dict[str, Any]] = []
    for node in raw_nodes:
        stated = dict(node)
        stated_type = stated["type"]

        if stated_type == vocabulary.TRIGGER:
            stated["actions"] = require_trigger_action(stated)

        if stated_type in vocabulary.TIMER_NODE_TYPES:
            checked = require_node_timers([stated])[0]
            stated.update({key: checked[key] for key in ("timer_minutes",)})
            if stated_type == vocabulary.REDIRECT_TO:
                stated["url"] = str(stated.get("url") or "").strip()

        if stated_type in vocabulary.RULE_NODE_TYPES:
            normalised = require_conditions([stated])[0]
            stated["conditions"] = normalised["conditions"]
            stated["kind"] = normalised["kind"]
            # "On a rule match, admin adds a Display Calendar node". A rule that
            # carries its own calendar names the node inline, so that nested spec
            # is normalised by the same rules a standalone node is. Leaving it raw
            # would let a hand-written assignment reach the engine unvalidated.
            if isinstance(stated.get("calendar"), dict):
                nested = {**stated["calendar"], "type": vocabulary.DISPLAY_CALENDAR}
                nested = require_node_timers([nested])[0]
                # The helper's keys are written one by one rather than with
                # ``update``, because ``update`` would let the helper's own shape
                # replace the node's and drop every other field it carried.
                checked = require_assignment(nested)
                nested["assignment"] = checked["assignment"]
                nested["meeting_types"] = checked["meeting_types"]
                stated["calendar"] = nested

        if stated_type in vocabulary.CALENDAR_NODE_TYPES:
            assignment = require_assignment(stated)
            stated["assignment"] = assignment["assignment"]
            stated["meeting_types"] = assignment["meeting_types"]

        nodes.append(stated)

    trigger = require_trigger_first(nodes)
    field_map = require_field_map(trigger)
    catch_all = require_catch_all(nodes)

    return {
        "slug": str(payload.get("slug") or "").strip(),
        "name": str(payload.get("name") or "").strip(),
        "nodes": nodes,
        "field_map": field_map,
        "catch_all": str(catch_all.get("name") or vocabulary.CATCH_ALL),
        "routing_link_base": str(payload.get("routing_link_base") or "").strip(),
    }


def rule_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The chain's rule nodes in declaration order, catch-all last.

    Evaluation walks this list, so the order a router was declared in is the
    order its rules are tried in. The catch-all is pulled to the end because the
    research makes it the final path: "Each router **must** end with a '**Catch
    All**' path".
    """
    rules = [node for node in nodes if node["type"] in vocabulary.RULE_NODE_TYPES]
    catch_alls = [node for node in rules if node["type"] == vocabulary.CATCH_ALL]
    named = [node for node in rules if node["type"] != vocabulary.CATCH_ALL]
    return named + catch_alls
