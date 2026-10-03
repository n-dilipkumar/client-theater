"""WF-053: route a booking to the owner of the CRM record.

The researched workflow, in the order the research states it: an admin creates an
**Ownership** scheduling link, a prospect lands on it (or a backend calls the
Edge API), Chili Piper resolves the owner of the guest's matching CRM record - the
lead, contact, or account owner - at booking time, availability is read from that
owner's connected calendar, the prospect books, and the owner gets the meeting.
Alternatively a **CRM Ownership** routing rule checks whether a rep from a team
owns the Lead, Contact *or* Account and routes to that rep.

Module map, in dependency order:

``vocabulary``
    The researched terms: the five link types, the three CRM objects, the Edge
    payloads, and the nodes refused on an Ownership path.
``calendars``
    Availability from the resolved owner's connected calendar, and the slot
    arithmetic the init call answers with.
``crm``
    The CRM seam - records, reps, teams, the Lead-to-Account setting - and the
    owner lookup that is the first arrow of the researched data flow.
``rules``
    The two researched rule kinds, the chain that cannot fail to fall through, and
    the forbidden-node check.
``engine``
    The flow end to end over the audited store: the two researched API calls as
    two methods, because the second one is checked against the list the first one
    returned.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name.
"""

from __future__ import annotations

from dsr.ownership_routing.calendars import (
    available_slots,
    busy_blocks,
    calendar_for,
    working_window,
)
from dsr.ownership_routing.crm import (
    RECORD_COLLECTION,
    REP_COLLECTION,
    SETTINGS_COLLECTION,
    StoreCrm,
)
from dsr.ownership_routing.engine import (
    BOOKING_COLLECTION,
    CANCELLED,
    CONFIRMED,
    DECISION_COLLECTION,
    LINK_COLLECTION,
    LINK_HISTORY_LIMIT,
    ROUTE_COLLECTION,
    OwnershipEngine,
)
from dsr.ownership_routing.errors import (
    AmbiguousOwner,
    BookingStateError,
    CalendarNotConnected,
    ForbiddenNodeOnOwnershipPath,
    GuestEmailRequired,
    GuestMismatch,
    IntervalError,
    LinkError,
    LinkTypeNotSupported,
    NoOwnerResolved,
    OwnershipError,
    OwnerUnknown,
    PrerequisiteError,
    RouteConsumed,
    RouteNotFound,
    RoutingRuleError,
    RulesDoNotFallThrough,
    SessionError,
    SlotNotOffered,
)
from dsr.ownership_routing.inferences import (
    by_id as inference_by_id,
    describe as describe_inferences,
)
from dsr.ownership_routing.rules import (
    CATCH_ALL_RULE,
    TEAM_RULE,
    VALUE_RULE,
    check_nodes,
    evaluate,
    nodes_warnings,
    normalise_rules,
    require_catch_all,
)
from dsr.ownership_routing.vocabulary import (
    BOOKING_STATES,
    CALENDAR_PROVIDERS,
    CATCH_ALL,
    CRM_OBJECT_TYPES,
    DISCOVERY_OPERATION,
    EDGE_ENDPOINTS,
    FORBIDDEN_ON_OWNERSHIP_PATH,
    FORBIDDEN_QUOTE,
    INIT_REQUIRED_FIELDS,
    L2A_SETTING,
    LINK_TYPES,
    OWNERSHIP,
    RESOLUTION_ORDER_RATIONALE,
    RESOLUTION_OUTCOMES,
    RESOLUTION_SOURCES,
    RULE_KINDS,
    SCHEDULE_REQUIRED_FIELDS,
    SUPPORTED_LINK_TYPES,
    normalise_domain,
    normalise_email,
    published_vocabulary,
    require_interval,
    require_link_type,
    require_object_type,
    require_resolution_source,
    require_rule_kind,
)

__all__ = [
    "AmbiguousOwner",
    "BOOKING_COLLECTION",
    "BOOKING_STATES",
    "BookingStateError",
    "CANCELLED",
    "CATCH_ALL",
    "CATCH_ALL_RULE",
    "CRM_OBJECT_TYPES",
    "CalendarNotConnected",
    "CONFIRMED",
    "CALENDAR_PROVIDERS",
    "DECISION_COLLECTION",
    "DISCOVERY_OPERATION",
    "EDGE_ENDPOINTS",
    "FORBIDDEN_ON_OWNERSHIP_PATH",
    "FORBIDDEN_QUOTE",
    "ForbiddenNodeOnOwnershipPath",
    "GuestEmailRequired",
    "GuestMismatch",
    "INIT_REQUIRED_FIELDS",
    "IntervalError",
    "L2A_SETTING",
    "LINK_COLLECTION",
    "LINK_HISTORY_LIMIT",
    "LINK_TYPES",
    "LinkError",
    "LinkTypeNotSupported",
    "NoOwnerResolved",
    "OWNERSHIP",
    "OwnershipEngine",
    "OwnershipError",
    "OwnerUnknown",
    "PrerequisiteError",
    "RECORD_COLLECTION",
    "REP_COLLECTION",
    "RESOLUTION_ORDER_RATIONALE",
    "RESOLUTION_OUTCOMES",
    "RESOLUTION_SOURCES",
    "ROUTE_COLLECTION",
    "RULE_KINDS",
    "RouteConsumed",
    "RouteNotFound",
    "RoutingRuleError",
    "RulesDoNotFallThrough",
    "SCHEDULE_REQUIRED_FIELDS",
    "SUPPORTED_LINK_TYPES",
    "SessionError",
    "SETTINGS_COLLECTION",
    "SlotNotOffered",
    "StoreCrm",
    "TEAM_RULE",
    "VALUE_RULE",
    "available_slots",
    "busy_blocks",
    "calendar_for",
    "check_nodes",
    "describe_inferences",
    "evaluate",
    "inference_by_id",
    "normalise_domain",
    "normalise_email",
    "normalise_rules",
    "nodes_warnings",
    "published_vocabulary",
    "require_catch_all",
    "require_interval",
    "require_link_type",
    "require_object_type",
    "require_resolution_source",
    "require_rule_kind",
    "working_window",
]
