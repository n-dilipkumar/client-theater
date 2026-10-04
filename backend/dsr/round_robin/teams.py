"""Teams, their members, and the license gate that decides who can be assigned.

The researched structure is a Team plus a Distribution that names it: "each
distribution consists of a Team, Round Robin type (Strict or Flexible), and Team
member's weights and credits". So a team here is a membership list, and the
weights and credits belong to the *distribution*, not to the member row. A team
member therefore carries identity, licensing and availability, and nothing about
how a particular distribution rotates through them.

That separation is load-bearing for two of the researched properties:

* "Distributions are reusable assets independent of the router." One team can be
  the member of several distributions, each with its own weights and its own
  cursor, and changing a weight in one must not change the other's rotation.
* "the same ``Distribution`` context is reused later for reassignment." The
  reassignment reads the distribution's ledger, not a copy of it frozen into the
  booking.

The license gate
-----------------

The research is unusually explicit that this is a hard gate:

    "All Team Members or Individuals you have assigned on this path must have a
    Concierge license assigned to them. Otherwise, if any prospects match to an
    unlicensed user, they will not be able to book a meeting and route to the Not
    Scheduled path."

Most implementations would model that as a warning on the team. It is modelled
here as exclusion: :func:`eligible_members` never returns an unlicensed member,
and :func:`combined_blocks` never counts an unlicensed member's busy time. The
last part is the less obvious half. An unlicensed member cannot take the meeting,
so narrowing the offered window by their calendar would offer a prospect a time
that no one eligible can hold.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.round_robin.errors import RoundRobinError
from dsr.round_robin.timeutil import parse

#: A member is either licensed or not. There is no third state, because the
#: research gives a binary: a Concierge license is assigned, or it is not.
LICENSED = "licensed"
UNLICENSED = "unlicensed"


def member_id(member: Mapping[str, Any]) -> str:
    """The stable id of a member row.

    Read off ``member_id`` and falling back to ``id``. A team member is stored as
    a list inside the team record rather than as its own record, so it has no
    record id of its own, and ``member_id`` is the field that identifies it
    across distributions.
    """
    return str(member.get("member_id") or member.get("id") or member.get("email") or "")


def is_licensed(member: Mapping[str, Any]) -> bool:
    """Whether a member carries a Concierge license.

    Absent means licensed. A team row written by an older importer that predates
    this flag should not silently exclude every member, which would make an
    upgrade look like a licensing failure. An explicit ``False`` is respected.
    """
    value = member.get("licensed", True)
    return bool(value)


def calendar_connected(member: Mapping[str, Any]) -> bool:
    """Whether a member's calendar is connected, so their free time is readable.

    Same default as the license gate: absent means connected. A member with no
    connected calendar has no readable free time, so their availability is
    reported as zero rather than as unknown, and the selection skips them.
    """
    value = member.get("calendar_connected", True)
    return bool(value)


def availability_blocks(member: Mapping[str, Any]) -> list[dict[str, str]]:
    """The member's busy blocks, in the shape the store round-trips.

    Each block is ``{"start": iso, "end": iso}``. A block with an unreadable
    timestamp raises rather than being dropped: dropping it would report a member
    as free for a period they are busy, and the prospect would be offered a slot
    the rep cannot hold.
    """
    raw = member.get("busy") or []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RoundRobinError(f"member {member_id(member)} has a busy list that is not a list")
    blocks: list[dict[str, str]] = []
    for block in raw:
        if not isinstance(block, Mapping):
            raise RoundRobinError(
                f"member {member_id(member)} has a busy entry that is not an object: {block!r}"
            )
        start = block.get("start")
        end = block.get("end")
        if not start or not end:
            raise RoundRobinError(
                f"member {member_id(member)} has a busy block without both a start and an end"
            )
        try:
            parse(start)
            parse(end)
        except ValueError as exc:
            raise RoundRobinError(
                f"member {member_id(member)} has an unreadable busy block: {exc}"
            ) from exc
        blocks.append({"start": str(start), "end": str(end)})
    return blocks


def parsed_blocks(member: Mapping[str, Any]) -> list[tuple[Any, Any]]:
    """The member's busy blocks as parsed datetimes."""
    return [(parse(block["start"]), parse(block["end"])) for block in availability_blocks(member)]


def weight_of(member: Mapping[str, Any], members: Mapping[str, Mapping[str, Any]]) -> float:
    """A member's weight, as declared on the distribution or on the member.

    The research puts weights on the distribution: "each distribution consists of
    a Team, Round Robin type (Strict or Flexible), and Team member's weights and
    credits". A member row may still carry a default so a team can be declared
    once and reused, which is the "reusable assets independent of the router"
    property. The distribution's weight wins when it declares one.

    A missing weight is 1.0 rather than 0.0, so a member nobody configured is
    still eligible. Defaulting to 0 would silently exclude members, which reads
    as a licensing failure and is not one.
    """
    declared = members.get(member_id(member), {}).get("weight")
    if declared is None:
        declared = member.get("weight", 1.0)
    try:
        value = float(declared)
    except (TypeError, ValueError) as exc:
        raise RoundRobinError(
            f"member {member_id(member)} has a weight that is not a number: {declared!r}"
        ) from exc
    if value < 0:
        raise RoundRobinError(f"member {member_id(member)} has a negative weight: {value}")
    return value


def validate_team(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a team declaration, refusing one that cannot be used.

    Refused rather than repaired:

    * no team name, so the team is not identifiable in a listing;
    * no members, so the distribution has nothing to rotate through;
    * a member with no id, so no weight, credit or decision could name it;
    * two members sharing an id, because the ledger would then credit one rep for
      two bookings and the rotation would skip a real person.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        raise RoundRobinError("a team needs a name")
    members = payload.get("members")
    if not isinstance(members, Sequence) or isinstance(members, (str, bytes)) or not members:
        raise RoundRobinError(f"team {name} needs at least one member")

    seen: set[str] = set()
    normalised: list[dict[str, Any]] = []
    for entry in members:
        if not isinstance(entry, Mapping):
            raise RoundRobinError(f"team {name} has a member that is not an object: {entry!r}")
        record = dict(entry)
        identifier = member_id(record)
        if not identifier:
            raise RoundRobinError(f"team {name} has a member with no member_id, id or email")
        if identifier in seen:
            raise RoundRobinError(f"team {name} lists member {identifier} twice")
        seen.add(identifier)
        record["member_id"] = identifier
        record["name"] = str(record.get("name") or identifier)
        record["email"] = str(record.get("email") or f"{identifier}@example.invalid")
        record["licensed"] = is_licensed(record)
        record["calendar_connected"] = calendar_connected(record)
        record["busy"] = availability_blocks(record)
        normalised.append(record)

    return {"name": name, "members": normalised}


def validate_members(members: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Index a member list by member id.

    Used to resolve the distribution's declared weights back onto the team's
    members, so a distribution may name weights for members the team does not
    list. Those are ignored rather than refused: the distribution may legitimately
    outlive a team edit.
    """
    return {member_id(member): member for member in members if member_id(member)}


def exclusion_reason(member: Mapping[str, Any]) -> str | None:
    """Why a member cannot be assigned, or ``None`` if they can.

    Two reasons, in the order an operator would fix them. A member with no
    Concierge license is excluded by the researched rule. A member whose calendar
    is not connected has no readable free time, so the distribution cannot know
    whether they are available; they are reported separately from the license
    gate because the fix is a calendar connection, not a licence purchase.
    """
    if not is_licensed(member):
        return "no Concierge license"
    if not calendar_connected(member):
        return "calendar not connected"
    return None


def eligible_members(
    members: Sequence[Mapping[str, Any]], *, ignore_availability: bool = False
) -> list[dict[str, Any]]:
    """The members a distribution may assign a booking to.

    ``ignore_availability`` is for the member listing an administrator reads, not
    for the selection: a member with a connected calendar but no free time in the
    window is eligible in principle and unavailable in practice, and the two
    states need different messages.
    """
    eligible: list[dict[str, Any]] = []
    for member in members:
        reason = exclusion_reason(member)
        if reason is None:
            eligible.append(dict(member))
    if ignore_availability:
        return eligible
    return eligible


def summarise(members: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts for the page header, over exactly the members listed."""
    excluded = [member for member in members if exclusion_reason(member)]
    return {
        "members": len(members),
        "eligible": len(members) - len(excluded),
        "excluded": len(excluded),
        "excluded_reasons": sorted(
            {str(reason) for member in excluded if (reason := exclusion_reason(member))}
        ),
    }
