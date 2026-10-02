"""Reading the instants a sync log is stamped with.

The same rule as :mod:`dsr.trend_health.timestamps`, for the same reason and
independently: a sync row stored with a fabricated time cannot be aged, cannot be
scheduled for retry, and cannot be told apart from a row the connector really
refused, so an unreadable instant is refused with the value that could not be
read rather than replaced with now.

The one thing this module adds is a notion this workflow needs and the trend
health workflow does not: an instant can be in the future, because a row carries
``next_retry_at`` and a connector may hand the room a batch whose rows are
scheduled for later. Forward tolerance is therefore wider here, and it is
published as an inference rather than buried in a number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.partial_failures.errors import InvalidPayload

#: How far ahead of the server clock a row's own instant may be stamped and still
#: be stored. Deliberately generous, because the researched retry queue schedules
#: work forward on purpose: a row whose third attempt is due in half an hour is
#: stamped half an hour from now and that is a correct answer, not clock skew.
#: See the ``future-tolerance`` inference.
FUTURE_TOLERANCE_SECONDS = 7 * 24 * 3600

#: The keys an instant may arrive under, in order of preference. The researched
#: camelCase first, because that is what HubSpot's and Salesforce's payloads use.
TIMESTAMP_KEYS: tuple[str, ...] = ("occurredAt", "occurred_at", "at", "timestamp", "finishedAt")


def parse_instant(value: Any, *, required: bool = True) -> datetime | None:
    """Parse one instant into an aware UTC datetime, or refuse it.

    Accepts an ISO-8601 string with or without a ``Z``, a space separator, a date
    on its own (midnight UTC), or a POSIX epoch in seconds or milliseconds.
    Anything else raises :class:`InvalidPayload` naming the value, which is the
    difference between a connector that can fix its payload and a bug report
    three weeks later.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, bool) or value is None:
        parsed = None
    elif isinstance(value, (int, float)):
        parsed = _from_epoch(float(value))
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = _from_epoch(_looks_like_epoch(text))

    if parsed is None:
        if not required:
            return None
        raise InvalidPayload(
            f"{value!r} is not an instant this workflow can read; use ISO-8601 "
            "(2026-09-27T12:00:00Z) or an epoch in seconds"
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_not_ahead(
    when: datetime, *, now: datetime, tolerance: int = FUTURE_TOLERANCE_SECONDS
) -> None:
    """Refuse an instant further ahead than the forward tolerance allows."""
    ahead = (when - now).total_seconds()
    if ahead > tolerance:
        raise InvalidPayload(
            f"an instant is {int(ahead)}s in the future, beyond the {tolerance}s forward "
            "tolerance; a retry schedule that far out is a configuration mistake, not a schedule"
        )


def iso(moment: datetime | None) -> str | None:
    """One instant, one spelling, everywhere: seconds precision, explicit offset."""
    if moment is None:
        return None
    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return aware.isoformat(timespec="seconds")


def plus_seconds(moment: datetime, seconds: float) -> datetime:
    return moment + timedelta(seconds=float(seconds))


def _from_epoch(number: float) -> datetime | None:
    # Anything past year 5138 in seconds is a millisecond stamp. Ten seconds of
    # slack so a caller is not off by a rounding error.
    if number <= 0:
        return None
    if number > 100_000_000_000:
        number = number / 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _looks_like_epoch(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0.0


__all__ = [
    "FUTURE_TOLERANCE_SECONDS",
    "TIMESTAMP_KEYS",
    "parse_instant",
    "check_not_ahead",
    "iso",
    "plus_seconds",
]
