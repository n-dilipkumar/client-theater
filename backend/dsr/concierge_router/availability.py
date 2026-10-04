"""The availability engine: a seller's busy blocks turned into bookable slots.

The researched ``data_flow`` names the step this models: "chosen
calendar/Meeting Type/assignee -> availability engine (Google/Outlook calendars)
-> slot list rendered in the ``Display Calendar`` modal".

It does not describe how availability is computed, because that belongs to Google
and Outlook, not to this product. So a seller's calendar is held here as its busy
blocks, and this module does the part that is this product's: given a busy set, a
meeting length, a horizon and a working window, which starts are actually free.

Slots are generated rather than stored. A stored slot list goes stale the moment
somebody books, and the researched second call commits against "the chosen slot",
so a slot that is stored and then taken must still be refused. Generating on read
means the second call recomputes against the same busy set it was offered from,
plus whatever the commit itself just added.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from dsr.concierge_router import vocabulary

#: The hours a seller is bookable, in UTC. The research names the availability
#: engine and no working hours, so these are a named default a seller row may
#: override with its own ``working_hours``.
DEFAULT_WORKING_HOUR_START = 9
DEFAULT_WORKING_HOUR_END = 17

#: The days a seller works. Monday to Friday, which is what a demo request means
#: everywhere the research sourced it. A seller row may override this too.
DEFAULT_WORKING_WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)

#: The gap between two meetings on one calendar. The research names no gap, and a
#: back-to-back bookable window is how a seller ends up in two calls at once.
DEFAULT_SLOT_GAP_MINUTES = 0


def busy_blocks(seller: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """A seller's busy blocks as parsed UTC intervals.

    A block the seller typed badly is skipped rather than refused: the researched
    availability engine is Google's or Outlook's, and neither fails a whole
    booking because one calendar entry is malformed. Dropping one block can only
    offer a slot that the commit step will then refuse if it collides.
    """
    parsed: list[tuple[datetime, datetime]] = []
    for block in seller.get("busy") or []:
        if not isinstance(block, dict):
            continue
        try:
            start = vocabulary.parse_timestamp(block.get("start"))
            end = vocabulary.parse_timestamp(block.get("end"))
        except ValueError:
            continue
        if end <= start:
            continue
        parsed.append((start, end))
    return sorted(parsed)


def working_hours(seller: dict[str, Any]) -> tuple[int, int, tuple[int, ...]]:
    """The seller's bookable window as ``(start hour, end hour, weekdays)``.

    Read from the seller row so a team can book outside a nine-to-five without a
    code change, which is the schema-flexibility rule: a new field is data, not a
    migration.
    """
    hours = seller.get("working_hours")
    if not isinstance(hours, dict):
        return (DEFAULT_WORKING_HOUR_START, DEFAULT_WORKING_HOUR_END, DEFAULT_WORKING_WEEKDAYS)
    start = int(hours.get("start_hour", DEFAULT_WORKING_HOUR_START))
    end = int(hours.get("end_hour", DEFAULT_WORKING_HOUR_END))
    raw_days = hours.get("weekdays")
    days = (
        tuple(int(day) for day in raw_days)
        if isinstance(raw_days, (list, tuple))
        else DEFAULT_WORKING_WEEKDAYS
    )
    return (start, end, days)


def duration_minutes(meeting_type: dict[str, Any]) -> int:
    """A Meeting Type's length, defaulting to the named thirty minutes.

    The research says the ``Display Calendar`` node chooses "the **Meeting
    Type(s)** to offer" and names no length, so the default is a constant rather
    than a literal in the middle of a loop.
    """
    try:
        stated = int(meeting_type.get("duration_minutes", vocabulary.DEFAULT_DURATION_MINUTES))
    except (TypeError, ValueError):
        return vocabulary.DEFAULT_DURATION_MINUTES
    return stated if stated > 0 else vocabulary.DEFAULT_DURATION_MINUTES


def _conflicts(
    start: datetime,
    end: datetime,
    blocks: list[tuple[datetime, datetime]],
    gap_minutes: int,
) -> bool:
    """Whether a candidate start collides with any busy block.

    Touching does not collide: a meeting ending at 10:00 and one starting at
    10:00 share an instant but not a minute of the seller's time.
    """
    padding = timedelta(minutes=gap_minutes)
    for block_start, block_end in blocks:
        if start < block_end + padding and block_start < end + padding:
            return True
    return False


def offer_slots(
    seller: dict[str, Any],
    meeting_type: dict[str, Any],
    *,
    now: datetime,
    horizon_days: int = vocabulary.DEFAULT_HORIZON_DAYS,
    max_slots: int = vocabulary.DEFAULT_MAX_SLOTS,
    step_minutes: int | None = None,
) -> list[dict[str, Any]]:
    """The bookable starts, earliest first.

    Four things bound the list, and each exists because the research implies it:

    * the seller's busy blocks, which are what the availability engine reads;
    * the seller's working hours, because a free Sunday at 03:00 is not an offer;
    * the horizon, "``DEFAULT_HORIZON_DAYS``", because an unbounded search over a
      year is a response nobody can render;
    * the cap, "``DEFAULT_MAX_SLOTS``", for the same reason.
    """
    blocks = busy_blocks(seller)
    hour_start, hour_end, weekdays = working_hours(seller)
    length = duration_minutes(meeting_type)
    step = step_minutes or length + DEFAULT_SLOT_GAP_MINUTES
    if step <= 0:
        step = length

    horizon = now + timedelta(days=max(1, horizon_days))
    slots: list[dict[str, Any]] = []
    day = now.replace(hour=hour_start, minute=0, second=0, microsecond=0)
    if day < now:
        day = day + timedelta(days=1)

    while day <= horizon and len(slots) < max_slots:
        if day.weekday() in weekdays:
            start = day
            last_start = day.replace(hour=hour_end) - timedelta(minutes=length)
            while start <= last_start and len(slots) < max_slots:
                end = start + timedelta(minutes=length)
                if start > now and not _conflicts(start, end, blocks, DEFAULT_SLOT_GAP_MINUTES):
                    slots.append(
                        {
                            "start": vocabulary.iso(start),
                            "end": vocabulary.iso(end),
                            "duration_minutes": length,
                            "meeting_type": str(meeting_type.get("name") or ""),
                        }
                    )
                start = start + timedelta(minutes=step)
        day = day + timedelta(days=1)

    return slots


def offer_slot(
    seller: dict[str, Any],
    meeting_types: list[dict[str, Any]],
    *,
    now: datetime,
    horizon_days: int = vocabulary.DEFAULT_HORIZON_DAYS,
    max_slots: int = vocabulary.DEFAULT_MAX_SLOTS,
) -> list[dict[str, Any]]:
    """The union of every offered Meeting Type's slots, earliest first.

    The research says the node chooses "the **Meeting Type(s)** to offer", so a
    node may offer several and the prospect picks among all of them. The union is
    sorted and de-duplicated on the start instant, because two Meeting Types
    starting together are one choice, not two.
    """
    seen: dict[str, dict[str, Any]] = {}
    for meeting_type in meeting_types:
        for slot in offer_slots(
            seller,
            meeting_type,
            now=now,
            horizon_days=horizon_days,
            max_slots=max_slots,
        ):
            seen.setdefault(slot["start"], slot)
    return sorted(seen.values(), key=lambda slot: slot["start"])[:max_slots]


def book_block(
    seller: dict[str, Any],
    slot: dict[str, Any],
) -> list[dict[str, Any]]:
    """The seller's busy blocks with this slot's block added.

    Returned rather than written, because the caller holds the store. The commit
    must add the block in the same write as the booking, or two prospects who saw
    the same free slot could both take it.
    """
    blocks = list(seller.get("busy") or [])
    blocks.append({"start": slot.get("start"), "end": slot.get("end")})
    return blocks
