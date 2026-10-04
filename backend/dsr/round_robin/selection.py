"""Choosing the member who takes the booking. Two modes, not interchangeable.

The research fixes both modes in one sentence:

    "Round Robin - a team link that rotates assignment across members, either
    strict (equal turns) or flexible (weighted by availability)"

**Strict** gives equal turns. It ignores weights entirely, because a weight is a
statement about relative capacity and equal turns is a statement that capacity is
not the point. The chosen member is the one with the fewest credits consumed this
cycle, ties broken on fewest turns, then on team member order. That last key is
what makes "equal turns" hold: without it two members who have each taken one
booking in a four-member team could keep taking them in preference to the two who
have taken none, and the rotation would never complete.

**Flexible** weights by availability. Each eligible member's weight is their
declared weight times their share of the combined free time, so a member with
three times the free time of the least available member is offered proportionally
more of the window. A member with no free time has a share of zero and is never
chosen. Ties break on fewest credits consumed, then on team member order, so the
result is deterministic.

Both modes skip a member who is not free at the instant the prospect chose. That
is the re-check the union forces, and it is why :func:`select_member` takes the
chosen slot rather than only the window: a member the window showed as free may
have taken another booking in between.

The cursor
----------

The distribution carries a ``cursor`` into its member list and a ``cycle``
counter. The cursor is advanced after every booking, whether or not the booking
was against the member the cursor pointed at, so the rotation advances on every
turn rather than only on some. A credit returned by a no-show is given back to
the member whose booking it was, and does not move the cursor backwards.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.round_robin.errors import NoEligibleMember, RoundRobinError
from dsr.round_robin.teams import (
    exclusion_reason,
    member_id,
    validate_members,
    weight_of,
)
from dsr.round_robin.vocabulary import FLEXIBLE, STRICT, require_mode


def ledger_entry(distribution: Mapping[str, Any], member: str) -> dict[str, Any]:
    """The ledger row for one member, defaulting to zero.

    A member the distribution has never seen reads as zero rather than missing,
    so a team that gains a member mid-cycle starts that member on an even footing
    with everyone else. That is the fair reading of equal turns.
    """
    ledger = distribution.get("credits") or {}
    entry = ledger.get(member) or {}
    if not isinstance(entry, Mapping):
        raise RoundRobinError(f"credit ledger entry for {member} is not an object: {entry!r}")
    return {
        "member_id": member,
        "credits_consumed": int(entry.get("credits_consumed") or 0),
        "turns_taken": int(entry.get("turns_taken") or 0),
        "bookings": int(entry.get("bookings") or 0),
        "credits_returned": int(entry.get("credits_returned") or 0),
    }


def ledger(distribution: Mapping[str, Any], members: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Every member's ledger row, in team member order."""
    return [ledger_entry(distribution, member_id(member)) for member in members]


def shares(window: Mapping[str, Any]) -> dict[str, float]:
    """Each member's share of the combined free time, from 0 to 1.

    Computed over the members that could actually be assigned, because an
    unlicensed member's free minutes are not part of the combined window: they
    are excluded from the window itself, so counting them here would hand the
    total to members nobody can book. When the combined window holds no free
    time at all, every share is zero and the caller falls back to equal turns.
    """
    counts = window.get("free_minutes_by_member") or {}
    total = int(window.get("total_free_minutes") or 0)
    if total <= 0:
        return {str(key): 0.0 for key in counts}
    return {str(key): int(value) / total for key, value in counts.items()}


def weight_scores(
    distribution: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    window: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Every eligible member's Flexible weight and why it is that number.

    Reported rather than only used, because "why did this prospect reach me" is
    asked often enough to deserve the arithmetic that answered it.

    The distribution's declared weights are indexed *first* and the team's member
    rows second. Order matters here: ``validate_members`` builds a dict keyed by
    member id, so whichever list comes last wins. Appending the member rows after
    the distribution's would let a member's own default of 1.0 overwrite a
    distribution that declared 50.0, which silently reduces every weighted
    distribution to an unweighted one.
    """
    declared = validate_members(list(members) + list(distribution.get("members") or []))
    shares_by_member = shares(window)
    scores: list[dict[str, Any]] = []
    for member in members:
        identifier = member_id(member)
        reason = exclusion_reason(member)
        share = float(shares_by_member.get(identifier, 0.0))
        weight = weight_of(member, declared)
        scores.append(
            {
                "member_id": identifier,
                "name": member.get("name"),
                "eligible": reason is None,
                "excluded_reason": reason,
                "weight": weight,
                "free_share": share,
                "free_minutes": int(
                    (window.get("free_minutes_by_member") or {}).get(identifier, 0)
                ),
                "score": round(weight * share, 6),
            }
        )
    return scores


def select_member(
    distribution: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    window: Mapping[str, Any],
    *,
    free_member_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The member who takes the booking, and the full ranking that chose them.

    ``free_member_ids`` is the slot's own annotation: the members free at the
    instant the prospect picked. Passing it is how the union's re-check happens
    here rather than after the fact. When it is omitted every eligible member is
    a candidate, which is what an administrator previewing a distribution wants.
    """
    mode = require_mode(distribution.get("mode"))
    if not members:
        raise NoEligibleMember("the distribution's team has no members")

    candidates: list[dict[str, Any]] = []
    for member in members:
        reason = exclusion_reason(member)
        if reason is not None:
            continue
        candidates.append(member)

    if not candidates:
        excluded = ", ".join(
            f"{member_id(member)} ({reason})"
            for member in members
            if (reason := exclusion_reason(member))
        )
        raise NoEligibleMember(
            "no member of this team can be assigned. Excluded: " + (excluded or "none")
        )

    if free_member_ids is not None:
        allowed = {str(identifier) for identifier in free_member_ids}
        free_now = [member for member in candidates if member_id(member) in allowed]
        if not free_now:
            raise NoEligibleMember(
                "no licensed member is free at the requested time. Free then: "
                + (", ".join(sorted(allowed)) or "nobody")
            )
        candidates = free_now

    order = {member_id(member): position for position, member in enumerate(members)}
    entries = {
        member_id(member): ledger_entry(distribution, member_id(member)) for member in members
    }
    scores = {score["member_id"]: score for score in weight_scores(distribution, members, window)}

    if mode == FLEXIBLE:
        ranked = sorted(
            candidates,
            key=lambda member: (
                -float(scores.get(member_id(member), {}).get("score") or 0.0),
                entries[member_id(member)]["credits_consumed"],
                order.get(member_id(member), 0),
            ),
        )
    elif mode == STRICT:
        ranked = sorted(
            candidates,
            key=lambda member: (
                entries[member_id(member)]["credits_consumed"],
                entries[member_id(member)]["turns_taken"],
                order.get(member_id(member), 0),
            ),
        )
    else:  # pragma: no cover - require_mode has already refused anything else
        raise RoundRobinError(f"mode {mode!r} is not a round robin mode")

    chosen = ranked[0]
    return {
        "member_id": member_id(chosen),
        "name": chosen.get("name"),
        "email": chosen.get("email"),
        "mode": mode,
        "rule": (
            "largest weight times free share"
            if mode == FLEXIBLE
            else "fewest credits consumed this cycle"
        ),
        "ledger_before": entries[member_id(chosen)],
        "scores": weight_scores(distribution, members, window),
        "ranking": [member_id(member) for member in ranked],
    }


def next_candidate(
    distribution: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    window: Mapping[str, Any],
    *,
    free_member_ids: Sequence[str] | None,
    after: str,
) -> dict[str, Any]:
    """The member who takes the booking when the first choice is not free.

    The union's re-check, made actionable. The chosen member is taken out of the
    candidate set and the selection runs again, so the rotation keeps its own
    rule rather than falling back to whoever happens to be next in the list.
    """
    remaining = [member for member in members if member_id(member) != str(after)]
    if not remaining:
        raise NoEligibleMember(
            f"{after} is the only member of this team, so there is no other member to advance to"
        )
    return select_member(distribution, remaining, window, free_member_ids=free_member_ids)


def advance(
    distribution: Mapping[str, Any], members: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The distribution's cursor and cycle after one booking.

    Pure: it reads the distribution and returns the two fields to persist, so the
    caller can write them in the same transaction as the booking. The cursor
    moves one position along the *eligible* members, so an unlicensed member
    does not consume a turn nobody should wait for, and the cycle counter
    increments when the cursor wraps.
    """
    eligible = [member_id(member) for member in members if exclusion_reason(member) is None]
    if not eligible:
        return {
            "cursor": int(distribution.get("cursor") or 0),
            "cycle": int(distribution.get("cycle") or 0),
        }
    cursor = int(distribution.get("cursor") or 0)
    cycle = int(distribution.get("cycle") or 0)
    cursor += 1
    if cursor >= len(eligible):
        cursor = 0
        cycle += 1
    return {"cursor": cursor, "cycle": cycle}
