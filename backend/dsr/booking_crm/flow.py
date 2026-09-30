"""The flow: a meeting type, a router path, and an ordered list of nodes.

This is the module the research's steps 1 and 2 land in. An admin adds
``Create or Update Record`` to one of the router's three paths, chooses an update
branch and a create branch, then adds ``Create Event``/``Create Engagement`` and
any of the three optional downstream nodes. The declaration is *data*, so a
deployment that adds a fourth node kind, or a fourth related object, ships a
record rather than a code path.

The ordering rule
-----------------
:func:`validate_nodes` is the one piece of this package that refuses rather than
reorders, and it refuses because the research refuses:

    [sourced] "Note this node must precede the **Create Event**, **Update Field**,
    **Add to Campaign**, and **Update Ownership** nodes"

Every other node needs the id of the record the create node produced, so a flow
that declares them the other way round cannot be executed - and silently sorting
it would make the declared order, which is the thing an admin reads to
understand their own router, a lie. So the nodes are validated once, at
declaration, and the stored flow carries the resolved plan.

Nothing falls through
---------------------
A flow may declare *only* the anchor node, and it may declare the anchor and
nothing else. What it may not do is declare a downstream node with no anchor
ahead of it, and it may not declare a node twice where the research describes
one. Both are refused with the node named, because "a rule that does not fall
through is a bug someone will hit in production": a flow whose third node has no
record to write to is exactly that.

The Sync Meeting Type toggle
----------------------------
:func:`sync_enabled` is the second thing that can refuse, and it refuses for a
reason the research states outright: the setting "is applied to all users in
your org". So it lives on the meeting type, and a run cannot carry its own
override - an override would be a per-user setting, which the research says does
not exist.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.booking_crm.errors import InvalidConfig, InvalidNode, NodeOrderError
from dsr.booking_crm.vocabulary import (
    ALL_NODES,
    ANCHOR_NODES,
    CREATE_ALWAYS_LEAD,
    CREATE_BRANCH_LABEL,
    CREATE_CONTACT_OR_LEAD,
    CREATE_LEAD,
    CREATE_MEANING,
    CREATE_NONE,
    CREATE_STATES,
    DEPENDENT_NODES,
    HUBSPOT,
    HUBSPOT_RELATED,
    MATCH_KEY_EMAIL,
    MATCH_KEYS,
    ORDERING_QUOTE,
    PATH_DISQUALIFIED,
    PATH_NOT_SCHEDULED,
    PATH_SCHEDULED,
    PATHS,
    RECORD_LEAD,
    RECORD_TYPES,
    RELATED_REQUIRES_CONTACT_QUOTE,
    SALESFORCE,
    SALESFORCE_RELATED,
    SELECTION_RULES,
    SYNC_TOGGLE_ORG_WIDE_QUOTE,
    UPDATE_BRANCHES,
    VENDOR_NODES,
)

#: The node kinds that may appear at most once, because the research describes
#: each of them as a single node in the palette. ``update_field`` /
#: ``update_property`` are *not* here: a router can carry several, one per
#: field, and the research's own example - ``Contact.Status = "Sales Qualified"``
#: - is a single field rather than a single node.
SINGLETON_NODES: frozenset[str] = frozenset(
    {
        "create_or_update_record",
        "create_or_update_contact",
        "create_event",
        "create_engagement",
        "related_object",
        "add_to_campaign",
        "update_ownership",
    }
)

#: The event-creating node name for each vendor. The research pairs them
#: explicitly: "``Create Event`` (Salesforce) or ``Create Engagement`` (HubSpot)".
EVENT_NODE: dict[str, str] = {SALESFORCE: "create_event", HUBSPOT: "create_engagement"}

#: The field-writing node name for each vendor. "``Update Field``/``Update
#: Property``" - the same split, the same way round.
FIELD_NODE: dict[str, str] = {SALESFORCE: "update_field", HUBSPOT: "update_property"}

#: The create-node name for each vendor. "``Create or Update Record``
#: (Salesforce) and ``Create or Update Contact`` (HubSpot)".
ANCHOR_NODE: dict[str, str] = {
    SALESFORCE: "create_or_update_record",
    HUBSPOT: "create_or_update_contact",
}

#: What the created record looks like in the CRM, per vendor. "``Create
#: Engagement`` (HubSpot)" writes an Engagement; "``Create Event`` (Salesforce)"
#: writes an Event. The Related Object types differ too, and the research lists
#: them side by side, so the pair belongs together.
EVENT_RECORD_TYPE: dict[str, str] = {SALESFORCE: "Event", HUBSPOT: "Engagement"}

#: The Related Object types each vendor's event node accepts.
RELATED_TYPES: dict[str, tuple[str, ...]] = {
    SALESFORCE: SALESFORCE_RELATED,
    HUBSPOT: HUBSPOT_RELATED,
}

#: The record type each Related Object belongs to, so the selection rule can find
#: candidates. "Deal" is a HubSpot Opportunity and "Ticket" is a Salesforce Case
#: by name, and the research's ``data_sources`` sentence lists both sets, so the
#: two map onto the two rules the research states.
RELATED_RECORD_TYPE: dict[str, str] = {
    "Account": "Account",
    "Case": "Case",
    "Opportunity": "Opportunity",
    "Campaign": "Campaign",
    "Company": "Company",
    "Deal": "Deal",
    "Ticket": "Ticket",
}

#: The record types the anchor node searches when matching by email, per vendor.
#:
#: [sourced] "matched/created CRM record (Lead or Contact, matched by email)". The
#: search order is this build's reading, not the research's: Salesforce searches
#: Lead then Contact, because a Lead is the pre-Account record and a conversion
#: (the L2A rule the research names) turns one into the other - so finding the
#: Lead first is what lets ``Only update matched Lead`` mean anything. HubSpot has
#: no Lead, so it searches Contact alone. Both orders are in the vocabulary.
MATCH_ORDER: dict[str, tuple[str, ...]] = {
    SALESFORCE: ("Lead", "Contact"),
    HUBSPOT: ("Contact",),
}

#: The value the create node's ``record_type`` may take when the create branch is
#: ``contact_or_lead`` - "Create Contact or Lead" is a branch, and the node's
#: ``record_type`` is which of the two it makes. A branch that names one record
#: type on its own does not need it, and declaring one that contradicts the branch
#: is refused.
CREATE_BRANCH_NEEDS_RECORD_TYPE = (CREATE_CONTACT_OR_LEAD,)

#: The named states a plan can be skipped for. Every one is a *state*, never a
#: silent drop, and each carries the rule that caused it on the result.
SKIP_NO_RECORD = "no_record"
SKIP_MEETING_TYPE_SYNC_OFF = "meeting_type_sync_off"
SKIP_RETRY_NOT_FAILED = "retry_only_offered_on_a_failure"

NODE_ORDER_MESSAGE = (
    "node {node!r} at position {index} is declared before the "
    "'Create or Update Record' node at position {anchor}, but [sourced] \"{quote}\". "
    "Every other node writes to the record the create node produced, so it has to "
    "run after it."
)


# --------------------------------------------------------------------------- #
# Node normalisation
# --------------------------------------------------------------------------- #


#: The declared-setting keys this build reads off a node. One set for every node
#: kind on purpose: the pass-through filter needs to recognise *any* of them so a
#: key is not both read and preserved, and the cost of a slightly larger set is
#: that a key this build happens to read for one node kind is also filtered out of
#: the pass-through for another - which is the correct answer anyway, because the
#: other node kind would have refused it.
DECLARED_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "update", "create", "record_type", "match_on", "fields", "l2a",
        "object", "related_object", "campaign", "child_events", "delete_event",
        "subject", "start", "end", "activity_assigned_to", "status", "assign_to",
        "fallback_mode", "skip_contact_owner", "crm_app_slug",
        "crm_owner_record_type", "attribute_rules",
    }
)


def normalise_nodes(
    raw: Any, *, vendor: str
) -> list[dict[str, Any]]:
    """Validate and canonicalise a declared node list.

    The vendor is taken first and everything else is checked against it, because
    the research pairs the node names and never mixes them: a Salesforce flow
    declaring ``create_engagement`` is a mistake, not a synonym.

    Returns the canonical list. Raises :class:`InvalidNode` for a node this build
    does not know or that belongs to the other vendor, and
    :class:`NodeOrderError` for the ordering rule.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise InvalidConfig("nodes must be a list of node objects")
    nodes: list[dict[str, Any]] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise InvalidNode(f"node at position {position} is not an object")
        name = str(entry.get("node") or entry.get("type") or "").strip()
        if not name:
            raise InvalidNode(f"node at position {position} names no node")
        if name not in ALL_NODES:
            raise InvalidNode(
                f"unknown node {name!r} at position {position}; this build knows "
                f"{list(ALL_NODES)}"
            )
        allowed = VENDOR_NODES[vendor]
        if name not in allowed:
            other = HUBSPOT if vendor == SALESFORCE else SALESFORCE
            raise InvalidNode(
                f"node {name!r} belongs to the {other} palette, not {vendor}; "
                f"{vendor} nodes are {list(allowed)}"
            )
        node = {"node": name, "position": position}
        node.update(_normalise_settings(name, entry, vendor))
        # Anything the flow carried that this build does not read is kept as it
        # arrived. That is the storage contract in one line: a team that adds a key
        # to a node - an owner queue, an SLA, a field this build has never heard of
        # - has it back on the round trip, and a future build that learns to read it
        # finds it already there.
        known = {"node", "position", "type"} | DECLARED_SETTING_KEYS
        node.update({key: value for key, value in entry.items() if key not in known})
        nodes.append(node)
    validate_nodes(nodes)
    return nodes


def _normalise_settings(name: str, entry: Mapping[str, Any], vendor: str) -> dict[str, Any]:
    """The per-node settings, checked against what the research allows."""
    if name in ANCHOR_NODES:
        return _anchor_settings(entry)
    if name in EVENT_NODE.values():
        return _event_settings(entry, vendor)
    if name == "related_object":
        return _related_settings(entry, vendor)
    if name in FIELD_NODE.values():
        return _field_settings(entry)
    if name == "add_to_campaign":
        return _campaign_settings(entry)
    if name == "update_ownership":
        return _ownership_settings(entry)
    return {}


def _anchor_settings(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The create node: an update branch, a create branch, and a field map.

    [sourced] the two branches: "Update matched Contact or Lead / Only update
    matched Lead" and "Create Contact or Lead / Create Lead / Always create Lead".
    """
    update = str(entry.get("update") or "").strip()
    if update not in UPDATE_BRANCHES:
        raise InvalidConfig(
            f"the create node's update branch is {update!r}; this build honours "
            f"{list(UPDATE_BRANCHES)}"
        )
    create = str(entry.get("create") or CREATE_NONE).strip()
    if create not in CREATE_STATES:
        raise InvalidConfig(
            f"the create node's create branch is {create!r}; this build honours "
            f"{list(CREATE_STATES)}"
        )
    record_type = str(entry.get("record_type") or "").strip()
    if create in CREATE_BRANCH_NEEDS_RECORD_TYPE and not record_type:
        raise InvalidConfig(
            "[sourced] 'Create Contact or Lead' is a branch, not a type: set "
            "record_type to 'contact' or 'lead' so the flow says which one it makes"
        )
    if record_type and record_type not in RECORD_TYPES:
        raise InvalidConfig(
            f"record_type is {record_type!r}; this build makes {list(RECORD_TYPES)}"
        )
    if record_type and create in (CREATE_LEAD, CREATE_ALWAYS_LEAD) and record_type != RECORD_LEAD:
        raise InvalidConfig(
            f'[sourced] "{CREATE_BRANCH_LABEL[create]}" creates a Lead, so '
            f"record_type={record_type!r} contradicts the branch"
        )
    match_on = str(entry.get("match_on") or MATCH_KEY_EMAIL).strip()
    if match_on not in MATCH_KEYS:
        raise InvalidConfig(
            f"match_on is {match_on!r}; [sourced] the research names one match key, "
            f"'matched by email', so this build honours {list(MATCH_KEYS)}"
        )
    fields = _field_map(entry)
    return {
        "update": update,
        "create": create,
        "record_type": record_type,
        "match_on": match_on,
        "fields": fields,
        "l2a": bool(entry.get("l2a", False)),
    }


def _field_map(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The Data Fields -> CRM fields map. [sourced] extensibility.

    "Data Fields can be mapped to custom CRM fields", and the update example is
    ``Contact.Status = "Sales Qualified"``. So each entry names a CRM property
    and a value, and a value may be a literal or a reference to a booking Data
    Field. The property name is arbitrary JSON, so a team adding a field ships a
    payload.
    """
    raw = entry.get("fields") or []
    if isinstance(raw, Mapping):
        raw = [{"field": key, "value": value} for key, value in raw.items()]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise InvalidConfig("the create node's fields must be a list or an object")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise InvalidConfig("each entry in the create node's fields must be an object")
        field = str(item.get("field") or item.get("property") or "").strip()
        if not field:
            raise InvalidConfig("a field map entry names no CRM field")
        entry_out: dict[str, Any] = {"field": field}
        if item.get("from_data_field"):
            entry_out["from_data_field"] = str(item["from_data_field"])
        else:
            if "value" not in item:
                raise InvalidConfig(
                    f"field map entry for {field!r} has neither a value nor a "
                    "from_data_field"
                )
            entry_out["value"] = item["value"]
        if item.get("object"):
            entry_out["object"] = str(item["object"])
        out.append(entry_out)
    return out


def _related_settings(entry: Mapping[str, Any], vendor: str) -> dict[str, Any]:
    """The Related Object node. Which of the vendor's objects, and nothing else.

    [sourced] "plus optional **Related Object** (Account, Case, Opportunity,
    Campaign / Deal, Ticket)". Which objects a vendor offers is one of the few
    things the research states exactly, so an object outside the list is refused
    rather than looked up.
    """
    related = str(entry.get("object") or entry.get("related_object") or "").strip()
    if not related:
        raise InvalidConfig(
            "[sourced] a Related Object node names one of "
            f"{list(RELATED_TYPES[vendor])}; this one names none"
        )
    if related not in RELATED_TYPES[vendor]:
        other = HUBSPOT if vendor == SALESFORCE else SALESFORCE
        raise InvalidConfig(
            f"Related Object is {related!r}; [sourced] the {vendor} palette offers "
            f"{list(RELATED_TYPES[vendor])} and the {other} one offers "
            f"{list(RELATED_TYPES[other])}"
        )
    settings: dict[str, Any] = {"object": related, "record_type": RELATED_RECORD_TYPE[related]}
    campaign = str(entry.get("campaign") or "").strip()
    if related == "Campaign":
        # A Campaign is the one related object the research does not give a
        # selection rule for, because a Campaign has no date to be near and no
        # Open status. So the node names one, and naming none is refused rather
        # than resolved to "the newest" behind the admin's back.
        if not campaign:
            raise InvalidConfig(
                "[sourced] a Related Object of Campaign has no selection rule - "
                "'For Cases, we will relate with the most recently created Open "
                "one, and for Opportunities, we will relate with the one that has "
                "the nearest Close Date' says nothing about Campaigns - so name the "
                "campaign explicitly"
            )
        settings["campaign"] = campaign
    return settings


def _event_settings(entry: Mapping[str, Any], vendor: str) -> dict[str, Any]:
    """The Event/Engagement node, including child Events and the delete mode."""
    from dsr.booking_crm.vocabulary import DELETE_EVENT_MODES

    delete_event = str(entry.get("delete_event") or "never").strip()
    if delete_event not in DELETE_EVENT_MODES:
        raise InvalidConfig(
            f"delete_event is {delete_event!r}; this build honours {list(DELETE_EVENT_MODES)}"
        )
    settings: dict[str, Any] = {
        "child_events": bool(entry.get("child_events", False)),
        "delete_event": delete_event,
        "subject": str(entry.get("subject") or "").strip(),
        "start": entry.get("start"),
        "end": entry.get("end"),
    }
    assigned_to = str(entry.get("activity_assigned_to") or "").strip()
    if assigned_to:
        from dsr.booking_crm.vocabulary import ACTIVITY_ASSIGNED_TO

        if assigned_to not in ACTIVITY_ASSIGNED_TO:
            raise InvalidConfig(
                f"activity_assigned_to is {assigned_to!r}; [sourced] HubSpot's "
                f"'Activity Assigned to' is {list(ACTIVITY_ASSIGNED_TO)}"
            )
        if vendor != HUBSPOT:
            raise InvalidConfig(
                "[sourced] 'Activity Assigned to' is documented on Chili Piper's "
                f"HubSpot nodes only; a {vendor} flow does not take one"
            )
        settings["activity_assigned_to"] = assigned_to
    return settings


def _field_settings(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The Update Field / Update Property node. Same shape as the create map."""
    fields = _field_map(entry)
    if not fields:
        raise InvalidConfig(
            "an update field node with no fields is a node that writes nothing; "
            "give it at least one CRM field"
        )
    return {"fields": fields}


def _campaign_settings(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The Add to Campaign node. [sourced] the status is fixed at ``Booked``."""
    from dsr.booking_crm.vocabulary import CAMPAIGN_MEMBER_STATUS

    status = str(entry.get("status") or CAMPAIGN_MEMBER_STATUS).strip()
    if status != CAMPAIGN_MEMBER_STATUS:
        raise InvalidConfig(
            f"status is {status!r}; [sourced] the research fixes it - "
            f"'CampaignMember created/updated with status {CAMPAIGN_MEMBER_STATUS}'"
        )
    settings: dict[str, Any] = {"status": status}
    campaign = str(entry.get("campaign") or "").strip()
    if campaign:
        settings["campaign"] = campaign
    return settings


def _ownership_settings(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The Update Ownership node, with Cal's two researched fallbacks."""
    from dsr.booking_crm.vocabulary import (
        OWNER_FALLBACK_ATTRIBUTE_RULES,
        OWNER_FALLBACK_MODES,
        OWNER_FALLBACK_RELATIONSHIP,
        OWNER_IDENTITIES,
    )

    assign_to = str(entry.get("assign_to") or "assignee").strip()
    if assign_to not in OWNER_IDENTITIES:
        raise InvalidConfig(
            f"assign_to is {assign_to!r}; this build assigns to {list(OWNER_IDENTITIES)}"
        )
    fallback = str(entry.get("fallback_mode") or OWNER_FALLBACK_RELATIONSHIP).strip()
    if fallback not in OWNER_FALLBACK_MODES:
        raise InvalidConfig(
            f"fallback_mode is {fallback!r}; [sourced] Cal's crmRecordOwnerFallbackMode "
            f"is {list(OWNER_FALLBACK_MODES)}"
        )
    settings: dict[str, Any] = {
        "assign_to": assign_to,
        "fallback_mode": fallback,
        "skip_contact_owner": bool(entry.get("skip_contact_owner", False)),
        "crm_app_slug": str(entry.get("crm_app_slug") or "").strip(),
        "crm_owner_record_type": str(entry.get("crm_owner_record_type") or "").strip(),
    }
    if fallback == OWNER_FALLBACK_ATTRIBUTE_RULES:
        rules = _attribute_rules(entry.get("attribute_rules"))
        if not rules:
            raise InvalidConfig(
                "[sourced] crmRecordOwnerFallbackMode 'attributeRules' needs at least "
                "one rule: {field, equals, owner}"
            )
        settings["attribute_rules"] = rules
    elif entry.get("attribute_rules"):
        raise InvalidConfig(
            "attribute_rules only applies to fallback_mode 'attributeRules'"
        )
    return settings


def _attribute_rules(raw: Any) -> list[dict[str, Any]]:
    """``attributeRules``: an ordered list of ``{field, equals, owner}``."""
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise InvalidConfig("attribute_rules must be a list of rule objects")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise InvalidConfig(f"attribute_rules[{index}] is not an object")
        field = str(item.get("field") or "").strip()
        owner = str(item.get("owner") or "").strip()
        if not field or not owner:
            raise InvalidConfig(
                f"attribute_rules[{index}] needs a field and an owner"
            )
        out.append({"field": field, "owner": owner, "equals": item.get("equals")})
    return out


# --------------------------------------------------------------------------- #
# The ordering rule
# --------------------------------------------------------------------------- #


def validate_nodes(nodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check a node list against the researched ordering rule.

    Two refusals, both naming the node and quoting the sentence:

    1. a dependent node declared before the anchor;
    2. a node that may appear only once, declared twice.

    Returns the resolved order so the caller stores it rather than recomputing
    it. A flow with no dependent nodes is legal - the anchor alone is a flow -
    and returns an order of one.
    """
    names = [str(node.get("node") or "") for node in nodes]

    anchors = [index for index, name in enumerate(names) if name in ANCHOR_NODES]
    dependents = [
        (index, name) for index, name in enumerate(names) if name in DEPENDENT_NODES
    ]

    if len(anchors) > 1:
        raise InvalidConfig(
            "[sourced] the flow builder carries one 'Create or Update Record' node "
            f"per path; this one declares {len(anchors)} at positions {anchors}"
        )

    if dependents and not anchors:
        offender, name = dependents[0]
        raise NodeOrderError(
            f"node {name!r} at position {offender} has no 'Create or Update Record' "
            f"node to follow, but [sourced] \"{ORDERING_QUOTE}\""
        )

    if anchors and dependents:
        anchor = anchors[0]
        for index, name in dependents:
            if index < anchor:
                raise NodeOrderError(
                    NODE_ORDER_MESSAGE.format(
                        node=name, index=index, anchor=anchor, quote=ORDERING_QUOTE
                    )
                )

    seen: dict[str, int] = {}
    for index, name in enumerate(names):
        if name in SINGLETON_NODES:
            if name in seen:
                raise InvalidConfig(
                    f"node {name!r} is declared at positions {seen[name]} and {index}; "
                    "the router palette carries one of each"
                )
            seen[name] = index

    return {
        "order": names,
        "anchor_position": anchors[0] if anchors else None,
        "dependents": [name for _, name in dependents],
    }


def plan_order(nodes: Sequence[Mapping[str, Any]]) -> list[str]:
    """The node names in the order they will run.

    The declared order, because the declared order is already validated and the
    research describes a *sequence* of nodes, not a set. Exposed separately so a
    caller cannot accidentally get the node list in some other order.
    """
    return [str(node.get("node") or "") for node in nodes]


# --------------------------------------------------------------------------- #
# The Sync Meeting Type to the CRM toggle
# --------------------------------------------------------------------------- #


def sync_enabled(meeting_type: Mapping[str, Any] | None) -> bool:
    """Whether this meeting type syncs to the CRM at all.

    [sourced] "your links will follow this pre-defined behavior, as these
    settings are applied to all users in your org". So it is one fact about the
    meeting type, read from the meeting type, and a run has no say in it. A run
    payload carrying its own ``sync_to_crm`` is refused by the engine rather than
    honoured, because honouring it would make the setting per-user.
    """
    if not meeting_type:
        return False
    return bool(meeting_type.get("sync_to_crm", False))


def sync_toggle_message(meeting_type_name: str) -> str:
    """The message a run stopped by the toggle carries.

    Quotes the org-wide sentence, so the rep reading it learns *why* their link
    cannot override it rather than only that it did not.
    """
    return (
        f"[sourced] Sync Meeting Type to the CRM is off for {meeting_type_name!r}, and "
        f'"{SYNC_TOGGLE_ORG_WIDE_QUOTE}" - so a run cannot carry its own override. '
        "Switch the toggle on the meeting type."
    )


# --------------------------------------------------------------------------- #
# Related objects
# --------------------------------------------------------------------------- #


def related_requires_contact_message(record_type: str | None) -> str:
    """The message a related object that needed a Contact does not get.

    The research gates the additional relation on a Contact, and the sentence is
    short enough to quote in full - which matters, because the natural reading of
    the paragraph is that the relation is simply optional.
    """
    found = f"a {record_type}" if record_type else "a Lead"
    return (
        f"[sourced] \"{RELATED_REQUIRES_CONTACT_QUOTE}\" - the flow matched {found}, "
        f"not a Contact, so the related {record_type or 'object'} was not resolved. "
        "The Event is still related to the record the create node produced, which is "
        "the researched default."
    )


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

#: What each router path means to this workflow. The research names the three and
#: says writes fire on all of them "automatically"; the one-line meaning of each
#: is this build's reading, and it is served so a deployment can disagree.
PATH_MEANING: dict[str, str] = {
    PATH_SCHEDULED: "A meeting was booked. The research's default path.",
    PATH_NOT_SCHEDULED: (
        "The request was qualified but no meeting was booked. The write still "
        "fires: [sourced] 'writes fire on the scheduled, not-scheduled and "
        "disqualified paths automatically'."
    ),
    PATH_DISQUALIFIED: (
        "The request did not qualify. The write still fires, which is why a "
        "disqualified path can carry a flow at all."
    ),
}


def normalise_path(value: Any) -> str:
    """The router path a run took, refusing anything the research does not name."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        raise InvalidConfig("a run must say which router path the booking took")
    if text in ("notscheduled", "notbooked"):
        text = PATH_NOT_SCHEDULED
    if text == "disqualify":
        text = PATH_DISQUALIFIED
    if text not in PATHS:
        raise InvalidConfig(
            f"path is {value!r}; [sourced] the router has three paths - {list(PATHS)}"
        )
    return text


def describe_plan(nodes: Sequence[Mapping[str, Any]], *, vendor: str) -> dict[str, Any]:
    """The stored plan: what will run, in what order, against which vendor.

    Kept on the flow record rather than recomputed on every read, for the same
    reason ``atomic_bundle`` does it: a plan that can be read without re-checking
    it is a plan a client can render without re-implementing the check.
    """
    order = plan_order(nodes)
    by_name = {str(node.get("node")): node for node in nodes}
    anchor_name = ANCHOR_NODE[vendor]
    anchor = by_name.get(anchor_name) or next(
        (node for name, node in by_name.items() if name in ANCHOR_NODES), None
    )
    related = by_name.get("related_object") or {}
    return {
        "vendor": vendor,
        "order": order,
        "has_anchor": bool(anchor),
        "event_node": EVENT_NODE[vendor] if EVENT_NODE[vendor] in order else "",
        "field_node": FIELD_NODE[vendor] if FIELD_NODE[vendor] in order else "",
        "has_campaign": "add_to_campaign" in order,
        "has_ownership": "update_ownership" in order,
        "related_object": str(related.get("object") or ""),
        "related_rule": SELECTION_RULES.get(str(related.get("object") or ""), ""),
        "create_child_event": any(bool(node.get("child_events")) for node in nodes),
        "delete_event": next(
            (str(node.get("delete_event")) for node in nodes if node.get("delete_event")),
            "never",
        ),
        "anchor": dict(anchor) if anchor else None,
        "ordering_quote": ORDERING_QUOTE,
    }


__all__ = [
    "ANCHOR_NODE",
    "CREATE_BRANCH_NEEDS_RECORD_TYPE",
    "CREATE_MEANING",
    "DECLARED_SETTING_KEYS",
    "EVENT_NODE",
    "EVENT_RECORD_TYPE",
    "FIELD_NODE",
    "MATCH_ORDER",
    "NODE_ORDER_MESSAGE",
    "PATH_MEANING",
    "RELATED_RECORD_TYPE",
    "RELATED_TYPES",
    "SINGLETON_NODES",
    "SKIP_MEETING_TYPE_SYNC_OFF",
    "SKIP_NO_RECORD",
    "SKIP_RETRY_NOT_FAILED",
    "describe_plan",
    "normalise_nodes",
    "normalise_path",
    "plan_order",
    "related_requires_contact_message",
    "sync_enabled",
    "sync_toggle_message",
    "validate_nodes",
]
