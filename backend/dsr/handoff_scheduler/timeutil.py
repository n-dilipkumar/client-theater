"""Time helpers for WF-055.

Every timestamp this package handles is an aware UTC datetime. The package has no
external calendar: an AE's availability arrives as free/busy blocks on their user
row inside the workspace, so the only arithmetic needed is turning an interval and
a duration into the grid of instants a meeting may start at.

The grid is aligned to the start of the interval rather than to midnight, so a
handoff whose interval begins at 09:30 offers 09:30, 10:00, 10:30 and so on.
Aligning to midnight instead would silently drop the first half hour of any
interval that does not begin on the hour, and an SDR would be told the AE is
unavailable at a time the AE is free.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def utcnow() -> datetime:
    """The current instant, aware and in UTC."""
    return datetime.now(UTC)


def parse(value: Any) -> datetime:
    """Read an ISO-8601 timestamp into an aware UTC datetime.

    A naive timestamp is read as UTC rather than as local time. A workspace
    declared in one timezone and read in another must produce the same instants,
    and guessing the host's local zone would make the same workspace offer
    different slots on two machines.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("a timestamp is required")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def iso(moment: datetime) -> str:
    """Render a datetime as an ISO-8601 string with an explicit UTC offset."""
    return moment.astimezone(UTC).isoformat()


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """True when two half-open intervals share any instant.

    Half-open, so a 09:00-09:30 meeting and a 09:30-10:00 meeting do not
    overlap. A handoff routinely books an AE back to back, and a closed interval
    on both ends would report every two consecutive slots as a conflict.
    """
    return start_a < end_b and start_b < end_a


def is_free(blocks: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    """True when no busy block covers any part of ``[start, end)``."""
    return not any(overlaps(start, end, busy_start, busy_end) for busy_start, busy_end in blocks)


def grid(
    start: datetime, end: datetime, duration_minutes: int, limit: int
) -> list[tuple[datetime, datetime]]:
    """The instants a meeting of ``duration_minutes`` may begin at.

    Bounded by ``limit`` because the interval arrives in a request body and an
    unbounded one is an easy way to make a page unusable. A slot is only emitted
    when it fits entirely inside the interval, so the last partial slot is not
    offered.
    """
    step = timedelta(minutes=int(duration_minutes))
    if step.total_seconds() <= 0:
        raise ValueError("duration_minutes must be positive")
    slots: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor + step <= end and len(slots) < limit:
        slots.append((cursor, cursor + step))
        cursor += step
    return slots
