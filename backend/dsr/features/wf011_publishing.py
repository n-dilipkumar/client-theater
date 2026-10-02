"""WF-011: take a room from draft to live and hand over the link.

Ported from ``feature/WF-011-take-a-room-from-draft-to-live-and``.

The domain logic is reused unchanged from :mod:`dsr.publishing`. This module is
only the three things the branch had to take out of a shared file: the HTTP
surface, the mapping from domain errors to responses, and the demo data.

What the port changed, and why
------------------------------

**The routes became a router the host discovers.** The branch edited
``dsr/api.py`` to add ``app.include_router(publishing_router)`` and registered
its handlers on the one shared FastAPI app. That single import line is what
made the twelve workflow branches mutually unmergeable: every one of them
appended a ``from dsr.routes_... import router`` to the same file, at the same
place. Here the same ``APIRouter`` is exported as ``router`` from a module the
plugin host finds by walking ``dsr/features/``, so nothing in the host names
WF-011 and two hundred features can land without a three-way conflict.

**The prefix is ``/api/publishing``, and it is this feature's own.** The
brief for this port recorded that the workflow was originally held back on the
belief that it collided with WF-009 on a publishing router. It does not:
WF-011 owns ``/rooms``, ``/rooms/{id}/status``, ``/share-link``, ``/access``,
``/events``, ``/webhooks`` and ``/webhooks/{id}/deliveries``; WF-009 owns
``/processes``, ``/submissions``, ``/workflows``, ``/publish``,
``/publications``, ``/folders`` and ``/subscriptions``. Zero concrete paths
overlap, so the host's route-collision check has nothing to complain about.
The prefix is not ticket-derived (``/api/wf-011``) because these routes are a
*view* over the generic ``room`` collection: they read the same records that
``/api/records/room`` owns and project them for the share pop-up, and a
``/api/publishing`` prefix says that more honestly than a ticket number does.
It is still unique against the core app and every mounted feature, which is
what the contract actually requires.

**``source=`` comes from the route.** The branch hardcoded a prose label into
every audited write - ``"WF-011 set status draft -> live"``,
``"WF-011 update link access settings"``, ``"WF-011 emit room event"`` - so the
audit log described a change but never named the endpoint that served it, and
no rename of the URL could ever have been noticed there. That is the defect
hard rule 4 of the port brief is about. ``source`` is now a *required* keyword
on :meth:`~dsr.publishing.PublishingService.set_status`, ``set_access``,
``subscribe`` and ``cancel``, and every route below builds it from
``router.prefix`` so it cannot drift. The event row and each delivery row are
attributed to the same request that caused them.

**The error mapping is exported.** FastAPI accepts exception handlers on the
app object only, never on a router, so the host has to attach them for us. The
branch instead repeated ``except PublishConflict: 409`` in three route bodies.
:data:`EXCEPTION_HANDLERS` replaces that with one registration, and
``PublishConflict`` is defined in ``dsr/publishing.py`` for this workflow, so
registering it globally cannot intercept an unrelated error anywhere else in
the product. The host refuses a second feature claiming the same type.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it
to 404, and two handlers for one type is a collision the host rejects.
``ValueError`` is likewise not claimed - it is a builtin, and a global handler
for it would swallow bad input to every other route in the product. The two
routes that raise it still catch it locally and answer 400.

**The service is built per request from ``StoreDep``.** The branch defined its
own ``get_store`` dependency reading ``request.app.state.store`` and had to
mount that router from ``api.py``. This module imports ``StoreDep`` from
``dsr.deps`` instead - the seam the contract names - so the routes work against
whatever store the host wired up, and importing this module never drags the
FastAPI app in with it.

**Demo data is a ``seed(db, context)`` export.** The contract forbids editing
``backend/seed.py``, and ten of the first twelve features rewrote it purely to
add demo rows. The rows live here instead, and the three rooms are produced by
driving the real service rather than by writing payload shapes by hand, so the
demo cannot show a status the workflow would not actually produce.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.publishing import (
    EVENT_SET_LIVE,
    EVENT_STATUS_CHANGED,
    EVENT_TYPES,
    STATUSES,
    PublishConflict,
    PublishingService,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-011-room-handover",
    "ticket": "WF-011",
    "name": "Take a room from draft to live and hand over the link",
    "description": (
        "Move a room between draft, live, accepting, accepted, disabled and declined; get a "
        "public link the seller copies; bound the link by expiry, view limit, password and "
        "identity check; and tell a subscriber about each transition."
    ),
    "nav": [{"id": "wf-011-room-handover", "label": "Publish"}],
}

router = APIRouter(prefix="/api/publishing", tags=["wf-011"])

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def get_publishing(store: RecordStore = StoreDep) -> PublishingService:
    """Build the service per request.

    ``DSR_PUBLIC_BASE_URL`` is read at call time for the same reason
    ``DSR_DB_PATH`` is: a module-level constant would be captured at import and
    a test could not point the public link somewhere else.
    """
    return PublishingService(
        store, base_url=os.environ.get("DSR_PUBLIC_BASE_URL", DEFAULT_BASE_URL)
    )


ServiceDep = Depends(get_publishing)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _publish_conflict(request: Request, exc: PublishConflict) -> JSONResponse:
    """Well-formed, but not allowed from the room's current state. 409.

    Asking a draft room for its public link, or publishing a template, lands
    here. These are decisions the domain made deliberately, not malformed
    requests, so they are not 400.
    """
    return JSONResponse(status_code=409, content={"error": "publish_conflict", "detail": str(exc)})


EXCEPTION_HANDLERS = {PublishConflict: _publish_conflict}


def _fail(status: int, message: str) -> HTTPException:
    """A human-readable error. ``detail`` stays a string so the UI can show it."""
    return HTTPException(status_code=status, detail=message)


# --------------------------------------------------------------------------- #
# The board: every room, with a status badge
# --------------------------------------------------------------------------- #


@router.get("/rooms", summary="Rooms with their publish state")
def list_rooms(
    status: str | None = Query(
        default=None,
        description=f"Comma-separated statuses to include. One of: {', '.join(STATUSES)}",
    ),
    tag: str | None = Query(default=None, description="Exact tag match, case sensitive"),
    q: str | None = Query(
        default=None, description="Case-insensitive match on name, account or id"
    ),
    owner: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Rooms with publish state, filtered like the vendor's dashboard list.

    ``live`` and ``accepting`` are mutually exclusive, so a filter naming both
    returns each room once.
    """
    requested = [value.strip() for value in (status or "").split(",") if value.strip()]
    unknown = [value for value in requested if value not in STATUSES]
    if unknown:
        raise _fail(400, f"unknown status {', '.join(unknown)}; expected {', '.join(STATUSES)}")
    return service.board(
        statuses=requested or None,
        tag=tag,
        q=q,
        owner=owner,
        include_archived=include_archived,
        limit=limit,
    )


@router.get("/rooms/{room_id}", summary="Everything the share pop-up shows")
def share_popup(room_id: str, service: PublishingService = ServiceDep) -> dict[str, Any]:
    """The share pop-up's view of one room, including the status drop-down."""
    return service.share(room_id)


@router.post("/rooms/{room_id}/status", summary="Set the room's status")
def set_status(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Set the status from the share drop-down.

    Publishing is the audited change and the transition event in the same call,
    so a downstream subscriber cannot learn about it later than the audit log
    says it happened. A :class:`PublishConflict` (a template going live, a
    stale revision) is mapped to 409 by :data:`EXCEPTION_HANDLERS`.
    """
    status = str(payload.get("status") or "").strip()
    if not status:
        raise _fail(400, "status is required")
    try:
        return service.set_status(
            room_id,
            status,
            source=f"POST {router.prefix}/rooms/{{room_id}}/status",
            actor=payload.get("actor"),
            expected_revision=payload.get("expected_revision"),
        )
    except ValueError as exc:
        raise _fail(400, str(exc)) from exc


@router.get("/rooms/{room_id}/share-link", summary="The link the seller copies")
def share_link(room_id: str, service: PublishingService = ServiceDep) -> dict[str, Any]:
    """The clipboard payload. A read, so it writes no audit row.

    A draft room answers 409 rather than an empty string: the caller should set
    the room live first, and the message says so.
    """
    return service.share_link(room_id)


@router.patch(
    "/rooms/{room_id}/access", summary="Link expiry, view limit, password, identity check"
)
def set_access(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Update the link's access settings.

    The stored password is a salted PBKDF2 hash, and the hash is never returned
    by any route under this prefix. Pass ``clear_password`` to remove it.
    """
    try:
        return service.set_access(
            room_id,
            payload,
            source=f"PATCH {router.prefix}/rooms/{{room_id}}/access",
            actor=payload.get("actor"),
            expected_revision=payload.get("expected_revision"),
        )
    except ValueError as exc:
        raise _fail(400, str(exc)) from exc


# --------------------------------------------------------------------------- #
# Transition events
# --------------------------------------------------------------------------- #


@router.get("/events", summary="Recorded transitions, newest first")
def list_events(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """The record of what subscribers were told. Scoped by the envelope's
    ``room_id``, not by a payload key, because the store reserves ``room_id``
    and never indexes it inside ``data``."""
    events = service.events(room_id=room_id, limit=limit)
    return {"count": len(events), "events": events, "event_types": list(EVENT_TYPES)}


@router.post("/webhooks", status_code=201, summary="Subscribe to room transitions")
def subscribe(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Register a webhook subscription for transition events."""
    try:
        return service.subscribe(
            source=f"POST {router.prefix}/webhooks",
            name=str(payload.get("name") or "").strip(),
            url=str(payload.get("url") or "").strip(),
            events=payload.get("events") or [],
            actor=payload.get("actor"),
        )
    except ValueError as exc:
        raise _fail(400, str(exc)) from exc


@router.get("/webhooks", summary="List webhook subscriptions")
def list_subscriptions(
    include_cancelled: bool = Query(default=False),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Active subscriptions, or every subscription including cancelled ones."""
    subscriptions = service.subscriptions(include_cancelled=include_cancelled)
    return {
        "count": len(subscriptions),
        "subscriptions": subscriptions,
        "event_types": list(EVENT_TYPES),
    }


@router.delete("/webhooks/{subscription_id}", summary="Cancel a subscription")
def cancel_subscription(
    subscription_id: str, service: PublishingService = ServiceDep
) -> dict[str, Any]:
    """Cancel a subscription. The history stays, so a past delivery is auditable."""
    return service.cancel(
        subscription_id, source=f"DELETE {router.prefix}/webhooks/{{subscription_id}}"
    )


@router.get("/webhooks/{subscription_id}/deliveries", summary="Recent delivery attempts")
def list_deliveries(
    subscription_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    service: PublishingService = ServiceDep,
) -> dict[str, Any]:
    """Recent delivery attempts, including the ones that failed.

    The payload is kept: a delivery you cannot inspect is a delivery you cannot
    debug.
    """
    deliveries = service.deliveries(subscription_id, limit=limit)
    return {"count": len(deliveries), "deliveries": deliveries}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: One webhook subscriber, so the panel has something to show on a fresh
#: database. It points at loopback port 1, which refuses instantly, so the demo
#: contains *failed* deliveries as well as successful ones and the panel can
#: show the difference between "we sent it" and "it arrived".
DEMO_SUBSCRIPTION = {
    "name": "CRM (seeded)",
    "url": "http://127.0.0.1:1/hooks/rooms",
    "events": [EVENT_SET_LIVE, EVENT_STATUS_CHANGED],
}

#: ``(index into context["room_ids"], path_through_the_flow, days_ago, access)``.
#:
#: The rows below are the states a room actually reads as on the board, chosen so
#: a reviewer can see every branch of this workflow without clicking:
#:
#:   * published 2 days ago, with an expiry still in the future - the ordinary
#:     live room, and the one whose share pop-up has a working link to copy;
#:   * published 11 days ago with a view limit and an identity check - the same
#:     room with the access settings the pop-up can change;
#:   * published then disabled 30 days ago - the case that emits
#:     ``revived_live`` rather than ``room.set_live`` when it is published again,
#:     and the case with an *elapsed* expiry, so the "not shareable" warning and
#:     the "Link expiry has passed" note are both visible;
#:   * left as a draft - the state every new room starts in.
DEMO_ROOMS: tuple[tuple[int, str, int | None, dict[str, Any]], ...] = (
    (0, "live", 2, {"expires_at": "2027-06-30"}),
    (1, "live", 11, {"max_views": 25, "require_identity_verification": True}),
    (2, "disabled_then_live", 30, {"expires_at": "2020-01-01", "max_views": 5}),
    (3, "draft", None, {}),
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed one subscriber and put the demo rooms into the states above.

    The transitions are produced by calling the real
    :class:`~dsr.publishing.PublishingService`, not by writing a ``status``
    field by hand, so the demo cannot show a shape - or an event, or an audit
    row, or a delivery - that the HTTP routes would not produce. Two extra
    ``db.update`` calls per room backdate ``status_changed_at`` and drop in the
    access settings, because those are stamped at the instant of the call by
    design and cannot be produced any other way in one pass.

    ``source="seed"`` rather than a route string: no route served this, and
    claiming one would be exactly the lie hard rule 4 of the port brief exists
    to prevent.
    """
    service = PublishingService(RecordStore(db))
    room_ids: list[tuple[str, str]] = context["room_ids"]

    service.subscribe(source="seed", actor="dana", **DEMO_SUBSCRIPTION)

    for index, path, days_ago, access in DEMO_ROOMS:
        if index >= len(room_ids):
            continue
        room_id = room_ids[index][0]

        if access:
            service.set_access(room_id, access, source="seed", actor="dana")

        if path == "live":
            service.set_status(room_id, "live", source="seed", actor="dana")
        elif path == "disabled_then_live":
            service.set_status(room_id, "live", source="seed", actor="dana")
            service.set_status(room_id, "disabled", source="seed", actor="dana")

        if days_ago is not None:
            when = (context["now"] - timedelta(days=days_ago)).isoformat(timespec="milliseconds")
            db.update(room_id, {"status_changed_at": when}, actor="dana", source="seed")

    # A template is never published and never shared: the board warns about it,
    # the share pop-up refuses, and the link endpoint answers 409. The demo
    # needs one for that to be reviewable.
    service.store.create(
        "room",
        {
            "name": "Standard Evaluation — template",
            "account": "House template",
            "owner": "dana",
            "kind": "template",
            "description": "A shell a room is generated from. It has no public link, ever.",
        },
        actor="dana",
        source="seed",
    )

    moved = sum(1 for row in DEMO_ROOMS if row[1] != "draft" and row[0] < len(room_ids))
    return (
        f"1 webhook subscriber, {moved} rooms transitioned from draft"
        " (live, live-with-access, disabled), 1 template"
    )
