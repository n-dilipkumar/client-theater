"""The researched vocabulary for WF-055, published as data.

Every term the workflow uses is here with the sentence it comes from, and every
term this build had to choose for itself says so instead of borrowing a quote it
does not have. :func:`published_vocabulary` is what ``GET /api/wf-055/vocabulary``
serves, and a client renders its pickers from it rather than from a list compiled
into the page, so a deployment that widens a vocabulary ships a record here rather
than a change to shared code.

The distinction the brief insists on, sourced behaviour against assumed behaviour,
is carried on the entries themselves. An entry either carries ``sourced_from`` (the
quoted sentence) or ``inference`` (the id of the registry entry in
:mod:`dsr.handoff_scheduler.inferences` that owns the choice). A test asserts every
entry is one or the other and never neither, which is what stops an assumption from
quietly acquiring a quotation.

Every string in this file is ASCII. The seeder prints the feature's return string on
a Windows console whose codec is cp1252, and a single RIGHTWARDS ARROW in one
recovered feature broke the entire seeder.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The three sources
# --------------------------------------------------------------------------- #

SOURCES: dict[str, str] = {
    "chilipiper_programmatic": (
        "https://help.chilipiper.com/hc/en-us/articles/"
        "50860790980627-How-do-I-Schedule-Chili-Piper-Meetings-Programmatically"
    ),
    "chilipiper_reassigning": (
        "https://help.chilipiper.com/hc/en-us/articles/42613941250835-Reassigning-Meetings"
    ),
    "chilipiper_concierge_flow": (
        "https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow"
    ),
}

# --------------------------------------------------------------------------- #
# The link type this workflow routes, and the two that sit beside it
# --------------------------------------------------------------------------- #

#: The scheduling link type the research names for a rep handing a lead to another
#: team member. Spelled as the research spells it, because the researched evidence
#: quotes it in a table of link types.
HANDOFF = "Handoff"

LINK_TYPES: tuple[dict[str, Any], ...] = (
    {
        "link_type": HANDOFF,
        "routed_by_this_workflow": True,
        "sourced_from": (
            "Handoff | When to use: a rep is handing off a lead to another team member - for "
            "example, an SDR booking a discovery call with an AE."
        ),
        "source": "chilipiper_concierge_flow",
    },
    {
        "link_type": "RoundRobin",
        "routed_by_this_workflow": False,
        "sourced_from": "WF-054 owns it; it rotates across a team rather than naming one AE",
        "source": "chilipiper_programmatic",
    },
    {
        "link_type": "Ownership",
        "routed_by_this_workflow": False,
        "sourced_from": "WF-053 owns it; it routes by the CRM record's owner, not by a declared path",
        "source": "chilipiper_programmatic",
    },
)

LINK_TYPE_NAMES: tuple[str, ...] = tuple(str(entry["link_type"]) for entry in LINK_TYPES)

# --------------------------------------------------------------------------- #
# The two request shapes the init call accepts
# --------------------------------------------------------------------------- #

GUEST_EMAIL_REQUEST = "GuestEmailRequest"
CRM_REQUEST = "CrmRequest"

REQUEST_TYPES: tuple[dict[str, Any], ...] = (
    {
        "type": GUEST_EMAIL_REQUEST,
        "carries": "guestEmail",
        "field_spelling": "guestEmail",
        "means": "the SDR typed the lead's email address into the Handoff scheduler",
        "sourced_from": (
            "POST .../handoff/workspace/{workspaceId}/booker/{userId}/init-simple with either "
            '{"type":"GuestEmailRequest", guestEmail, interval}'
        ),
        "source": "chilipiper_programmatic",
    },
    {
        "type": CRM_REQUEST,
        "carries": "id",
        "field_spelling": "id",
        "stored_as": "crm_record_id",
        "means": "the SDR named a CRM Lead or Contact record instead of typing an email",
        "sourced_from": (
            'or {"type":"CrmRequest", id, interval}. data_sources: "CRM Lead/Contact record for '
            'the CrmRequest form"'
        ),
        "source": "chilipiper_programmatic",
    },
)

REQUEST_TYPE_NAMES: tuple[str, ...] = tuple(str(entry["type"]) for entry in REQUEST_TYPES)

# --------------------------------------------------------------------------- #
# The two roles on the meeting
# --------------------------------------------------------------------------- #

BOOKER = "booker"
ASSIGNEE = "assignee"

MEETING_ROLES: tuple[dict[str, Any], ...] = (
    {
        "role": BOOKER,
        "who": "the SDR who opened the Handoff scheduler",
        "chosen_by": "the caller of the researched init call",
        "sourced_from": "SDR picks a path and a slot; the meeting is booked with the AE and the SDR is the booker",
        "source": "chilipiper_concierge_flow",
    },
    {
        "role": ASSIGNEE,
        "who": "the AE named by the routing path",
        "chosen_by": "the routing path that matched",
        "sourced_from": "meeting created with SDR as Booker and AE as Assignee",
        "source": "chilipiper_programmatic",
    },
)

MEETING_ROLE_NAMES: tuple[str, ...] = tuple(str(entry["role"]) for entry in MEETING_ROLES)

# --------------------------------------------------------------------------- #
# The two field spellings the researched API uses and this build stores
# --------------------------------------------------------------------------- #

API_FIELD_NAMES: tuple[dict[str, Any], ...] = (
    {
        "api": "routingId",
        "stored_as": "the routing record's own id",
        "note": "the researched schedule call carries routingId in its path",
        "sourced_from": "pass the corresponding routingId, routerId, pathId, and startTime to the schedule call",
        "source": "chilipiper_programmatic",
    },
    {
        "api": "routerId",
        "stored_as": "router_ref",
        "note": "a foreign key to the router record, so a later reassignment reopens the same router",
        "sourced_from": "pass the corresponding routingId, routerId, pathId, and startTime to the schedule call",
        "source": "chilipiper_programmatic",
    },
    {
        "api": "pathId",
        "stored_as": "path_id",
        "note": "declared on the router, not minted per request",
        "sourced_from": "returns one or more routing paths, each with its own pathId and startTimes",
        "source": "chilipiper_programmatic",
    },
    {
        "api": "startTimes",
        "stored_as": "start_times",
        "note": "per routing path, never merged across paths",
        "sourced_from": "returns one or more routing paths, each with its own pathId and startTimes",
        "source": "chilipiper_programmatic",
    },
    {
        "api": "startTime",
        "stored_as": "start_at",
        "note": "the schedule call takes one startTime that must have been in that path's startTimes",
        "sourced_from": "POST .../path/{pathId}/booker/{userId}/schedule-simple with {startTime}",
        "source": "chilipiper_programmatic",
    },
    {
        "api": "crmExplicits",
        "stored_as": "crm_explicits",
        "note": "kept verbatim on the routing, so the context a rule was measured against is reproducible",
        "sourced_from": "crmExplicits - additional CRM context to pass through to routing rules",
        "source": "chilipiper_programmatic",
    },
)

# --------------------------------------------------------------------------- #
# Per-path availability, and the derivation behind it
# --------------------------------------------------------------------------- #

PATH_AVAILABILITY: dict[str, Any] = {
    "operation": "intersection",
    "over": "the path's assignee and every invitee whose Required toggle is on",
    "offered_when": "every one of those people is free for the whole slot",
    "not_required_invitee": "invited to the meeting, and their calendar is not read at all",
    "recheck_at_booking": True,
    "per_path": True,
    "inference": "inference_path_availability_is_intersection",
    "sourced_from": (
        "returns time slots per routing path. The init response returns one or more routing paths, "
        "each with its own pathId and startTimes."
    ),
    "source": "chilipiper_concierge_flow",
}

# --------------------------------------------------------------------------- #
# The Required toggle
# --------------------------------------------------------------------------- #

REQUIRED_TOGGLE: dict[str, Any] = {
    "field": "required",
    "on": "the invitee's availability is considered, so it narrows the path's startTimes",
    "off": "the invitee is still added to the meeting, and their availability is not considered",
    "default": False,
    "sourced_from": (
        "Chili Piper will not consider their availability while displaying the calendar unless you "
        "toggle the Required button."
    ),
    "source": "chilipiper_concierge_flow",
}

# --------------------------------------------------------------------------- #
# CRM context
# --------------------------------------------------------------------------- #

CRM_EXPLICITS: dict[str, Any] = {
    "field": "crmExplicits",
    "shape": "an object of arbitrary keys",
    "permitted_fields": "all of them. The research does not decide which, and a fixed list would defeat the feature",
    "read_by": "the match block of each declared routing path",
    "cannot_shadow": ["request_type", "guest_email", "crm_record_id"],
    "shadowed_keys_are": "dropped from the rule context and reported in the response",
    "kept_verbatim_on": "the routing record",
    "sourced_from": (
        "crmExplicits lets an integrator pass arbitrary CRM context into routing rules; "
        "crmExplicits - additional CRM context to pass through to routing rules"
    ),
    "source": "chilipiper_programmatic",
    "inference": "inference_crm_explicits_cannot_shadow_the_researched_fields",
}

# --------------------------------------------------------------------------- #
# Routing states, meeting states, and the two outcomes an evaluation reports
# --------------------------------------------------------------------------- #

OPEN = "open"
BOOKED = "booked"

ROUTING_STATES: tuple[dict[str, Any], ...] = (
    {
        "state": OPEN,
        "live": True,
        "note": "paths are on offer and no meeting has been booked against them",
        "sourced_from": "SDR picks a path and a slot",
        "source": "chilipiper_concierge_flow",
    },
    {
        "state": BOOKED,
        "live": False,
        "note": "a meeting was booked on one of the paths; the routing cannot be booked again",
        "sourced_from": "the meeting is booked with the AE and the SDR is the booker",
        "source": "chilipiper_concierge_flow",
    },
)

LIVE_ROUTING_STATES: frozenset[str] = frozenset(
    str(entry["state"]) for entry in ROUTING_STATES if entry["live"]
)

CONFIRMED = "confirmed"
CANCELLED = "cancelled"

MEETING_STATES: tuple[dict[str, Any], ...] = (
    {
        "state": CONFIRMED,
        "live": True,
        "note": "the meeting is on the assignee's calendar with the SDR as the booker",
        "sourced_from": "meeting created with SDR as Booker and AE as Assignee",
        "source": "chilipiper_programmatic",
    },
    {
        "state": CANCELLED,
        "live": False,
        "note": "released. The routing that produced it stays booked, because its slot was taken",
        "sourced_from": "reassignment later respects your Handoff/ChiliCal User controls",
        "source": "chilipiper_reassigning",
    },
)

LIVE_MEETING_STATES: frozenset[str] = frozenset(
    str(entry["state"]) for entry in MEETING_STATES if entry["live"]
)

PATHS_OFFERED = "paths_offered"
NO_AVAILABILITY = "no_availability"

EVALUATION_OUTCOMES: tuple[dict[str, Any], ...] = (
    {
        "outcome": PATHS_OFFERED,
        "means": "at least one matched path has a free slot the SDR can book",
        "sourced_from": "returns one or more routing paths, each with its own pathId and startTimes",
        "source": "chilipiper_concierge_flow",
    },
    {
        "outcome": NO_AVAILABILITY,
        "means": "paths matched, and every one of them has no free slot in the interval",
        "sourced_from": "returns time slots per routing path",
        "source": "chilipiper_concierge_flow",
    },
)

EVALUATION_OUTCOME_NAMES: tuple[str, ...] = tuple(
    str(entry["outcome"]) for entry in EVALUATION_OUTCOMES
)

# --------------------------------------------------------------------------- #
# What this plugin hands to a later reassignment, and what it does not own
# --------------------------------------------------------------------------- #

REASSIGNMENT_HANDOFF: dict[str, Any] = {
    "carries_on_the_meeting": [
        "workspace_ref",
        "router_ref",
        "path_id",
        "booker_ref",
        "assignee_ref",
    ],
    "rule": (
        "A booked meeting names the workspace, the router and the path it came from, so a later "
        "reassignment reopens that same routing context instead of choosing a new router."
    ),
    "owned_by_this_plugin": False,
    "owned_instead_by": "WF-063 reassigns a booked meeting",
    "sourced_from": (
        "reassignment later respects your Handoff/ChiliCal User controls and the Distribution "
        "settings of the meeting booked"
    ),
    "source": "chilipiper_reassigning",
    "inference": "inference_this_plugin_does_not_own_reassignment",
}

POD_PARTITION: dict[str, Any] = {
    "one_workspace_is": "one SDR/AE pod",
    "rule": (
        "A workspace holds one pod's users and the routers declared for it, and the researched init "
        "call is scoped to workspace/{workspaceId}, so a third party running one router per pod runs "
        "one workspace per pod."
    ),
    "sourced_from": "workspaces partition SDR/AE pods so a third party can run one router per pod",
    "source": "chilipiper_programmatic",
    "inference": "inference_one_workspace_is_one_pod",
}

# --------------------------------------------------------------------------- #
# The researched APIs
# --------------------------------------------------------------------------- #

EDGE_API_CALLS: tuple[dict[str, Any], ...] = (
    {
        "call": "init-simple",
        "method": "POST",
        "path": "/api/fire-edge/v1/org/handoff/workspace/{workspaceId}/booker/{userId}/init-simple",
        "payload": (
            '{"type":"GuestEmailRequest", guestEmail, interval} or {"type":"CrmRequest", id, '
            "interval}, plus optional routerId and crmExplicits"
        ),
        "answers": ["routingId", "routers[].pathResults[].startTimes"],
        "sourced_from": (
            "POST /api/fire-edge/v1/org/handoff/workspace/{workspaceId}/booker/{userId}/init-simple"
        ),
        "source": "chilipiper_programmatic",
    },
    {
        "call": "schedule-simple",
        "method": "POST",
        "path": (
            "/api/fire-edge/v1/org/handoff/routing/{routingId}/router/{routerId}/path/{pathId}"
            "/booker/{userId}/schedule-simple"
        ),
        "payload": '{"startTime":"2026-01-05T14:30:00Z"}',
        "answers": ["the meeting, with the SDR as Booker and the AE as Assignee"],
        "sourced_from": (
            "POST /api/fire-edge/v1/org/handoff/routing/{routingId}/router/{routerId}/path/{pathId}"
            "/booker/{userId}/schedule-simple with {startTime}"
        ),
        "source": "chilipiper_programmatic",
    },
    {
        "call": "workspace-list",
        "method": "discovery",
        "path": "/api/fire-edge/v1/org/handoff/workspace",
        "payload": {},
        "answers": ["the workspaces in this org"],
        "sourced_from": "Lookup ops: workspace-list, user-find.",
        "source": "chilipiper_programmatic",
    },
    {
        "call": "user-find",
        "method": "discovery",
        "path": "/api/fire-edge/v1/org/handoff/user",
        "payload": {},
        "answers": ["the user records a router's booker and assignee are named from"],
        "sourced_from": "Lookup ops: workspace-list, user-find.",
        "source": "chilipiper_programmatic",
    },
)

# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _require(value: Any, allowed: tuple[str, ...], label: str) -> str:
    """Reject a value outside a published vocabulary.

    Raises :class:`~dsr.handoff_scheduler.errors.HandoffError` rather than a bare
    ``ValueError``, and that is load-bearing rather than cosmetic: the feature
    module registers one handler for the domain hierarchy and nothing else, so a
    plain ``ValueError`` raised here would escape it and answer 500 for what every
    other out-of-vocabulary value in this package answers 400 for.
    """
    from dsr.handoff_scheduler.errors import HandoffError

    text = str(value or "").strip()
    if text not in allowed:
        raise HandoffError(f"{label} must be one of {', '.join(allowed)}; got {value!r}")
    return text


def require_request_type(value: Any) -> str:
    return _require(value, REQUEST_TYPE_NAMES, "type")


def require_role(value: Any) -> str:
    return _require(value, MEETING_ROLE_NAMES, "role")


def require_routing_state(value: Any) -> str:
    return _require(value, tuple(str(e["state"]) for e in ROUTING_STATES), "state")


def require_meeting_state(value: Any) -> str:
    return _require(value, tuple(str(e["state"]) for e in MEETING_STATES), "state")


def require_outcome(value: Any) -> str:
    return _require(value, EVALUATION_OUTCOME_NAMES, "outcome")


def published_vocabulary() -> dict[str, Any]:
    """Every researched term, served so a client renders its pickers from here."""
    return {
        "sources": dict(SOURCES),
        "link_types": [dict(entry) for entry in LINK_TYPES],
        "link_type_names": list(LINK_TYPE_NAMES),
        "request_types": [dict(entry) for entry in REQUEST_TYPES],
        "request_type_names": list(REQUEST_TYPE_NAMES),
        "meeting_roles": [dict(entry) for entry in MEETING_ROLES],
        "meeting_role_names": list(MEETING_ROLE_NAMES),
        "api_field_names": [dict(entry) for entry in API_FIELD_NAMES],
        "path_availability": dict(PATH_AVAILABILITY),
        "required_toggle": dict(REQUIRED_TOGGLE),
        "crm_explicits": dict(CRM_EXPLICITS),
        "routing_states": [dict(entry) for entry in ROUTING_STATES],
        "live_routing_states": sorted(LIVE_ROUTING_STATES),
        "meeting_states": [dict(entry) for entry in MEETING_STATES],
        "live_meeting_states": sorted(LIVE_MEETING_STATES),
        "evaluation_outcomes": [dict(entry) for entry in EVALUATION_OUTCOMES],
        "evaluation_outcome_names": list(EVALUATION_OUTCOME_NAMES),
        "reassignment_handoff": dict(REASSIGNMENT_HANDOFF),
        "pod_partition": dict(POD_PARTITION),
        "edge_api_calls": [dict(entry) for entry in EDGE_API_CALLS],
    }
