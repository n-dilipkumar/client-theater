"""The last-visit time frame, and its midnight-UTC boundary.

Sourced
-------
Two quotations, and the second one is a constraint rather than a convenience:

* "Time frame (last-visit based, up to 90 days, midnight-UTC based)".
* "You can only set timeframes within the last 90 days." and "This timeframe is
  based on midnight UTC".

So a window is half-open at the bottom on a midnight-UTC line, and it may not
reach back further than ninety of those lines. :func:`resolve_window` is the
only place that decides either, which is what keeps the table, the views, and
the API from drifting into three different ideas of "last 30 days".

Design inference
----------------
The research says the boundary is midnight UTC without saying which midnight,
and there are two readings:

* **Midnight of the day the request arrives.** Read this way ``days=90`` always
  fits, because a ninety-day window opens on the same date the ninety-day bound
  does, merely later in the day.
* **A rolling ninety times twenty-four hours.** Read this way ``days=90`` at
  14:33 UTC opens before the bound and would be refused, which would make the
  documented maximum unusable for most of the day.

The first is taken, because the second makes "you can only set timeframes
within the last 90 days" false for ninety days a year. A window is therefore
clamped to midnight by *snapping down*, so a caller passing 14:33 gets the start
of that day, and the effective window is never shorter than it asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.market_intent.errors import InvalidTimeframe, TimeframeTooLong

#: "You can only set timeframes within the last 90 days."
MAX_DAYS = 90

#: What a request that names no window gets. The research names no default, so
#: this is an inference: thirty days is the shortest of the vendor's own
#: examples, and it is short enough that a company that has genuinely gone quiet
#: falls out of a view rather than being tracked forever.
DEFAULT_DAYS = 30


@dataclass(frozen=True)
class Window:
    """A resolved time frame, both ends in UTC and both snapped to midnight."""

    start: datetime
    end: datetime
    days: int
    requested_days: int
    #: Set when the caller's own ``start`` was not on a midnight-UTC line, so a
    #: response can say the boundary moved rather than leaving the caller to
    #: wonder why their 09:15 became 00:00.
    snapped_from: str | None = None

    @property
    def earliest(self) -> datetime:
        return self.start

    def contains(self, moment: datetime | str | None) -> bool:
        """Whether a timestamp falls in the window.

        Inclusive at both ends. The research does not say, and a company whose
        last visit is *exactly* on the start line has demonstrated intent on the
        line the window is drawn from; excluding it would make a boundary
        observable behaviour of the product with no stated rule behind it.
        """
        parsed = as_utc(moment)
        if parsed is None:
            return False
        return self.start <= parsed <= self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "requested_days": self.requested_days,
            "max_days": MAX_DAYS,
            "snapped_from": self.snapped_from,
            "basis": "midnight-utc",
            "last_visit_based": True,
        }


def as_utc(value: Any) -> datetime | None:
    """Parse a timestamp into an aware UTC datetime, or ``None``.

    A naive timestamp returns ``None`` rather than being assumed to be UTC. The
    window is drawn on midnight-UTC lines, so silently assuming a timezone would
    put a company in or out of a view on a difference of hours that nobody
    declared. Callers that require one raise :class:`InvalidTimeframe`.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None
    return parsed.astimezone(timezone.utc)


def require_utc(value: Any, *, what: str) -> datetime:
    """Parse a timestamp that must be present and timezone-aware."""
    parsed = as_utc(value)
    if parsed is None:
        raise InvalidTimeframe(
            f"{what} must be an ISO-8601 timestamp with a timezone offset; "
            "the time frame is based on midnight UTC, so a naive timestamp has no "
            "defensible place on the boundary"
        )
    return parsed


def midnight_utc(moment: datetime) -> datetime:
    """The start of the UTC day containing ``moment``."""
    floor = moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return floor


def earliest_boundary(now: datetime) -> datetime:
    """The oldest start a window may have: ninety midnights before ``now``."""
    return midnight_utc(now) - timedelta(days=MAX_DAYS)


def resolve_window(
    *,
    now: datetime,
    days: Any = None,
    start: Any = None,
    end: Any = None,
    default_days: int = DEFAULT_DAYS,
) -> Window:
    """Turn a requested time frame into a window on midnight-UTC lines.

    ``days`` and ``start``/``end`` are two ways of saying the same thing. A
    request may use either; using both is refused rather than resolved in
    favour of one, because the two answers would be different windows and a
    caller who sends both has said something ambiguous.
    """
    if days is not None and (start is not None or end is not None):
        raise InvalidTimeframe(
            "give either days or start/end, not both: they are two ways of naming one "
            "window and a caller that sends both has not said which it means"
        )

    requested_days = default_days
    if days is not None:
        if isinstance(days, bool) or not isinstance(days, (int, float)):
            raise InvalidTimeframe(f"days must be a whole number, got {days!r}")
        if int(days) != float(days):
            raise InvalidTimeframe(f"days must be a whole number, got {days!r}")
        requested_days = int(days)
        if requested_days < 1:
            raise InvalidTimeframe(f"a time frame must span at least one day, got {requested_days}")
        if requested_days > MAX_DAYS:
            raise TimeframeTooLong(
                f"a time frame may span at most {MAX_DAYS} days, got {requested_days}; "
                f"the oldest boundary the product will open a window at is "
                f"{earliest_boundary(now).isoformat()} (midnight UTC)"
            )
        floor = midnight_utc(now) - timedelta(days=requested_days)
    else:
        floor = midnight_utc(require_utc(start, what="start")) if start is not None else (
            midnight_utc(now) - timedelta(days=requested_days)
        )
        if days is None and start is None:
            requested_days = default_days

    snapped_from: str | None = None
    if start is not None:
        raw = require_utc(start, what="start")
        snapped_from = raw.isoformat() if raw != floor else None

    ceiling = require_utc(end, what="end") if end is not None else now
    if ceiling < floor:
        raise InvalidTimeframe(
            f"the time frame ends at {ceiling.isoformat()} and opens at "
            f"{floor.isoformat()}; it cannot close before it opens"
        )

    if floor < earliest_boundary(now):
        raise TimeframeTooLong(
            f"the time frame opens at {floor.isoformat()}, which is older than the "
            f"{MAX_DAYS}-day limit; the oldest boundary the product will open a window "
            f"at is {earliest_boundary(now).isoformat()} (midnight UTC)"
        )

    return Window(
        start=floor,
        end=ceiling,
        days=max(1, (ceiling - floor).days),
        requested_days=requested_days,
        snapped_from=snapped_from,
    )
