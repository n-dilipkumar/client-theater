"""Routing paths: the admin's declaration of where a lead goes.

The researched step 1 is:

    "Admin builds a **Handoff Router** in the workspace, defining routing paths
    (e.g. region -> AE pod, product line -> AE)."

So a path names one assignee, which is what separates this workflow from the round
robin one that sits beside it in this repository. A round robin path names a team
and chooses among them; a handoff path names **one AE**, and the whole evaluation
is about which paths apply and what each of those two AEs is free for.

``invitees`` and the Required toggle
------------------------------------

The researched feature list names the field and its default in one line:

    "``Additional Invitee(s)`` (e.g. always invite an SE or manager; toggle
    ``Required`` to include their availability)"

and the evidence states the consequence of leaving it alone:

    "Chili Piper will not consider their availability while displaying the
    calendar unless you toggle the Required button."

So an invitee is added to the meeting either way, and the toggle decides whether
their calendar narrows the path's ``startTimes``. That default is **off**, and it
is off because the evidence says their availability is not considered *unless* the
button is toggled. This is the one place in the package where an absent flag means
the restrictive answer rather than the permissive one, and the reason is a quoted
sentence rather than a house style: a not-required invitee is normally someone
optional such as a manager, and making their calendar mandatory by default would
empty most paths.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.handoff_scheduler.errors import HandoffError
from dsr.handoff_scheduler.workspaces import (
    ASSIGNEE,
    calendar_connected,
    has_role,
    require_user,
    role_article,
    roles_of,
    user_id,
)

#: The default for the researched ``Required`` toggle. Off, because the evidence
#: says availability is not considered unless the button is toggled.
DEFAULT_REQUIRED = False


def path_id(path: Mapping[str, Any]) -> str:
    """The researched ``pathId`` of a routing path.

    Read off ``path_id`` and falling back to ``id``. The researched vocabulary
    spells the field ``pathId`` in camel case in the API and this build stores it
    snake case, so both are read; the vocabulary entry records the mapping rather
    than leaving a reader to find it.
    """
    return str(path.get("path_id") or path.get("pathId") or path.get("id") or "")


def required_of(invitee: Mapping[str, Any]) -> bool:
    """Whether an invitee's availability narrows the path.

    Absent means not required, which is the researched default and the opposite
    of the permissive default the rest of this package uses. The reason is in the
    module docstring and on the vocabulary entry.
    """
    return bool(invitee.get("required", DEFAULT_REQUIRED))


def validate_path(entry: Mapping[str, Any], workspace: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one declared routing path, refusing one that cannot be used.

    Refused rather than repaired:

    * no ``path_id``, because the researched schedule call carries it in the path
      and nothing could book the route without it;
    * no assignee, because "region -> AE pod, product line -> AE" is a statement
      about a person and a path with nobody at the end routes nowhere;
    * an assignee who is not on the workspace, or who cannot hold the assignee
      role, or whose calendar is not connected;
    * an invitee who is not on the workspace, or who is the assignee, or who is
      listed twice. The assignee is refused as an invitee because the meeting
      already carries them, and listing them twice in two roles is a declaration
      the SDR would have to unpick at the invite list.
    """
    if not isinstance(entry, Mapping):
        raise HandoffError(f"a routing path is not an object: {entry!r}")

    identifier = path_id(entry)
    if not identifier:
        raise HandoffError(
            "a routing path needs a path_id; the researched schedule call carries it"
        )

    assignee = require_user(
        workspace, entry.get("assignee_ref") or entry.get("assignee"), role=ASSIGNEE
    )
    if not calendar_connected(assignee):
        raise HandoffError(
            f"path {identifier} is assigned to {user_id(assignee)}, whose calendar is not connected. "
            "Their free time is unreadable, so a meeting could be offered that they cannot hold"
        )

    declared_match = entry.get("match") or {}
    if not isinstance(declared_match, Mapping):
        raise HandoffError(f"path {identifier} has a match block that is not an object")

    raw_invitees = entry.get("invitees") or []
    if isinstance(raw_invitees, (str, bytes)) or not isinstance(raw_invitees, Sequence):
        raise HandoffError(f"path {identifier} has an invitees value that is not a list")
    invitees: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_invitees:
        if not isinstance(raw, Mapping):
            raise HandoffError(f"path {identifier} has an invitee that is not an object: {raw!r}")
        invitee = require_user(
            workspace, raw.get("user_ref") or raw.get("user_id") or raw.get("user")
        )
        reference = user_id(invitee)
        if reference == user_id(assignee):
            raise HandoffError(
                f"path {identifier} lists its assignee {reference} as an invitee. "
                "The meeting already carries them as the Assignee"
            )
        if reference in seen:
            raise HandoffError(f"path {identifier} lists invitee {reference} twice")
        seen.add(reference)
        invitees.append(
            {
                "user_ref": reference,
                "name": str(invitee.get("name") or reference),
                "email": str(invitee.get("email") or ""),
                "roles": roles_of(invitee),
                "required": required_of(raw),
            }
        )

    return {
        "path_id": identifier,
        "name": str(entry.get("name") or identifier),
        "assignee_ref": user_id(assignee),
        "assignee_name": str(assignee.get("name") or user_id(assignee)),
        "assignee_email": str(assignee.get("email") or ""),
        "assignee_roles": roles_of(assignee),
        "match": dict(declared_match),
        "invitees": invitees,
        "note": str(entry.get("note") or ""),
    }


def validate_paths(
    payload: Mapping[str, Any], workspace: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Normalise every declared routing path, refusing a duplicate ``path_id``.

    A duplicate would make the researched schedule call ambiguous: two paths would
    answer to the same ``pathId`` and the one chosen would be whichever the store
    read first.
    """
    declared = payload.get("paths")
    if isinstance(declared, (str, bytes)) or not isinstance(declared, Sequence) or not declared:
        raise HandoffError(
            "a Handoff Router needs at least one routing path. "
            "The researched step is 'defining routing paths (e.g. region -> AE pod)'"
        )
    paths = [validate_path(entry, workspace) for entry in declared]
    seen: set[str] = set()
    for path in paths:
        if path["path_id"] in seen:
            raise HandoffError(f"router declares path {path['path_id']} twice")
        seen.add(path["path_id"])
    return paths


def gating_user_ids(path: Mapping[str, Any]) -> list[str]:
    """The users whose availability narrows this path, in a stable order.

    The assignee first, because they are the person the lead is being handed to,
    then the required invitees in declaration order. A not-required invitee is
    absent, which is the researched consequence of leaving the toggle alone.
    """
    gating = [str(path.get("assignee_ref") or "")]
    for invitee in path.get("invitees") or []:
        if required_of(invitee):
            gating.append(str(invitee.get("user_ref") or ""))
    return [reference for reference in gating if reference]


def ignored_user_ids(path: Mapping[str, Any]) -> list[str]:
    """The invitees whose availability is deliberately not consulted.

    Reported rather than omitted, because "my manager was invited and the path had
    no slots" is the question this list exists to answer, and the answer is that
    their calendar was never read.
    """
    return [
        str(invitee.get("user_ref") or "")
        for invitee in path.get("invitees") or []
        if not required_of(invitee)
    ]


def assignee_can_be_assigned(path: Mapping[str, Any], workspace: Mapping[str, Any]) -> str | None:
    """Why this path's assignee can no longer be assigned, or ``None``.

    Re-checked at booking time rather than trusted from the declaration, because a
    workspace is edited after the router that reads it. An assignee whose calendar
    was disconnected between the routing opening and the booking landing must not
    receive the meeting.
    """
    reference = str(path.get("assignee_ref") or "")
    for user in (workspace.get("data") or workspace).get("users") or []:
        if user_id(user) != reference:
            continue
        if not has_role(user, ASSIGNEE):
            return (
                f"{reference} is not {role_article(ASSIGNEE)} {ASSIGNEE} on this workspace any more"
            )
        if not calendar_connected(user):
            return f"{reference} has no connected calendar any more"
        return None
    return f"{reference} is no longer on this workspace"
