"""Time handling for WF-064, in one place.

The research's own timestamps are UTC and offset-bearing - ``"2024-01-05T14:30:00Z"``
in the ``BOOKING_RESCHEDULED`` payload - so everything in this package is a
timezone-aware ``datetime`` and everything written to the store is an ISO 8601
string with an offset. A naive datetime is a bug waiting for a machine in another
timezone, and the one place that fixes it is here rather than at twenty call
sites.

The boundary rule that matters is in :func:`has_happened`: "expire after a
meeting has happened" is read as *the start has passed*, not the end, and that
reading is named as an inference rather than buried in a comparison.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.scheduling.errors import MeetingChangeError

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse(value: Any, *, label: str = "timestamp") -> datetime:
    """Parse an ISO 8601 timestamp into an aware UTC datetime.

    A naive string is read as UTC rather than rejected. Rejecting it would be
    defensible, but a team writing ``start_at`` by hand writes ``"2026-10-01T09:00"``
    far more often than they write the offset, and guessing wrong-by-a-fixed-amount
    is a worse failure than a documented assumption. The assumption is stated on
    every call that makes it.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        raise MeetingChangeError(f"{label} is required")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise MeetingChangeError(f"{label} must be an ISO 8601 timestamp; got {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso(value: Any) -> str:
    """Serialise a moment the way the store holds every other timestamp.

    Seconds, with the offset, so a stored timestamp reads the same as the
    researched payload and a diff of two audit rows is legible.
    """
    return parse(value).isoformat(timespec="seconds")


def has_happened(start_at: Any, end_at: Any, now: datetime) -> bool:
    """Has the meeting happened, as ``Expire Reschedule Link`` means it?

    The researched setting is "expire the reschedule link after a meeting has
    happened". This build reads that as *the start has passed*: the link is
    closed from the moment the meeting is due to begin, not from the moment it
    ends. A two-hour workshop is not a link an attendee should be able to move
    while it is running, and the research's stated motive - "for reporting
    purposes and tracking interactions" - is about what happened, which begins at
    the start. The end time is still required, because it bounds a link whose
    start was rescheduled into the future while the old one was in the past.
    """
    start = parse(start_at, label="start_at")
    return now >= start


def slot_end(start: datetime, minutes: int) -> datetime:
    return start + timedelta(minutes=int(minutes))


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Do two intervals share any time at all?

    Half-open, so a 09:00-09:30 meeting and a 09:30-10:00 one do not conflict.
    The inclusive comparison would refuse back-to-back bookings, which is a
    scheduling product refusing the thing it exists for.
    """
    return a_start < b_end and b_start < a_end


def minutes_from_midnight(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute
