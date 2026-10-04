"""WF-052: qualify a lead without offering any calendar.

The researched workflow in full. A form or lead payload arrives, the router's
rules are evaluated against the Chili Piper Data Fields in that payload and the
CRM values it carries, and the answer is a verdict, a proposed owner, the
``schedulingAllowed`` signal and a ``routingLink`` the caller may redirect to
later. No calendar is read, no slot is computed, no routing session is consumed,
and no automation fires.

The package is layered so each layer can be tested without the one below it:

* :mod:`~dsr.lead_qualification.vocabulary` - the published terms and the
  researched sentence behind each.
* :mod:`~dsr.lead_qualification.errors` - the refusals, each with its own status.
* :mod:`~dsr.lead_qualification.rules` - pure. Reads dictionaries, returns
  dictionaries. Imports nothing but the standard library and this package's
  vocabulary and errors.
* :mod:`~dsr.lead_qualification.engine` - the only module that touches the store,
  and it does so through :class:`~dsr.store.RecordStore`.
* :mod:`~dsr.lead_qualification.inferences` - every judgement call, served as data.

Nothing here imports :mod:`dsr.api`, and nothing here opens a database
connection.
"""

from __future__ import annotations

from dsr.lead_qualification.engine import (
    ASSIGNEE_COLLECTION,
    COLLECTIONS,
    NO_SIDE_EFFECTS,
    ROUTER_COLLECTION,
    VERDICT_COLLECTION,
    LeadQualificationEngine,
    require_room,
)
from dsr.lead_qualification.errors import (
    AssigneeNotFound,
    AssigneeRefused,
    IntervalSupplied,
    PayloadRefused,
    QualificationError,
    RoomRequired,
    RouterAlreadyExists,
    RouterDisabled,
    RouterNotFound,
    RouterRefused,
    VerdictNotFound,
)
from dsr.lead_qualification.inferences import describe
from dsr.lead_qualification.rules import (
    Condition,
    Match,
    Rule,
    compare,
    dotted_get,
    evaluate,
    parse_rules,
    refuses_interval,
    route_id_for,
    routing_link_for,
    validate_router,
)
from dsr.lead_qualification.vocabulary import (
    ACCESS_PATTERNS,
    ASSIGNMENT_TYPES,
    CALLER_PATHS,
    CRM_OBJECTS,
    EDGE_PATH,
    GUARANTEES,
    INTERVAL_FIELD,
    OPERATORS,
    QUOTES,
    RULE_KINDS,
    RULE_SOURCES,
    TICKET,
    VERDICTS,
    published_vocabulary,
)

__all__ = [
    "ACCESS_PATTERNS",
    "ASSIGNEE_COLLECTION",
    "ASSIGNMENT_TYPES",
    "AssigneeNotFound",
    "AssigneeRefused",
    "CALLER_PATHS",
    "COLLECTIONS",
    "CRM_OBJECTS",
    "Condition",
    "EDGE_PATH",
    "GUARANTEES",
    "INTERVAL_FIELD",
    "IntervalSupplied",
    "LeadQualificationEngine",
    "Match",
    "NO_SIDE_EFFECTS",
    "OPERATORS",
    "PayloadRefused",
    "QUOTES",
    "QualificationError",
    "ROUTER_COLLECTION",
    "RULE_KINDS",
    "RULE_SOURCES",
    "RoomRequired",
    "RouterAlreadyExists",
    "RouterDisabled",
    "RouterNotFound",
    "RouterRefused",
    "Rule",
    "TICKET",
    "VERDICTS",
    "VERDICT_COLLECTION",
    "VerdictNotFound",
    "compare",
    "describe",
    "dotted_get",
    "evaluate",
    "parse_rules",
    "published_vocabulary",
    "refuses_interval",
    "require_room",
    "route_id_for",
    "routing_link_for",
    "validate_router",
]
