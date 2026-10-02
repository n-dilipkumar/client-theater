"""The daily allowance, and the window it resets in.

HubSpot is explicit about both halves, which is why this is a module and not a
number in a wizard:

    "The **daily** limit resets at midnight based on your time zone setting."

    "You can also check the number of calls used during the current day using
    this endpoint."

So the window is a *local* midnight, which means two connections in two
time zones have two different windows, and a run sized against the wrong one
would be cut off mid-page. The room therefore stores the window's state per
connection and re-reads it on every call rather than caching a reset time that
would go stale across the boundary.

**A fixed UTC offset, not a named zone.** :class:`zoneinfo` on Windows needs the
IANA database shipped separately, and a deployment without it would get a
backfill that cannot answer a question it exists to answer. A connection
declares ``quota_timezone_offset_hours`` - which is what "your time zone setting"
reduces to for a connection that does not observe daylight saving - and the
register in :mod:`dsr.crm_backfill.inferences` records the simplification.

**A vendor that declares no limit is not unlimited.** A connection without a
``daily_limit`` reports ``limited: false`` and the run proceeds, because the
research only documents a daily limit for HubSpot and inventing one for the
others would be a claim nobody can check.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

#: Every vendor call the room makes costs one of these. Charged by the engine,
#: which is the only thing that knows a call happened.
CALL_REASONS: tuple[str, ...] = (
    "job.create",
    "job.read",
    "page.read",
    "page.submit",
    "quota.read",
)


def window_start(now: datetime, offset_hours: int) -> datetime:
    """The start of the local day ``now`` falls in, as a UTC instant.

    Local midnight at a fixed offset, so the window is half-open:
    ``[window_start, next_window_start)``. A call made exactly at local midnight
    belongs to the new day, which is what "resets at midnight" means.
    """
    local = now.astimezone(timezone.utc) + timedelta(hours=int(offset_hours))
    midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local - timedelta(hours=int(offset_hours))


def next_reset(now: datetime, offset_hours: int) -> datetime:
    """When the allowance refills, as a UTC instant."""
    return window_start(now, offset_hours) + timedelta(days=1)


def offset_for(connection: Mapping[str, Any]) -> int:
    """The connection's own time zone, as a whole-hour offset from UTC."""
    raw = connection.get("quota_timezone_offset_hours")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def limit_for(connection: Mapping[str, Any]) -> int | None:
    """The connection's declared daily limit, or ``None`` when it declares none."""
    raw = connection.get("daily_limit")
    if raw is None or raw == "":
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def report(connection: Mapping[str, Any], used: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """What is left of today's allowance, and when it comes back.

    ``used`` is the quota record the engine keeps on the run's connection: the
    calls charged since the window opened, and the instant the window opened.
    """
    offset = offset_for(connection)
    limit = limit_for(connection)
    started = _parse(used.get("window_started_at")) or window_start(now, offset)
    calls = int(used.get("calls") or 0)
    if started < window_start(now, offset):
        # The window rolled over while nothing was charging it, which is what
        # happens when a run is idle across local midnight. A stale count carried
        # into a new day would spend an allowance nobody has.
        started = window_start(now, offset)
        calls = 0
    remaining = None if limit is None else max(0, limit - calls)
    return {
        "limited": limit is not None,
        "daily_limit": limit,
        "calls_today": calls,
        "remaining": remaining,
        "time_zone_offset_hours": offset,
        "window_started_at": started.isoformat(),
        "resets_at": next_reset(now, offset).isoformat(),
        "hours_to_reset": round((next_reset(now, offset) - now).total_seconds() / 3600.0, 3),
        "quote": "The **daily** limit resets at midnight based on your time zone setting.",
    }


def check(
    connection: Mapping[str, Any],
    used: Mapping[str, Any],
    now: datetime,
    *,
    estimated_calls: int | None,
) -> dict[str, Any]:
    """Whether a run of this size fits in what is left of today.

    Reports the verdict rather than raising, so the engine can put it in the
    run's log and in the response together with everything else it found. The
    refusal itself is raised by the engine, which is the layer that knows what to
    do about it.

    A run whose size cannot be estimated is not refused. The research says the
    limit is "relevant when sizing a backfill against remaining quota" - sizing
    is the requirement, and a plan with no size has nothing to compare. It is
    reported as ``unverifiable`` so a reviewer can see the check did not pass,
    it could not run.
    """
    state = report(connection, used, now)
    if not state["limited"]:
        return {**state, "verdict": "not_limited", "estimated_calls": estimated_calls}
    if estimated_calls is None:
        return {**state, "verdict": "unverifiable", "estimated_calls": None}
    if estimated_calls > int(state["remaining"] or 0):
        return {**state, "verdict": "exceeds_remaining", "estimated_calls": estimated_calls}
    return {**state, "verdict": "fits", "estimated_calls": estimated_calls}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
