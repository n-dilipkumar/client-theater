"""The availability engine behind the ``discover or route`` call.

The research's data flow for this call is: "lead/link/handoff payload +
``interval{startsAt,duration}`` -> routing + availability engine -> ``routeId`` +
slot list". The interval is not decoration - it is what distinguishes
*schedule programmatically* from *return a booking URL*, and without one there
are no slots to book. So this module is the half of call #1 that turns a window
and a host's calendar into the list a caller then picks from.

What is sourced and what is not
--------------------------------

Sourced:

* The output is a list of ``startTimes``, and "The ``startTime`` in responses is
  ISO-8601 UTC". So every string this module emits ends in ``Z``, has
  second precision, and denotes UTC. The two halves of "pass it back verbatim"
  are *these* strings and *those* strings.
* Availability is read from Google/Outlook calendars, and for a handoff router
  it is read *per routing path*. So the busy set is a function of the path.

Not sourced, and therefore an inference (``slot-generation-is-a-grid``):

* The grid step, the working hours, the lead time, and the cap on how many slots
  one session may return. The research publishes none of them. They are here
  because a slot list is the product's whole input, and an unbounded list is not
  a decision a caller can make.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Sequence

from dsr.headless_booking.errors import HeadlessBookingError

#: How a slot is written on the wire. Second precision and an explicit ``Z``,
#: always: this string is what a caller passes back verbatim, so it has to be
#: unambiguous without a second format.
SLOT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: A grid step, used where the meeting length is not available to fall back to.
#: The *default* for an asset is its own meeting length - see
#: :data:`DEFAULT_SLOT_FALLS_BACK_TO_MEETING` - and 30 minutes is the fallback
#: for a caller of this function who supplies no length either. Unsourced.
DEFAULT_SLOT_MINUTES = 30

#: The default meeting length, which is the ``duration`` an asset is booked for.
#: Unsourced.
DEFAULT_MEETING_MINUTES = 30

#: Whether an asset's *start* grid falls back to its meeting length rather than to
#: a fixed :data:`DEFAULT_SLOT_MINUTES`. Read by :func:`dsr.headless_booking.assets
#: .normalise` and by the session opener; a boolean so the decision is named
#: somewhere rather than being an ``or`` in two places.
#:
#: A grid finer than the meeting length produces a slot list containing starts
#: that overlap each other - 09:00 and 09:30 on a 30-minute grid for a 45-minute
#: meeting - so a caller can pick two and try to book both. An earlier version of
#: this module refused any asset whose length was not a multiple of the grid,
#: which sounds safer and is not: 45 minutes is the single most common length of a
#: sales demo, so the rule forbade the ordinary case to prevent a corner one. The
#: overlap is caught where it actually bites instead, at the book call, by the
#: availability recheck that produces the researched ``slot_taken`` refusal. So
#: the second booking is refused with the researched reason rather than being
#: made impossible to express.
DEFAULT_SLOT_FALLS_BACK_TO_MEETING = True

#: Working hours, in the asset's local time. Unsourced.
DEFAULT_WORK_START_HOUR = 9
DEFAULT_WORK_END_HOUR = 17

#: How far ahead of "now" the first offered slot may be. A session opened for a
#: slot that has already started would hand a caller a time in the past.
#: Unsourced.
DEFAULT_LEAD_MINUTES = 30

#: The cap on slots returned by one session. The research publishes no limit; an
#: unbounded slot list is not something a caller can act on, and a cap is a
#: bound a deployment can change. Unsourced.
DEFAULT_MAX_SLOTS = 40

#: A window longer than this is refused. Without a cap, `duration` is an
#: unbounded amount of work from an untrusted number in a JSON body.
MAX_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Busy:
    """One block on a host's calendar."""

    starts_at: datetime
    ends_at: datetime
    label: str = ""

    def overlaps(self, starts_at: datetime, ends_at: datetime) -> bool:
        """Half-open overlap, so a meeting ending at 09:30 does not block 09:30.

        Half-open on both sides, which is the researched position implicitly: a
        back-to-back booking is a booking, not a conflict.
        """
        return self.starts_at < ends_at and starts_at < self.ends_at


def parse_instant(raw: object, *, field: str) -> datetime:
    """Parse a caller-supplied instant into an aware UTC datetime.

    A bare ``YYYY-MM-DD`` is accepted as midnight UTC: a date is unambiguous,
    and refusing it would be refusing the most common way a person writes one.
    Anything else must carry an explicit UTC designator. A naive local time is
    refused rather than assumed, because the alternative is silently booking a
    slot five hours from the one the caller meant - the exact failure the
    researched "Slot times are UTC" sentence exists to prevent, and the
    direction of the error would be invisible in the audit log.
    """
    text = str(raw or "").strip()
    if not text:
        raise HeadlessBookingError(f"{field} is required")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise HeadlessBookingError(
            f"{field} {text!r} is not an ISO-8601 instant; send UTC, e.g. 2026-10-01T09:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        raise HeadlessBookingError(
            f"{field} {text!r} carries no UTC designator; slot times are UTC, so an ambiguous "
            "instant is refused rather than guessed at"
        )
    return parsed.astimezone(timezone.utc)


def parse_interval(payload: object) -> tuple[datetime, timedelta]:
    """Read ``interval{startsAt,duration}`` - the field that makes this a booking.

    ``duration`` is read as minutes. The research writes ``interval{startsAt,
    duration}`` and calls it a window, and minutes is the unit every other
    duration in this product uses; the acceptance is documented here rather than
    assumed silently.
    """
    if payload is None:
        raise HeadlessBookingError(
            "interval{startsAt,duration} is required: it is what distinguishes scheduling "
            "programmatically from returning a booking URL, and without it there are no slots"
        )
    if not isinstance(payload, dict):
        raise HeadlessBookingError("interval must be an object with startsAt and duration")
    if "startsAt" not in payload:
        raise HeadlessBookingError("interval.startsAt is required")
    starts_at = parse_instant(payload.get("startsAt"), field="interval.startsAt")
    if payload.get("duration") in (None, ""):
        raise HeadlessBookingError("interval.duration is required")
    try:
        minutes = float(payload["duration"])
    except (TypeError, ValueError) as exc:
        raise HeadlessBookingError(
            f"interval.duration {payload['duration']!r} is not a number of minutes"
        ) from exc
    if minutes <= 0:
        raise HeadlessBookingError("interval.duration must be a positive number of minutes")
    if minutes > MAX_WINDOW_DAYS * 24 * 60:
        raise HeadlessBookingError(
            f"interval.duration must be at most {MAX_WINDOW_DAYS} days; a longer window is not "
            "something a session can answer in one list of slots"
        )
    return starts_at, timedelta(minutes=minutes)


def _floor_to_grid(moment: datetime, step_minutes: int) -> datetime:
    """The first grid point at or after ``moment``.

    The grid is anchored to the Unix epoch rather than to the window, so two
    sessions opened over overlapping windows produce the *same* slot strings for
    the same instants. A grid anchored to each window would shift the two lists
    against each other, and a caller holding both would see the same hour twice
    under two spellings.
    """
    step = timedelta(minutes=step_minutes)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = moment - epoch
    seconds = step.total_seconds()
    index = -(-elapsed.total_seconds() // seconds)  # ceiling division
    return epoch + step * int(index)


def format_slot(moment: datetime) -> str:
    """The wire form of a slot: ISO-8601, UTC, seconds, ``Z``."""
    return moment.astimezone(timezone.utc).strftime(SLOT_FORMAT)


def slots(
    *,
    window_start: datetime,
    window: timedelta,
    busy: Sequence[Busy],
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    meeting_minutes: int = DEFAULT_MEETING_MINUTES,
    work_start_hour: int = DEFAULT_WORK_START_HOUR,
    work_end_hour: int = DEFAULT_WORK_END_HOUR,
    work_days: Iterable[int] | None = None,
    utc_offset_minutes: int = 0,
    now: datetime | None = None,
    lead_minutes: int = DEFAULT_LEAD_MINUTES,
    max_slots: int = DEFAULT_MAX_SLOTS,
) -> list[str]:
    """Every slot the host is free for, as wire strings, in ascending order.

    A slot is offered when all of the following hold:

    * it starts at least ``lead_minutes`` after ``now`` - a session must not
      hand back a slot that has already begun;
    * it falls inside working hours *in the asset's local time*, not in UTC, so
      a router configured for a Sydney desk does not offer its Australian
      morning as a European night;
    * it lies on a working weekday, judged in the same local time;
    * it overlaps no busy block and no already-booked meeting.

    Working hours are checked on the local *wall clock* rather than as a UTC
    offset applied to working hours, and the distinction is not academic: the
    meeting has to *end* inside the window in local time, and that comparison
    needs local datetimes rather than two times-of-day. What this build does not
    model is daylight saving - a single fixed ``utc_offset_minutes`` per asset,
    because a per-day offset is a per-asset table and the research publishes no
    timezone data at all. The inference ``local-time-is-one-fixed-offset`` says
    so, and names what breaks without DST: a desk that moves its clocks moves
    its working hours twice a year.
    """
    if slot_minutes <= 0:
        raise HeadlessBookingError("slot_minutes must be a positive number of minutes")
    if meeting_minutes <= 0:
        raise HeadlessBookingError("meeting_minutes must be a positive number of minutes")
    if max_slots <= 0:
        raise HeadlessBookingError("max_slots must be a positive number of slots")

    days = set(work_days) if work_days is not None else {0, 1, 2, 3, 4}  # Mon-Fri
    offset = timedelta(minutes=utc_offset_minutes)
    meeting = timedelta(minutes=meeting_minutes)
    window_end = window_start + window

    # `now` defaults to the window start, so a pure-function test that does not
    # care about the clock gets the whole window rather than an empty list.
    reference = (now or window_start).astimezone(timezone.utc)
    earliest = reference + timedelta(minutes=lead_minutes)

    start_from = max(window_start, earliest)
    if start_from >= window_end:
        return []

    # Working hours are boundaries in the *local* wall clock, so the comparison
    # has to be between local datetimes, not between two times-of-day. A 90
    # minute meeting starting at 16:00 ends at 17:30 local, which is outside a
    # 09:00-17:00 window; the `date` equality below is what stops a 23:30 start
    # from wrapping at midnight and passing a 09:00-17:00 check.
    if not 0 <= work_start_hour < work_end_hour <= 24:
        raise HeadlessBookingError(
            f"working hours must satisfy 0 <= start < end <= 24, got "
            f"{work_start_hour}:00-{work_end_hour}:00"
        )
    local_open = time(work_start_hour, 0)
    local_close = time(work_end_hour, 0)

    results: list[str] = []
    cursor = _floor_to_grid(start_from, slot_minutes)
    while cursor < window_end and len(results) < max_slots:
        if cursor + meeting > window_end:
            break
        local_start = cursor + offset
        local_end = local_start + meeting
        inside_hours = (
            local_start.weekday() in days
            and local_start.time() >= local_open
            and local_end.date() == local_start.date()
            and local_end.time() <= local_close
        )
        if inside_hours and not any(block.overlaps(cursor, cursor + meeting) for block in busy):
            results.append(format_slot(cursor))
        cursor += timedelta(minutes=slot_minutes)
    return results


def parse_calendar_block(payload: object) -> Busy:
    """Read one busy block from a calendar webhook or a manual entry.

    Accepts the two shapes this product can be handed: an explicit
    ``{startsAt, endsAt}`` pair, and a single ``startTime`` with a ``duration``
    in minutes, which is the same shape the interval uses. A block with no end
    is accepted when a duration is given and refused when it is not - a busy
    block with no end is an all-day event at best and unbounded at worst.
    """
    if not isinstance(payload, dict):
        raise HeadlessBookingError("a calendar block must be an object")
    starts_at = parse_instant(
        payload.get("startsAt") or payload.get("startTime"), field="calendar.startsAt"
    )
    raw_end = payload.get("endsAt") or payload.get("endTime")
    if raw_end not in (None, ""):
        ends_at = parse_instant(raw_end, field="calendar.endsAt")
    elif payload.get("duration") not in (None, ""):
        try:
            minutes = float(payload["duration"])
        except (TypeError, ValueError) as exc:
            raise HeadlessBookingError(
                f"calendar duration {payload['duration']!r} is not a number of minutes"
            ) from exc
        ends_at = starts_at + timedelta(minutes=minutes)
    else:
        raise HeadlessBookingError(
            "a calendar block needs endsAt, or a duration in minutes; an unbounded busy block "
            "would make every later slot unavailable"
        )
    if ends_at <= starts_at:
        raise HeadlessBookingError("a calendar block must end after it starts")
    return Busy(starts_at=starts_at, ends_at=ends_at, label=str(payload.get("label") or ""))
