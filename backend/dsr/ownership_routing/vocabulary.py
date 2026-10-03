"""The vocabulary the WF-053 research fixes by name, served as data.

Served at ``GET /api/wf-053/vocabulary`` so a client renders its pickers from the
same source the validator enforces against. A value added in one place reaches
every client at once, and a reviewer can read what this workflow believes is true
without reading a function body.

Each constant carries the sentence from
``docs/research/digital-sales-room-workflows/wf/WF-053.md`` that fixes it. The
quote is not decoration: it is what makes the constant arguable, and the inference
register in :mod:`dsr.ownership_routing.inferences` names the values that *no*
sentence fixes.
"""

from __future__ import annotations

from typing import Any

#: The one link type this workflow routes. Quoted: "**Ownership** - routes to the
#: owner of the guest's CRM record (lead, contact, or account owner), resolved at
#: booking time."
OWNERSHIP = "Ownership"

#: All five link types the research names, with the fourth-and-fifth split being
#: this workflow's own boundary rather than a claim about Chili Piper.
#:
#: "Scheduling Links (types: Personal / Admin (one-on-one) / Round Robin / Group /
#: Ownership)". The last three - Round Robin, Group, Ownership - are the ones whose
#: recipient is decided by a rule rather than named on the link, and of those only
#: Ownership is decided by *the CRM owner*. The other two are other workflows'
#: subject; WF-054 is the round-robin one. Listing all five keeps the picker honest
#: about what exists, and the supported set keeps the router honest about what it does.
LINK_TYPES: tuple[dict[str, str], ...] = (
    {
        "type": "Personal",
        "label": "Personal",
        "routes_by": "a named user",
        "supported": False,
        "note": "the link's owner is a person, so ownership routing is not consulted",
    },
    {
        "type": "Admin",
        "label": "Admin (one-on-one)",
        "routes_by": "a named admin",
        "supported": False,
        "note": "one-on-one with a named admin",
    },
    {
        "type": "RoundRobin",
        "label": "Round Robin",
        "routes_by": "a Distribution's turn",
        "supported": False,
        "note": "the researched round-robin workflow, WF-054",
    },
    {
        "type": "Group",
        "label": "Group",
        "routes_by": "whoever picks the slot",
        "supported": False,
        "note": "group availability, any member",
    },
    {
        "type": OWNERSHIP,
        "label": OWNERSHIP,
        "routes_by": "the CRM record's owner",
        "supported": True,
        "note": "lead, contact, or account owner, resolved at booking time",
    },
)

#: The link types this router will serve. One, and it is not negotiable per request.
SUPPORTED_LINK_TYPES: tuple[str, ...] = (OWNERSHIP,)

#: The CRM objects whose owner an Ownership link can resolve, in the research's own
#: order. The evidence sentence lists them alphabetically - "lead, contact, or
#: account owner" - so the order here is *not* sourced precedence; it is the lookup
#: order this build chose, and :data:`RESOLUTION_ORDER_RATIONALE` plus the
#: ``resolution-order`` inference say so.
CRM_OBJECT_TYPES: tuple[str, ...] = ("lead", "contact", "account")

RESOLUTION_ORDER_RATIONALE = (
    "The evidence sentence 'lead, contact, or account owner' is alphabetical, so it fixes the "
    "set of objects and not the order they are tried in. Lead is tried first because it is the "
    "only one of the three that is about a person who has not been through conversion: a "
    "converted prospect is a Contact on an Account, and the Lead's owner is the person who was "
    "working the record before the conversion. Contact before Account because a Contact is a "
    "person with an owner, while an Account owner is the account team - a coarser answer to the "
    "same question."
)

#: The calendar providers the research names as the availability source:
#: "Google/Outlook calendar of the resolved owner".
CALENDAR_PROVIDERS: tuple[dict[str, str], ...] = (
    {"provider": "google", "label": "Google Calendar"},
    {"provider": "outlook", "label": "Outlook"},
)

#: The Edge API operation that enumerates Ownership links programmatically:
#: "Discovery: ``scheduling-link-list-ownership`` (Edge API operation id) to
#: enumerate Ownership links programmatically."
DISCOVERY_OPERATION = "scheduling-link-list-ownership"

#: The two endpoints the researched data flow calls, kept as data so the page can
#: show the shape of the call it is making and a test can assert the route names
#: the vendor's operation rather than a paraphrase of it.
EDGE_ENDPOINTS: dict[str, str] = {
    "init": "POST /api/fire-edge/v1/org/schedulingLinks/init-simple",
    "schedule": "POST /api/fire-edge/v1/org/schedulingLinks/routing/{routeId}/schedule-simple",
}

#: The payload the init call sends, as researched:
#: ``{ "link": { "type": "Ownership", "linkId": "..." }, "guestEmail": "...",
#: "interval": {...} }``.
#:
#: ``interval`` is the availability window and is *not* optional on this workflow's
#: own read: the flow says availability is read from the owner's calendar and the
#: prospect books from it, so a session opened without an interval would have no
#: slots to offer. See the ``interval-required`` inference.
INIT_REQUIRED_FIELDS: tuple[str, ...] = ("link", "guestEmail", "interval")

#: The fields inside ``link`` the researched payload names.
INIT_LINK_FIELDS: tuple[str, ...] = ("type", "linkId")

#: The payload the schedule call sends, as researched: ``{ "startTime",
#: "guestEmail" }``. Both are load-bearing: the time must be one the route offered,
#: and the guest must be the one who was routed.
SCHEDULE_REQUIRED_FIELDS: tuple[str, ...] = ("startTime", "guestEmail")

#: The two Concierge node kinds the research distinguishes for CRM ownership, from
#: "Rules are either **CRM Ownership** rules (check Lead/Contact/Account owner
#: against a **Team**) or **Without Ownership** rules (CRM values or Data Field
#: values)".
RULE_KINDS: tuple[dict[str, str], ...] = (
    {
        "kind": "crm_ownership",
        "label": "CRM Ownership",
        "quote": "checks whether a rep from Team A owns the Lead, Contact, or Account object",
        "supported": True,
    },
    {
        "kind": "without_ownership",
        "label": "Without Ownership",
        "quote": "CRM values or Data Field values",
        "supported": True,
    },
)

#: The terminal node every rule chain has to end in. "Admin adds ``Routing Rule`` /
#: ``Catch All`` nodes."
CATCH_ALL = "catch_all"

#: The node the research says not to use on an Ownership path, with its warning:
#: "Set Chili Piper to update the owner of a record in Salesforce to the rep who got
#: the meeting" / "you should not use this node in **Ownership** paths."
UPDATE_OWNERSHIP_NODE = "update_ownership"

#: The sibling node named in the same warning: "If you have any Assign To nodes in
#: Ownership-related paths, this could prevent your Router from being published."
ASSIGN_TO_NODES: tuple[str, ...] = ("assign_to", "assign_to_team", "assign_to_distribution")

#: Every node an Ownership path refuses, in one place so the endpoint that lists
#: them and the validator that enforces them cannot drift.
FORBIDDEN_ON_OWNERSHIP_PATH: tuple[str, ...] = (UPDATE_OWNERSHIP_NODE, *ASSIGN_TO_NODES)

FORBIDDEN_QUOTE = (
    "you should not use this node in **Ownership** paths. If you have any Assign To nodes in "
    "Ownership-related paths, this could prevent your Router from being published."
)

#: How the resolution can be sourced, from the extensibility note: "routes may be
#: pre-resolved from your own CRM ('a lead-owner link resolved from your CRM')
#: rather than letting Chili Piper do the lookup."
RESOLUTION_SOURCES: tuple[dict[str, str], ...] = (
    {
        "source": "crm",
        "label": "Look the owner up in the CRM",
        "quote": "routes to the owner of the guest's CRM record (lead, contact, or account owner)",
        "default": True,
    },
    {
        "source": "pre_resolved",
        "label": "The caller already knows the owner",
        "quote": "routes may be pre-resolved from your own CRM ('a lead-owner link resolved from your CRM')",
        "default": False,
    },
)

#: Where the workspace's Lead-to-Account setting comes from. The research does not
#: configure it: "For Lead-to-Account (L2A) Matching in **Salesforce**, we will use
#: the existing settings defined in your workspace." So it is read from a declared
#: setting rather than invented here, and the only default is "use what the
#: workspace says".
L2A_SETTING = "lead_to_account_matching"

#: The booking states, kept to the two the researched flow produces. A booking is
#: confirmed or cancelled; there is no pending state here because the researched
#: flow's second call *is* the confirmation - the caller chose the slot and sent it.
BOOKING_STATES: tuple[str, ...] = ("confirmed", "cancelled")

#: The outcome of one resolution attempt, which is what a routing decision records.
#: ``resolved`` is the researched path. ``catch_all`` is the researched
#: ``Catch All`` node. ``unroutable`` is a chain that has no host and no catch-all,
#: which :class:`~dsr.ownership_routing.errors.RulesDoNotFallThrough` makes
#: undeclirable rather than something a caller can observe at runtime.
RESOLUTION_OUTCOMES: tuple[str, ...] = ("resolved", "catch_all", "unroutable")


def normalise_email(value: Any) -> str:
    """Case-insensitive. Two addresses differing only in case are one address.

    The guest's address is the lookup key for the whole workflow - "so Chili Piper
    can resolve the owner from your CRM" - so the normaliser is what stops two
    spellings of one prospect resolving to two different owners.
    """
    return "" if value is None else str(value).strip().casefold()


def normalise_domain(value: Any) -> str:
    """Case-insensitive, without a leading ``www.`` or a trailing dot."""
    text = "" if value is None else str(value).strip().casefold()
    if text.startswith("www."):
        text = text[4:]
    return text.rstrip(".")


def require_link_type(value: Any) -> str:
    """Validate a link type, accepting any spelling of a published one.

    The Edge payload sends ``"type": "Ownership"`` but the same word appears in
    prose as ``ownership`` and in a URL as ``Ownership``; a lookup that only
    accepted one spelling would refuse a caller who had read the docs carefully.
    Case is therefore folded, but nothing else is: ``Owner`` is not ``Ownership``
    and is not a published type.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError("link type is required; this workflow routes Ownership links")
    folded = text.casefold()
    for entry in LINK_TYPES:
        if entry["type"].casefold() == folded:
            return entry["type"]
    known = ", ".join(entry["type"] for entry in LINK_TYPES)
    raise ValueError(f"unknown link type {text!r}; Chili Piper publishes: {known}")


def require_object_type(value: Any) -> str:
    """Validate a CRM object type against the three the research names."""
    text = "" if value is None else str(value).strip().casefold()
    if not text:
        raise ValueError("object type is required")
    if text not in CRM_OBJECT_TYPES:
        known = ", ".join(CRM_OBJECT_TYPES)
        raise ValueError(f"unknown CRM object type {text!r}; the research names: {known}")
    return text


def require_rule_kind(value: Any) -> str:
    """Validate a routing-rule kind. ``catch_all`` is a node, not a rule kind."""
    text = (
        "" if value is None else str(value).strip().casefold().replace("-", "_").replace(" ", "_")
    )
    known = {entry["kind"] for entry in RULE_KINDS}
    if text not in known:
        raise ValueError(
            f"unknown rule kind {text!r}; the research names: {', '.join(sorted(known))}"
        )
    return text


def require_resolution_source(value: Any) -> str:
    """Validate the resolution source, defaulting to the CRM lookup."""
    text = (
        "" if value is None else str(value).strip().casefold().replace("-", "_").replace(" ", "_")
    )
    if not text:
        return "crm"
    known = {entry["source"] for entry in RESOLUTION_SOURCES}
    if text not in known:
        raise ValueError(
            f"unknown resolution source {text!r}; expected one of {', '.join(sorted(known))}"
        )
    return text


def require_interval(value: Any) -> dict[str, Any]:
    """Validate the availability window, returning it normalised.

    ``start`` and ``end`` are ISO timestamps; ``duration_minutes`` is the meeting
    length; ``min_notice_minutes`` and ``max_days`` bound how far out the window
    reaches. Every one of these is this build's shape, not a field Chili Piper's
    documented payload names - the research writes only ``"interval": {...}`` - so
    they are named here and served at ``/vocabulary`` for a client to render from,
    and the ``interval-shape`` inference records that the vendor's exact field names
    were not sourced.
    """
    if not isinstance(value, dict):
        raise ValueError("interval must be an object with start, end and duration_minutes")
    payload = dict(value)

    start = payload.get("start")
    end = payload.get("end")
    if not start or not end:
        raise ValueError("interval needs both start and end")

    duration = payload.get("duration_minutes")
    if duration is None:
        raise ValueError("interval needs duration_minutes")
    try:
        duration = int(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval duration_minutes must be a whole number") from exc
    if duration <= 0:
        raise ValueError("interval duration_minutes must be greater than zero")

    notice = int(payload.get("min_notice_minutes") or 0)
    if notice < 0:
        raise ValueError("interval min_notice_minutes cannot be negative")

    max_days = payload.get("max_days")
    if max_days is not None:
        try:
            max_days = int(max_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval max_days must be a whole number of days") from exc
        if max_days <= 0:
            raise ValueError("interval max_days must be greater than zero")

    # Ordering is checked on the parsed values rather than on the strings, so
    # "2026-09-27T12:00:00Z" and "2026-09-27T12:00:00+00:00" compare correctly.

    try:
        start_at = _parse(start)
        end_at = _parse(end)
    except ValueError as exc:
        raise ValueError(f"interval timestamps must be ISO 8601: {exc}") from exc
    if end_at <= start_at:
        raise ValueError("interval must end after it starts")

    return {
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
        "duration_minutes": duration,
        "min_notice_minutes": notice,
        "max_days": max_days,
    }


def _parse(value: Any):
    """Parse an ISO 8601 timestamp into an aware datetime.

    A trailing ``Z`` is accepted because the researched payloads and most calendar
    APIs use it and ``datetime.fromisoformat`` only learned to read it in 3.11.
    """
    from datetime import datetime, timezone

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def published_vocabulary() -> dict[str, Any]:
    """Every published term, as one payload a client can render from.

    The endpoint is not decoration: a picker compiled into the frontend is a second
    copy of this vocabulary that drifts the first time somebody adds a term here.
    """
    return {
        "link_types": [dict(entry) for entry in LINK_TYPES],
        "supported_link_types": list(SUPPORTED_LINK_TYPES),
        "crm_object_types": list(CRM_OBJECT_TYPES),
        "resolution_order": list(CRM_OBJECT_TYPES),
        "resolution_order_rationale": RESOLUTION_ORDER_RATIONALE,
        "calendar_providers": [dict(entry) for entry in CALENDAR_PROVIDERS],
        "discovery_operation": DISCOVERY_OPERATION,
        "edge_endpoints": dict(EDGE_ENDPOINTS),
        "init_required_fields": list(INIT_REQUIRED_FIELDS),
        "init_link_fields": list(INIT_LINK_FIELDS),
        "schedule_required_fields": list(SCHEDULE_REQUIRED_FIELDS),
        "rule_kinds": [dict(entry) for entry in RULE_KINDS],
        "catch_all": CATCH_ALL,
        "forbidden_on_ownership_path": list(FORBIDDEN_ON_OWNERSHIP_PATH),
        "forbidden_quote": FORBIDDEN_QUOTE,
        "resolution_sources": [dict(entry) for entry in RESOLUTION_SOURCES],
        "l2a_setting": L2A_SETTING,
        "booking_states": list(BOOKING_STATES),
        "resolution_outcomes": list(RESOLUTION_OUTCOMES),
    }
