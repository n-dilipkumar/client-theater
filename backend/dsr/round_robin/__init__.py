"""WF-054: distribute bookings across a team by round robin.

The researched workflow, in the order the research states it: an admin creates a
Team and a Round Robin distribution, either Strict (equal turns) or Flexible
(weighted by availability), with per-member weights and credits; a prospect opens
the team link, or a backend calls the researched init call; the distribution is
evaluated and one combined availability window is returned; the prospect books,
the chosen member is credited, and the distribution advances; and if a rep
no-shows, an admin marks the prospect No-Show so credits can be credited back.

Module map, in dependency order:

``errors``
    The one error type the host registers, and the three subclasses that draw
    the line between "you typed it wrong" and "the world moved on".
``timeutil``
    Every timestamp is an aware UTC datetime, plus the slot grid and the free-time
    arithmetic the window is built from.
``vocabulary``
    The researched terms, each with the sentence it comes from, plus the ones this
    build chose, which say so.
``teams``
    Membership, the per-member weights a distribution declares, and the license
    gate that decides who can be assigned at all.
``availability``
    The single combined window. Its module docstring carries the derivation of
    the union, which the research left open and Jev ratified.
``selection``
    The two modes, the tie-breaks, and the cursor that advances on each booking.
``credits``
    The ledger: consumed on booking, returned on a no-show.
``engine``
    The flow end to end, over the audited store.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with one by name.
"""

from __future__ import annotations

from dsr.round_robin.availability import (
    MAX_RANGE_DAYS,
    MAX_SLOTS,
    combined_window,
    explain_missing,
    find_slot,
    free_members_at,
    normalise_interval,
)
from dsr.round_robin.credits import (
    CREDIT_PER_BOOKING,
    apply_consumption,
    apply_return,
    can_return,
    empty_ledger,
)
from dsr.round_robin.engine import (
    BOOKING_COLLECTION,
    CREDIT_MOVEMENT_COLLECTION,
    DEFAULT_DURATION_MINUTES,
    DISTRIBUTION_COLLECTION,
    NO_SHOW_COLLECTION,
    ROUTE_COLLECTION,
    SUPPORTED_LINK_TYPES,
    TEAM_COLLECTION,
    RoundRobinEngine,
)
from dsr.round_robin.errors import (
    NoEligibleMember,
    RoundRobinConflict,
    RoundRobinError,
    RoundRobinNotFound,
)
from dsr.round_robin.inferences import INFERENCE_IDS, INFERENCES, describe
from dsr.round_robin.selection import (
    advance,
    ledger,
    next_candidate,
    select_member,
    shares,
    weight_scores,
)
from dsr.round_robin.teams import (
    exclusion_reason,
    member_id,
    summarise,
    validate_team,
    weight_of,
)
from dsr.round_robin.timeutil import UTC, free_minutes, grid, iso, overlaps, parse, utcnow
from dsr.round_robin.vocabulary import (
    ALLOCATION,
    BOOKED,
    BOOKING_STATUSES,
    CANCELLED,
    CONFIRMED,
    CREDIT_BACK_FLAG,
    CREDIT_CONSUMED,
    CREDIT_DIRECTIONS,
    CREDIT_RETURNED,
    ELIGIBLE,
    EVALUATION_OUTCOMES,
    FLEXIBLE,
    LICENSE_GATE,
    LINK_TYPES,
    MODE_NAMES,
    NO_SHOW,
    OPEN,
    ROUND_ROBIN,
    ROUND_ROBIN_MODES,
    ROUTE_STATES,
    SOURCES,
    STRICT,
    published_vocabulary,
    require_mode,
)

__all__ = [
    "ALLOCATION",
    "BOOKED",
    "BOOKING_COLLECTION",
    "BOOKING_STATUSES",
    "CANCELLED",
    "CREDIT_BACK_FLAG",
    "CREDIT_CONSUMED",
    "CREDIT_DIRECTIONS",
    "CREDIT_MOVEMENT_COLLECTION",
    "CREDIT_PER_BOOKING",
    "CREDIT_RETURNED",
    "CONFIRMED",
    "DEFAULT_DURATION_MINUTES",
    "DISTRIBUTION_COLLECTION",
    "ELIGIBLE",
    "EVALUATION_OUTCOMES",
    "FLEXIBLE",
    "INFERENCES",
    "INFERENCE_IDS",
    "LICENSE_GATE",
    "LINK_TYPES",
    "MAX_RANGE_DAYS",
    "MAX_SLOTS",
    "MODE_NAMES",
    "NO_SHOW",
    "NO_SHOW_COLLECTION",
    "OPEN",
    "ROUND_ROBIN",
    "ROUND_ROBIN_MODES",
    "ROUTE_COLLECTION",
    "ROUTE_STATES",
    "SOURCES",
    "STRICT",
    "SUPPORTED_LINK_TYPES",
    "TEAM_COLLECTION",
    "UTC",
    "NoEligibleMember",
    "RoundRobinConflict",
    "RoundRobinEngine",
    "RoundRobinError",
    "RoundRobinNotFound",
    "advance",
    "apply_consumption",
    "apply_return",
    "can_return",
    "combined_window",
    "describe",
    "empty_ledger",
    "exclusion_reason",
    "explain_missing",
    "find_slot",
    "free_members_at",
    "free_minutes",
    "grid",
    "iso",
    "ledger",
    "member_id",
    "next_candidate",
    "normalise_interval",
    "overlaps",
    "parse",
    "published_vocabulary",
    "require_mode",
    "select_member",
    "shares",
    "summarise",
    "utcnow",
    "validate_team",
    "weight_of",
    "weight_scores",
]
