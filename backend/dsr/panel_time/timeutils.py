"""Time and duration handling for the panel-time package (WF-057).

Three jobs, all of them things the research names but does not specify:

* **ISO 8601 durations.** ``meetingDuration`` is a researched parameter and
  Graph's documented spelling is ``PT1H``. :func:`parse_duration` reads the
  ``P[nY][nM][nW][nD][T[nH][nM][nS]]`` subset, which is every shape a meeting
  length can take, and refuses the rest rather than guessing.
* **RFC 3339 instants.** ``timeMin``, ``timeMax`` and every candidate start are
  absolute instants. Everything is normalised to UTC on the way in and rendered
  back with a ``Z``, so a search's result does not depend on the machine's
  local zone.
* **The working window's time zone.** This is the honest gap, and it is handled
  here rather than pretended away. The research says nothing about the time zone
  a panel's working hours are expressed in, and **this interpreter ships no IANA
  database** - ``zoneinfo.available_timezones()`` is empty, and no ``tzdata``
  package is installed. :class:`Clock` therefore resolves a named zone when the
  platform can and falls back to UTC when it cannot, and it *records which it
  did* so the response says so rather than quietly using the wrong offset. A
  deployment that installs ``tzdata`` gets real local hours with no code change;
  one that does not gets UTC hours and a warning, which is the same thing the
  research would have required it to reason about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dsr.panel_time.errors import ConstraintError

#: ``P1Y2M3W4DT5H6M7S`` and the shorter forms of it. Years and months are parsed
#: for completeness but converted with 365 and 30 days respectively: a meeting
#: duration expressed in years is a caller bug, and being approximate about it
#: costs nothing because the shape is refused by the panel's own limits.
_DURATION = re.compile(
    r"^(?P<sign>[+-])?P"
    r"(?:(?P<years>\d+(?:\.\d+)?)Y)?"
    r"(?:(?P<months>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<weeks>\d+(?:\.\d+)?)W)?"
    r"(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)

_YEAR = timedelta(days=365)
_MONTH = timedelta(days=30)
_WEEK = timedelta(days=7)


def tzdb_available() -> bool:
    """Whether this interpreter can resolve named IANA zones at all."""
    try:
        return bool(ZoneInfo("Europe/London"))
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError):
        return False


@dataclass(frozen=True)
class Clock:
    """A named time zone, resolved exactly if the platform can and UTC if not.

    ``exact`` is the whole point. A caller that asked for ``Europe/London`` and
    got UTC must be able to tell, because every working-hour decision this
    package makes is expressed in this zone.
    """

    name: str = "UTC"
    exact: bool = True

    @classmethod
    def resolve(cls, name: str | None) -> "Clock":
        wanted = (name or "UTC").strip() or "UTC"
        if wanted.upper() in ("UTC", "Z", "ETC/UTC"):
            return cls(name="UTC", exact=True)
        try:
            ZoneInfo(wanted)
        except (ZoneInfoNotFoundError, ValueError, KeyError, OSError):
            # Not a typo - the database is simply absent. Say which it was.
            return cls(name=wanted, exact=tzdb_available() and wanted.upper() == "UTC")
        return cls(name=wanted, exact=True)

    def local(self, instant: datetime) -> datetime:
        """``instant`` in this clock's zone, or UTC when it could not be resolved."""
        if self.exact and self.name.upper() not in ("UTC", "Z", "ETC/UTC"):
            return instant.astimezone(ZoneInfo(self.name))
        return instant.astimezone(timezone.utc)

    def offset_minutes(self, instant: datetime) -> int:
        # utcoffset() is a timedelta (or None), never a number of minutes, so
        # it has to go through total_seconds(). The previous form only worked
        # when the zone fell back to UTC, because timedelta(0) is falsy and
        # short-circuited the int() call; on an interpreter that ships the IANA
        # database a resolved zone returns a non-zero timedelta, which is
        # truthy, and int(timedelta) raises TypeError. So the fallback is on
        # None rather than on falsiness, and the arithmetic is explicit.
        offset = self.local(instant).utcoffset()
        if offset is None:
            return 0
        return int(offset.total_seconds() // 60)

    def describe(self) -> dict[str, object]:
        """What a response says about the zone, so a reader is never guessing."""
        return {
            "time_zone": self.name,
            "exact": self.exact,
            "tzdb_available": tzdb_available(),
            "offset_minutes": self.offset_minutes(datetime.now(timezone.utc)),
            "note": (
                ""
                if self.exact
                else (
                    f"{self.name!r} could not be resolved by this interpreter, so the "
                    "working window is evaluated in UTC. Install the 'tzdata' package to "
                    "get real local hours; nothing else about this feature changes."
                )
            ),
        }


def parse_duration(text: object, *, field: str = "duration") -> timedelta:
    """An ISO 8601 duration, as a positive :class:`~datetime.timedelta`.

    Refuses anything empty, non-ISO, zero or negative. A zero-length meeting has
    no slot arithmetic worth doing and a negative one is a caller bug that would
    otherwise surface as a confusing empty suggestion list.
    """
    if isinstance(text, timedelta):
        return _require_positive(text, field)
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        # A plain number is read as minutes, because that is the only unit a
        # caller passing a bare number can have meant without saying so.
        return _require_positive(timedelta(minutes=float(text)), field)
    if not isinstance(text, str) or not text.strip():
        raise ConstraintError(f"{field} must be an ISO 8601 duration such as 'PT1H'")
    match = _DURATION.match(text.strip())
    if match is None or match.group(0) in ("P", "PT"):
        raise ConstraintError(
            f"{field} is not an ISO 8601 duration: {text!r}; expected something like 'PT1H' or 'PT30M'"
        )
    parts = {key: float(value) for key, value in match.groupdict().items() if key != "sign" and value}
    if not parts:
        raise ConstraintError(f"{field} is an empty duration: {text!r}")
    span = (
        parts.get("years", 0.0) * _YEAR
        + parts.get("months", 0.0) * _MONTH
        + parts.get("weeks", 0.0) * _WEEK
        + timedelta(days=parts.get("days", 0.0))
        + timedelta(hours=parts.get("hours", 0.0))
        + timedelta(minutes=parts.get("minutes", 0.0))
        + timedelta(seconds=parts.get("seconds", 0.0))
    )
    if match.group("sign") == "-":
        span = -span
    return _require_positive(span, field)


def _require_positive(span: timedelta, field: str) -> timedelta:
    if span.total_seconds() <= 0:
        raise ConstraintError(f"{field} must be a positive duration; got {span.total_seconds():g}s")
    return span


def format_duration(span: timedelta) -> str:
    """The canonical ISO 8601 spelling of a duration, for a response body."""
    seconds = int(round(span.total_seconds()))
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    text = "P"
    if days:
        text += f"{days}D"
    if hours or minutes or secs or text == "P":
        text += "T"
        if hours:
            text += f"{hours}H"
        if minutes:
            text += f"{minutes}M"
        if secs or text.endswith("T"):
            text += f"{secs}S"
    return f"{sign}{text}"


def parse_instant(value: object, *, field: str = "instant") -> datetime:
    """An RFC 3339 instant, normalised to UTC and made timezone-aware.

    A naive timestamp is *read* as UTC rather than as the machine's local time.
    A panel that means local time says so with ``timeZone``, and a caller who
    forgets must not get a result that silently depends on where the server runs.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ConstraintError(
                f"{field} is not an RFC 3339 instant: {value!r} ({exc})"
            ) from exc
    else:
        raise ConstraintError(f"{field} must be an RFC 3339 instant such as '2026-10-05T09:00:00Z'")
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_instant(moment: datetime) -> str:
    """The canonical ``...Z`` spelling, at second precision."""
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_clock_time(value: object, *, field: str = "local time") -> tuple[int, int]:
    """``"09:30"`` or ``"09:30:00"`` as ``(hour, minute)`` in 24-hour form."""
    if not isinstance(value, str) or not value.strip():
        raise ConstraintError(f"{field} must be a local time such as '09:30'")
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        raise ConstraintError(f"{field} is not a local time: {value!r}; expected 'HH:MM'")
    try:
        numbers = [int(part) for part in parts]
    except ValueError as exc:
        raise ConstraintError(f"{field} is not a local time: {value!r} ({exc})") from exc
    hour, minute = numbers[0], numbers[1]
    seconds = numbers[2] if len(numbers) == 3 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= seconds <= 59):
        raise ConstraintError(f"{field} is out of range: {value!r}")
    return hour, minute


def overlaps(
    start: datetime, end: datetime, other_start: datetime, other_end: datetime
) -> bool:
    """Whether two half-open intervals ``[start, end)`` and ``[other_start,
    other_end)`` share any instant.

    Half-open is the reading both providers' ``busy`` blocks use, and it is the
    reading that makes an ordinary diary work: a 09:00-10:00 block and a
    10:00-11:00 meeting do not conflict. An implementation that treated the ends
    as inclusive would refuse to book the entire back half of every working day.
    """
    return start < other_end and other_start < end
