"""Instants, in one place, so a deferral is a number and not a formatting habit.

Every write in this package records when it happened, and every deferral records
when the room will try again. Both need the same three things: an ISO string that
sorts lexicographically, a parse that treats a naive value as UTC rather than
guessing, and a ``plus_seconds`` that keeps the arithmetic in one line.

The vocabulary is deliberately small. A module that needed four date helpers
would be a module whose dates disagreed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    """The current instant, in UTC."""
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """An ISO-8601 string with seconds precision.

    Seconds precision is not laziness. Two deferrals in the same millisecond
    would otherwise tie, and the store's ordering has to break that tie by
    insertion order rather than by a value that says two different instants were
    the same.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_instant(value: Any) -> datetime | None:
    """Read an instant out of a record, or ``None`` when there is not one there.

    A naive value is read as UTC rather than as local time. The room writes UTC,
    so a naive value is either a hand-written fixture or a record from another
    writer; treating it as local would make the same row defer for a different
    length of time depending on the machine that read it.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def plus_seconds(moment: datetime, seconds: float) -> datetime:
    """``moment`` moved forward by ``seconds``, as a whole number of seconds.

    Whole seconds because the vendors' own advice is in whole seconds, and a
    deferral of 29.9997s is a deferral of 30s that somebody will read as a bug.
    """
    return moment + timedelta(seconds=int(seconds))


__all__ = ["utcnow", "iso", "parse_instant", "plus_seconds"]
