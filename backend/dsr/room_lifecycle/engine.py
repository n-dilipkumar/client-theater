"""The guarded write behind archive and restore.

One code path for both directions, because the research describes them as the
same transition in opposite directions with the same permission and the same
applicability rule. Two code paths would let the guard drift.
"""

from __future__ import annotations

from typing import Any

from dsr.room_lifecycle import (
    ACTIVE,
    ARCHIVED,
    DEFAULT_ACTOR,
    REQUIRED_PERMISSION,
    RoomBadRequest,
    RoomConflict,
    RoomForbidden,
    RoomNotFound,
    capabilities_for,
    room_state,
    status_of,
)
from dsr.store import RecordStore

ROOMS = "room"


class RoomLifecycle:
    """Reads and writes one room's lifecycle state.

    Deliberately narrow: it does not create, delete, comment, upload or share.
    Those capabilities already exist on other features, and duplicating them
    here would be a second answer to the same question.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def get(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(room_id)
        if record is None or record.get("collection") != ROOMS:
            raise RoomNotFound(f"no room with id {room_id!r}")
        return record

    def state(self, room_id: str, role: str | None = None) -> dict[str, Any]:
        return room_state(self.get(room_id), role)

    def list_actions(self, room_id: str, role: str | None = None) -> list[str]:
        return self.state(room_id, role)["available_actions"]

    def archive(
        self,
        room_id: str,
        *,
        actor: str | None = None,
        role: str | None = None,
        capabilities: Any = None,
    ) -> dict[str, Any]:
        return self._transition(
            room_id,
            action="archive",
            expected=ACTIVE,
            target=ARCHIVED,
            actor=actor,
            role=role,
            capabilities=capabilities,
        )

    def restore(
        self,
        room_id: str,
        *,
        actor: str | None = None,
        role: str | None = None,
        capabilities: Any = None,
    ) -> dict[str, Any]:
        return self._transition(
            room_id,
            action="restore",
            expected=ARCHIVED,
            target=ACTIVE,
            actor=actor,
            role=role,
            capabilities=capabilities,
        )

    def _transition(
        self,
        room_id: str,
        *,
        action: str,
        expected: str,
        target: str,
        actor: str | None,
        role: str | None,
        capabilities: Any = None,
    ) -> dict[str, Any]:
        room = self.get(room_id)
        status = status_of(room)

        permission = REQUIRED_PERMISSION[action]
        if permission not in capabilities_for(role, capabilities):
            raise RoomForbidden(
                f"the {permission} permission is required to {action} a room, and "
                f"the caller's role does not carry it"
            )

        if status != expected:
            # Naming the state the room is actually in is what makes this
            # debuggable: "archive applies to active rooms; room X is archived"
            # says which of the two halves of the check failed.
            raise RoomConflict(
                f"{action} applies to {expected} rooms; room {room_id} is {status}"
            )

        # The only field written is `status`. Every other consequence is
        # derived, so there is nothing left behind for a restore to clean up.
        updated = self.store.update(
            room_id,
            {"status": target},
            actor=actor or DEFAULT_ACTOR,
            source=f"POST /api/wf-005/rooms/{room_id}/{action}",
        )
        return room_state(updated, role)


def create(
    store: RecordStore,
    data: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
    source: str = "POST /api/rooms",
) -> dict[str, Any]:
    """Create a room, defaulting it to active.

    The default is written into the payload rather than assumed at read time: a
    record without the field is invisible to the dynamic index, so a room with no
    stored status would not appear in an ``active`` filter.
    """
    payload = dict(data or {})
    status = str(payload.get("status") or ACTIVE).strip().lower()
    if status not in (ACTIVE, ARCHIVED):
        raise RoomBadRequest(f"status must be 'active' or 'archived'; got {status!r}")
    payload["status"] = status
    return store.create(ROOMS, payload, actor=actor or DEFAULT_ACTOR, source=source)


__all__ = ["RoomLifecycle", "create", "ROOMS"]
