"""Working hours, slot grids, and the arithmetic behind an availability answer.

This module is pure: it takes normalised host schedules, a window, and a moment,
and answers with candidate slots. It knows nothing about records, HTTP, or
Cal.com; :mod:`dsr.inroom_scheduling.availability` supplies the schedules and
subtracts the busy time.

Three decisions live here, and each is a judgement call listed in
:mod:`dsr.inroom_scheduling.inferences` rather than a quiet choice:

**Weekdays are ISO numbers 1 to 7, Monday first.** The research writes
``days`` nowhere - it never describes a schedule shape at all - so this build
defines one. ISO is chosen over 0-based because ``date.isoweekday()`` speaks it,
and a reader comparing this to a Cal schedule does not have to remember that
Sunday is 7 in one and 0 in the other.

**Naive timestamps are read as UTC.** ``GET /v2/slots`` takes ``start`` and
``end`` as ISO instants and a separate ``timeZone``, and the research does not
say which of the two governs a naive value. Treating a naive value as UTC is the
reading that never invents an offset.

**Busy time is subtracted, not generated around.** Slots come from the schedule
and are then marked unavailable. The alternative - generating only free slots -
cannot answer "why is 11:00 not offered", and a slot grid that cannot say why is
a grid the prospect cannot trust.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dsr.inroom_scheduling.errors import SchedulingError
from dsr.inroom_scheduling.vocabulary import MAX_SLOT_WINDOW_DAYS

#: Sentinel for "the schedule says every day", so a host with no ``days`` key is
#: bookable rather than unbookable.
EVERY_DAY: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)


def parse_instant(value: Any, *, field: str = "timestamp") -> datetime:
    """An ISO-8601 instant as an aware UTC datetime.

    A naive value is read as UTC, per the module docstring. A value that is not a
    string, or is not parseable, is refused by name rather than defaulted.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SchedulingError(f"{field} is not an ISO-8601 instant: {value!r}") from exc
    else:
        raise SchedulingError(f"{field} is required and must be an ISO-8601 instant")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


#: The zone names that mean UTC, so the common case needs no database at all.
_UTC_ALIASES = frozenset({"UTC", "utc", "Etc/UTC", "etc/utc", "Z", "GMT", "gmt", "Zulu"})

#: ``UTC+05:30``, ``+05:30``, ``GMT-3``. A fixed offset rather than an IANA name,
#: because a fixed offset resolves on every host while an IANA name needs a tz
#: database that a Python install does not always carry.
_FIXED_OFFSET = re.compile(
    r"^(?:UTC|GMT)?\s*(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$",
    re.IGNORECASE,
)


def resolve_zone(name: Any, *, field: str = "timeZone") -> tzinfo:
    """A time zone, resolved without assuming the host has an IANA database.

    Three forms are accepted, in this order:

    1. the UTC aliases, which always resolve;
    2. a fixed offset - ``UTC+05:30``, ``+05:30``, ``GMT-3``;
    3. an IANA name, resolved through :mod:`zoneinfo` when the host has a tz
       database.

    The first two exist because a tz database is not a Python standard-library
    guarantee: on a host without one, ``ZoneInfo("UTC")`` raises, and a feature
    whose seeded demo cannot resolve its own time zone is a feature nobody can
    review. A named zone this host cannot resolve is refused by name, with the
    fixed-offset form offered, rather than silently treated as UTC - a schedule
    published in the wrong zone is a meeting at the wrong hour, and a silent
    fallback would make that invisible.
    """
    if not isinstance(name, str) or not name.strip():
        raise SchedulingError(f"{field} is required and must be a time zone name")
    text = name.strip()

    if text in _UTC_ALIASES:
        return timezone.utc

    offset_match = _FIXED_OFFSET.match(text)
    if offset_match:
        hours = int(offset_match.group("hours"))
        minutes = int(offset_match.group("minutes") or 0)
        if hours > 23 or minutes > 59:
            raise SchedulingError(f"{field} is not a usable fixed offset: {name!r}")
        delta = timedelta(hours=hours, minutes=minutes)
        if offset_match.group("sign") == "-":
            delta = -delta
        return timezone(delta)

    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise SchedulingError(
            f"{field} names no zone this host can resolve: {name!r}. This host has no IANA time zone "
            "database, so use UTC, or a fixed offset such as UTC+05:30."
        ) from exc


def parse_window(
    start: Any,
    end: Any,
    *,
    now: datetime,
    max_days: int = MAX_SLOT_WINDOW_DAYS,
) -> tuple[datetime, datetime]:
    """The requested slot window, as a half-open UTC range.

    Bounded three ways, all of which a reviewer would otherwise have to take on
    trust: the end must be after the start, the window cannot begin in the past,
    and it cannot be longer than ``max_days``.
    """
    start_dt = parse_instant(start, field="start")
    end_dt = parse_instant(end, field="end")
    if end_dt <= start_dt:
        raise SchedulingError("end must be after start")
    if start_dt < now:
        raise SchedulingError(
            "start is in the past; a slot query cannot answer for a window that has passed"
        )
    if end_dt - start_dt > timedelta(days=max_days):
        raise SchedulingError(f"the slot window may not exceed {max_days} days")
    return (start_dt, end_dt)


def _parse_clock(value: Any, *, field: str, fallback: time) -> time:
    if value in (None, ""):
        return fallback
    try:
        hours, _, minutes = str(value).partition(":")
        return time(hour=int(hours), minute=int(minutes or 0))
    except (TypeError, ValueError) as exc:
        raise SchedulingError(f"{field} is not a HH:MM time: {value!r}") from exc


def normalise_host(spec: Mapping[str, Any], *, field: str = "host") -> dict[str, Any]:
    """One host's working hours, with every field resolved.

    The defaults are the ones that make a bare host sensible rather than
    unbookable: no ``days`` means every day, no ``start``/``end`` means the
    working day, and no interval means a 30-minute grid.
    """
    username = str(spec.get("username") or "").strip()
    if not username:
        raise SchedulingError(f"{field} needs a username")
    zone_name = str(spec.get("time_zone") or "UTC")
    resolve_zone(zone_name, field=f"{field}.time_zone")

    raw_days = spec.get("days")
    if raw_days in (None, ""):
        days = list(EVERY_DAY)
    else:
        if not isinstance(raw_days, (list, tuple)):
            raise SchedulingError(
                f"{field}.days must be a list of ISO weekday numbers, 1 (Monday) to 7"
            )
        try:
            days = sorted({int(day) for day in raw_days})
        except (TypeError, ValueError) as exc:
            raise SchedulingError(
                f"{field}.days must be ISO weekday numbers, 1 (Monday) to 7"
            ) from exc
        bad = [day for day in days if day not in EVERY_DAY]
        if bad:
            raise SchedulingError(
                f"{field}.days must be ISO weekday numbers, 1 (Monday) to 7; got {bad}"
            )

    open_at = _parse_clock(spec.get("start"), field=f"{field}.start", fallback=time(9, 0))
    close_at = _parse_clock(spec.get("end"), field=f"{field}.end", fallback=time(17, 0))
    if close_at <= open_at:
        raise SchedulingError(f"{field}.end must be after {field}.start")

    interval = spec.get("slot_interval_minutes", 30)
    notice = spec.get("minimum_notice_minutes", 0)
    try:
        interval_minutes = int(interval)
        notice_minutes = int(notice)
    except (TypeError, ValueError) as exc:
        raise SchedulingError(
            f"{field} slot_interval_minutes and minimum_notice_minutes must be whole minutes"
        ) from exc
    if interval_minutes < 1:
        raise SchedulingError(f"{field}.slot_interval_minutes must be at least 1")
    if notice_minutes < 0:
        raise SchedulingError(f"{field}.minimum_notice_minutes cannot be negative")

    return {
        "username": username,
        "time_zone": zone_name,
        "days": days,
        "start": open_at.strftime("%H:%M"),
        "end": close_at.strftime("%H:%M"),
        "slot_interval_minutes": interval_minutes,
        "minimum_notice_minutes": notice_minutes,
    }


def working_windows(
    host: Mapping[str, Any],
    window: tuple[datetime, datetime],
) -> list[tuple[datetime, datetime]]:
    """The host's working windows inside ``window``, in UTC.

    Built by walking each *local* date the request touches, because "nine to five
    on Tuesday" is a local statement: a naive UTC nine-to-five would drift by the
    host's offset across the year, which is precisely the bug time zones are
    there to prevent. The local day is also what decides the weekday, so a
    Saturday-evening slot in Auckland is a Saturday in Auckland.
    """
    zone = resolve_zone(host["time_zone"], field="host.time_zone")
    start_dt, end_dt = window
    days = set(host["days"])
    open_at = _parse_clock(host.get("start"), field="host.start", fallback=time(9, 0))
    close_at = _parse_clock(host.get("end"), field="host.end", fallback=time(17, 0))

    windows: list[tuple[datetime, datetime]] = []
    first = start_dt.astimezone(zone).date() - timedelta(days=1)
    last = end_dt.astimezone(zone).date() + timedelta(days=1)
    cursor: date = first
    while cursor <= last:
        if cursor.isoweekday() in days:
            opens = datetime.combine(cursor, open_at, tzinfo=zone).astimezone(timezone.utc)
            closes = datetime.combine(cursor, close_at, tzinfo=zone).astimezone(timezone.utc)
            begin = max(opens, start_dt)
            finish = min(closes, end_dt)
            if finish > begin:
                windows.append((begin, finish))
        cursor += timedelta(days=1)
    return windows


def snap_to_grid(moment: datetime, interval_minutes: int) -> datetime:
    """Round ``moment`` up to the next point on an ``interval_minutes`` grid.

    The grid is measured from midnight, so every host contributes on the same
    phase: a half-hour grid must not offer 09:07 to one host and 09:30 to another.
    """
    if interval_minutes < 1:
        raise SchedulingError("a slot grid interval must be at least one minute")
    moment = moment.replace(second=0, microsecond=0)
    remainder = (moment.hour * 60 + moment.minute) % interval_minutes
    if remainder == 0:
        return moment
    return moment + timedelta(minutes=interval_minutes - remainder)


def candidate_starts(
    hosts: Sequence[Mapping[str, Any]],
    *,
    length_minutes: int,
    window: tuple[datetime, datetime],
    now: datetime,
) -> list[datetime]:
    """Every start time on the grid at least one host could offer, ascending.

    A union across hosts rather than an intersection, because availability is a
    question about the whole event type: a slot on a personal event has one host, a
    slot on a dynamic ``usernames`` query has several, and the union is the grid
    both are drawn from. Whether enough of them are actually free is decided by
    :mod:`dsr.inroom_scheduling.availability`, which is where the busy time lives.

    Each host steps on **its own** ``slot_interval_minutes``, snapped to its own
    grid, rather than on one shared interval. A shared interval would have to be
    somebody's - the first host's, in practice - and a dynamic query naming a
    15-minute-grid host and a 30-minute-grid host would then never offer the
    quarter hour the first one can take. The cost is a grid that can be finer than
    any single host's, which
    :func:`~dsr.inroom_scheduling.availability.slot_grid` answers honestly by
    naming which hosts are free for each candidate.
    """
    length = timedelta(minutes=length_minutes)
    seen: set[datetime] = set()
    for host in hosts:
        interval = max(1, int(host.get("slot_interval_minutes") or 30))
        step = timedelta(minutes=interval)
        notice = timedelta(minutes=int(host.get("minimum_notice_minutes") or 0))
        for opens, closes in working_windows(host, window):
            cursor = snap_to_grid(opens, interval)
            while cursor + length <= closes:
                if cursor >= now + notice:
                    seen.add(cursor)
                cursor += step
    return sorted(seen)


def overlaps(first: tuple[datetime, datetime], second: tuple[datetime, datetime]) -> bool:
    """Do two half-open intervals share any time? Touching ends do not overlap."""
    return first[0] < second[1] and second[0] < first[1]


def iso(moment: datetime) -> str:
    """An aware UTC instant as the ISO string the API and the store both use."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_or_none(value: Any) -> datetime | None:
    """Best-effort parse for values this product wrote earlier.

    Used when reading a stored ``start`` back for a comparison, where a value this
    product wrote cannot be unparseable - and a stored value that somehow is
    should read as "no information" rather than crash a listing.
    """
    if value in (None, ""):
        return None
    try:
        return parse_instant(value)
    except SchedulingError:
        return None


def minutes_between(first: datetime, second: datetime) -> int:
    return int(round((second - first).total_seconds() / 60))


def merge_ranges(ranges: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Coalesce overlapping intervals, so ``free`` is a subtraction and not a set
    of puzzles.

    Busy time arrives as one interval per booking and per live hold, and a hold
    and a booking can overlap. Merging first means the "is this slot busy" test is
    a single comparison against a sorted list rather than a loop per candidate.
    """
    ordered = sorted((start, end) for start, end in ranges if end > start)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged
