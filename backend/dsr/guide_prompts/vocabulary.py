"""The vocabulary and the sourced rules of WF-118.

Everything a reader needs to check a claim in this feature against the research
it came from lives here, so the page and the tests render the rules rather than
restating them. The GitHub issue quoted the research specification in full, and
where the specification is silent this module records the reading taken and the
reason, marked as an inference or as a Jev decision with its audit id.

No framework and no store: this module imports nothing from ``dsr``.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
#
# The record payloads are ordinary JSON in ``records.data`` -- no migration, no
# typed column -- so a team can add a field without coordinating with anyone.
# --------------------------------------------------------------------------- #

#: One row per in-app guide definition, in any lifecycle state.
GUIDES = "wf118_guide"

#: One ordering document per application. The research scopes throttling to "each
#: application", so the order list and its throttle are keyed by application.
ORDERS = "wf118_guide_order"

#: One row per (application, visitor) pair: the clocks the two mechanisms read.
SESSIONS = "wf118_guide_session"

#: One row per interaction (shown, dismissed, advanced, completed). The history is
#: a sequence rather than a single overwritten field, so a review can replay it.
EVENTS = "wf118_guide_event"

COLLECTIONS: dict[str, str] = {
    "guides": GUIDES,
    "orders": ORDERS,
    "sessions": SESSIONS,
    "events": EVENTS,
}

# --------------------------------------------------------------------------- #
# The two mechanisms
#
# The research describes two distinct timers and the issue is explicit that they
# must not be merged into one. One is global and between different guides; the
# other is per guide and per visitor.
# --------------------------------------------------------------------------- #

#: Minimum time between one automatic guide and the next, scoped to an
#: application. "The countdown for the next guide launch begins after the previous
#: guide is dismissed or completed."
THROTTLE_SCOPE = "application"

#: Gap since that guide was dismissed by that visitor, scoped per guide per
#: visitor. "Repeat guide display relaunches a guide automatically after an
#: eligible visitor has dismissed it, at an interval you define."
REPEAT_SCOPE = "guide_visitor"

MECHANISMS: dict[str, str] = {
    "throttle": THROTTLE_SCOPE,
    "repeat": REPEAT_SCOPE,
}

# --------------------------------------------------------------------------- #
# Interval units
#
# Two different unit sets on two different settings. Validate both. The throttle
# toggle is Day/Minute/Hour; the repeat interval is Day or Hour only.
# --------------------------------------------------------------------------- #

REPEAT_UNITS: tuple[str, ...] = ("day", "hour")
THROTTLE_UNITS: tuple[str, ...] = ("minute", "hour", "day")

#: "You can set the interval to a number between 1 and 10,000."
REPEAT_RANGE: tuple[int, int] = (1, 10_000)

#: The throttle interval is a count too. The research does not print a ceiling, so
#: the reading taken is the same 1..10,000 bound as the repeat interval.
THROTTLE_RANGE: tuple[int, int] = (1, 10_000)

SECONDS_PER_UNIT: dict[str, int] = {
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
}

# --------------------------------------------------------------------------- #
# Activation card -> Edit display options
# --------------------------------------------------------------------------- #

#: "**A set number of dismissals.** ... The minimum is two dismissals, which
#: includes guide completion." The floor is 2 and it is not configurable below 2.
MIN_STOP_AFTER = 2

#: "Stop showing after" chooses between these two.
STOP_AFTER_NEVER = "never"
STOP_AFTER_COUNT = "count"
STOP_AFTER_MODES: tuple[str, ...] = (STOP_AFTER_NEVER, STOP_AFTER_COUNT)

#: "**Delay guide display** lets you set a wait period of up to 60 seconds before
#: the guide appears, so you can avoid interrupting visitors the moment they land
#: on a page."
DELAY_MAX_SECONDS = 60

# --------------------------------------------------------------------------- #
# Dismissal
#
# "Dismissal has several causes, and they are all the same event to you: a close
# control, a step advance via pendo.onGuideAdvanced(), and Esc is a dismissal."
# Completion counts as a dismissal too.
# --------------------------------------------------------------------------- #

EVENT_SHOWN = "shown"
EVENT_DISMISSED = "dismissed"
EVENT_ADVANCED = "advanced"
EVENT_COMPLETED = "completed"

INTERACTION_EVENTS: tuple[str, ...] = (
    EVENT_SHOWN,
    EVENT_DISMISSED,
    EVENT_ADVANCED,
    EVENT_COMPLETED,
)

#: Every event that restarts both clocks. ``shown`` is deliberately absent: "A
#: guide nobody interacts with does not block the next one."
DISMISSAL_EVENTS: tuple[str, ...] = (EVENT_DISMISSED, EVENT_ADVANCED, EVENT_COMPLETED)

#: The causes the research names, in its own words, so the page can show them.
DISMISSAL_CAUSES: dict[str, str] = {
    "close": "The visitor closes the guide with its close control.",
    "esc": "The visitor presses the keyboard Esc key.",
    "dismissed_callback": "pendo.onGuideDismissed() is called from a code block.",
    "advanced": "The visitor advances a step with pendo.onGuideAdvanced().",
    "completed": "The visitor completes the guide; completion counts as a dismissal.",
}

# --------------------------------------------------------------------------- #
# Order list moves
#
# "Reorder guides with the row arrows or drag-and-drop". The row arrows are the
# top/up/down/bottom moves; drag-and-drop is the full-list replacement.
# --------------------------------------------------------------------------- #

ORDER_MOVES: tuple[str, ...] = ("top", "up", "down", "bottom")

#: "Guides that ignore throttling appear first." The order list is therefore
#: two-tier, not one flat list: the unthrottled tier is ranked before the
#: throttled tier, and each tier keeps its own row order.
ORDER_TIERS: tuple[str, ...] = ("ignore_throttling", "throttled")

# --------------------------------------------------------------------------- #
# Guide status and the filters the ordering table offers
# --------------------------------------------------------------------------- #

STATUS_STAGING = "staging"
STATUS_PUBLIC = "public"
GUIDE_STATUSES: tuple[str, ...] = (STATUS_STAGING, STATUS_PUBLIC)

#: "Filter the ordering table by Product Area, category, status and Page location".
ORDER_FILTERS: tuple[str, ...] = ("product_area", "category", "status", "page_location")

# --------------------------------------------------------------------------- #
# The reading taken where the research is silent, and the two Jev decisions.
#
# The issue says "Decide whether ... and record the answer", for two questions.
# Both were put to Jev, and the audit ids below are the records.
# --------------------------------------------------------------------------- #

DECISION_PROGRAMMATIC = "programmatic_launch"
DECISION_PLATFORM = "order_list_platform_scope"

#: A programmatic launch's guide key is the same space as an automatic one, so
#: there is no separate identifier for it.
PROGRAMMATIC_LAUNCH_CUE = "showGuideById"


def vocabulary() -> dict[str, Any]:
    """The whole vocabulary, the rules, the decisions and the sources."""
    return {
        "ticket": "WF-118",
        "name": "Order, throttle and repeat in-app guide prompts",
        "collections": dict(COLLECTIONS),
        "mechanisms": dict(MECHANISMS),
        "throttle_scope": THROTTLE_SCOPE,
        "repeat_scope": REPEAT_SCOPE,
        "repeat_units": list(REPEAT_UNITS),
        "throttle_units": list(THROTTLE_UNITS),
        "repeat_range": list(REPEAT_RANGE),
        "throttle_range": list(THROTTLE_RANGE),
        "seconds_per_unit": dict(SECONDS_PER_UNIT),
        "stop_after_modes": list(STOP_AFTER_MODES),
        "min_stop_after": MIN_STOP_AFTER,
        "delay_max_seconds": DELAY_MAX_SECONDS,
        "guide_statuses": list(GUIDE_STATUSES),
        "order_filters": list(ORDER_FILTERS),
        "order_tiers": list(ORDER_TIERS),
        "order_moves": list(ORDER_MOVES),
        "interaction_events": list(INTERACTION_EVENTS),
        "dismissal_events": list(DISMISSAL_EVENTS),
        "dismissal_causes": dict(DISMISSAL_CAUSES),
        "activation_fields": [
            "repeat",
            "stop_after",
            "ignore_throttling",
            "delay_seconds",
        ],
        "decisions": decisions(),
        "sources": list(SOURCES),
    }


def decisions() -> list[dict[str, Any]]:
    """Every judgement this build recorded, with its gate and its alternative."""
    return [
        {
            "id": DECISION_PROGRAMMATIC,
            "decision": (
                "How a programmatic showGuideById launch interacts with the app-level throttle."
            ),
            "selected": "bypasses_gate",
            "alternative_rejected": (
                "Subjecting a programmatic launch to the throttle gate, as if it were "
                "an automatic guide."
            ),
            "verdict": "pass",
            "confidence": 0.98,
            "audit_id": "jev-20261013T194510-856-10906",
            "note": (
                "A programmatic launch is not an automatic guide, so it bypasses the "
                "throttle gate. Its dismissal still restarts both clocks, because a "
                "dismissal always restarts them."
            ),
        },
        {
            "id": DECISION_PLATFORM,
            "decision": "Whether the guide order list is shared across platforms or per platform.",
            "selected": "shared",
            "alternative_rejected": (
                "A separate order list and throttle per platform (web and mobile)."
            ),
            "verdict": "pass",
            "confidence": 0.98,
            "audit_id": "jev-20261013T194511-856-11340",
            "note": (
                "One shared order list per application. Throttling is application-level "
                "and a mobile guide cannot override throttling for a single guide, so a "
                "per-platform list would introduce a throttle scope the research does "
                "not describe."
            ),
        },
        {
            "id": "throttle_starts_on_dismissal",
            "decision": "When the throttle countdown starts.",
            "selected": "on_dismissal_or_completion",
            "alternative_rejected": "Starting the countdown on display.",
            "verdict": "recorded",
            "audit_id": None,
            "note": (
                "Quoted from the research: the countdown begins after the previous "
                "guide is dismissed or completed. A guide nobody interacts with does "
                "not block the next one."
            ),
        },
        {
            "id": "completion_is_a_dismissal",
            "decision": "Whether completing a guide counts toward the stop threshold.",
            "selected": "counts",
            "alternative_rejected": "Counting only explicit dismissals.",
            "verdict": "recorded",
            "audit_id": None,
            "note": (
                "Quoted from the research: the minimum is two dismissals, which "
                "includes guide completion. So the floor is two and cannot go lower."
            ),
        },
        {
            "id": "advance_is_a_dismissal",
            "decision": "Whether a step advance is the same event as a dismissal.",
            "selected": "same_event",
            "alternative_rejected": "Treating advance as a non-dismissal interaction.",
            "verdict": "recorded",
            "audit_id": None,
            "note": (
                "The issue names a close control, a step advance via "
                "pendo.onGuideAdvanced(), and Esc as the same event."
            ),
        },
    ]


#: The five sources the research cites, reproduced verbatim from the issue.
SOURCES: tuple[str, ...] = (
    "https://support.pendo.io/hc/en-us/articles/360031864452-Order-and-throttle-your-guides",
    "https://support.pendo.io/hc/en-us/articles/360031864692-Overlay-guide-activation-options",
    "https://support.pendo.io/hc/en-us/articles/360031864612-Define-guide-settings",
    "https://support.pendo.io/hc/en-us/articles/360032203891-Create-a-guide-permalink",
    "https://web-sdk.pendo.io/public/guides#showguidebyidid-reason",
)


__all__ = [
    "COLLECTIONS",
    "DECISION_PLATFORM",
    "DECISION_PROGRAMMATIC",
    "DELAY_MAX_SECONDS",
    "DISMISSAL_CAUSES",
    "DISMISSAL_EVENTS",
    "EVENTS",
    "GUIDES",
    "GUIDE_STATUSES",
    "INTERACTION_EVENTS",
    "MECHANISMS",
    "MIN_STOP_AFTER",
    "ORDER_FILTERS",
    "ORDER_MOVES",
    "ORDER_TIERS",
    "ORDERS",
    "REPEAT_RANGE",
    "REPEAT_SCOPE",
    "REPEAT_UNITS",
    "SECONDS_PER_UNIT",
    "SESSIONS",
    "SOURCES",
    "STATUS_PUBLIC",
    "STATUS_STAGING",
    "STOP_AFTER_COUNT",
    "STOP_AFTER_MODES",
    "STOP_AFTER_NEVER",
    "THROTTLE_RANGE",
    "THROTTLE_SCOPE",
    "THROTTLE_UNITS",
    "decisions",
    "vocabulary",
]
