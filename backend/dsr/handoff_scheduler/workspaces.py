"""Workspaces, their users, and the gates that decide who may book and who may be booked.

The researched structure is a workspace plus the routers declared inside it:

    "Handoff uses the rules in a Handoff router to evaluate availability and
    returns time slots per routing path."

and the researched init path carries both halves of that scope:
``workspace/{workspaceId}/booker/{userId}/init-simple``. So a workspace here is one
SDR/AE pod with its user records, and a router belongs to exactly one of them.

The two roles
-------------

The research publishes both role names in one sentence:

    "meeting created with SDR as Booker and AE as Assignee"

so a user carries a ``roles`` list rather than a single ``role`` field. A list
rather than a third enum value because a user may be both in a small pod, and
inventing a ``both`` value would be a term the research does not publish. Absent
means both roles, so a user row written by an importer that predates this flag is
not silently unable to book or to be booked.

The calendar gate
-----------------

There is no researched licence rule in this workflow, so there is exactly one
gate: a user whose calendar is not connected has no readable free time. Treating
unreadable as free is how an SDR ends up booked onto an AE who never accepted,
so a user with no connected calendar cannot be assigned and cannot gate a path's
availability. The reason is reported in words, because the fix is a calendar
connection and an SDR needs to know that rather than infer a busy AE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.handoff_scheduler.errors import HandoffError
from dsr.handoff_scheduler.timeutil import parse

#: The two role names the research publishes. Lower snake case because they are
#: stored in ``records.data`` and read by a filter, not rendered.
BOOKER = "booker"
ASSIGNEE = "assignee"

ROLE_NAMES: tuple[str, ...] = (BOOKER, ASSIGNEE)


def role_article(role: str) -> str:
    """The indefinite article for a role, so a refusal reads as English.

    "not a assignee" is the sort of thing a reader stops trusting a message over.
    The article is derived from the role rather than passed at each raise site, so
    a third role cannot reintroduce the problem by being added without one.
    """
    return "an" if str(role)[:1].lower() in "aeiou" else "a"


def user_id(user: Mapping[str, Any]) -> str:
    """The stable id of a user row.

    Read off ``user_id`` and falling back to ``id`` and then to ``email``. A user
    is stored as a list inside the workspace record rather than as its own
    record, so it has no record id of its own, and ``user_id`` is the field that
    identifies it across routers.
    """
    return str(user.get("user_id") or user.get("id") or user.get("email") or "")


def roles_of(user: Mapping[str, Any]) -> list[str]:
    """The roles a user holds.

    Absent means both. A workspace row written by an older importer should not
    make every user unable to book and unable to be booked, which would read as a
    licensing failure rather than as a missing field. An explicit empty list is
    respected: a user nobody may book or assign is a thing an admin can mean.
    """
    declared = user.get("roles")
    if declared is None:
        return list(ROLE_NAMES)
    if isinstance(declared, str) or not isinstance(declared, Sequence):
        raise HandoffError(
            f"user {user_id(user)} has a roles value that is not a list: {declared!r}"
        )
    seen: list[str] = []
    for entry in declared:
        role = str(entry or "").strip()
        if role not in ROLE_NAMES:
            raise HandoffError(
                f"user {user_id(user)} has the role {role!r}, which is not one of "
                + ", ".join(ROLE_NAMES)
            )
        if role not in seen:
            seen.append(role)
    return seen


def has_role(user: Mapping[str, Any], role: str) -> bool:
    """Whether a user holds one role."""
    return role in roles_of(user)


def calendar_connected(user: Mapping[str, Any]) -> bool:
    """Whether a user's calendar is connected, so their free time is readable.

    Absent means connected, for the same reason absent roles mean both.
    """
    return bool(user.get("calendar_connected", True))


def busy_blocks(user: Mapping[str, Any]) -> list[dict[str, str]]:
    """The user's busy blocks, in the shape the store round-trips.

    Each block is ``{"start": iso, "end": iso}``. A block with an unreadable
    timestamp raises rather than being dropped: dropping it would report an AE as
    free for a period they are busy, and the SDR would be offered a slot the AE
    cannot hold.
    """
    raw = user.get("busy") or []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise HandoffError(f"user {user_id(user)} has a busy list that is not a list")
    blocks: list[dict[str, str]] = []
    for block in raw:
        if not isinstance(block, Mapping):
            raise HandoffError(
                f"user {user_id(user)} has a busy entry that is not an object: {block!r}"
            )
        start = block.get("start")
        end = block.get("end")
        if not start or not end:
            raise HandoffError(
                f"user {user_id(user)} has a busy block without both a start and an end"
            )
        try:
            parse(start)
            parse(end)
        except ValueError as exc:
            raise HandoffError(f"user {user_id(user)} has an unreadable busy block: {exc}") from exc
        blocks.append({"start": str(start), "end": str(end)})
    return blocks


def parsed_blocks(user: Mapping[str, Any]) -> list[tuple[Any, Any]]:
    """The user's busy blocks as parsed datetimes."""
    return [(parse(block["start"]), parse(block["end"])) for block in busy_blocks(user)]


def validate_workspace(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a workspace declaration, refusing one that cannot be used.

    Refused rather than repaired:

    * no workspace name, so the workspace is not identifiable in a listing;
    * no users, so a router declared in it can name nobody;
    * a user with no id, so no router path, invitee or booking could name them;
    * two users sharing an id, because the router would then route to whichever
      of the two happened to be read first.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HandoffError("a workspace needs a name")
    users = payload.get("users")
    if isinstance(users, (str, bytes)) or not isinstance(users, Sequence) or not users:
        raise HandoffError(f"workspace {name} needs at least one user")

    seen: set[str] = set()
    normalised: list[dict[str, Any]] = []
    for entry in users:
        if not isinstance(entry, Mapping):
            raise HandoffError(f"workspace {name} has a user that is not an object: {entry!r}")
        record = dict(entry)
        identifier = user_id(record)
        if not identifier:
            raise HandoffError(f"workspace {name} has a user with no user_id, id or email")
        if identifier in seen:
            raise HandoffError(f"workspace {name} lists user {identifier} twice")
        seen.add(identifier)
        record["user_id"] = identifier
        record["name"] = str(record.get("name") or identifier)
        record["email"] = str(record.get("email") or f"{identifier}@example.invalid")
        record["roles"] = roles_of(record)
        record["calendar_connected"] = calendar_connected(record)
        record["busy"] = busy_blocks(record)
        normalised.append(record)

    return {"name": name, "users": normalised}


def index_users(workspace: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index a workspace's user list by user id.

    Used to resolve a router path's assignee and its invitees back onto real user
    rows. A workspace validated by :func:`validate_workspace` has no duplicate
    ids, so the last write here cannot hide a user.
    """
    body = workspace.get("data") if "data" in workspace else workspace
    users = (body or {}).get("users") or []
    return {user_id(user): dict(user) for user in users if user_id(user)}


def find_user(workspace: Mapping[str, Any], reference: Any) -> dict[str, Any] | None:
    """The user a reference names, or ``None``.

    Read by ``user_id`` and falling back to email and then to name, because the
    researched init call names the booker by ``userId`` while an integrator
    configuring a router in a spreadsheet will have an email address to hand.
    """
    text = str(reference or "").strip()
    if not text:
        return None
    users = index_users(workspace)
    if text in users:
        return users[text]
    folded = text.lower()
    for user in users.values():
        if str(user.get("email") or "").lower() == folded:
            return user
    for user in users.values():
        if str(user.get("name") or "").lower() == folded:
            return user
    return None


def require_user(
    workspace: Mapping[str, Any], reference: Any, *, role: str | None = None
) -> dict[str, Any]:
    """The user a reference names, refused with a reason if there is none.

    The message names the users the workspace does have, because "user not found"
    on its own leaves an SDR looking at a router they can see.
    """
    user = find_user(workspace, reference)
    if user is None:
        known = ", ".join(sorted(index_users(workspace))) or "none"
        raise HandoffError(
            f"user {reference!r} is not on workspace "
            f"{str((workspace.get('data') or workspace).get('name') or workspace.get('id') or '')!r}. "
            f"It has these users: {known}"
        )
    if role is not None and not has_role(user, role):
        raise HandoffError(
            f"user {user_id(user)} is not {role_article(role)} {role} on this workspace. "
            f"Their roles are {', '.join(roles_of(user)) or 'none'}"
        )
    return user


def exclusion_reason(user: Mapping[str, Any], *, role: str | None = None) -> str | None:
    """Why a user cannot take the part asked of them, or ``None`` if they can.

    In the order an operator would fix them. A user whose calendar is not
    connected has no readable free time, so they cannot be assigned and cannot
    gate a path. A user who does not hold the role cannot be asked for it, which
    is a different fix: edit the workspace, do not connect a calendar.
    """
    if role is not None and not has_role(user, role):
        return f"not {role_article(role)} {role} on this workspace"
    if not calendar_connected(user):
        return "calendar not connected"
    return None


def summarise(users: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts for the page header, over exactly the users listed.

    Bookers and assignees are counted separately because the two numbers answer
    different questions: a pod with no booker cannot open a routing, and a pod
    with no assignee has nothing to route to.
    """
    bookers = [user for user in users if has_role(user, BOOKER)]
    assignees = [user for user in users if has_role(user, ASSIGNEE)]
    unconnected = [user for user in users if not calendar_connected(user)]
    return {
        "users": len(users),
        "bookers": len(bookers),
        "assignees": len(assignees),
        "calendar_not_connected": len(unconnected),
        "user_ids": [user_id(user) for user in users],
    }
