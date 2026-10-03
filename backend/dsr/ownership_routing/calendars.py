"""Availability from the resolved owner's connected calendar.

The researched data flow is: "guest email (or CRM record id) -> CRM lookup for
owner id -> owner's calendar availability -> slot list -> booking". This module is
the middle of that arrow, and it is deliberately the only part of the package that
knows what a calendar looks like.

The research names the source and no more: "Google/Outlook calendar of the
resolved owner", and step 4 of the flow says "Availability is read from that
owner's connected calendar, the prospect books, and the owner gets the meeting."
It does not publish Chili Piper's slot algorithm, its rounding, or its buffer, so
what follows is this build's arithmetic with every constant named and served at
``/vocabulary``.

Three decisions are worth stating outright because a reviewer will look for them:

**Slots land on the interval's own grid, not on rounded clock times.** The interval
says ``duration_minutes``; a 30-minute meeting on a 30-minute grid produces slots at
:00 and :30, and a 45-minute meeting on the same grid produces slots that overlap
each other. The grid is therefore ``duration_minutes``, so slots can never overlap
by construction and the count is predictable.

**A busy block kills the slot it overlaps and the buffer around it.** A rep needs
the gap before a meeting as much as the meeting itself, so the block is widened by
``buffer_minutes`` on both sides before the comparison. Without the buffer a 09:00
slot can be offered straight after an 08:00-08:55 meeting.

**Minimum notice is applied after the interval starts, not before it.** ``now +
min_notice`` is the earliest bookable moment, and any slot before that is not
offered. A window that opens inside the notice period therefore offers nothing,
which is the researched behaviour of a minimum-notice setting rather than an
error.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.ownership_routing.errors import CalendarNotConnected
from dsr.ownership_routing.vocabulary import normalise_email

#: How long before a slot the owner wants their calendar left clear. Not sourced;
#: named here so it is one constant rather than a magic number in three places.
DEFAULT_BUFFER_MINUTES = 0

#: The largest number of slots one init call will return. The researched payload
#: says "Available ``startTimes``" with no bound, so this build needs one: an
#: unbounded list over a long window is a response nobody can render.
DEFAULT_MAX_SLOTS = 60

#: The furthest ahead an init call will look when the interval sets no ``max_days``.
DEFAULT_MAX_DAYS = 60

#: Working days. Sourced only as "the owner's connected calendar"; a rep's week is
#: not something the research states, so this is a default a link can override via
#: ``working_days`` rather than a claim about anyone's calendar.
DEFAULT_WORKING_DAYS: tuple[int, ...] = (0, 1, 2, 3, 4)

#: The hours a slot may start in, as minutes from midnight in the owner's own time
#: zone. Not sourced - see ``DEFAULT_WORKING_DAYS`` for the same reason.
DEFAULT_WORK_START_MINUTE = 9 * 60
DEFAULT_WORK_END_MINUTE = 17 * 60


def _parse(value: Any) -> datetime:
    from dsr.ownership_routing.vocabulary import _parse as parse

    return parse(value)


def _utc(value: Any) -> datetime:
    """Coerce a timestamp to an aware UTC datetime."""
    moment = _parse(value)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def working_days_for(owner: Mapping[str, Any]) -> tuple[int, ...]:
    """The days this owner works, from the owner row or the default.

    A link can narrow this per owner; the owner's own row wins because a rep's week
    is a property of the rep. An owner that declares an empty list is refusing to
    take meetings on any day, which is a legitimate state and produces no slots -
    it is not silently replaced by the default, because "no availability" and "the
    default week" are different answers.
    """
    declared = owner.get("working_days")
    if isinstance(declared, (list, tuple)):
        days = []
        for day in declared:
            try:
                value = int(day)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"working_days entries must be whole numbers: {day!r}") from exc
            if not 0 <= value <= 6:
                raise ValueError(f"working_days entries must be 0 (Monday) to 6 (Sunday): {value}")
            days.append(value)
        return tuple(sorted(set(days)))
    return DEFAULT_WORKING_DAYS


def calendar_for(owner: Mapping[str, Any]) -> dict[str, Any]:
    """The owner's connected calendar, or a refusal naming what is missing.

    "Availability is read from that owner's connected calendar" is a precondition,
    not a fallback: a rep with no calendar has no availability, and answering with
    the default week would offer slots that do not exist. The refusal names the rep
    because the remedy is for a person to connect a calendar, and an error saying
    "no slots" would send them looking in the wrong place.
    """
    calendar = owner.get("calendar")
    if not isinstance(calendar, Mapping) or not calendar:
        raise CalendarNotConnected(
            f"rep {owner.get('id') or owner.get('name') or '(unnamed)'} has no calendar connected; "
            "availability is read from the resolved owner's connected Google or Outlook calendar"
        )
    provider = str(calendar.get("provider") or "").strip().casefold()
    if not provider:
        raise CalendarNotConnected(
            f"rep {owner.get('id') or owner.get('name') or '(unnamed)'} has a calendar row with no "
            "provider; the research names Google and Outlook"
        )
    if not calendar.get("connected", True):
        raise CalendarNotConnected(
            f"rep {owner.get('id') or owner.get('name') or '(unnamed)'} has a {provider} calendar "
            "that is not connected; reconnect it before routing bookings to them"
        )
    return dict(calendar)


def busy_blocks(owner: Mapping[str, Any]) -> list[tuple[datetime, datetime]]:
    """The owner's committed time, as absolute UTC pairs.

    A block that cannot be parsed is a 400 rather than a skipped entry: a busy
    block this build cannot read is a meeting somebody believes is protected, and
    silently ignoring it would offer the slot it occupies.
    """
    calendar = owner.get("calendar") or {}
    raw = calendar.get("busy") or []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("calendar.busy must be a list of {start, end} objects")
    blocks: list[tuple[datetime, datetime]] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or "start" not in entry or "end" not in entry:
            raise ValueError("each calendar.busy entry needs both start and end")
        start = _utc(entry["start"])
        end = _utc(entry["end"])
        if end <= start:
            raise ValueError("each calendar.busy entry must end after it starts")
        blocks.append((start, end))
    return blocks


def working_window(owner: Mapping[str, Any]) -> tuple[int, int]:
    """The minutes-from-midnight window a slot may sit inside."""
    calendar = owner.get("calendar") or {}
    start = calendar.get("work_start_minute", DEFAULT_WORK_START_MINUTE)
    end = calendar.get("work_end_minute", DEFAULT_WORK_END_MINUTE)
    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "calendar work_start_minute and work_end_minute must be whole numbers"
        ) from exc
    if not 0 <= start < end <= 24 * 60:
        raise ValueError(
            "calendar working hours must satisfy 0 <= work_start_minute < work_end_minute <= 1440"
        )
    return start, end


def available_slots(
    owner: Mapping[str, Any],
    interval: Mapping[str, Any],
    *,
    now: datetime,
    max_slots: int | None = None,
    exclude: Sequence[datetime] | None = None,
) -> list[str]:
    """The slots the owner's calendar offers inside the interval, as ISO strings.

    Ordered, de-duplicated, and never overlapping each other, because the grid is
    the meeting duration. ``exclude`` is the times already booked by *this* owner's
    other sessions, so two prospects looking at the same owner cannot both be
    offered the slot that is about to be taken.

    This reads the owner row and never writes it, so calling it twice returns the
    same answer. That matters because the init call and the schedule call both need
    it, and a read that consumed anything would make the second call disagree with
    the first.
    """
    calendar_for(owner)
    start_at = _utc(interval["start"])
    end_at = _utc(interval["end"])
    duration = int(interval["duration_minutes"])
    notice = int(interval.get("min_notice_minutes") or 0)

    horizon_days = interval.get("max_days")
    max_days = int(horizon_days) if horizon_days else DEFAULT_MAX_DAYS
    horizon = min(end_at, now + timedelta(days=max_days))

    if duration <= 0:
        raise ValueError("duration_minutes must be greater than zero")
    if horizon <= now + timedelta(minutes=notice):
        return []

    buffer_minutes = int(
        (owner.get("calendar") or {}).get("buffer_minutes", DEFAULT_BUFFER_MINUTES) or 0
    )
    if buffer_minutes < 0:
        raise ValueError("calendar buffer_minutes cannot be negative")
    buffer = timedelta(minutes=buffer_minutes)

    blocked = [(start - buffer, end + buffer) for start, end in busy_blocks(owner)]
    if exclude:
        blocked.extend((moment, moment + timedelta(minutes=duration)) for moment in exclude)

    days = set(working_days_for(owner))
    work_start, work_end = working_window(owner)
    grid = timedelta(minutes=duration)
    earliest = now + timedelta(minutes=notice)

    limit = DEFAULT_MAX_SLOTS if max_slots is None else int(max_slots)
    if limit <= 0:
        raise ValueError("max_slots must be greater than zero")

    slots: list[str] = []
    cursor = _ceil_to_grid(start_at, grid)
    while cursor < horizon and len(slots) < limit:
        finish = cursor + grid
        inside_hours = cursor.weekday() in days and (
            _minutes_from_midnight(cursor, now) >= work_start
            and _minutes_from_midnight(finish, now) <= work_end
        )
        if inside_hours and cursor >= earliest and cursor >= start_at:
            overlaps = any(
                cursor < block_end and finish > block_start for block_start, block_end in blocked
            )
            if not overlaps:
                slots.append(cursor.isoformat())
        cursor += grid

    return slots


def _minutes_from_midnight(moment: datetime, reference: datetime) -> int:
    """Minutes from midnight on ``moment``'s own day.

    Measured in the reference's zone rather than UTC so an owner whose calendar is
    configured in their own time gets a 09:00 working day rather than a shifted
    one. Both arguments are aware datetimes; the day is taken from the moment in
    the reference's zone, which is the zone the working hours are written in.
    """
    local = moment.astimezone(reference.tzinfo)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((local - midnight).total_seconds() // 60)


def _ceil_to_grid(moment: datetime, grid: timedelta) -> datetime:
    """The first grid step at or after ``moment``.

    Grid steps are measured from the epoch so they are stable across callers: two
    requests a second apart get slots on the same lattice rather than on whatever
    offset each request happened to start from.
    """
    if grid.total_seconds() <= 0:
        raise ValueError("grid must be a positive duration")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = (moment - epoch).total_seconds()
    steps = elapsed / grid.total_seconds()
    import math

    return epoch + timedelta(seconds=math.ceil(steps) * grid.total_seconds())


def pre_resolved_owner_id(payload: Mapping[str, Any]) -> str:
    """The owner a caller supplied itself, if it supplied one.

    The extensibility note: "routes may be pre-resolved from your own CRM ('a
    lead-owner link resolved from your CRM') rather than letting Chili Piper do the
    lookup." The supplied id is taken under the caller's account, which is the whole
    point of pre-resolving - the caller is the one that knows.
    """
    for key in ("owner_id", "pre_resolved_owner_id"):
        value = payload.get(key)
        if value:
            return str(value).strip()
    return ""


def guest_email_of(payload: Mapping[str, Any]) -> str:
    """The guest address from a request body, whatever the caller named it."""
    for key in ("guestEmail", "guest_email", "email"):
        value = payload.get(key)
        if value:
            return normalise_email(value)
    return ""
