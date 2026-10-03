"""Instant helpers for WF-049, kept beside the code that uses them.

Duplicated per package by convention here - :mod:`dsr.partial_failures` and
:mod:`dsr.trend_health` each carry their own - so a workflow that needs a
different tolerance for a slightly-future timestamp can set one without
negotiating with every other workflow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

#: A timestamp this far into the future is treated as a caller's clock skew
#: rather than a lie. A reading stamped six seconds ahead of the room's clock
#: still happened; one stamped a day ahead cannot.
FUTURE_TOLERANCE_SECONDS = 30

#: The keys a caller may use to stamp *when* an observation was taken.
TIMESTAMP_KEYS = ("observed_at", "at", "timestamp")


def parse_instant(value: Any) -> datetime | None:
    """An ISO instant, or ``None`` when nothing readable is there.

    Naive timestamps are read as UTC: a vendor that omits the offset from its
    logs is more plausibly in UTC than in whatever timezone the room's host
    happens to sit in.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def first_present(payload: Any, keys: tuple[str, ...]) -> Any:
    """The first of *keys* the payload carries with a non-empty value."""
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def observed_at(payload: Any, *, now: datetime | None = None) -> datetime:
    """When an observation happened, and never in the future.

    A reading stamped more than :data:`FUTURE_TOLERANCE_SECONDS` ahead of the
    room's clock is refused rather than clamped: the honest answer to a
    timestamp the room cannot explain is "when?", not "assume now".
    """
    now = now or datetime.now(timezone.utc)
    raw = first_present(payload, TIMESTAMP_KEYS)
    parsed = parse_instant(raw) if raw is not None else None
    moment = parsed or now
    if moment > now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS):
        raise ValueError(f"the observation is stamped {iso(moment)}, ahead of the room's clock")
    return moment


def check_not_ahead(moment: datetime | None, *, now: datetime | None = None) -> None:
    """Refuse a timestamp more than the tolerance ahead of the room's clock."""
    if moment is None:
        return
    now = now or datetime.now(timezone.utc)
    if moment > now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS):
        raise ValueError(f"the timestamp {iso(moment)} is ahead of the room's clock")


def iso(moment: datetime | None) -> str | None:
    """The one instant format everything in this workflow speaks."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "FUTURE_TOLERANCE_SECONDS",
    "TIMESTAMP_KEYS",
    "parse_instant",
    "first_present",
    "observed_at",
    "check_not_ahead",
    "iso",
]
