"""Role gates for the room's document library (WF-003).

Roles live in code, not in the database. That is deliberate: a role is a rule
about what a *caller* may do, and rules are versioned with the application. A
record's own fields stay free-form JSON so a team can add whatever its
documents need without coordinating with anyone.

Sourced
-------
Every rule below is taken from the vendor documentation recorded in
``docs/research/digital-sales-room-workflows/wf/WF-003.md``:

* "The *New* button appears only for Room Collaborators and Content
  Contributors, and neither can delete a document someone else uploaded."
* "In an archived room, only instance administrators see the *New* button."
* Room role descriptions: Room Collaborator "can manage pages and documents";
  Content Contributor can "upload documents"; Viewer "cannot upload documents".

Design inference
----------------
Two rules have no primary source and are marked ``Inference`` below:

* Closing *status changes* and *deletes* in an archived room. The research
  lists "archived-room read-only behaviour" among the features of this
  workflow but only documents the closure of uploads.
* ``in_review`` as an intermediate workflow status. Only ``draft`` (on ingest)
  and ``published`` (after publish) appear in any primary source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

VIEWER = "viewer"
CONTENT_CONTRIBUTOR = "content_contributor"
ROOM_COLLABORATOR = "room_collaborator"
INSTANCE_ADMIN = "instance_admin"

#: The complete role vocabulary this application understands. A caller that
#: presents anything else is treated as a Viewer, so an unrecognised role can
#: never widen access.
ROLES: tuple[str, ...] = (VIEWER, CONTENT_CONTRIBUTOR, ROOM_COLLABORATOR, INSTANCE_ADMIN)

#: Human labels, using the vendor's own role names.
ROLE_LABELS: dict[str, str] = {
    VIEWER: "Viewer",
    CONTENT_CONTRIBUTOR: "Content Contributor",
    ROOM_COLLABORATOR: "Room Collaborator",
    INSTANCE_ADMIN: "Instance administrator",
}

#: The role a caller gets when it presents no role at all. Failing closed is
#: the point: forgetting to send a role must not accidentally grant upload.
DEFAULT_ROLE = VIEWER

# Room lifecycle states. Only these two are documented; a team with more states
# is free to add its own, and only ``archived`` changes the gate below.
ROOM_ACTIVE = "active"
ROOM_ARCHIVED = "archived"


def normalise_role(value: Any) -> str:
    """Map a caller-supplied role onto the known vocabulary, defaulting closed."""
    if isinstance(value, str):
        candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
        if candidate in ROLE_LABELS:
            return candidate
    return DEFAULT_ROLE


@dataclass(frozen=True)
class Capabilities:
    """What one caller may do to one room's document library.

    This is a pure value so the API can hand it to the UI, which uses it to
    decide whether to render the *New* button at all rather than rendering a
    button that fails.
    """

    role: str
    role_label: str
    room_status: str
    can_view: bool
    can_upload: bool
    can_manage_status: bool
    can_delete: bool
    can_delete_others: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capabilities(
    role: Any,
    *,
    room_status: str = ROOM_ACTIVE,
    uploaded_by: str | None = None,
    actor: str | None = None,
) -> Capabilities:
    """Resolve what ``role`` may do in a room with the given status.

    ``uploaded_by``/``actor`` are only consulted for the delete decision: the
    documented rule is that neither a Room Collaborator nor a Content
    Contributor can delete a document someone else uploaded, which makes the
    library append-only for everyone but an instance administrator and the
    person who uploaded the file.
    """
    resolved = normalise_role(role)
    is_admin = resolved == INSTANCE_ADMIN
    archived = str(room_status or "").strip().lower() == ROOM_ARCHIVED

    can_upload = resolved in (CONTENT_CONTRIBUTOR, ROOM_COLLABORATOR) or is_admin
    can_manage_status = can_upload
    can_delete_others = is_admin

    # Sourced: uploads close in an archived room except for instance admins.
    if archived and not is_admin:
        can_upload = False

    # Inference: an archived room is read-only for everyone but an instance
    # administrator. The research records "archived-room read-only behaviour"
    # as a feature of this workflow without enumerating which writes close.
    if archived and not is_admin:
        can_manage_status = False
        can_delete = False
    elif is_admin:
        can_delete = True
    else:
        # Sourced: nobody below instance admin may delete someone else's file,
        # so ownership is the only route to deletion below that tier. Ownership
        # is only reachable by a role that can upload in the first place,
        # otherwise a Viewer who happens to match ``uploaded_by`` would gain a
        # write they were never granted.
        can_delete = (
            can_upload and uploaded_by is not None and actor is not None and uploaded_by == actor
        )

    return Capabilities(
        role=resolved,
        role_label=ROLE_LABELS[resolved],
        room_status=room_status or ROOM_ACTIVE,
        can_view=True,
        can_upload=can_upload,
        can_manage_status=can_manage_status,
        can_delete=can_delete,
        can_delete_others=can_delete_others,
    )


def role_vocabulary() -> list[dict[str, str]]:
    """The role list, for a client that wants to render a role switcher.

    Exposed over HTTP so the UI never hard-codes the vocabulary either.
    """
    return [{"id": role, "label": ROLE_LABELS[role]} for role in ROLES]
