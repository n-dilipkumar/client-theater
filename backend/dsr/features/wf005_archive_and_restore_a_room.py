"""WF-005: archive and restore a digital sales room.

Adds the transition the product was missing. The ``archived`` room status, the
read-only gate that follows from it, and the notice four features already render
all existed; nothing performed the move. This feature supplies the two routes
and the derived state a UI needs to offer them.

The domain rules live in ``dsr.room_lifecycle``, beside the feature rather than
inside the host, so the folder stays small and the rules are testable without a
request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.permissions import ROLE_LABELS
from dsr.room_lifecycle import (
    ACTIVE,
    ARCHIVE_CONFIRMATION,
    ARCHIVE_NOTICE,
    ARCHIVED,
    ROLE_CAPABILITIES,
    RoomBadRequest,
    RoomConflict,
    RoomForbidden,
    RoomNotFound,
    RoomRefusal,
    available_actions,
    room_state,
    status_of,
)
from dsr.room_lifecycle.engine import RoomLifecycle, create
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-005-room-lifecycle",
    "ticket": "WF-005",
    "name": "Archive and restore a room",
    "description": (
        "Close a room to buyers while keeping its content, and bring a "
        "mistaken archive back. Both directions need the same update permission "
        "and write only the status, so a restore has nothing to clean up."
    ),
}

router = APIRouter(prefix="/api/wf-005", tags=["WF-005"])


def lifecycle(store: RecordStore) -> RoomLifecycle:
    return RoomLifecycle(store)


def _refusal(request: Request, exc: RoomRefusal) -> JSONResponse:
    """One handler for every refusal, mapped to the status each one means.

    404 for an unknown room, 403 for a missing permission, 409 for a state the
    action does not apply to, 400 for a malformed request. Collapsing these into
    one 4xx would make a caller's next step unguessable.
    """
    status = {
        RoomNotFound: 404,
        RoomForbidden: 403,
        RoomConflict: 409,
        RoomBadRequest: 400,
    }.get(type(exc), 422)
    code = {
        404: "room_not_found",
        403: "room_forbidden",
        409: "room_conflict",
        400: "room_bad_request",
    }[status]
    return JSONResponse(status_code=status, content={"error": code, "detail": str(exc)})


EXCEPTION_HANDLERS = {RoomRefusal: _refusal}


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Actor") or "dana")


def _role(request: Request) -> str | None:
    return request.headers.get("X-Role")


def _capabilities(request: Request) -> Any:
    declared = request.headers.get("X-Capabilities")
    if not declared:
        return None
    return [item.strip() for item in declared.split(",") if item.strip()]


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The rules this workflow applies, readable without a store.

    Exposed so a reviewer can check what the feature claims against the code
    that enforces it, and so the page can render the rules rather than restate
    them.
    """
    return {
        "statuses": [ACTIVE, ARCHIVED],
        "actions": {
            "archive": {
                "applies_to": ACTIVE,
                "requires": "update",
                "effect": "closes the room to buyers while keeping its content",
                "confirmation": ARCHIVE_CONFIRMATION,
            },
            "restore": {
                "applies_to": ARCHIVED,
                "requires": "update",
                "effect": "returns an archived room to active",
            },
        },
        "roles": dict(ROLE_LABELS),
        "role_capabilities": {
            role: sorted(caps) for role, caps in sorted(ROLE_CAPABILITIES.items())
        },
        "notice": ARCHIVE_NOTICE,
        "writes": ["status"],
        "inferred": [
            {
                "claim": "archiving closes status changes and deletes in that room",
                "basis": (
                    "The research lists archived-room read-only behaviour as a feature "
                    "but only documents the closure of uploads. This build reads it as "
                    "closing every write, which is what dsr.permissions already gates."
                ),
            }
        ],
    }


@router.get("/rooms/{room_id}/state")
def room_state_route(
    room_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Everything a UI needs to render this room's lifecycle, derived.

    ``available_actions`` comes from the same two rules the write path enforces,
    so the page cannot offer a button the route would refuse.
    """
    return lifecycle(store).state(room_id, _role(request))


@router.get("/rooms/{room_id}/actions")
def room_actions_route(
    room_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    return {
        "room_id": room_id,
        "status": status_of(lifecycle(store).get(room_id)),
        "actions": lifecycle(store).list_actions(room_id, _role(request)),
    }


@router.post("/rooms")
def create_room(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create a room, active unless the payload says otherwise.

    Only here so the demo and the tests have a room to archive; the shipped
    product creates rooms through WF-001.
    """
    record = create(store, payload, actor=_actor(request), source="POST /api/wf-005/rooms")
    return room_state(record, _role(request))


@router.post("/rooms/{room_id}/archive")
def archive_room(
    room_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Close the room to buyers, keeping its content.

    Idempotent in neither direction: archiving an archived room is a 409, not a
    no-op, because a caller that thinks it archived something should be told
    when it had already happened.
    """
    updated = lifecycle(store).archive(
        room_id,
        actor=_actor(request),
        role=_role(request),
        capabilities=_capabilities(request),
    )
    return updated


@router.post("/rooms/{room_id}/restore")
def restore_room(
    room_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Return an archived room to active. Needs the same permission as archive."""
    return lifecycle(store).restore(
        room_id,
        actor=_actor(request),
        role=_role(request),
        capabilities=_capabilities(request),
    )


@router.get("/rooms")
def list_rooms(
    request: Request,
    status: str | None = Query(default=None, description="active | archived | all"),
    limit: int = Query(default=100, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Rooms filtered by lifecycle state, with their actions attached."""
    want = (status or "all").strip().lower()
    if want not in (ACTIVE, ARCHIVED, "all"):
        raise RoomBadRequest(f"status must be 'active', 'archived' or 'all'; got {status!r}")

    role = _role(request)
    rows = store.list("room", limit=limit)
    if want != "all":
        rows = [r for r in rows if status_of(r) == want]
    return {
        "status": want,
        "count": len(rows),
        "rooms": [room_state(r, role) for r in rows],
    }


@router.get("/summary")
def summary(
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Counts per state, and what this caller could do to each."""
    role = _role(request)
    rows = store.list("room", limit=1000)
    by_status: dict[str, int] = {ACTIVE: 0, ARCHIVED: 0}
    for row in rows:
        by_status[status_of(row)] = by_status.get(status_of(row), 0) + 1
    return {
        "total": len(rows),
        "by_status": by_status,
        "caller_actions": {s: available_actions(s, role) for s in (ACTIVE, ARCHIVED)},
    }


def seed(db, context):
    """Two rooms, one of each state, so the page has both sides to show."""
    from dsr.store import RecordStore

    store = RecordStore(db)
    room_ids = context.get("room_ids") or []
    if len(room_ids) < 2:
        return None
    archived_id, _ = room_ids[1]
    room = store.get(archived_id)
    if room is not None and status_of(room) == ACTIVE:
        store.update(
            archived_id,
            {"status": ARCHIVED},
            actor="seed",
            source="POST /api/wf-005/rooms/%s/archive" % archived_id,
        )
    return "1 room archived, 1 active: archive and restore both start from a real state"
