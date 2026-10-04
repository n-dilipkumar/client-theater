"""The single combined availability window, and why it is a union.

Step 3 of the researched flow says:

    "Chili Piper evaluates the distribution and returns a single combined
    availability window."

and the data flow reads "union/intersection of member calendars -> slot list".
The research does not say which of the two operations belongs to which mode. The
issue requires the implementer to derive it and record the derivation rather than
assume it, so the derivation is stated here in full and is served at
``GET /api/wf054/inferences``.

The derivation
--------------

**A union, for both modes.** An instant is on offer when at least one *licensed,
calendar-connected* member is free for the whole slot.

Four pieces of evidence fix it, and one of them settles the question on its own:

1. **The combined window has to survive a real team.** An intersection of
   free/busy is empty as soon as any two members hold a busy block at the same
   time, and on a working team that is nearly always. The research promises *one
   combined window*, and an intersection routinely produces none.

2. **"flexible (weighted by availability)" measures a volume.** A weight can only
   be computed from a quantity that varies across members. Under an intersection
   every offered slot is free for every member, so every member's availability is
   identical and every weight is equal. Flexible would collapse into Strict,
   which the research presents as two modes that are not interchangeable.

3. **The license gate excludes a member from the combination, not just from the
   choice.** "if any prospects match to an unlicensed user, they will not be able
   to book a meeting and route to the Not Scheduled path." Narrowing the window by
   the calendar of someone who cannot take the meeting would offer a prospect a
   time no eligible rep can hold.

4. **Reassignment reopens the same Distribution against a changed calendar.** "the
   same ``Distribution`` context is reused later for reassignment", and
   "round-robin credit state moves with the host". A union keeps a slot on offer
   after the member who was free at it has taken a booking and their calendar has
   narrowed, which is exactly the state reassignment produces.

**Which is why booking re-checks.** A union cannot promise that the member the
distribution chose is still free at the instant the prospect picked, because
another booking may have landed in between. :func:`free_members_at` exists so the
booking step can confirm it, and the engine advances the distribution to the next
eligible member when the chosen one is not free. That re-check is the price of
the union, and paying it is cheaper than an empty window.

Jev was asked which of the two rules is correct and selected ``union_with_recheck``
at confidence 1.00, audit ``jev-20261004T045227-22564-47815``. The first ask,
which offered three options, returned ``uncertain`` at 0.44
(``jev-20261004T045140-22932-00253``) because the options were too close to
separate on the evidence given. The narrowed re-ask is the one that is enforced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from dsr.round_robin.errors import RoundRobinError
from dsr.round_robin.teams import parsed_blocks
from dsr.round_robin.timeutil import free_minutes, grid, is_free, iso

#: How many slots one evaluation may return, and how many days one interval may
#: span. Both bounded because the interval arrives in a request body and an
#: unbounded one is an easy way to make a page unusable.
MAX_SLOTS = 200
MAX_RANGE_DAYS = 120

#: The derivation's identifier, referenced from ``vocabulary.CALENDAR_COMBINATION``.
DERIVATION_ID = "inference_calendar_combination"


def normalise_interval(
    interval: Mapping[str, Any] | None, *, default_minutes: int = 30
) -> dict[str, Any]:
    """Read the researched ``interval`` object into a normalised one.

    The researched init call sends ``{"link": {...}, "interval": {...}}`` and the
    research does not give the interval's fields. This build takes ``start``,
    ``end``, ``duration_minutes``, ``max_days`` and ``min_notice_minutes``; an
    absent start means "now", and an absent end means ``max_days`` after it. A
    distribution whose interval has already closed is refused rather than
    answered with no slots, because that is a configuration the caller can fix.
    """
    body = dict(interval or {})
    # Presence, not truthiness: an explicit 0 must be refused rather than
    # silently replaced by the default. `body.get("max_days") or 14` would read a
    # caller's `max_days: 0` as absent and answer with a fortnight they did not
    # ask for, which is the kind of thing that only shows up as a wrong window.
    duration = int(body["duration_minutes"]) if "duration_minutes" in body else default_minutes
    if duration <= 0:
        raise RoundRobinError(f"duration_minutes must be positive; got {duration}")
    max_days = int(body["max_days"]) if "max_days" in body else 14
    if max_days <= 0:
        raise RoundRobinError(f"max_days must be positive; got {max_days}")
    if max_days > MAX_RANGE_DAYS:
        raise RoundRobinError(f"max_days may be at most {MAX_RANGE_DAYS}; got {max_days}")

    from dsr.round_robin.timeutil import parse, utcnow

    start_text = body.get("start")
    if start_text:
        start = parse(start_text)
    else:
        start = utcnow()
    end_text = body.get("end")
    if end_text:
        end = parse(end_text)
    else:
        from datetime import timedelta

        end = start + timedelta(days=max_days)
    if end <= start:
        raise RoundRobinError("the interval must end after it starts")

    notice = int(body["min_notice_minutes"]) if "min_notice_minutes" in body else 0
    if notice < 0:
        raise RoundRobinError("min_notice_minutes cannot be negative")

    return {
        "start": iso(start),
        "end": iso(end),
        "duration_minutes": duration,
        "max_days": max_days,
        "min_notice_minutes": notice,
    }


def free_members_at(
    members: Sequence[Mapping[str, Any]], start: datetime, end: datetime
) -> list[str]:
    """The ids of the licensed, connected members free for all of ``[start, end)``.

    The booking-time re-check. A member with no connected calendar is not free
    here: their free time is unreadable, and treating unreadable as free is how
    a prospect ends up booked onto a rep who never accepted.
    """
    from dsr.round_robin.teams import exclusion_reason, member_id

    free: list[str] = []
    for member in members:
        if exclusion_reason(member) is not None:
            continue
        if is_free(parsed_blocks(member), start, end):
            free.append(member_id(member))
    return free


def combined_window(
    members: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
    duration_minutes: int,
    min_notice_minutes: int = 0,
    now: datetime | None = None,
    limit: int = MAX_SLOTS,
) -> dict[str, Any]:
    """The one combined availability window, and each member's free minutes in it.

    A slot is on offer when at least one licensed, connected member is free for
    its whole duration, and it is annotated with the member ids that are free at
    that instant. ``min_notice_minutes`` pushes the first bookable instant forward
    so a prospect cannot take a meeting that starts as they submit the form.

    The per-member free minute counts are what Flexible weights by, so they are
    computed here rather than recomputed by the selection.
    """
    if end <= start:
        raise RoundRobinError("the availability range must end after it starts")

    from datetime import timedelta

    from dsr.round_robin.teams import calendar_connected, exclusion_reason, is_licensed, member_id

    reference = now or start
    # The notice runs from the later of "now" and the interval's own start. From
    # `start` alone, a distribution whose interval opens inside the notice window
    # would have every slot before the notice and answer with nothing, which is
    # the wrong reason to offer no times.
    earliest = max(reference, start) + timedelta(minutes=int(min_notice_minutes))

    free_by_member: dict[str, int] = {}
    readable = [member for member in members if is_licensed(member) and calendar_connected(member)]
    for member in readable:
        free_by_member[member_id(member)] = free_minutes(parsed_blocks(member), start, end)

    slots: list[dict[str, Any]] = []
    for slot_start, slot_end in grid(start, end, duration_minutes, limit):
        if slot_start < earliest:
            continue
        free_here = free_members_at(members, slot_start, slot_end)
        if not free_here:
            continue
        slots.append(
            {
                "start_at": iso(slot_start),
                "end_at": iso(slot_end),
                "duration_minutes": int(duration_minutes),
                "free_member_ids": free_here,
                "free_count": len(free_here),
            }
        )

    excluded = [
        {"member_id": member_id(member), "reason": reason}
        for member in members
        if (reason := exclusion_reason(member))
    ]
    return {
        "start": iso(start),
        "end": iso(end),
        "duration_minutes": int(duration_minutes),
        "min_notice_minutes": int(min_notice_minutes),
        "operation": "union",
        "slots": slots,
        "slot_count": len(slots),
        "free_minutes_by_member": free_by_member,
        "total_free_minutes": sum(free_by_member.values()),
        "excluded_members": excluded,
        "derivation": DERIVATION_ID,
    }


def find_slot(window: Mapping[str, Any], start_at: str) -> dict[str, Any] | None:
    """The slot beginning exactly at ``start_at``, or ``None``.

    Exact match on the instant rather than a containment test, so booking 09:15
    against a 09:00-09:30 slot is refused rather than quietly rounded.
    """
    target = str(start_at)
    for slot in window.get("slots") or []:
        if str(slot.get("start_at")) == target:
            return dict(slot)
    return None


def explain_missing(window: Mapping[str, Any], start_at: str) -> str:
    """Why a requested time is not on offer, in a sentence a rep can act on.

    "No such slot" on its own is a dead end for someone who typed 14:17. The two
    cases that actually happen are that no licensed, connected member is free then,
    and that the time is before the interval's minimum notice.
    """
    if find_slot(window, start_at) is not None:
        return "the requested time is available"
    text = str(start_at)
    slots = list(window.get("slots") or [])
    following = next((slot for slot in slots if str(slot["start_at"]) > text), None)
    if following is None:
        excluded = window.get("excluded_members") or []
        if excluded:
            names = ", ".join(str(entry["member_id"]) for entry in excluded)
            return (
                f"{text} is not on offer. The interval closes at {window.get('end')}, and "
                f"these members are excluded from assignment: {names}"
            )
        return (
            f"{text} is not on offer. It falls outside the interval {window.get('start')} to "
            f"{window.get('end')}, or inside the minimum notice"
        )
    return (
        f"{text} is not on offer: no licensed member is free for the whole slot. The next open "
        f"slot is {following['start_at']}"
    )
