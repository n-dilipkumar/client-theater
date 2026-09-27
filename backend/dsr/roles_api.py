"""HTTP surface for WF-004: the Share dialog and *Who Has Access*.

Ported from ``backend/dsr/access_api.py`` on
``feature/WF-004-invite-buyers-to-a-room-with-a-role``, renamed to
``roles_api.py`` because the domain module beside it is ``dsr.roles``: this
branch is about granting, and ``dsr.access`` belongs to the branch that is
about verifying identity. The domain module itself is :mod:`dsr.roles` and the
plugin that mounts this router is
``dsr.features.wf_004-invite-buyer``.

The branch registered these routes on the shared ``app`` object from
``dsr/api.py`` under ``prefix="/api"``, which put them at
``GET /api/rooms/{room_id}/access`` and friends. Three changes follow from the
contract, and each is a port requirement rather than a preference:

* **The router owns a prefix.** ``/api/wf-004-invite-buyer``. The bare
  ``/api/rooms/{room_id}/access`` is core vocabulary that several other
  workflows want, and the host refuses a feature that claims a concrete route
  already taken. The branch was never merged, so nothing external depended on
  the old paths.
* **Dependencies come from :mod:`dsr.deps`.** The branch built the service from
  ``request.app.state.store`` via its own ``get_service``; the contract's seam
  is ``StoreDep``, which is the same object and is the one a feature is
  supposed to import.
* **Every write is handed the path this router actually serves.** See
  :func:`_source`. The branch hard-coded ``f"PATCH /api/access/{access_id}"``
  and friends inside the service, so its audit log went on naming routes the
  app had stopped serving.

The response bodies are deliberately *not* the raw records. The dialog needs
derived facts — whether a grant lapses within seven days, which roles this
actor may hand out, whether a Viewer is allowed to be here at all — and
computing them once on the server keeps the UI from re-deriving policy that
only the service knows.

Domain refusals travel as :class:`~dsr.roles.AccessError`. FastAPI accepts
exception handlers on the app object only, so this module exports
``EXCEPTION_HANDLERS`` and the host attaches it; that export replaces the
``@app.exception_handler(AccessError)`` block the branch added to ``dsr/api.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.roles import (
    DEFAULT_ROLE,
    EXPIRING_SOON_DAYS,
    INVITATION_TTL_HOURS,
    AccessError,
    AccessService,
    role_vocabulary,
)
from dsr.store import RecordStore

router = APIRouter(prefix="/api/wf-004-invite-buyer", tags=["wf004"])

ACTOR = Query(
    default=None,
    description=(
        "The person acting on the room, by email or id. Their role in the room "
        "decides what they may do; the room owner is read from the room's "
        "`owner` field."
    ),
)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# ``AccessError`` and its subclasses are this workflow's own types, raised by
# :mod:`dsr.roles` and by nothing else in the product. Mapping the base class is
# what makes a refusal's own status code reach the client: 403 when the actor's
# role does not permit the action, 404 for a record that is gone, 400 for a
# value that makes no sense, 428 when a destructive change arrived unconfirmed.
# Kept off a blanket 400 so a client can tell "you may not" from "that is not
# valid", which is the whole reason the branch split the hierarchy.
#
# Only the base is mapped, deliberately. Mapping a subclass as well would be two
# features' worth of load-order dependence in one file, and the host refuses two
# features mapping the same type anyway.


def _access_error(request: Request, exc: AccessError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {AccessError: _access_error}


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the port brief is that a write's audit row must name the route that served
    it. Writing the string by hand is how the same defect shipped once already:
    the branch recorded ``PATCH /api/access/{id}``, which was a real path then
    and dead code the moment a prefix was added.
    """
    return f"{verb} {router.prefix}{suffix}"


def get_service(store: RecordStore = StoreDep) -> AccessService:
    """The role service, built over the process-wide audited store.

    A dependency rather than a direct ``AccessService(...)`` at each call site,
    so the routes stay thin translations of one service call and the store is
    reached only through the shared seam.
    """
    return AccessService(store)


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
        source=_source("POST", f"/rooms/{room_id}/invitations"),
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
    return service.accept(
        invitation_id, actor, source=_source("POST", f"/invitations/{invitation_id}/accept")
    )


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
        source=_source("PATCH", f"/access/{access_id}"),
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
    return service.remove_access(
        access_id,
        actor=actor or "",
        source=_source("DELETE", f"/access/{access_id}"),
        confirm=confirm,
    )
