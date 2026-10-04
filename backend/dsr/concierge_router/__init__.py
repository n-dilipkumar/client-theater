"""WF-051: the Concierge Router.

Route-and-book a web form request inline. A router is a declared flow of nodes;
a webform post enters at the ``Trigger`` node, its fields map onto Data Fields,
the routing rules are evaluated against those fields and against the room's CRM
records, and the matched ``Display Calendar`` node names a seller and the Meeting
Types to offer.

The package is laid out so the line between what the research published and what
this build chose is visible without reading the code:

``vocabulary``
    Every researched term, each beside the sentence that fixes it, and the
    constants this build named where the research published none.
``inferences``
    Every decision the research left open, with the reading chosen, the reason,
    and the reading that was rejected.
``errors``
    One hierarchy hanging off a single base, so the feature module registers one
    handler. Each refusal carries its own ``code`` and ``status``, because these
    refusals are not all the same kind of thing.
``nodes``
    The declaration rules, checked when a router is saved rather than when a
    prospect arrives.
``rules``
    Rule evaluation against Data Field values and against live CRM values, kept
    apart because the research keeps them apart.
``availability``
    A seller's busy blocks turned into the slot list the modal renders.
``engine``
    The two researched API calls, and every write.

This package imports nothing but the store. No framework, no ``dsr.api``, no
``sqlite3``, and no ``AuditedDatabase``.
"""

from dsr.concierge_router.availability import offer_slot, offer_slots
from dsr.concierge_router.engine import (
    BOOKINGS,
    OWNED_COLLECTIONS,
    ROUTERS,
    SELLERS,
    ConciergeRouterEngine,
)
from dsr.concierge_router.errors import (
    AssignmentNotBookable,
    GuestIdentityRequired,
    GuestMismatch,
    MeetingTypeNotOffered,
    NoRuleMatched,
    RedirectWithoutUrl,
    RouteConsumed,
    RouteNotFound,
    RouteNotSchedulable,
    RouterDeclarationError,
    RouterError,
    RouterNotPublished,
    RouterUnavailable,
    RulesDoNotFallThrough,
    SlotNotOffered,
    TimerShorterThanElapsed,
    TriggerActionMissing,
    TriggerMustBeFirst,
    UnknownNodeType,
    UnknownRouter,
)
from dsr.concierge_router.nodes import rule_nodes, validate_declaration

__all__ = [
    "BOOKINGS",
    "OWNED_COLLECTIONS",
    "ROUTERS",
    "SELLERS",
    "AssignmentNotBookable",
    "ConciergeRouterEngine",
    "GuestIdentityRequired",
    "GuestMismatch",
    "MeetingTypeNotOffered",
    "NoRuleMatched",
    "RedirectWithoutUrl",
    "RouteConsumed",
    "RouteNotFound",
    "RouteNotSchedulable",
    "RouterDeclarationError",
    "RouterError",
    "RouterNotPublished",
    "RouterUnavailable",
    "RulesDoNotFallThrough",
    "SlotNotOffered",
    "TimerShorterThanElapsed",
    "TriggerActionMissing",
    "TriggerMustBeFirst",
    "UnknownNodeType",
    "UnknownRouter",
    "offer_slot",
    "offer_slots",
    "rule_nodes",
    "validate_declaration",
]
