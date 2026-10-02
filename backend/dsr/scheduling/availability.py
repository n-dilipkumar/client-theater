"""Availability, recomputed - and the one researched rule about it.

Step 2 of the flow says a reschedule "re-opens the same Distribution/Meeting Type
context; availability is recomputed". That recomputation is the reason this module
exists, and so is the one sentence the research gives about how it behaves:

    ``GET /v2/slots?...&bookingUidToReschedule=abc123def456`` - "will ensure that
    the original booking time appears within the returned available slots when
    rescheduling."

So the exclusion this product would otherwise make - "this slot is taken by a
booking, therefore it is not available" - is suspended for exactly one booking:
the one being rescheduled. Without that suspension the attendee could move a
meeting to any time except its own, which is the opposite of what the parameter
is for. Every implementation of a scheduling product would get this wrong by
omission, because "the original booking time appears as available" is a sentence
that reads like a detail until you try to reschedule a meeting and find its own
time missing.

The slot itself is reported as ``original_slot: true`` with the uid it belongs to,
so a caller can tell "your own time, come back to it" from "a genuinely new time"
without a second request.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.scheduling.errors import MeetingChangeError
from dsr.scheduling.timeutil import UTC, iso, overlaps, slot_end

#: How many days a single availability read may span, and how many slots it may
#: return. Both bounded because the endpoint takes a date range from a query
#: string and an unbounded one is an easy way to make a page unusable.
MAX_RANGE_DAYS = 120
MAX_SLOTS = 200

#: The default range when a caller names none: the next fortnight, which is the
#: horizon a rep rescheduling next week's meeting is working in.
DEFAULT_RANGE_DAYS = 14


def _bounds(from_at: Any, to_at: Any) -> tuple[datetime, datetime]:
    start = from_at if isinstance(from_at, datetime) else datetime.fromisoformat(str(from_at))
    end = to_at if isinstance(to_at, datetime) else datetime.fromisoformat(str(to_at))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return start.astimezone(UTC), end.astimezone(UTC)


def _day_start(day: datetime) -> datetime:
    return day.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def slot_view(booking: Mapping[str, Any]) -> dict[str, Any] | None:
    """A booking as the availability computation sees it.

    ``None`` for a row without usable times, so a half-populated booking narrows
    availability by accident - which would make a reschedule fail for a reason
    the caller cannot see. A booking with no times holds no slot, so it must not
    block one either.
    """
    start = booking.get("start_at")
    end = booking.get("end_at")
    if not start or not end:
        return None
    return {
        "uid": str(booking.get("uid") or booking.get("id") or ""),
        "start_at": start,
        "end_at": end,
        "status": booking.get("status"),
    }


def available_slots(
    *,
    host_email: str,
    window: tuple[int, int, list[int]],
    duration_minutes: int,
    existing: Sequence[Mapping[str, Any]],
    from_at: datetime,
    to_at: datetime,
    booking_uid_to_reschedule: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every slot the host is free in, with the researched exception applied.

    ``existing`` is the host's live bookings. A slot is unavailable when it
    overlaps one of them - unless that booking's uid is
    ``booking_uid_to_reschedule``, which is the whole point of the parameter.
    """
    start_bound, end_bound = _bounds(from_at, to_at)
    if end_bound <= start_bound:
        raise MeetingChangeError("the availability range must end after it starts")
    if (end_bound - start_bound) > timedelta(days=MAX_RANGE_DAYS):
        raise MeetingChangeError(
            f"the availability range may span at most {MAX_RANGE_DAYS} days; "
            "ask for a shorter range"
        )

    window_start, window_end, days = window
    step = timedelta(minutes=int(duration_minutes))
    if step.total_seconds() <= 0:
        raise MeetingChangeError("duration_minutes must be positive")

    held: list[tuple[datetime, datetime, str]] = []
    for booking in existing:
        view = slot_view(booking)
        if view is None:
            continue
        try:
            held.append(
                (
                    datetime.fromisoformat(str(view["start_at"])),
                    datetime.fromisoformat(str(view["end_at"])),
                    view["uid"],
                )
            )
        except ValueError as exc:
            raise MeetingChangeError(
                f"booking {view['uid'] or '?'} has an unreadable start_at or end_at: {exc}"
            ) from exc

    exclude_uid = str(booking_uid_to_reschedule or "").strip()
    reference = now or start_bound
    slots: list[dict[str, Any]] = []
    day = _day_start(start_bound)
    last_day = _day_start(end_bound)

    while day <= last_day and len(slots) < MAX_SLOTS:
        if day.weekday() in days:
            cursor = day + timedelta(minutes=window_start)
            window_close = day + timedelta(minutes=window_end)
            while cursor + step <= window_close and len(slots) < MAX_SLOTS:
                close = cursor + step
                if cursor >= start_bound and close <= end_bound:
                    conflict = next(
                        (
                            uid
                            for held_start, held_end, uid in held
                            if overlaps(cursor, close, held_start, held_end)
                        ),
                        None,
                    )
                    is_original = bool(exclude_uid) and conflict == exclude_uid
                    if conflict is None or is_original:
                        slots.append(
                            {
                                "host_email": host_email,
                                "start_at": iso(cursor),
                                "end_at": iso(close),
                                "duration_minutes": int(duration_minutes),
                                "original_slot": is_original,
                                "replaces_uid": exclude_uid if is_original else None,
                                "in_past": cursor < reference,
                            }
                        )
                cursor += step
        day += timedelta(days=1)

    return slots


def find_slot(slots: Sequence[Mapping[str, Any]], start_at: str) -> dict[str, Any] | None:
    """The slot beginning exactly at ``start_at``, or ``None``.

    Exact match on the instant rather than a containment test, so a reschedule to
    09:15 of a 09:00-09:30 slot is refused instead of quietly rounded. A caller
    that means a different slot has to name a different time.
    """
    target = str(start_at)
    for slot in slots:
        if str(slot.get("start_at")) == target:
            return dict(slot)
    return None


def explain_missing(
    slots: Sequence[Mapping[str, Any]],
    start_at: str,
    *,
    booking_uid_to_reschedule: str | None = None,
) -> str:
    """Why a requested time is not on offer, in a sentence a rep can act on.

    "No such slot" on its own is a dead end for someone who typed 14:17. The
    three cases are the three that actually happen: outside the meeting type's
    hours, already taken by another booking, or in the past.
    """
    if find_slot(slots, start_at) is not None:
        return "the requested time is available"
    text = str(start_at)
    for slot in slots:
        if str(slot.get("start_at")) > text:
            following = slot
            break
    else:
        following = None

    if following is None:
        return (
            f"{text} is outside the meeting type's available hours, or past the end of the "
            "range that was searched"
        )
    if following.get("in_past"):
        return f"{text} is in the past; a meeting cannot be booked into it"
    return (
        f"{text} is not a slot on offer: it is not aligned to the meeting type's hours, or it "
        f"overlaps a booking that is not being rescheduled. The next open slot is "
        f"{following.get('start_at')}"
        + (
            f", which is the original booking time for {booking_uid_to_reschedule}"
            if following.get("original_slot")
            else ""
        )
    )


def slot_end_for(start_at: str, duration_minutes: int) -> str:
    """The end of a slot, for a caller that has a start and a length."""
    start = datetime.fromisoformat(str(start_at))
    return iso(slot_end(start, duration_minutes))
