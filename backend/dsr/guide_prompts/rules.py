"""The pure rules of WF-118: validation, ordering, and the two clocks.

Everything here is a function over plain data. No store, no FastAPI, no clock
read from the environment: a caller passes the moment it is evaluating, so a
test freezes the clock by passing a datetime rather than by patching ``now``.

The two mechanisms are modelled separately on purpose. The app throttle talks
about two different guides, and its countdown starts when the previous guide is
dismissed or completed. The per-guide repeat talks about one guide and one
visitor, and its clock starts when that visitor dismissed that guide. Merging
them into one timer is the defect the issue names.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .errors import EventInvalid, OrderInvalid
from .vocabulary import (
    DELAY_MAX_SECONDS,
    DISMISSAL_EVENTS,
    GUIDE_STATUSES,
    INTERACTION_EVENTS,
    MIN_STOP_AFTER,
    ORDER_MOVES,
    REPEAT_RANGE,
    REPEAT_UNITS,
    SECONDS_PER_UNIT,
    STATUS_STAGING,
    STOP_AFTER_COUNT,
    STOP_AFTER_MODES,
    STOP_AFTER_NEVER,
    THROTTLE_RANGE,
    THROTTLE_UNITS,
)

KEY_MIN = 3
KEY_MAX = 64
NAME_MAX = 120
#: Keys are immutable once a guide exists, so the pattern is deliberately narrow:
#: a lowercase slug that survives being written into a permalink.
_KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_"


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp, or a datetime, into an aware datetime.

    A naive timestamp is read as UTC rather than as local time, so a value stored
    by one process is read the same way by another. ``None`` and the empty string
    both mean "no moment recorded".
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def to_iso(value: Any) -> str | None:
    """Render a moment as an ISO 8601 string in UTC, or ``None``."""
    moment = parse_time(value)
    return None if moment is None else moment.astimezone(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Key
# --------------------------------------------------------------------------- #


def key_problem(key: Any) -> str | None:
    """Return why a guide key is unusable, or ``None`` when it is fine."""
    if key is None or key == "":
        return "key is required"
    text = str(key)
    if not (KEY_MIN <= len(text) <= KEY_MAX):
        return f"key must be {KEY_MIN} to {KEY_MAX} characters"
    if text[0].isdigit():
        return "key must start with a letter"
    bad = sorted({character for character in text if character not in _KEY_ALPHABET})
    if bad:
        return f"key must match [a-z0-9_], found {' '.join(repr(c) for c in bad)}"
    return None


# --------------------------------------------------------------------------- #
# Intervals
# --------------------------------------------------------------------------- #


def _interval_problem(
    value: Any, unit: Any, units: tuple[str, ...], bounds: tuple[int, int], label: str
) -> str | None:
    if unit not in units:
        return f"{label} unit must be one of {', '.join(units)}"
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return f"{label} must be a whole number"
    low, high = bounds
    if not (low <= value <= high):
        return f"{label} must be between {low} and {high}"
    return None


def repeat_interval_problem(every: Any, unit: Any) -> str | None:
    """The per-guide repeat interval: 1..10,000 of Day or Hour."""
    return _interval_problem(every, unit, REPEAT_UNITS, REPEAT_RANGE, "repeat interval")


def throttle_interval_problem(count: Any, unit: Any) -> str | None:
    """The app throttle interval: 1..10,000 of Minute, Hour or Day."""
    return _interval_problem(count, unit, THROTTLE_UNITS, THROTTLE_RANGE, "throttle interval")


def interval_seconds(count: int, unit: str) -> int:
    """A count and a unit as a number of seconds."""
    return int(count) * SECONDS_PER_UNIT[unit]


def repeat_interval_seconds(every: int, unit: str) -> int:
    return interval_seconds(every, unit)


def throttle_interval_seconds(count: int, unit: str) -> int:
    return interval_seconds(count, unit)


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


def delay_problem(seconds: Any) -> str | None:
    """The display delay: 0..60 seconds, so a prompt cannot hold for a minute."""
    if seconds is None:
        return None
    if isinstance(seconds, bool):
        return "delay must be a whole number of seconds"
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return "delay must be a whole number of seconds"
    if not (0 <= value <= DELAY_MAX_SECONDS):
        return f"delay must be between 0 and {DELAY_MAX_SECONDS} seconds"
    return None


def stop_after_problem(mode: Any, count: Any) -> str | None:
    """The stop threshold. ``count`` mode must be at least two dismissals."""
    if mode not in STOP_AFTER_MODES:
        return f"stop_after mode must be one of {', '.join(STOP_AFTER_MODES)}"
    if mode == STOP_AFTER_NEVER:
        return None
    if isinstance(count, bool):
        return f"stop_after count must be an integer of at least {MIN_STOP_AFTER}"
    try:
        value = int(count)
    except (TypeError, ValueError):
        return f"stop_after count must be an integer of at least {MIN_STOP_AFTER}"
    if value < MIN_STOP_AFTER:
        return (
            f"stop_after count must be at least {MIN_STOP_AFTER} dismissals; "
            "completion counts as a dismissal"
        )
    return None


def normalise_activation(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fill an activation payload from its defaults, without validating it.

    Defaults are the off state, because the Activation card starts off: repeat
    off, stop after never, throttling respected, no delay.
    """
    source = dict(raw or {})
    repeat = dict(source.get("repeat") or {})
    stop = dict(source.get("stop_after") or {})
    mode = str(stop.get("mode") or STOP_AFTER_NEVER)
    return {
        "repeat": {
            "enabled": bool(repeat.get("enabled")),
            "every": _as_int(repeat.get("every"), 1),
            "unit": str(repeat.get("unit") or REPEAT_UNITS[0]),
        },
        "stop_after": {
            "mode": mode,
            "count": _as_int(stop.get("count"), MIN_STOP_AFTER),
        },
        "ignore_throttling": bool(source.get("ignore_throttling")),
        "delay_seconds": _as_int(source.get("delay_seconds"), 0),
    }


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def activation_errors(raw: Mapping[str, Any] | None) -> list[str]:
    """Every problem in an activation payload, as a list of sentences."""
    activation = normalise_activation(raw)
    problems: list[str] = []
    repeat = activation["repeat"]
    if repeat["enabled"]:
        problem = repeat_interval_problem(repeat["every"], repeat["unit"])
        if problem:
            problems.append(problem)
    stop = activation["stop_after"]
    problem = stop_after_problem(stop["mode"], stop["count"])
    if problem:
        problems.append(problem)
    problem = delay_problem(activation["delay_seconds"])
    if problem:
        problems.append(problem)
    return problems


# --------------------------------------------------------------------------- #
# Guides and orders
# --------------------------------------------------------------------------- #


def guide_errors(data: Mapping[str, Any]) -> list[str]:
    """Every problem in a guide payload, as a list of sentences."""
    problems: list[str] = []
    problem = key_problem(data.get("key"))
    if problem:
        problems.append(problem)
    name = str(data.get("name") or "").strip()
    if not name:
        problems.append("name is required")
    elif len(name) > NAME_MAX:
        problems.append(f"name must be at most {NAME_MAX} characters")
    status = str(data.get("status") or STATUS_STAGING)
    if status not in GUIDE_STATUSES:
        problems.append(f"status must be one of {', '.join(GUIDE_STATUSES)}")
    problems.extend(activation_errors(data.get("activation")))
    return problems


def order_errors(
    guide_keys: Any, throttling: Mapping[str, Any] | None, known_keys: Iterable[str]
) -> list[str]:
    """Every problem in an ordering document, as a list of sentences."""
    problems: list[str] = []
    if not isinstance(guide_keys, list):
        problems.append("guide_keys must be a list")
    else:
        unknown = sorted({str(k) for k in guide_keys} - set(known_keys))
        if unknown:
            problems.append(f"guide_keys names unknown guides: {', '.join(unknown)}")
        if len(guide_keys) != len(set(guide_keys)):
            problems.append("guide_keys must not repeat a guide")
    throttle = dict(throttling or {})
    if throttle.get("enabled"):
        problem = throttle_interval_problem(throttle.get("count"), throttle.get("unit"))
        if problem:
            problems.append(problem)
    return problems


def rank(guides: Iterable[Mapping[str, Any]], guide_keys: Iterable[str]) -> list[dict[str, Any]]:
    """Order guides into the two tiers the research describes.

    "Guides that ignore throttling appear first." So the unthrottled tier is
    ranked ahead of the throttled tier, and each tier keeps the row order the
    ordering table set. A guide the order list does not name yet is appended to
    its tier, sorted by key, so a newly authored guide is visible rather than
    silently invisible.
    """
    by_key: dict[str, Mapping[str, Any]] = {str(g["key"]): g for g in guides}
    named = [str(k) for k in guide_keys if str(k) in by_key]
    extra = sorted(key for key in by_key if key not in named)
    ordered = named + extra

    def ignores(key: str) -> bool:
        return bool(normalise_activation(by_key[key].get("activation"))["ignore_throttling"])

    tier_one = [key for key in ordered if ignores(key)]
    tier_two = [key for key in ordered if not ignores(key)]

    ranked: list[dict[str, Any]] = []
    for index, key in enumerate([*tier_one, *tier_two]):
        ranked.append(
            {
                "key": key,
                "name": by_key[key].get("name") or key,
                "tier": 0 if ignores(key) else 1,
                "ignore_throttling": ignores(key),
                "position": index,
            }
        )
    return ranked


def move(guide_keys: Iterable[str], key: str, direction: str) -> list[str]:
    """Apply one row arrow to the order list, and return the new order.

    ``top`` and ``bottom`` move the guide to an end. ``up`` and ``down`` swap it
    with its neighbour. Drag-and-drop is a full replacement of the list and does
    not go through here.
    """
    if direction not in ORDER_MOVES:
        raise OrderInvalid([f"move must be one of {', '.join(ORDER_MOVES)}"])
    keys = [str(k) for k in guide_keys]
    if key not in keys:
        raise OrderInvalid([f"{key!r} is not in this order list"])
    rest = [k for k in keys if k != key]
    if direction == "top":
        return [key, *rest]
    if direction == "bottom":
        return [*rest, key]
    index = keys.index(key)
    swap = index - 1 if direction == "up" else index + 1
    if swap < 0 or swap >= len(keys):
        return keys
    reordered = list(keys)
    reordered[index], reordered[swap] = reordered[swap], reordered[index]
    return reordered


# --------------------------------------------------------------------------- #
# Sessions: the two clocks
# --------------------------------------------------------------------------- #


def default_session(app_id: str, user_id: str, started_at: Any) -> dict[str, Any]:
    """A visitor's session state, with both clocks unset."""
    return {
        "app_id": app_id,
        "user_id": user_id,
        "started_at": to_iso(started_at),
        "active_guide_key": None,
        "last_dismissed_at": None,
        "last_shown_at": None,
        "programmatic_launches": 0,
        "guides": {},
    }


def is_dismissal(event: Any) -> bool:
    """True when an interaction restarts both clocks."""
    return str(event) in DISMISSAL_EVENTS


def validate_event(event: Any) -> str:
    text = str(event)
    if text not in INTERACTION_EVENTS:
        raise EventInvalid(f"event must be one of {', '.join(INTERACTION_EVENTS)}, not {event!r}")
    return text


def _guide_state(session: Mapping[str, Any], key: str) -> dict[str, Any]:
    state = dict((session.get("guides") or {}).get(key) or {})
    state.setdefault("shown_count", 0)
    state.setdefault("dismissed_count", 0)
    state.setdefault("last_shown_at", None)
    state.setdefault("last_dismissed_at", None)
    return state


def apply_shown(
    session: Mapping[str, Any], key: str, at: Any, *, programmatic: bool = False
) -> dict[str, Any]:
    """Record that a guide was launched.

    A ``shown`` event deliberately does not start either countdown: "A guide
    nobody interacts with does not block the next one." A programmatic launch is
    counted separately so the page can show that it bypassed the order list.
    """
    updated = _copy_session(session)
    moment = to_iso(at)
    updated["active_guide_key"] = key
    updated["last_shown_at"] = moment
    if programmatic:
        updated["programmatic_launches"] = int(updated.get("programmatic_launches") or 0) + 1
    state = _guide_state(updated, key)
    state["shown_count"] = int(state["shown_count"]) + 1
    state["last_shown_at"] = moment
    updated["guides"][key] = state
    return updated


def apply_dismissal(session: Mapping[str, Any], key: str, event: Any, at: Any) -> dict[str, Any]:
    """Record a dismissal, a step advance or a completion.

    All three are the same event here. Each one restarts the per-guide repeat
    clock and the app-level throttle clock, and clears the active guide so the
    next guide in the order list becomes eligible.
    """
    validate_event(event)
    if not is_dismissal(event):
        raise EventInvalid(f"{event!r} does not restart the clocks; only a dismissal does")
    updated = _copy_session(session)
    moment = to_iso(at)
    updated["active_guide_key"] = None
    updated["last_dismissed_at"] = moment
    state = _guide_state(updated, key)
    state["dismissed_count"] = int(state["dismissed_count"]) + 1
    state["last_dismissed_at"] = moment
    updated["guides"][key] = state
    return updated


def _copy_session(session: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(session)
    updated["guides"] = {str(k): dict(v) for k, v in (session.get("guides") or {}).items()}
    return updated


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #


def evaluate(
    guide: Mapping[str, Any],
    order: Mapping[str, Any],
    session: Mapping[str, Any],
    at: Any,
    *,
    position: int | None = None,
    tier: int | None = None,
) -> dict[str, Any]:
    """One guide's eligibility at a moment, with the moment it became eligible.

    Both clocks are computed here and the later one wins: a guide is relaunched
    when its own repeat interval has passed *and* the app throttle has released.
    The display delay is added on top, measured from the visitor's landing moment
    when nothing else holds the guide back.
    """
    moment = parse_time(at)
    key = str(guide["key"])
    activation = normalise_activation(guide.get("activation"))
    state = _guide_state(session, key)
    shown = int(state["shown_count"])
    dismissed = int(state["dismissed_count"])

    stop = activation["stop_after"]
    suppressed = False
    suppress_reason = ""
    if stop["mode"] == STOP_AFTER_COUNT and dismissed >= stop["count"]:
        suppressed = True
        suppress_reason = f"stop_after reached at {stop['count']} dismissals"
    elif dismissed > 0 and not activation["repeat"]["enabled"]:
        suppressed = True
        suppress_reason = "repeat is off and the guide was dismissed"

    last_dismissed = parse_time(state["last_dismissed_at"])
    repeat_due: datetime | None = None
    if activation["repeat"]["enabled"] and last_dismissed is not None:
        repeat_due = last_dismissed + timedelta(
            seconds=repeat_interval_seconds(
                activation["repeat"]["every"], activation["repeat"]["unit"]
            )
        )

    throttle = dict(order.get("throttling") or {})
    throttle_due: datetime | None = None
    throttled = not activation["ignore_throttling"]
    if throttled and throttle.get("enabled"):
        app_dismissed = parse_time(session.get("last_dismissed_at"))
        if app_dismissed is not None:
            unit = str(throttle.get("unit") or "day")
            seconds = SECONDS_PER_UNIT.get(unit, SECONDS_PER_UNIT["day"])
            throttle_due = app_dismissed + timedelta(
                seconds=_as_int(throttle.get("count"), 1) * seconds
            )

    holds = [d for d in (repeat_due, throttle_due) if d is not None]
    base = max(holds) if holds else (parse_time(session.get("started_at")) or moment)
    eligible_at = base + timedelta(seconds=activation["delay_seconds"])

    active_key = session.get("active_guide_key")
    is_active = active_key == key
    # An active guide is not a candidate again -- it is already on screen. It does
    # not block a *different* guide, because a guide nobody interacts with starts
    # no countdown, so the next guide in the order list stays eligible.
    eligible = (not suppressed) and moment >= eligible_at and not is_active

    return {
        "key": key,
        "name": guide.get("name") or key,
        "status": guide.get("status") or STATUS_STAGING,
        "tier": tier,
        "position": position,
        "ignore_throttling": activation["ignore_throttling"],
        "repeat": activation["repeat"],
        "stop_after": activation["stop_after"],
        "delay_seconds": activation["delay_seconds"],
        "shown_count": shown,
        "dismissed_count": dismissed,
        "suppressed": suppressed,
        "suppress_reason": suppress_reason,
        "repeat_due_at": to_iso(repeat_due),
        "throttle_due_at": to_iso(throttle_due),
        "eligible_at": to_iso(eligible_at),
        "eligible": eligible,
        "active": is_active,
        "blocked_by_active": bool(active_key),
    }


def ranking(
    guides: Iterable[Mapping[str, Any]],
    order: Mapping[str, Any],
    session: Mapping[str, Any],
    at: Any,
) -> list[dict[str, Any]]:
    """Every guide ranked into its tier, each with its eligibility at ``at``."""
    by_key = {str(g["key"]): g for g in guides}
    ranked = rank(by_key.values(), order.get("guide_keys") or [])
    return [
        evaluate(
            by_key[row["key"]],
            order,
            session,
            at,
            position=row["position"],
            tier=row["tier"],
        )
        for row in ranked
    ]


def select_next(
    guides: Iterable[Mapping[str, Any]],
    order: Mapping[str, Any],
    session: Mapping[str, Any],
    at: Any,
) -> dict[str, Any]:
    """The next automatic guide to launch, or the reason none can.

    The order list is the queue and the throttle is the gate; the first eligible
    guide in tier order is the answer. One guide that is not dismissed at all
    never blocks the queue, because neither clock has started.
    """
    views = ranking(guides, order, session, at)
    for view in views:
        if view["eligible"]:
            return {"guide": view, "blocked": None, "ranking": views, "at": to_iso(at)}
    if session.get("active_guide_key"):
        blocked = f"guide {session['active_guide_key']!r} is active and not dismissed"
    else:
        blocked = "no guide is eligible at this moment"
    return {"guide": None, "blocked": blocked, "ranking": views, "at": to_iso(at)}


def stop_after_label(activation: Mapping[str, Any] | None) -> str:
    """A human-readable stop-after label, so a page never re-derives it."""
    normalised = normalise_activation(activation)
    stop = normalised["stop_after"]
    if stop["mode"] == STOP_AFTER_NEVER:
        return "Never"
    return f"After {stop['count']} dismissals"


__all__ = [
    "activation_errors",
    "apply_dismissal",
    "apply_shown",
    "default_session",
    "delay_problem",
    "evaluate",
    "guide_errors",
    "interval_seconds",
    "is_dismissal",
    "key_problem",
    "move",
    "normalise_activation",
    "order_errors",
    "parse_time",
    "rank",
    "ranking",
    "repeat_interval_problem",
    "repeat_interval_seconds",
    "select_next",
    "stop_after_label",
    "stop_after_problem",
    "throttle_interval_problem",
    "throttle_interval_seconds",
    "to_iso",
    "validate_event",
]
