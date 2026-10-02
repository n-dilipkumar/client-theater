"""The rules the ladder is evaluated against, and the stored override for them.

Three of the four things here are sourced and one is not:

* ``windows`` - 7 / 14 / 30 days, quoted from the Dock help article in the
  research's evidence block. Changeable, but the defaults are the source's.
* ``count_only_external`` - the metric is described as giving "a quick pulse on
  workspace health and **external engagement**". That is the source saying the
  metric is about buyers, and a rep opening their own room is not a buyer.
* ``min_events`` - **not sourced.** "tons" and "a decent amount" carry the
  recency windows in the same two sentences, so the source clearly distinguishes a
  busy workspace from a quiet one, and then gives no number for either. See
  :mod:`dsr.trend_health.inferences`, entry ``volume-floor``.

They live in a record, not a column, which is the shape this product uses for
everything: a team that wants Hot to mean "four views in a week" sends one PATCH
and no migration, no redeploy, and no coordination with anyone. That is also what
makes the inference bounded - the two default numbers below are a starting point,
not a decision baked into a function body.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.trend_health.errors import InvalidRules
from dsr.trend_health.vocabulary import RULES_COLLECTION, RULES_RECORD_ID, WINDOW_KEYS

#: The windows the research names, in days.
DEFAULT_WINDOWS: dict[str, int] = {"hot": 7, "warm": 14, "cold": 30}

#: The volume floors, which the research does not number. Chosen to be readable
#: on a deal desk rather than derived: Hot wants a genuinely busy week, and Warm
#: accepts any real fortnight of engagement that is not a busy week. Both are one
#: PATCH away from being whatever a team actually means.
DEFAULT_MIN_EVENTS: dict[str, int] = {"hot": 5, "warm": 2}

#: The sourced reading of "external engagement".
DEFAULT_COUNT_ONLY_EXTERNAL = True

#: What the ladder does with the defaults, ready to be evaluated.
DEFAULT_RULES: dict[str, Any] = {
    "windows": dict(DEFAULT_WINDOWS),
    "min_events": dict(DEFAULT_MIN_EVENTS),
    "count_only_external": DEFAULT_COUNT_ONLY_EXTERNAL,
}

_ALLOWED_KEYS = frozenset(DEFAULT_RULES)


def merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay a partial patch on a rules mapping and validate the result.

    A patch to ``windows`` or ``min_events`` merges key by key, so retuning the
    Hot floor does not silently reset Warm. ``count_only_external`` is a scalar
    and replaces outright.

    Unknown keys are refused rather than ignored. A patch that says
    ``{"min_event": 3}`` and a patch that says ``{"min_events": 3}`` look
    identical to the person who typed them, and only one of them does anything -
    which is exactly how a threshold ends up quietly wrong.
    """
    if not isinstance(patch, Mapping):
        raise InvalidRules(f"the rules patch must be a JSON object; got {type(patch).__name__}")

    unknown = sorted(set(patch) - _ALLOWED_KEYS)
    if unknown:
        raise InvalidRules(
            f"unknown rule(s) {', '.join(unknown)}; this workflow configures "
            f"{', '.join(sorted(_ALLOWED_KEYS))}"
        )

    merged: dict[str, Any] = {
        "windows": dict(base.get("windows") or DEFAULT_WINDOWS),
        "min_events": dict(base.get("min_events") or DEFAULT_MIN_EVENTS),
        "count_only_external": base.get("count_only_external", DEFAULT_COUNT_ONLY_EXTERNAL),
    }

    for key, value in patch.items():
        if key in ("windows", "min_events"):
            if not isinstance(value, Mapping):
                raise InvalidRules(
                    f"{key} must be a JSON object of integers; got {type(value).__name__}"
                )
            unknown_sub = sorted(set(value) - set(merged[key]))
            if unknown_sub:
                allowed = ", ".join(sorted(merged[key]))
                raise InvalidRules(
                    f"unknown {key} key(s) {', '.join(unknown_sub)}; expected {allowed}"
                )
            merged[key].update(value)
        else:
            merged[key] = value

    return validate(merged)


def validate(rules: Mapping[str, Any]) -> dict[str, Any]:
    """Check a complete rules mapping, or say precisely what is wrong with it.

    The ordering constraint is the only one that is not a type check, and it is
    worth stating why it is not load-bearing: the ladder is total for any windows
    at all, because its last two branches are recency tests that do not read the
    windows. Ordering is enforced because an unordered configuration reads as a
    mistake to whoever set it, and a "Hot window" of 30 days that is wider than
    the "Cold window" is not a health model anyone means.
    """
    windows = rules.get("windows")
    if not isinstance(windows, Mapping):
        raise InvalidRules("windows must be a JSON object with hot, warm and cold")

    for key in WINDOW_KEYS:
        if key not in windows:
            raise InvalidRules(
                f"windows.{key} is required; the researched windows are 7, 14 and 30 days"
            )
        value = windows[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRules(f"windows.{key} must be a number of days; got {value!r}")
        if int(value) < 1:
            raise InvalidRules(f"windows.{key} must be at least 1 day; got {value!r}")

    hot, warm, cold = (int(windows[key]) for key in WINDOW_KEYS)
    if not hot < warm < cold:
        raise InvalidRules(
            f"windows must increase from hot to cold so the ladder stays ordered; got "
            f"{hot}/{warm}/{cold} days"
        )

    floors = rules.get("min_events")
    if not isinstance(floors, Mapping):
        raise InvalidRules("min_events must be a JSON object with hot and warm")
    for key in ("hot", "warm"):
        if key not in floors:
            raise InvalidRules(f"min_events.{key} is required")
        value = floors[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRules(f"min_events.{key} must be a whole number of events; got {value!r}")
        # A count of events is a whole number; 2.5 events is not a threshold
        # anyone means, and silently truncating it would hide the mistake behind
        # a number that looks deliberate. A window may be fractional - 6.5 days
        # is a threshold someone could genuinely want - so only the floors are
        # held to whole numbers.
        if float(value) != int(value):
            raise InvalidRules(f"min_events.{key} must be a whole number of events; got {value!r}")
        if int(value) < 1:
            raise InvalidRules(
                f"min_events.{key} must be at least 1; a floor of 0 would make the bucket "
                "unreachable rather than always true"
            )

    if not isinstance(rules.get("count_only_external"), bool):
        raise InvalidRules("count_only_external must be true or false")

    return {
        "windows": {key: int(windows[key]) for key in WINDOW_KEYS},
        "min_events": {key: int(floors[key]) for key in ("hot", "warm")},
        "count_only_external": bool(rules["count_only_external"]),
    }


def effective(record: Mapping[str, Any] | None) -> tuple[dict[str, Any], str]:
    """The rules in force, and whether they came from the defaults or a record.

    A stored override that has become invalid - someone typed it, or a future
    change narrowed the schema - falls back to the defaults rather than raising
    in the middle of a dashboard read. The stored copy is still served as
    ``stored`` so the mismatch is visible instead of being papered over.
    """
    if not record:
        return validate(DEFAULT_RULES), "defaults"
    try:
        merged = merge(DEFAULT_RULES, {k: v for k, v in record.items() if k in _ALLOWED_KEYS})
    except InvalidRules:
        return validate(DEFAULT_RULES), "defaults"
    return merged, "override"


def describe(rules: Mapping[str, Any], origin: str) -> dict[str, Any]:
    """The rules as the API serves them, next to where they came from."""
    return {
        "rules": dict(rules),
        "source": origin,
        "record_id": RULES_RECORD_ID,
        "collection": RULES_COLLECTION,
    }


__all__ = [
    "DEFAULT_RULES",
    "DEFAULT_WINDOWS",
    "DEFAULT_MIN_EVENTS",
    "DEFAULT_COUNT_ONLY_EXTERNAL",
    "merge",
    "validate",
    "effective",
    "describe",
]
