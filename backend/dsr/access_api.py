"""HTTP surface for WF-004: the Share dialog and *Who Has Access*.

Kept in its own module so ``dsr.api`` stays the composition root. Every route
is a thin translation of one :class:`~dsr.access.AccessService` call, and every
write still goes through the audited store underneath.

The response bodies are deliberately *not* the raw records. The dialog needs
derived facts — whether a grant lapses within seven days, which roles this
actor may hand out, whether a Viewer is allowed to be here at all — and
computing them once on the server keeps the UI from re-deriving policy that
only the service knows.

Domain refusals travel as :class:`~dsr.access.AccessError`, which ``dsr.api``
maps onto status codes the same way it already handles ``RecordNotFound``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from dsr.access import (
    DEFAULT_ROLE,
    EXPIRING_SOON_DAYS,
    INVITATION_TTL_HOURS,
    AccessService,
    role_vocabulary,
)

router = APIRouter(prefix="/api", tags=["access"])

ACTOR = Query(
    default=None,
    description=(
        "The person acting on the room, by email or id. Their role in the room "
        "decides what they may do; the room owner is read from the room's "
        "`owner` field."
    ),
)


def get_service(request: Request) -> AccessService:
    """The access service, built over the process-wide audited store."""
    return AccessService(request.app.state.store)


ServiceDep = Depends(get_service)


# --------------------------------------------------------------------------- #
# Role vocabulary
# --------------------------------------------------------------------------- #


@router.get("/access/roles", summary="Room roles and the delegation rule")
def roles(actor_role: str | None = Query(default=None)) -> dict[str, Any]:
    """The role vocabulary, annotated with what this actor may assign.

    The UI reads this instead of hard-coding three roles, so a team that adds a
    fourth role on the server shows up in the Share dialog with no frontend
    change.
    """
    return {
        "roles": role_vocabulary(actor_role),
        "default_role": DEFAULT_ROLE,
        "expiring_soon_days": EXPIRING_SOON_DAYS,
        "invitation_ttl_hours": INVITATION_TTL_HOURS,
    }


# --------------------------------------------------------------------------- #
# Who has access
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/access", summary="Everything the Share dialog needs")
def room_access(
    room_id: str,
    actor: str | None = ACTOR,
    service: AccessService = ServiceDep,
) -> dict[str, Any]:
    """Members, pending invitations, and the imminent-expiry banner.

    Grants past the end of their expiration date in UTC are filtered out here
    rather than deleted by a background job, because the documented cut-off is
    silent.
    """
    return service.snapshot(room_id, actor)


@router.post("/rooms/{room_id}/invitations", status_code=201, summary="Invite buyers to a room")
def invite(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = ACTOR,
    service: AccessService = ServiceDep,
) -> dict[str, Any]:
    """Send one invitation per address, with one role and one expiry for all.

    ``emails`` accepts a list or a single string. A body with no addresses is a
    400 rather than a silent no-op, because the Share dialog always sends at
    least one.
    """
    emails = payload.get("emails", payload.get("email"))
    return service.invite(
        room_id,
        emails if emails is not None else [],
        role=str(payload.get("role") or DEFAULT_ROLE),
        access_valid_until=payload.get("access_valid_until"),
        actor=actor or "",
    )


@router.post("/invitations/{invitation_id}/accept", summary="Accept an invitation")
def accept(
    invitation_id: str,
    actor: str | None = Query(default=None),
    service: AccessService = ServiceDep,
) -> dict[str, Any]:
    """Turn a pending invitation into an access grant.

    The invitee joins the room on acceptance, with the role and expiry the
    inviter chose. Past 48 hours the invitation is recorded as expired and the
    caller must send a new one.
    """
    return service.accept(invitation_id, actor)


# --------------------------------------------------------------------------- #
# Changing and removing access
# --------------------------------------------------------------------------- #


@router.patch("/access/{access_id}", summary="Change a role or an expiry")
def update_access(
    access_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = ACTOR,
    confirm: bool = Query(default=False, description="Acknowledge the change; required for a role change"),
    service: AccessService = ServiceDep,
) -> dict[str, Any]:
    """Edit one row of *Who Has Access*.

    Pass ``set_expiry: true`` to change the date, including clearing it back to
    *No Expiration*. A role change needs ``confirm=true``: the research records
    that the product being mirrored makes this change without asking, which is
    a safety gap rather than a requirement.
    """
    return service.update_access(
        access_id,
        actor=actor or "",
        role=payload.get("role"),
        access_valid_until=payload.get("access_valid_until"),
        set_expiry=bool(payload.get("set_expiry")),
        confirm=confirm,
    )


@router.delete("/access/{access_id}", summary="Remove someone from a room")
def remove_access(
    access_id: str,
    actor: str | None = ACTOR,
    confirm: bool = Query(default=False, description="Acknowledge the removal"),
    service: AccessService = ServiceDep,
) -> dict[str, Any]:
    """Remove a person from *Who Has Access* and revoke their open invitation.

    Soft delete: the grant stays in the table so the audit trail and the
    invitation that produced it remain readable.
    """
    return service.remove_access(access_id, actor=actor or "", confirm=confirm)
