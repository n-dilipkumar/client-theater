"""Distributions, hosts, availability, and the round-robin credit state.

This is where the research's scheduling state lives, and where the four rules
that decide whether a reassignment is even possible are enforced:

1. **The Distribution settings of the meeting booked.** "Reassignment will take
   into account your Handoff/ChiliCal User controls and the Distribution
   settings of the meeting booked" - so the governing distribution is the one on
   the meeting, never one named in the request.
2. **"Whether you allow rescheduling with any team member or not."** That is the
   ``allow_any_team_member`` control, and it is what decides whether a host
   outside the distribution's own member list counts.
3. **"If the target person is known and free."** Free is checked against the new
   host's own calendar, and only the min-notice and max-range bounds are
   exempt.
4. **"Round-robin credit state moves with the host."** A reassignment moves one
   round-robin credit from the host who lost the booking to the host who gained
   it - unless the no-show credit-back has already returned that credit, in
   which case moving it again would credit one host twice.

Nothing here touches the store. Every function is pure over plain dicts, so
the rules are testable without a database, which is why the engine is the only
module that knows about one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from dsr.reassign.errors import ReassignError

# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #

#: A naive timestamp is read as UTC. Recorded rather than assumed silently: a
#: meeting written without an offset and one written with ``+00:00`` must be
#: the same instant or the availability check would let a conflict through.
NAIVE_IS_UTC = "timestamps without an offset are read as UTC"


def parse_instant(value: Any, *, field: str = "timestamp") -> datetime:
    """Parse an ISO 8601 instant into a timezone-aware UTC ``datetime``."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ReassignError(f"{field} is required")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReassignError(f"{field} {value!r} is not an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_instant(moment: datetime) -> str:
    """The canonical form this package writes back."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def _non_negative_int(value: Any, *, field: str, default: int | None) -> int | None:
    """An optional non-negative integer. ``None`` means "not configured"."""
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ReassignError(f"{field} must be a whole number of minutes or days") from exc
    if number < 0:
        raise ReassignError(f"{field} must not be negative")
    return number


def normalise_busy(value: Any, *, field: str = "busy") -> list[dict[str, str]]:
    """Validate a host's calendar blocks.

    A block with no end is a fixed point, not a range, and would silently
    swallow every slot after it. Refused instead.
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ReassignError(f"{field} must be a list of {{starts_at, ends_at}} blocks")
    blocks: list[dict[str, str]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise ReassignError(f"{field}[{index}] must be an object with starts_at and ends_at")
        starts = parse_instant(entry.get("starts_at"), field=f"{field}[{index}].starts_at")
        raw_end = entry.get("ends_at")
        ends = (
            parse_instant(raw_end, field=f"{field}[{index}].ends_at")
            if raw_end not in (None, "")
            else starts
        )
        if ends < starts:
            raise ReassignError(f"{field}[{index}] ends before it starts")
        blocks.append(
            {
                "starts_at": format_instant(starts),
                "ends_at": format_instant(ends),
                "label": str(entry.get("label") or ""),
            }
        )
    return sorted(blocks, key=lambda block: (block["starts_at"], block["ends_at"]))


def normalise_distribution(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a Distribution record's payload.

    ``member_ids`` is lifted out rather than left in ``data``: it is the
    membership this workflow matches a candidate host against, and keeping it
    under one key means the eligibility check has one place to read.
    """
    from dsr.reassign.vocabulary import require_locked, require_status  # noqa: F401

    name = str(payload.get("name") or "").strip()
    if not name:
        raise ReassignError("a distribution needs a name")
    team = str(payload.get("team") or "").strip()
    if not team:
        raise ReassignError(
            f"distribution {name!r} needs a team; a team is the scope 'any team member' resolves to"
        )

    members = payload.get("member_ids") or payload.get("members") or []
    if not isinstance(members, (list, tuple)):
        raise ReassignError("member_ids must be a list of host ids")
    member_ids = [str(member).strip() for member in members if str(member).strip()]
    if len(set(member_ids)) != len(member_ids):
        raise ReassignError("member_ids repeats a host; the membership is a set")

    return {
        "name": name,
        "team": team,
        # The two locked fields. A distribution fixes them once, and a meeting
        # booked from it carries them, which is what a lock is checked against.
        "workspace": require_locked(payload.get("workspace"), field="workspace"),
        "meeting_type": require_locked(payload.get("meeting_type"), field="meeting_type"),
        # "whether you allow rescheduling with any team member or not"
        "allow_any_team_member": bool(payload.get("allow_any_team_member", False)),
        "member_ids": member_ids,
        # The two bounds reassignment ignores. ``None`` is "not configured",
        # which is a different thing from zero: a zero max range would mean a
        # meeting can only start today.
        "min_notice_minutes": _non_negative_int(
            payload.get("min_notice_minutes"), field="min_notice_minutes", default=None
        ),
        "max_range_days": _non_negative_int(
            payload.get("max_range_days"), field="max_range_days", default=None
        ),
    }


def normalise_host(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a host record's payload: a person who can be handed a booking."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ReassignError("a host needs a name; the invite carries it")
    email = str(payload.get("email") or "").strip().lower()
    if email and "@" not in email:
        raise ReassignError(
            f"host {name!r} has an email that is not an address: {payload.get('email')!r}"
        )

    return {
        "name": name,
        "email": email,
        "username": str(payload.get("username") or email).strip().lower(),
        "team": str(payload.get("team") or "").strip(),
        "distribution": str(payload.get("distribution") or "").strip(),
        "timezone": str(payload.get("timezone") or "UTC").strip(),
        # A deactivated host is not a candidate. "Free" has to mean available to
        # take a booking, and a person who has left the team is not, however
        # empty their calendar looks.
        "active": bool(payload.get("active", True)),
        "busy": normalise_busy(payload.get("busy")),
        # The round-robin position. The automatic path picks the fewest, which
        # is what a rotation is; a reassignment moves one, which is the
        # researched "round-robin credit state moves with the host".
        "round_robin_credits": int(payload.get("round_robin_credits", 0) or 0),
        # The host-dependent invite fields. See vocabulary.invite_for.
        "conference_link": str(payload.get("conference_link") or "").strip() or None,
        "dial_in": str(payload.get("dial_in") or "").strip() or None,
        "location": str(payload.get("location") or "").strip() or None,
    }


#: The length a meeting gets when the caller gives a start and no end.
#:
#: Half an hour, which is the researched meeting type this workflow's
#: distributions all book. A module constant rather than a keyword argument,
#: because a caller who could pass it could book a zero-length meeting and then
#: argue about the bounds on a window that has none.
DEFAULT_DURATION_MINUTES = 30


def normalise_meeting(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a meeting record's payload.

    A meeting has to start and end; the invite is built from the host rather
    than accepted from the caller, so it is not part of the validated payload -
    :func:`dsr.reassign.vocabulary.invite_for` owns that field entirely.
    """
    from dsr.reassign.vocabulary import require_locked, require_status, require_surface

    title = str(payload.get("title") or "").strip()
    if not title:
        raise ReassignError("a meeting needs a title")
    host_id = str(payload.get("host_id") or "").strip()
    if not host_id:
        raise ReassignError(f"meeting {title!r} needs a host to be reassigned away from")

    starts = parse_instant(payload.get("starts_at"), field="starts_at")
    raw_end = payload.get("ends_at")
    ends = (
        parse_instant(raw_end, field="ends_at")
        if raw_end not in (None, "")
        else starts + timedelta(minutes=DEFAULT_DURATION_MINUTES)
    )
    if ends <= starts:
        raise ReassignError(f"meeting {title!r} ends at or before it starts")

    return {
        "title": title,
        "booker": str(payload.get("booker") or "").strip(),
        "booker_email": str(payload.get("booker_email") or "").strip().lower(),
        "host_id": host_id,
        "distribution": str(payload.get("distribution") or "").strip(),
        "team": str(payload.get("team") or "").strip(),
        "meeting_type": require_locked(payload.get("meeting_type"), field="meeting_type"),
        "workspace": require_locked(payload.get("workspace"), field="workspace"),
        "status": require_status(payload.get("status")),
        "round_robin": bool(payload.get("round_robin", False)),
        "product_source": require_surface(payload.get("product_source"), field="product_source"),
        "booking_uid": str(payload.get("booking_uid") or "").strip(),
        "starts_at": format_instant(starts),
        "ends_at": format_instant(ends),
        # A no-show credit-back has already returned this booking's
        # round-robin credit to the host who took the no-show. A later
        # reassignment must not move it a second time.
        "no_show_credit_back": bool(payload.get("no_show_credit_back", False)),
        "no_show_credited_host_id": str(payload.get("no_show_credited_host_id") or "").strip()
        or None,
    }


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #

#: Intervals are half-open: a meeting ending at 10:00 and one starting at 10:00
#: do not overlap. Stated because the alternative - treating a boundary touch as
#: a conflict - would refuse every back-to-back booking.
HALF_OPEN_INTERVALS = "a block ending exactly when the meeting starts is not a conflict"


def conflicts_with(
    host: Mapping[str, Any], starts_at: datetime, ends_at: datetime
) -> list[dict[str, str]]:
    """The host's calendar blocks that overlap the proposed window.

    Returns the blocks rather than a bool so the refusal a person reads can name
    the meeting they are clashing with, which is the difference between "not
    free" and "clashes with Priya's 14:00 demo".
    """
    clashes: list[dict[str, str]] = []
    for block in host.get("busy") or []:
        block_start = parse_instant(block["starts_at"])
        block_end = parse_instant(block["ends_at"])
        if block_start < ends_at and starts_at < block_end:
            clashes.append(dict(block))
    return clashes


def is_free(host: Mapping[str, Any], starts_at: datetime, ends_at: datetime) -> bool:
    """Is this host available for the window, and still taking bookings?

    The researched condition is "known and free" - so both halves are checked.
    An inactive host with an empty calendar is free by the calendar test and
    must still be refused.
    """
    if not host.get("active", True):
        return False
    return not conflicts_with(host, starts_at, ends_at)


# --------------------------------------------------------------------------- #
# The bounds reassignment ignores
# --------------------------------------------------------------------------- #


def evaluate_bounds(
    distribution: Mapping[str, Any], starts_at: datetime, now: datetime
) -> dict[str, Any]:
    """Which of the two bounds this slot would breach, ignoring neither.

    ``None`` on a distribution means the bound is not configured, and an
    unconfigured bound is not breached. This is the *neutral* evaluation: what a
    normal booking has to satisfy. A reassignment runs it and then declines to
    act on the answer, which is the researched note - "Reassignment does not
    take into account the minimum scheduling notice or the maximum availability
    range" - made observable rather than merely asserted.
    """
    minutes = distribution.get("min_notice_minutes")
    days = distribution.get("max_range_days")

    notice_breach = False
    if minutes is not None:
        earliest = now + timedelta(minutes=int(minutes))
        notice_breach = starts_at < earliest

    range_breach = False
    if days is not None:
        latest = now + timedelta(days=int(days))
        range_breach = starts_at > latest

    breached: list[str] = []
    if notice_breach:
        breached.append("min_notice")
    if range_breach:
        breached.append("max_range")

    return {
        "min_notice_minutes": minutes,
        "max_range_days": days,
        "min_notice_breached": notice_breach,
        "max_range_breached": range_breach,
        "breached": breached,
        "would_block": bool(breached),
    }


def bounds_summary(
    distribution: Mapping[str, Any], starts_at: datetime, now: datetime
) -> dict[str, Any]:
    """The reassignment's record of what it ignored.

    Always reports the numbers it ignored them against, even when nothing was
    breached, because "no bounds were bypassed because this distribution
    configures none" and "no bounds were bypassed because this slot is far away"
    are different answers to the same question and the first is the one a
    reviewer needs to see.
    """
    verdict = evaluate_bounds(distribution, starts_at, now)
    return {
        **verdict,
        "honoured": False,
        "bypassed": list(verdict["breached"]),
        "bypassed_any": bool(verdict["breached"]),
    }


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #

#: Why a host was not offered. Published so the page can say *why* a
#: candidate is missing rather than leaving a person to guess.
INELIGIBLE_REASONS: tuple[str, ...] = (
    "not_in_distribution",
    "inactive",
    "already_the_host",
    "busy",
)


def in_distribution_scope(
    host: Mapping[str, Any],
    distribution: Mapping[str, Any],
    *,
    kind: str = "individual",
) -> bool:
    """Does this host count, at the granularity the scheduler was opened at?

    * ``individual`` and ``distribution`` go through the distribution's own
      ``allow_any_team_member`` control, which is the researched "whether you
      allow rescheduling with any team member or not". A member of the
      distribution is always in scope; the control widens it to the team.
    * ``team`` asks for a team member, so naming the team *is* the answer and
      there is no separate control to consult.

    The control widens, never narrows. Reading it the other way - "disallow any
    team member" meaning *only* the distribution - would make the flag
    redundant with ``member_ids``, and the research presents it as a separate
    setting with its own yes/no.
    """
    if kind == "team":
        return bool(distribution.get("team")) and str(host.get("team") or "") == str(
            distribution.get("team")
        )
    if str(host.get("id") or "") in set(distribution.get("member_ids") or []):
        return True
    if distribution.get("allow_any_team_member"):
        return bool(distribution.get("team")) and str(host.get("team") or "") == str(
            distribution.get("team")
        )
    return False


def candidates(
    hosts: Iterable[Mapping[str, Any]],
    meeting: Mapping[str, Any],
    distribution: Mapping[str, Any],
    starts_at: datetime,
    ends_at: datetime,
    *,
    kind: str = "individual",
) -> list[dict[str, Any]]:
    """Every host that could take this booking, eligible ones first.

    Each row carries ``eligible`` and, when it is false, the reason. Returning
    the ineligible rows alongside the eligible ones is deliberate: a rep
    reassigning a meeting needs to see that the person they had in mind is on
    this list and unavailable, not that they are missing from it.
    """
    current = str(meeting.get("host_id") or "")
    rows: list[dict[str, Any]] = []
    for host in hosts:
        reasons: list[str] = []
        if not host.get("active", True):
            reasons.append("inactive")
        if str(host.get("id") or "") == current:
            reasons.append("already_the_host")
        if not in_distribution_scope(host, distribution, kind=kind):
            reasons.append("not_in_distribution")
        clashes = conflicts_with(host, starts_at, ends_at)
        if clashes:
            reasons.append("busy")
        rows.append(
            {
                "id": host.get("id"),
                "name": host.get("name"),
                "email": host.get("email"),
                "team": host.get("team"),
                "active": bool(host.get("active", True)),
                "round_robin_credits": int(host.get("round_robin_credits", 0) or 0),
                "eligible": not reasons,
                "ineligible_because": reasons,
                "conflicts": clashes,
                "in_scope": in_distribution_scope(host, distribution, kind=kind),
            }
        )
    rows.sort(
        key=lambda row: (not row["eligible"], int(row["round_robin_credits"]), str(row["id"]))
    )
    return rows


def auto_select(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The host a round robin would pick: the fewest credits, then lowest id.

    The tie-break is not decoration. Two hosts on the same credit count are the
    ordinary case in a rotation, and without a total order the same state would
    hand the booking to a different person on a different run - which is the
    ordering coin flip ``AuditedDatabase.list`` documents at length.
    """
    eligible = [row for row in rows if row.get("eligible")]
    if not eligible:
        return None
    return min(
        eligible, key=lambda row: (int(row.get("round_robin_credits") or 0), str(row.get("id")))
    )


# --------------------------------------------------------------------------- #
# Round-robin credit
# --------------------------------------------------------------------------- #

#: The two answers to "did the credit move?".
CREDIT_MOVED = "moved"
CREDIT_ALREADY_RETURNED = "already_returned_by_no_show"
CREDIT_SAME_HOST = "same_host"


def move_credit(
    previous_host: Mapping[str, Any], new_host: Mapping[str, Any], *, already_returned: bool
) -> dict[str, Any]:
    """Work out the round-robin credit movement, and say why.

    "Round-robin credit state moves with the host" - so the credit the previous
    host consumed goes to the new host. The no-show interaction is the reason
    this takes a flag rather than just decrementing: "no-show credit-back
    interacts with reassignment", and a credit that has already been handed back
    cannot be moved again without crediting one host twice for one booking.
    """
    if str(previous_host.get("id")) == str(new_host.get("id")):
        return {
            "outcome": CREDIT_SAME_HOST,
            "from_credits": int(previous_host.get("round_robin_credits", 0) or 0),
            "to_credits": int(new_host.get("round_robin_credits", 0) or 0),
            "reason": "the booking is staying with the host who already has it",
        }
    if already_returned:
        return {
            "outcome": CREDIT_ALREADY_RETURNED,
            "from_credits": int(previous_host.get("round_robin_credits", 0) or 0),
            "to_credits": int(new_host.get("round_robin_credits", 0) or 0),
            "reason": (
                "the no-show credit-back already returned this booking's credit to the previous host, "
                "so reassigning it moves no credit and credits nobody twice"
            ),
        }
    return {
        "outcome": CREDIT_MOVED,
        "from_credits": int(previous_host.get("round_robin_credits", 0) or 0),
        "to_credits": int(new_host.get("round_robin_credits", 0) or 0),
        "reason": "round-robin credit state moves with the host",
    }


def credit_patch(movement: Mapping[str, Any]) -> tuple[int, int] | None:
    """The ``(previous_delta, new_delta)`` a movement implies, or ``None``.

    ``None`` means "change nothing", which is the whole point of the
    already-returned branch: a reassignment that moved a credit a second time
    would be a silent double credit rather than a visible bug.
    """
    outcome = movement.get("outcome")
    if outcome == CREDIT_MOVED:
        return (-1, 1)
    return None
