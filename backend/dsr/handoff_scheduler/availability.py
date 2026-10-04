"""Per-path availability, and why a path's window is an intersection.

The evidence gives the shape of the answer twice:

    "Handoff uses the rules in a Handoff router to evaluate availability and
    returns time slots per routing path."

    "The init response returns one or more routing paths, each with its own
    ``pathId`` and ``startTimes``."

and it gives the effect of the ``Required`` toggle on the extra invitees:

    "Chili Piper will not consider their availability while displaying the
    calendar unless you toggle the Required button."

What the evidence does not say is which calendar operation combines the people on
one path. The issue requires an implementer who needs a flow the evidence does not
contain to derive it and record the derivation, so it is derived here and served at
``GET /api/wf-055/inferences``.

The derivation
--------------

**A path's ``startTimes`` are the intersection of its assignee's free time with the
free time of every invitee whose ``Required`` toggle is on. A not-required
invitee's calendar is not read at all.**

The second half is not a derivation. It is the quoted sentence about the toggle,
and it is why :func:`ignored_user_ids` reports those users rather than dropping
them.

The first half is a derivation, and one piece of evidence settles it on its own:

1. **A path names one AE.** "region -> AE pod, product line -> AE". There is no
   choice to make inside a path, so there is nothing for a union to choose
   between. Under a union the path would offer an instant when its own AE is busy,
   and the SDR would book a meeting the AE cannot attend.
2. **The union belongs to the neighbouring workflow, and the contrast is the
   argument.** WF-054, one directory along in this repository, derives a **union**
   for a round robin distribution, and puts the derivation in the same place this
   one is. A distribution names a *team*, so a union is right there: an instant is
   on offer when at least one licensed member is free, and the booking re-checks
   which member that is. A handoff path names a *person*, so the same operation
   would be wrong. Two workflows in one product choosing opposite operations is
   only defensible if the reason for the difference is written down, and it is.
3. **Reassignment survives it.** "reassignment later respects your Handoff/ChiliCal
   User controls and the Distribution settings of the meeting booked." An
   intersection answers precisely: the AE has to be free, because the meeting will
   sit on the AE's calendar.
4. **The gate set is small enough to intersect.** A path names one assignee and a
   handful of invitees. An intersection of two to four real calendars is routinely
   non-empty over a working week, which is what makes it usable. The same
   operation over a whole team's calendars would not be, which is the other half
   of why WF-054 needed a union and this does not.

**Which is why booking re-checks the gate set rather than trusting the list.**
The offered slots were computed when the routing opened, and an AE may have taken
another meeting since. :func:`busy_at` exists so the schedule step can confirm
the slot, and the engine refuses with the name of whoever took it. Refusing is the
right answer here where WF-054 advances the rotation: there is nobody else on the
path to advance to, and quietly booking a different AE would hand the lead to
somebody the SDR did not pick.

Jev was asked which of the two operations is correct for this subject and selected
``intersection_with_gate_recheck`` at confidence 1.00, audit
``jev-20261004T065905-27100-45006``. The evidence in that ask carried the contrast
with WF-054's union explicitly, because the whole argument rests on the two
workflows naming different subjects rather than on this one being right in the
abstract.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.handoff_scheduler.errors import HandoffError
from dsr.handoff_scheduler.paths import gating_user_ids, ignored_user_ids, required_of
from dsr.handoff_scheduler.timeutil import grid, is_free, iso, parse
from dsr.handoff_scheduler.workspaces import (
    calendar_connected,
    find_user,
    parsed_blocks,
    user_id,
)

#: How many slots one path may return, and how many days one interval may span.
#: Both bounded because the interval arrives in a request body and an unbounded one
#: is an easy way to make a page unusable.
MAX_SLOTS = 200
MAX_RANGE_DAYS = 120

#: The derivation's identifier, referenced from ``vocabulary.PATH_AVAILABILITY``.
DERIVATION_ID = "inference_path_availability_is_intersection"


def normalise_interval(
    interval: Mapping[str, Any] | None, *, default_minutes: int = 30
) -> dict[str, Any]:
    """Read the researched ``interval`` object into a normalised one.

    The researched init call sends ``interval`` alongside the request and the
    research does not give its fields. This build takes ``start``, ``end``,
    ``duration_minutes``, ``max_days`` and ``min_notice_minutes``; an absent start
    means "now", and an absent end means ``max_days`` after it. A caller's interval
    overrides the router's, which is what the researched payload implies: it sends
    its own ``interval`` next to the link.
    """
    body = dict(interval or {})
    # Presence, not truthiness: an explicit 0 must be refused rather than silently
    # replaced by the default. `body.get("max_days") or 14` would read a caller's
    # `max_days: 0` as absent and answer with a fortnight they did not ask for.
    duration = int(body["duration_minutes"]) if "duration_minutes" in body else default_minutes
    if duration <= 0:
        raise HandoffError(f"duration_minutes must be positive; got {duration}")
    max_days = int(body["max_days"]) if "max_days" in body else 14
    if max_days <= 0:
        raise HandoffError(f"max_days must be positive; got {max_days}")
    if max_days > MAX_RANGE_DAYS:
        raise HandoffError(f"max_days may be at most {MAX_RANGE_DAYS}; got {max_days}")

    from dsr.handoff_scheduler.timeutil import utcnow

    start_text = body.get("start")
    if start_text:
        start = parse(start_text)
    else:
        start = utcnow()
    end_text = body.get("end")
    if end_text:
        end = parse(end_text)
    else:
        end = start + timedelta(days=max_days)
    if end <= start:
        raise HandoffError("the interval must end after it starts")

    notice = int(body["min_notice_minutes"]) if "min_notice_minutes" in body else 0
    if notice < 0:
        raise HandoffError("min_notice_minutes cannot be negative")

    return {
        "start": iso(start),
        "end": iso(end),
        "duration_minutes": duration,
        "max_days": max_days,
        "min_notice_minutes": notice,
    }


def _gate_users(path: Mapping[str, Any], workspace: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The users whose free time narrows this path, resolved against the workspace.

    A gate user the workspace no longer carries is dropped here rather than
    refused, and :func:`path_window` reports it under ``unresolved_users``. The
    declaration was validated when the router was saved, so the only way to reach
    this is a workspace edited afterwards, and an SDR needs the routing to come
    back with the paths it can rather than a refusal over one stale row.
    """
    resolved: list[dict[str, Any]] = []
    for reference in gating_user_ids(path):
        user = find_user(workspace, reference)
        if user is not None:
            resolved.append(user)
    return resolved


def busy_at(users: Sequence[Mapping[str, Any]], start: datetime, end: datetime) -> list[str]:
    """Which of these users are busy for any part of ``[start, end)``.

    The booking-time re-check. A user with no connected calendar counts as busy
    rather than as free: their free time is unreadable, and treating unreadable as
    free is how a lead gets booked onto an AE who never accepted.
    """
    taken: list[str] = []
    for user in users:
        reference = user_id(user)
        if not calendar_connected(user) or not is_free(parsed_blocks(user), start, end):
            taken.append(reference)
    return taken


def path_window(
    path: Mapping[str, Any],
    workspace: Mapping[str, Any],
    *,
    start: datetime,
    end: datetime,
    duration_minutes: int,
    min_notice_minutes: int = 0,
    now: datetime | None = None,
    limit: int = MAX_SLOTS,
) -> dict[str, Any]:
    """The ``startTimes`` one routing path offers, and why.

    A slot is on offer when the assignee and every required invitee are free for
    its whole duration. ``min_notice_minutes`` pushes the first bookable instant
    forward so an SDR cannot book a handoff that starts as they submit it.

    The not-required invitees are named under ``ignored_user_ids`` and never read,
    which is the researched consequence of leaving the ``Required`` toggle alone.
    """
    if end <= start:
        raise HandoffError("the availability range must end after it starts")

    gating = _gate_users(path, workspace)
    reference = now or start
    # The notice runs from the later of "now" and the interval's own start. From
    # `start` alone, an interval that opens inside the notice window would have
    # every slot before the notice and answer with nothing, which is the wrong
    # reason to offer no times.
    earliest = max(reference, start) + timedelta(minutes=int(min_notice_minutes))

    slots: list[dict[str, Any]] = []
    for slot_start, slot_end in grid(start, end, duration_minutes, limit):
        if slot_start < earliest:
            continue
        if busy_at(gating, slot_start, slot_end):
            continue
        slots.append(
            {
                "start_at": iso(slot_start),
                "end_at": iso(slot_end),
                "duration_minutes": int(duration_minutes),
            }
        )

    all_busy = busy_at(gating, max(reference, start), end) if end > max(reference, start) else []
    unresolved = [
        reference_id
        for reference_id in gating_user_ids(path)
        if find_user(workspace, reference_id) is None
    ]

    return {
        "start": iso(start),
        "end": iso(end),
        "duration_minutes": int(duration_minutes),
        "min_notice_minutes": int(min_notice_minutes),
        "operation": "intersection_of_required_calendars",
        "gating_user_ids": [user_id(user) for user in gating],
        "ignored_user_ids": ignored_user_ids(path),
        "unresolved_user_ids": unresolved,
        "slots": slots,
        "slot_count": len(slots),
        "busy_user_ids": all_busy,
        "invitees": [
            {
                "user_ref": str(invitee.get("user_ref") or ""),
                "name": str(invitee.get("name") or ""),
                "required": required_of(invitee),
            }
            for invitee in path.get("invitees") or []
        ],
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
    """Why a requested time is not on offer, in a sentence an SDR can act on.

    The two cases that actually happen are that someone on the path's gate set is
    busy then, and that the time falls outside the interval or inside the minimum
    notice. The first names who, because "no slot" on its own tells an SDR nothing
    about whether the AE or the required SE took it.
    """
    if find_slot(window, start_at) is not None:
        return "the requested time is available"
    text = str(start_at)
    slots = list(window.get("slots") or [])
    following = next((slot for slot in slots if str(slot["start_at"]) > text), None)

    gating = [str(one) for one in window.get("gating_user_ids") or []]
    if gating and following is not None:
        return (
            f"{text} is not on offer. These people gate this path and one of them is busy then: "
            f"{', '.join(gating)}. The next open slot is {following['start_at']}"
        )
    if gating:
        return (
            f"{text} is not on offer, and no slot on this path is open for the whole interval "
            f"{window.get('start')} to {window.get('end')}. These people gate it: {', '.join(gating)}"
        )
    unresolved = [str(one) for one in window.get("unresolved_user_ids") or []]
    if unresolved:
        return (
            f"{text} is not on offer, and this path has nobody to gate it. These users are named "
            f"by the path but are no longer on the workspace: {', '.join(unresolved)}"
        )
    return (
        f"{text} is not on offer. It falls outside the interval {window.get('start')} to "
        f"{window.get('end')}, or inside the minimum notice"
    )
