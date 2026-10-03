"""The alert rules of WF-049, and the evaluation that fires them.

The researched automation: *"Quota polling on a fixed interval; alert rules
fire when remaining budget crosses a threshold or when the change-stream lag
exceeds N seconds."* Two families, two verbs, and the difference matters:

* a **budget** rule watches one half of the normalised quota pair and fires
  when remaining budget drops *below* its threshold (a percentage, or an
  absolute count);
* a **lag** rule watches the change-stream lag and fires when it *exceeds* its
  threshold, in seconds.

The channels are the research's own list: "room alert rules
(Slack/email/webhook)". The room does not *deliver* to Slack or email here -
this product holds no outbound credentials - so a fire is a record with the
channel list on it, and the webhook channel is the seam a delivery
integration consumes.

Evaluation runs on read. Nothing here is a background job, for the same reason
WF-021's classification recomputes on read: a monitor that only a timer can
see is a monitor nobody can debug, and the room's dashboard evaluates every
time it is loaded, which is exactly what "Quota polling on a fixed interval"
means from the room's side.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.integ_monitor.errors import InvalidRule
from dsr.integ_monitor.health import check_lag
from dsr.integ_monitor.timestamps import iso, parse_instant
from dsr.integ_monitor.vocabulary import CHANNELS, METRIC_COMPARISON, METRICS

#: How long a rule stays quiet after it fires, in minutes.
#:
#: Not sourced. The research says a rule fires when a threshold is crossed and
#: says nothing about the second, third or fortieth poll that sees the same
#: crossing. Without a cooldown, a rule watching a starved connector fires on
#: every poll for as long as the connector stays starved - which turns "we
#: have an alerting problem" into "we have a notification-spam problem", and
#: trains the operator to mute the room. Crossings, not states, fire.
DEFAULT_COOLDOWN_MINUTES = 30

#: The reading this build considers "starved" when a rule ships without one.
DEFAULT_BUDGET_THRESHOLD_PCT = 20.0

#: The reading this build considers a stalled change stream, in seconds, when
#: a rule ships without one.
DEFAULT_LAG_THRESHOLD_SECONDS = 300.0

#: How many fires one rule keeps in its own history.
#: A rule whose history grows without bound grows a record without bound, and
#: the audit log already carries the growth. The cap and the flag make the
#: loss visible. Not sourced.
FIRE_HISTORY_LIMIT = 25


def check_rule(payload: Mapping[str, Any]) -> dict[str, Any]:
    """An alert rule the room can actually evaluate, or a refusal naming why.

    Every field the evaluator later reads is validated here rather than
    defaulted silently: a rule stored with a metric the room does not watch,
    a channel the room does not speak, or a threshold that is not a number is
    a rule that *looks* armed and never fires, which is worse than a refused
    rule the operator is still looking at.
    """
    metric = str(payload.get("metric") or "").strip()
    if metric not in METRICS:
        raise InvalidRule(
            f"metric must be one of {', '.join(METRICS)}; got {metric!r}. A rule watching a "
            "metric this room does not compute would never fire."
        )
    threshold = payload.get("threshold")
    if threshold in (None, ""):
        raise InvalidRule(
            f"threshold is required: a {metric} rule fires when the value is "
            f"{METRIC_COMPARISON[metric]} a number, and there is no number"
        )
    try:
        value = float(threshold)
    except (TypeError, ValueError) as exc:
        raise InvalidRule(f"threshold must be a number; got {threshold!r}") from exc
    if value < 0:
        raise InvalidRule("threshold cannot be negative")

    channels = payload.get("channels")
    if not channels:
        raise InvalidRule(
            f"channels must include at least one of {', '.join(CHANNELS)}; a rule with no "
            "channel fires into nowhere"
        )
    if not isinstance(channels, list):
        raise InvalidRule("channels must be a list")
    unknown = [str(c) for c in channels if str(c) not in CHANNELS]
    if unknown:
        raise InvalidRule(
            f"channel(s) {', '.join(unknown)} are not ones this room speaks ({', '.join(CHANNELS)})"
        )

    cooldown = payload.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
    try:
        cooldown = float(cooldown)
    except (TypeError, ValueError) as exc:
        raise InvalidRule(f"cooldown_minutes must be a number; got {cooldown!r}") from exc
    if cooldown < 0:
        raise InvalidRule("cooldown_minutes cannot be negative")

    return {
        "metric": metric,
        "comparison": METRIC_COMPARISON[metric],
        "threshold": value,
        "channels": [str(c) for c in channels],
        "cooldown_minutes": cooldown,
        "enabled": bool(payload.get("enabled", True)),
        "vendor": str(payload.get("vendor") or "") or None,
    }


def merge_rule(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """A partial patch onto an existing rule, re-validated as a whole.

    A patch is merged before it is validated so that disabling a rule cannot
    be refused for reasons that only apply to the fields being changed, and
    validated *after* the merge so that the stored rule is always one the
    evaluator can run.
    """
    merged = dict(current)
    merged.pop("comparison", None)  # derived from metric, never stored independently
    for key in ("metric", "threshold", "channels", "cooldown_minutes", "enabled", "vendor"):
        if key in patch:
            merged[key] = patch[key]
    return check_rule(merged)


def evaluate_rules(
    rules: Sequence[Mapping[str, Any]],
    values: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Every rule against every current reading, with what fired and why.

    ``values`` are the connector-level readings the dashboard already computed:
    one per connector per metric, ``{"connector_id", "label", "metric",
    "value", "known"}``. A rule fires when its metric's value crosses its
    threshold on *any* connector it is not scoped away from, and the fire
    names the connector it is about - an alert that says "quota is low"
    without saying whose is an alert the operator has to re-do.

    Suppression is per rule, not per connector: the rule's ``last_fired_at``
    is what the cooldown reads. ``fired_at`` on the returned entry carries the
    instant this evaluation chose to fire, which is the instant the delivery
    integration should stamp.
    """
    now = now or datetime.now(timezone.utc)
    evaluated: list[dict[str, Any]] = []
    for rule in rules:
        data = rule.get("data") if isinstance(rule, Mapping) and "data" in rule else rule
        rule_id = rule.get("id") if isinstance(rule, Mapping) else None
        if not data.get("enabled", True):
            evaluated.append({
                "rule_id": rule_id,
                "metric": data.get("metric"),
                "fired": False,
                "suppressed": False,
                "reason": "disabled",
            })
            continue
        wanted_vendor = data.get("vendor")
        in_scope = [
            value
            for value in values
            if value.get("metric") == data.get("metric")
            and value.get("known")
            and (not wanted_vendor or value.get("vendor") == wanted_vendor)
        ]
        candidates = [
            value for value in in_scope if _crosses(data, float(value["value"]))
        ]
        worst = (
            min(candidates, key=lambda value: float(value["value"]))
            if data.get("comparison") == "below"
            else max(candidates, key=lambda value: float(value["value"]))
        ) if candidates else None

        last_fired = parse_instant(data.get("last_fired_at"))
        cooldown = timedelta(minutes=float(data.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)))
        quiet_until = last_fired + cooldown if last_fired else None
        suppressed = quiet_until is not None and now < quiet_until

        entry: dict[str, Any] = {
            "rule_id": rule_id,
            "metric": data.get("metric"),
            "comparison": data.get("comparison"),
            "threshold": data.get("threshold"),
            "channels": data.get("channels") or [],
            "in_scope": len(in_scope),
            "worst_value": worst.get("value") if worst else None,
            "worst_connector": worst.get("label") if worst else None,
            "worst_connector_id": worst.get("connector_id") if worst else None,
            "fired": bool(worst) and not suppressed,
            "suppressed": bool(worst) and suppressed,
        }
        if worst is None:
            entry["reason"] = (
                f"no {data.get('metric')} reading is {data.get('comparison')} "
                f"{data.get('threshold')}"
                if in_scope
                else f"no {data.get('metric')} reading is available"
            )
        elif suppressed:
            entry["reason"] = (
                f"crossed, but the rule fired at {data.get('last_fired_at')} and its cooldown "
                f"runs to {iso(quiet_until)}"
            )
        else:
            entry["reason"] = (
                f"{worst.get('label')}: {worst.get('metric')} is {worst.get('value')}, "
                f"{data.get('comparison')} {data.get('threshold')}"
            )
            entry["fired_at"] = iso(now)
        evaluated.append(entry)

    return {
        "evaluated_at": iso(now),
        "cooldown_default_minutes": DEFAULT_COOLDOWN_MINUTES,
        "rules": evaluated,
        "fired": [entry for entry in evaluated if entry.get("fired")],
    }


def _crosses(rule: Mapping[str, Any], value: float) -> bool:
    threshold = float(rule.get("threshold") or 0)
    if rule.get("comparison") == "below":
        return value < threshold
    return value > threshold


def record_fire(
    data: Mapping[str, Any], entry: Mapping[str, Any], *, moment: datetime | None = None
) -> dict[str, Any]:
    """The rule data after a fire, with the fire kept in its own history.

    Written back onto the rule by the engine when :meth:`evaluate` actually
    fired, so the cooldown and the history are the same fact and a rule that
    never fired has no fire in it.
    """
    moment = moment or datetime.now(timezone.utc)
    fires = list(data.get("fires") or [])
    fires.append(
        {
            "fired_at": iso(moment),
            "connector_id": entry.get("worst_connector_id"),
            "connector": entry.get("worst_connector"),
            "metric": data.get("metric"),
            "value": entry.get("worst_value"),
            "threshold": data.get("threshold"),
            "channels": list(data.get("channels") or []),
        }
    )
    truncated = False
    while len(fires) > FIRE_HISTORY_LIMIT:
        fires = fires[1:]
        truncated = True
    return {
        "last_fired_at": iso(moment),
        "fires": fires,
        "fires_truncated": truncated,
        "fire_count": int(data.get("fire_count") or 0) + 1,
    }


def metric_value(metric: str, *, daily: Mapping[str, Any] | None, window: Mapping[str, Any] | None,
                 lag_seconds: float | None = None) -> float | None:
    """The reading one metric asks of one connector's current state.

    Budget metrics are percentages when a maximum is known and counts when it
    is not, because a percentage against a guessed maximum would be authoritative
    noise - and both shapes are documented in ``METRIC_UNITS`` so the dashboard
    and the rule editor can say which they are showing.
    """
    if metric == "stream_lag":
        return lag_seconds
    reading = daily if metric == "daily_remaining" else window
    if not reading or not reading.get("known"):
        return None
    if reading.get("remaining_pct") is not None:
        return float(reading["remaining_pct"])
    if reading.get("remaining") is not None:
        return float(reading["remaining"])
    return None


#: The unit each metric reads in, when the maximum is known (percentage) and
#: when it is not (count). Published so a rule editor can label its threshold.
METRIC_UNITS = {
    "daily_remaining": "percent or count",
    "window_remaining": "percent or count",
    "stream_lag": "seconds",
}


def now_utc() -> datetime:
    """The clock the room evaluates on. Injectable in the engine, not here."""
    return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_COOLDOWN_MINUTES",
    "DEFAULT_BUDGET_THRESHOLD_PCT",
    "DEFAULT_LAG_THRESHOLD_SECONDS",
    "FIRE_HISTORY_LIMIT",
    "METRIC_UNITS",
    "check_rule",
    "merge_rule",
    "evaluate_rules",
    "record_fire",
    "metric_value",
    "now_utc",
]
