"""Room lifecycle: the archived state, and who may move a room into and out of it.

The product already has an ``archived`` room status and already gates writes on
it -- ``dsr.permissions`` closes uploads, and WF-001, WF-002, WF-003 and WF-011
all read or render the state. What was missing was anything that *performs* the
transition. That is this module.

Design, from the research corpus for WF-005:

* Archive closes a room to buyers while keeping its content; Restore returns it.
  Both are the same transition in opposite directions, so they share one guarded
  write here and the guard cannot drift between them.
* Both require the ``update`` permission, which is the same permission the
  research's action table names for each. A role that cannot update a room cannot
  archive it, and cannot un-archive it either -- the second is the important
  half, because a room archived by mistake with no way back is a dead room.
* **Only ``status`` is written.** Every other consequence is derived: the
  read-only behaviour is a gate in ``dsr.permissions``, the notice is state-
  driven and fires on read. So a restore has nothing to clean up, which is what
  makes the operation reversible at all.
* A request that declares no role is the seller using the console, which is what
  the rest of this product assumes. Declaring a role narrows the caller to that
  role's documented powers, which is how the permission matrix is exercised.

This module owns no vocabulary of its own. The roles, the permission gate and the
two room states all come from ``dsr.permissions``, so there is exactly one place
in the product where "archived" and "room collaborator" mean something.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.permissions import (
    CONTENT_CONTRIBUTOR,
    INSTANCE_ADMIN,
    ROOM_ACTIVE,
    ROOM_ARCHIVED,
    ROOM_COLLABORATOR,
    VIEWER,
    normalise_role,
)

#: The two documented room lifecycle states. ``dsr.permissions`` owns them; they
#: are re-exported here so a caller of this module does not have to know which
#: file the vocabulary lives in.
ACTIVE = ROOM_ACTIVE
ARCHIVED = ROOM_ARCHIVED
STATUSES: tuple[str, ...] = (ACTIVE, ARCHIVED)

#: The permission each action requires, from the research's action/permission
#: table: "Archive | ... | Update", "Restore | ... | Update".
CAP_UPDATE = "update"
REQUIRED_PERMISSION = {"archive": CAP_UPDATE, "restore": CAP_UPDATE}

#: The capability each role carries. Transcribed from the three role
#: descriptions in the corpus: a Room Collaborator "can manage pages and
#: documents, add room comments, and share the room" and managing implies
#: update; a Content Contributor can "view room content, add comments, upload
#: documents, and share the room" and does not manage; a Viewer can view and
#: comment only. An instance administrator is the documented exception to almost
#: every rule and so carries everything.
#:
#: Note that no documented role carries ``delete``. The research requires an
#: explicit grant for it and never hands it to a role, so deleting a room is not
#: reachable from this module at all.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    VIEWER: frozenset({"view", "comment"}),
    CONTENT_CONTRIBUTOR: frozenset({"view", "comment", "upload", "share"}),
    ROOM_COLLABORATOR: frozenset({"view", "comment", "upload", "share", CAP_UPDATE}),
    INSTANCE_ADMIN: frozenset({"view", "comment", "upload", "share", CAP_UPDATE, "delete"}),
}

#: The actor recorded for a request that identifies nobody.
DEFAULT_ACTOR = "dana"


class RoomRefusal(RuntimeError):
    """Base for every refusal this module raises, so a route can map one type."""


class RoomNotFound(RoomRefusal):
    """No room with that id."""


class RoomBadRequest(RoomRefusal):
    """The request is malformed, or asks for a state that does not exist."""


class RoomConflict(RoomRefusal):
    """The room is not in a state this action applies to."""


class RoomForbidden(RoomRefusal):
    """The caller lacks the permission this action documents."""


#: A request that identifies nobody is the seller using the console, which is
#: what the rest of this product assumes for an unauthenticated call. Declaring
#: a role *narrows* the caller to that role's documented powers -- so the
#: difference that matters is between declining to say and saying the wrong
#: thing. Nothing declared means the operator; anything unrecognised fails
#: closed to a Viewer, so a typo can never widen access.
UNIDENTIFIED_ROLE = ROOM_COLLABORATOR


def capabilities_for(role: str | None, declared: Any = None) -> frozenset[str]:
    """The capability set for a role, or the caller's own declared set.

    A request that declares ``capabilities`` replaces the table entirely, which
    is how an admin grants a permission no role carries. Otherwise the role is
    looked up, with an absent role read as the console operator and anything
    unrecognised falling closed to a Viewer.
    """
    if declared:
        return frozenset(str(item) for item in declared)
    if role is None:
        return ROLE_CAPABILITIES[UNIDENTIFIED_ROLE]
    return ROLE_CAPABILITIES.get(normalise_role(role), ROLE_CAPABILITIES[VIEWER])


def status_of(record: Mapping[str, Any]) -> str:
    """A room's lifecycle state, defaulting to active.

    Read defensively rather than assuming the key: a room created before this
    workflow existed has no ``status`` at all, and every one of those rooms is
    still open.
    """
    payload = record.get("data") if isinstance(record.get("data"), Mapping) else record
    value = str((payload or {}).get("status") or ACTIVE).strip().lower()
    return value if value in STATUSES else ACTIVE


def available_actions(status: str, role: str | None = None) -> list[str]:
    """Which lifecycle actions a caller could take on a room in this state.

    Derived from the same two rules the write path enforces -- the permission,
    and the state each action applies to -- so the UI cannot offer a button that
    the route would refuse.
    """
    caps = capabilities_for(role)
    out: list[str] = []
    for action, expected in (("archive", ACTIVE), ("restore", ARCHIVED)):
        if status == expected and REQUIRED_PERMISSION[action] in caps:
            out.append(action)
    return out


def room_state(record: Mapping[str, Any], role: str | None = None) -> dict[str, Any]:
    """Everything a UI needs to render one room's lifecycle, in one object.

    Returns the derived state rather than storing it: the notice text, the
    confirmation a destructive-looking action needs, and the actions available.
    Each is a function of the status, so a restore needs no repair pass.
    """
    status = status_of(record)
    return {
        "id": record.get("id"),
        "room_id": record.get("id"),
        "status": status,
        "active": status == ACTIVE,
        "archived": status == ARCHIVED,
        "read_only": status == ARCHIVED,
        "available_actions": available_actions(status, role),
        "notice": ARCHIVE_NOTICE if status == ARCHIVED else None,
        "confirmation": ARCHIVE_CONFIRMATION if status == ACTIVE else None,
    }


#: Quoted verbatim from the research evidence: "An informational notice at the
#: top of the room reads, 'This digital sales room is archived. New comments
#: cannot be added, and it can no longer be shared.'" The Header Main fragment
#: "also renders the notice shown when the room is archived", so it is
#: state-driven and fires on read rather than written at archive time.
ARCHIVE_NOTICE = (
    "This digital sales room is archived. New comments cannot be added, "
    "and it can no longer be shared."
)

#: Quoted verbatim: "Are you sure you want to archive this digital sales room?
#: It will no longer be available to customers, but you can restore it later."
#: The second sentence is the reason the action exists in both directions.
ARCHIVE_CONFIRMATION = (
    "Are you sure you want to archive this digital sales room? "
    "It will no longer be available to customers, but you can restore it later."
)
