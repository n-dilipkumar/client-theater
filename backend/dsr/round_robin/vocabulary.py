"""The researched vocabulary for WF-054, published as data.

Every term the workflow uses is here with the sentence it comes from, and every
term this build had to choose for itself says so instead of borrowing a quote it
does not have. :func:`published_vocabulary` is what ``GET /api/wf054/vocabulary``
serves, and a client renders its pickers from it rather than from a list compiled
into the page - so a deployment that widens a vocabulary ships a record here
rather than a change to shared code.

The distinction the brief insists on - sourced behaviour versus assumed behaviour
- is carried on the entries themselves. An entry either carries ``sourced_from``
(the quoted sentence) or ``inference`` (the id of the registry entry in
:mod:`dsr.round_robin.inferences` that owns the choice). A test asserts every
entry is one or the other and never neither, which is what stops an assumption
from quietly acquiring a quotation.
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
    "chilipiper_concierge_flow": (
        "https://help.chilipiper.com/hc/en-us/articles/28522554434323-Creating-a-Concierge-Flow"
    ),
    "chilipiper_meetings_activity": "https://help.chilipiper.com/hc/en-us/articles/31428605286931",
}

#: The link type this workflow routes. The research lists five link types and
#: names this one for round robin.
ROUND_ROBIN = "RoundRobin"

LINK_TYPES: tuple[dict[str, Any], ...] = (
    {
        "link_type": ROUND_ROBIN,
        "routed_by_this_workflow": True,
        "sourced_from": 'Admin creates a Team and a Round Robin distribution ... Link type is "RoundRobin"',
        "source": "chilipiper_programmatic",
    },
    {
        "link_type": "Ownership",
        "routed_by_this_workflow": False,
        "sourced_from": "WF-053 owns it; it routes by the CRM record's owner, not by team membership",
        "source": "chilipiper_programmatic",
    },
    {
        "link_type": "Personal",
        "routed_by_this_workflow": False,
        "sourced_from": "Scheduling Links (types: Personal / Admin (one-on-one) / Round Robin / Group / Ownership)",
        "source": "chilipiper_concierge_flow",
    },
    {
        "link_type": "Admin",
        "routed_by_this_workflow": False,
        "sourced_from": "Scheduling Links (types: Personal / Admin (one-on-one) / Round Robin / Group / Ownership)",
        "source": "chilipiper_concierge_flow",
    },
    {
        "link_type": "Group",
        "routed_by_this_workflow": False,
        "sourced_from": "Scheduling Links (types: Personal / Admin (one-on-one) / Round Robin / Group / Ownership)",
        "source": "chilipiper_concierge_flow",
    },
)

LINK_TYPE_NAMES: tuple[str, ...] = tuple(str(entry["link_type"]) for entry in LINK_TYPES)

# --------------------------------------------------------------------------- #
# The two round robin modes
#
# Sourced exactly, and they are not interchangeable: "either strict (equal turns)
# or flexible (weighted by availability)".
# --------------------------------------------------------------------------- #

STRICT = "strict"
FLEXIBLE = "flexible"

ROUND_ROBIN_MODES: tuple[dict[str, Any], ...] = (
    {
        "mode": STRICT,
        "label": "Strict",
        "selects_by": "equal turns",
        "rule": (
            "The eligible member with the fewest credits consumed this cycle wins. Ties break on "
            "the number of turns already taken, then on team member order, so a rotation visits "
            "every member once before any member is visited twice."
        ),
        "weights_used": False,
        "availability_used": "gates the choice only: a member with no free time in the window cannot be chosen",
        "sourced_from": (
            "Round Robin - a team link that rotates assignment across members, either strict "
            "(equal turns) or flexible (weighted by availability)"
        ),
        "source": "chilipiper_concierge_flow",
    },
    {
        "mode": FLEXIBLE,
        "label": "Flexible",
        "selects_by": "weighted by availability",
        "rule": (
            "Each eligible member's weight is their declared weight times their share of the "
            "combined free time. The largest weight wins; ties break on fewest credits consumed, "
            "then on team member order."
        ),
        "weights_used": True,
        "availability_used": "the weight itself: free minutes in the window scale the choice",
        "sourced_from": (
            "Round Robin - a team link that rotates assignment across members, either strict "
            "(equal turns) or flexible (weighted by availability)"
        ),
        "source": "chilipiper_concierge_flow",
    },
)

MODE_NAMES: tuple[str, ...] = tuple(str(entry["mode"]) for entry in ROUND_ROBIN_MODES)

# --------------------------------------------------------------------------- #
# How member calendars are combined
#
# The research writes "union/intersection of member calendars" and does not say
# which operation applies to which mode. The derivation is recorded in
# ``inference_calendar_combination`` and was put to Jev, audit
# ``jev-20261004T045227-22564-47815``, which selected ``union_with_recheck`` at
# confidence 1.00.
# --------------------------------------------------------------------------- #

CALENDAR_COMBINATION: dict[str, Any] = {
    "operation": "union",
    "applies_to": "both modes",
    "offered_when": "at least one licensed member is free for the whole slot",
    "annotated_with": "the member ids free at that instant",
    "recheck_at_booking": True,
    "inference": "inference_calendar_combination",
    "sourced_from": "union/intersection of member calendars -> slot list",
    "source": "chilipiper_programmatic",
}

# --------------------------------------------------------------------------- #
# The license gate
# --------------------------------------------------------------------------- #

LICENSE_GATE: dict[str, Any] = {
    "rule": "a member with no Concierge license is excluded from assignment",
    "is_a_warning": False,
    "excluded_from": ["selection", "the combined window"],
    "researched_effect": "route to the Not Scheduled path",
    "sourced_from": (
        "All Team Members or Individuals you have assigned on this path must have a Concierge "
        "license assigned to them. Otherwise, if any prospects match to an unlicensed user, they "
        "will not be able to book a meeting and route to the Not Scheduled path."
    ),
    "source": "chilipiper_concierge_flow",
}

# --------------------------------------------------------------------------- #
# The credit ledger
#
# Two directions, and the research names both: "credit consumed on the selected
# member (or credited back on no-show)".
# --------------------------------------------------------------------------- #

CREDIT_CONSUMED = "consumed"
CREDIT_RETURNED = "returned"

CREDIT_DIRECTIONS: tuple[dict[str, Any], ...] = (
    {
        "direction": CREDIT_CONSUMED,
        "when": "a booking is taken on the member the distribution chose",
        "amount": 1,
        "sourced_from": "credit consumed on the selected member (or credited back on no-show)",
        "source": "chilipiper_programmatic",
    },
    {
        "direction": CREDIT_RETURNED,
        "when": "an admin marks the prospect No-Show and the distribution sets credit back",
        "amount": 1,
        "sourced_from": "if the Distribution associated with the meeting is set to credit back assignees for No-Shows",
        "source": "chilipiper_meetings_activity",
    },
)

CREDIT_DIRECTION_NAMES: tuple[str, ...] = tuple(
    str(entry["direction"]) for entry in CREDIT_DIRECTIONS
)

CREDIT_BACK_FLAG: dict[str, Any] = {
    "field": "credit_back_on_no_show",
    "type": "boolean",
    "default": False,
    "effect": "a no-show returns the credit the booking consumed",
    "standing_rule": True,
    "triggered_by": "an admin marking the prospect No-Show in Meetings Activity",
    "sourced_from": (
        "no-show credit-back is admin-triggered but the Meeting Type flag makes it a standing rule"
    ),
    "source": "chilipiper_programmatic",
}

# --------------------------------------------------------------------------- #
# Route states and booking states
# --------------------------------------------------------------------------- #

OPEN = "open"
BOOKED = "booked"

ROUTE_STATES: tuple[dict[str, Any], ...] = (
    {
        "state": OPEN,
        "live": True,
        "note": "slots are on offer and no booking has been taken against them",
        "sourced_from": "Chili Piper evaluates the distribution and returns a single combined availability window",
        "source": "chilipiper_programmatic",
    },
    {
        "state": BOOKED,
        "live": False,
        "note": "a slot was booked and a credit consumed; the route cannot be booked again",
        "sourced_from": "Prospect books; the chosen member is credited, and the distribution advances",
        "source": "chilipiper_programmatic",
    },
)

LIVE_ROUTE_STATES: frozenset[str] = frozenset(
    str(entry["state"]) for entry in ROUTE_STATES if entry["live"]
)

CONFIRMED = "confirmed"
CANCELLED = "cancelled"
NO_SHOW = "no_show"

BOOKING_STATUSES: tuple[dict[str, Any], ...] = (
    {
        "status": CONFIRMED,
        "live": True,
        "note": "the meeting is on the chosen member's calendar and holds a credit",
        "sourced_from": "Prospect books; the chosen member is credited",
        "source": "chilipiper_programmatic",
    },
    {
        "status": CANCELLED,
        "live": False,
        "note": "released. A cancelled booking no-shows nothing and holds no credit",
        "sourced_from": "the meeting is released",
        "source": "chilipiper_meetings_activity",
    },
    {
        "status": NO_SHOW,
        "live": False,
        "note": (
            "the meeting happened and the rep did not attend. Whether the credit comes back "
            "depends on the distribution's credit_back_on_no_show flag"
        ),
        "sourced_from": "Mark as No-Show ... useful if the Distribution associated with the meeting is set to credit back assignees for No-Shows",
        "source": "chilipiper_meetings_activity",
    },
)

LIVE_BOOKING_STATUSES: frozenset[str] = frozenset(
    str(entry["status"]) for entry in BOOKING_STATUSES if entry["live"]
)

#: The outcomes an evaluation can report. Each is a fact a rep reads, not a tone.
ELIGIBLE = "eligible"
ALLOCATION = "allocation"

EVALUATION_OUTCOMES: tuple[dict[str, Any], ...] = (
    {
        "outcome": ALLOCATION,
        "means": "a member was chosen and slots were offered",
        "sourced_from": "Chili Piper evaluates the distribution and returns a single combined availability window",
        "source": "chilipiper_programmatic",
    },
    {
        "outcome": ELIGIBLE,
        "means": "no member is available in the window, so nothing was offered",
        "sourced_from": "they will not be able to book a meeting and route to the Not Scheduled path",
        "source": "chilipiper_concierge_flow",
    },
)

EVALUATION_OUTCOME_NAMES: tuple[str, ...] = tuple(
    str(entry["outcome"]) for entry in EVALUATION_OUTCOMES
)

# --------------------------------------------------------------------------- #
# The reassignment handoff
# --------------------------------------------------------------------------- #

REUSE_DISTRIBUTION: dict[str, Any] = {
    "field": "distribution_ref",
    "on": "a booking and a route",
    "rule": (
        "The booking and the route both name the distribution they came from, so a later "
        "reassignment reopens that same Distribution rather than choosing a new team."
    ),
    "carries_on_the_distribution": ["cursor", "cycle", "credits", "turn"],
    "sourced_from": "the same Distribution context is reused later for reassignment",
    "source": "chilipiper_programmatic",
}

CREDIT_MOVES_WITH_HOST: dict[str, Any] = {
    "rule": "round-robin credit state moves with the host when a meeting is reassigned",
    "implemented_as": (
        "A reassignment records the credit against the new host and returns it against the old "
        "one, so the ledger follows the booking rather than the original assignee."
    ),
    "sourced_from": "round-robin credit state moves with the host",
    "source": "chilipiper_meetings_activity",
}

# --------------------------------------------------------------------------- #
# The researched APIs
# --------------------------------------------------------------------------- #

EDGE_API_CALLS: tuple[dict[str, Any], ...] = (
    {
        "call": "init-simple",
        "method": "POST",
        "path": "/api/fire-edge/v1/org/schedulingLinks/init-simple",
        "payload": {"link": {"type": "RoundRobin", "linkId": "..."}, "interval": "{...}"},
        "answers": ["routingId", "startTimes"],
        "sourced_from": 'POST .../schedulingLinks/init-simple with {"link":{"type":"RoundRobin","linkId":...}}',
        "source": "chilipiper_programmatic",
    },
    {
        "call": "schedule-simple",
        "method": "POST",
        "path": "/api/fire-edge/v1/org/schedulingLinks/routing/[routeId]/schedule-simple",
        "payload": {"startTime": "2026-01-05T14:30:00Z", "guestEmail": "lead@example.com"},
        "answers": ["the booking"],
        "sourced_from": "POST .../schedulingLinks/routing/[routeId]/schedule-simple",
        "source": "chilipiper_programmatic",
    },
    {
        "call": "scheduling-link-list-round-robin",
        "method": "discovery",
        "path": "/api/fire-edge/v1/org/schedulingLinks",
        "payload": {},
        "answers": ["the RoundRobin links in this workspace"],
        "sourced_from": "discovery op scheduling-link-list-round-robin",
        "source": "chilipiper_programmatic",
    },
)

# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _require(value: Any, allowed: tuple[str, ...], label: str) -> str:
    """Reject a value outside a published vocabulary.

    Raises :class:`~dsr.round_robin.errors.RoundRobinError` rather than a bare
    ``ValueError``, and that is load-bearing rather than cosmetic: the feature
    module registers one handler for the domain hierarchy and nothing else, so a
    plain ``ValueError`` raised here would escape it and answer 500 for what
    every other out-of-vocabulary value in this package answers 400 for.
    """
    from dsr.round_robin.errors import RoundRobinError

    text = str(value or "").strip()
    if text not in allowed:
        raise RoundRobinError(f"{label} must be one of {', '.join(allowed)}; got {value!r}")
    return text


def require_mode(value: Any) -> str:
    return _require(value, MODE_NAMES, "mode")


def require_link_type(value: Any) -> str:
    return _require(value, LINK_TYPE_NAMES, "link type")


def require_route_state(value: Any) -> str:
    return _require(value, tuple(str(e["state"]) for e in ROUTE_STATES), "state")


def require_booking_status(value: Any) -> str:
    return _require(value, tuple(str(e["status"]) for e in BOOKING_STATUSES), "status")


def require_credit_direction(value: Any) -> str:
    return _require(value, CREDIT_DIRECTION_NAMES, "direction")


def require_outcome(value: Any) -> str:
    return _require(value, EVALUATION_OUTCOME_NAMES, "outcome")


def published_vocabulary() -> dict[str, Any]:
    """Every researched term, served so a client renders its pickers from here."""
    return {
        "sources": dict(SOURCES),
        "link_types": [dict(entry) for entry in LINK_TYPES],
        "link_type_names": list(LINK_TYPE_NAMES),
        "modes": [dict(entry) for entry in ROUND_ROBIN_MODES],
        "mode_names": list(MODE_NAMES),
        "calendar_combination": dict(CALENDAR_COMBINATION),
        "license_gate": dict(LICENSE_GATE),
        "credit_directions": [dict(entry) for entry in CREDIT_DIRECTIONS],
        "credit_direction_names": list(CREDIT_DIRECTION_NAMES),
        "credit_back_flag": dict(CREDIT_BACK_FLAG),
        "route_states": [dict(entry) for entry in ROUTE_STATES],
        "live_route_states": sorted(LIVE_ROUTE_STATES),
        "booking_statuses": [dict(entry) for entry in BOOKING_STATUSES],
        "live_booking_statuses": sorted(LIVE_BOOKING_STATUSES),
        "evaluation_outcomes": [dict(entry) for entry in EVALUATION_OUTCOMES],
        "evaluation_outcome_names": list(EVALUATION_OUTCOME_NAMES),
        "reuse_distribution": dict(REUSE_DISTRIBUTION),
        "credit_moves_with_host": dict(CREDIT_MOVES_WITH_HOST),
        "edge_api_calls": [dict(entry) for entry in EDGE_API_CALLS],
    }
