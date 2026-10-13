"""WF-122: reveal a data room's sections as trust grows.

The rules live in :mod:`dsr.competitive_baseline` and are not restated here. This
module is the three things a feature contributes, and the three things it must
never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the
  write.

Two route-parameter names are chosen for the route verifier
-----------------------------------------------------------

``tools/verify_all_routes.py`` substitutes a value for every path placeholder it
knows and leaves an unknown one in the URL, which then fails as a request that
never answered. So this feature uses ``room_id``, ``section_id`` and
``access_id``, all of which the verifier knows, rather than inventing a name it
would leave in place.

The one link per organisation
-----------------------------

The access link is returned once, at creation and at rotation, and only its hash
is stored. Redemption at ``GET /access/{access_id}`` resolves the token, sets an
HttpOnly, SameSite=Lax cookie and redirects, so the token does not live in
browser history or in a Referer header. That is the requirement the research
states, not a nicety.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from dsr.competitive_baseline import vocabulary as vocab
from dsr.competitive_baseline.engine import DisclosureEngine
from dsr.competitive_baseline.errors import (
    DisclosureError,
    DisclosureNotFound,
    DisclosureRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-122-tiered-disclosure",
    "ticket": "WF-122",
    "name": "Tiered data room disclosure",
    "description": (
        "Reveal a data room's sections as trust grows. Each section carries a "
        "minimum tier, every recipient starts at tier zero, and the seller "
        "unlocks financials, legal or security for specific recipients as "
        "diligence progresses. A below-tier section shows a teaser, an on-request "
        "marker or nothing at all, and a link is revoked or expires when the "
        "process ends."
    ),
    "nav": [{"id": "wf-122-tiers", "label": "Tiered disclosure"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-122", tags=["WF-122"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


def _engine(store: RecordStore) -> DisclosureEngine:
    """The engine, with the clock left at its production default.

    The real clock here rather than a request-supplied value: a caller that could
    pass its own ``now`` could pass a future one and revive an expired link.
    Tests inject the clock into the engine directly.
    """
    return DisclosureEngine(store)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _disclosure_error(request: Request, exc: DisclosureError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "disclosure_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _disclosure_refused(request: Request, exc: DisclosureRefused) -> JSONResponse:
    """409 for a change the room's current state will not accept.

    409 rather than 400 because nothing about the request is malformed: a revoked
    or expired link is a state, and a client that retries the same call gets the
    same answer. ``reason`` is a stable token the page branches on.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "disclosure_conflict", "reason": exc.reason, "detail": str(exc)},
    )


def _disclosure_not_found(request: Request, exc: DisclosureNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a link may not see."""
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such section, recipient or access link."},
    )


EXCEPTION_HANDLERS = {
    DisclosureError: _disclosure_error,
    DisclosureRefused: _disclosure_refused,
    DisclosureNotFound: _disclosure_not_found,
}


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The room's headline numbers. Reads only."""
    return _engine(store).summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it."""
    return vocab.vocabulary()


# --------------------------------------------------------------------------- #
# Tier policy
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/policy")
def read_policy(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """A room's tier labels and its default below-tier state."""
    return _engine(store).read_policy(room_id)


@router.put("/rooms/{room_id}/policy")
def set_policy(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Write or replace a room's tier policy."""
    return _engine(store).set_policy(
        room_id, payload, source=_source("PUT", "/rooms/{room_id}/policy"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/sections")
def list_sections(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every section in a room, in the seller's order, at every tier."""
    return {"room_id": room_id, "sections": _engine(store).list_sections(room_id)}


@router.post("/rooms/{room_id}/sections")
def create_section(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add a section with a minimum tier and a below-tier state."""
    return _engine(store).create_section(
        room_id, payload, source=_source("POST", "/rooms/{room_id}/sections"), actor="rep"
    )


@router.get("/sections/{section_id}")
def read_section(section_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One section, at the seller's level of detail."""
    return _engine(store).read_section(section_id)


@router.patch("/sections/{section_id}")
def update_section(
    section_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Change a section's name, tier, below-tier state, teaser or order."""
    return _engine(store).update_section(
        section_id, payload, source=_source("PATCH", "/sections/{section_id}"), actor="rep"
    )


@router.delete("/sections/{section_id}")
def delete_section(section_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Remove a section from the room tree."""
    return _engine(store).delete_section(
        section_id, source=_source("DELETE", "/sections/{section_id}"), actor="rep"
    )


@router.get("/rooms/{room_id}/view")
def preview_room(
    room_id: str, tier: int = Query(0, ge=0), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """The room as a viewer at ``tier`` would see it. A seller's preview.

    This is the route that proves the visibility rule: the sections it omits are
    omitted from the response, not hidden by a page.
    """
    return _engine(store).render_room(room_id, tier)


# --------------------------------------------------------------------------- #
# Recipients and their links
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/recipients")
def list_recipients(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every counterparty link in a room, with its derived state."""
    return {"room_id": room_id, "recipients": _engine(store).list_recipients(room_id)}


@router.post("/rooms/{room_id}/recipients")
def create_recipient(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Issue one access link for one counterparty organisation.

    The response carries the raw token once. A later read of the recipient does
    not, because the store keeps only its hash.
    """
    return _engine(store).create_recipient(
        room_id, payload, source=_source("POST", "/rooms/{room_id}/recipients"), actor="rep"
    )


@router.get("/recipients/{access_id}")
def read_recipient(access_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One recipient, without its token."""
    return _engine(store).read_recipient(access_id)


@router.patch("/recipients/{access_id}")
def update_recipient(
    access_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit a recipient's organisation, name, tier or expiry.

    The link's state and token are refused here; they change only through the
    revoke and rotate routes.
    """
    return _engine(store).update_recipient(
        access_id, payload, source=_source("PATCH", "/recipients/{access_id}"), actor="rep"
    )


@router.post("/recipients/{access_id}/unlock")
def unlock(
    access_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Raise a link's tier, verify the write, and log the unlock."""
    return _engine(store).unlock(
        access_id,
        payload.get("tier"),
        source=_source("POST", "/recipients/{access_id}/unlock"),
        actor="rep",
    )


@router.post("/recipients/{access_id}/revoke")
def revoke(access_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Revoke a link and log it."""
    return _engine(store).revoke(
        access_id, source=_source("POST", "/recipients/{access_id}/revoke"), actor="rep"
    )


@router.post("/recipients/{access_id}/rotate")
def rotate(access_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Re-issue a link's token, so the old one stops resolving."""
    return _engine(store).rotate_token(
        access_id, source=_source("POST", "/recipients/{access_id}/rotate"), actor="rep"
    )


@router.get("/recipients/{access_id}/view")
def recipient_view(access_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The room as one recipient sees it, with their grants and link state.

    A revoked or expired link is rendered at tier -1 with no grants, so the
    response shows nothing rather than a stale room.
    """
    return _engine(store).render_for_recipient(access_id)


@router.get("/rooms/{room_id}/requests")
def list_requests(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Access requests a below-tier recipient has made for an on-request section."""
    return {"room_id": room_id, "requests": _engine(store).list_requests(room_id)}


@router.get("/rooms/{room_id}/events")
def list_events(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The unlock, revoke, rotation, grant and request log, oldest first."""
    return {"room_id": room_id, "events": _engine(store).events_of(room_id)}


# --------------------------------------------------------------------------- #
# Section-level access
# --------------------------------------------------------------------------- #


@router.post("/sections/{section_id}/grant")
def grant_section(
    section_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Reveal one section to one recipient below their tier."""
    recipient_id = str(payload.get("recipient_id") or "").strip()
    if not recipient_id:
        raise DisclosureError(
            "The grant could not be read.", {"recipient_id": "Name the recipient."}
        )
    return _engine(store).grant_section(
        section_id,
        recipient_id,
        note=payload.get("note"),
        source=_source("POST", "/sections/{section_id}/grant"),
        actor="rep",
    )


@router.post("/sections/{section_id}/request")
def request_section(
    section_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """A recipient asks for an on-request section."""
    recipient_id = str(payload.get("recipient_id") or "").strip()
    if not recipient_id:
        raise DisclosureError(
            "The request could not be read.", {"recipient_id": "Name the recipient."}
        )
    return _engine(store).request_section(
        section_id,
        recipient_id,
        note=payload.get("note"),
        source=_source("POST", "/sections/{section_id}/request"),
        actor="rep",
    )


# --------------------------------------------------------------------------- #
# The access link itself
# --------------------------------------------------------------------------- #


@router.get("/access/{access_id}")
def redeem(access_id: str, store: RecordStore = StoreDep) -> Response:
    """Resolve a token, set the access cookie, and redirect.

    The token never appears in a page URL: it is exchanged for a cookie here and
    the browser is sent to the room. An unknown token answers 404 rather than
    setting a cookie for a link that does not exist.
    """
    engine = _engine(store)
    _ = engine.resolve_token(access_id)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=vocab.TOKEN_COOKIE,
        value=access_id,
        max_age=vocab.TOKEN_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path=router.prefix,
    )
    return response


@router.get("/me")
def me(request: Request, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Who the caller's access cookie names, and what they may see.

    A request with no cookie, or with a stale one, is answered as anonymous
    rather than as an error: a viewer who has no link yet is not a fault.
    """
    token = request.cookies.get(vocab.TOKEN_COOKIE)
    if not token:
        return {"authenticated": False}
    engine = _engine(store)
    try:
        recipient = engine.resolve_token(token)
    except DisclosureNotFound:
        return {"authenticated": False}
    view = engine.render_for_recipient(recipient["id"])
    return {
        "authenticated": True,
        "recipient": view["recipient"],
        "room_id": view["room_id"],
        "state": view["state"],
        "viewer_tier": view["viewer_tier"],
        "sections": view["sections"],
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the disclosure states the research says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a section, a
    link state or an audit row the HTTP routes would not produce. ``source="seed"``
    rather than a route string: no route served this, and claiming one would be
    the lie the contract's write rule exists to prevent.

    The states seeded, and why each is here:

    * a room with sections at tiers zero to three, so the ladder is visible;
    * a section below tier as a **teaser**, one as **on request**, and one as
      **hidden**, so all three below-tier states are shown;
    * a recipient left at **tier zero**, so a below-tier view has something to
      render;
    * a recipient **unlocked to tier two**, so a full view above the curated room
      is shown;
    * a **section grant** below that recipient's tier, so section-level access is
      shown rather than only a whole-tier bump;
    * an **open access request** for the on-request section;
    * a **revoked** link and an **expired** link, so the two ways a room closes
      are both visible.

    Every character of the returned string is ASCII, so the seeder can print it
    on a Windows console. A single non-cp1252 character in one feature broke the
    whole seeder once.
    """
    store = RecordStore(db)
    now = context["now"]
    room_ids = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id
    engine = DisclosureEngine(store, now=lambda: now)
    source, actor = "seed", "dana"

    engine.set_policy(
        room_id,
        {
            "tier_labels": {
                "0": "Curated room",
                "1": "Financials",
                "2": "Legal",
                "3": "Security",
            },
            "default_below_tier": vocab.HIDDEN,
        },
        source=source,
        actor=actor,
    )

    overview = engine.create_section(
        room_id,
        {
            "name": "Company overview",
            "min_tier": 0,
            "summary": "The curated introduction every party sees first.",
            "documents": ["Enterprise Overview Deck"],
            "order": 1,
        },
        source=source,
        actor=actor,
    )
    financials = engine.create_section(
        room_id,
        {
            "name": "Financial statements",
            "min_tier": 1,
            "below_tier": vocab.TEASER,
            "teaser": "Three years of statements, once financials unlock.",
            "documents": ["Pricing One-Pager"],
            "order": 2,
        },
        source=source,
        actor=actor,
    )
    legal = engine.create_section(
        room_id,
        {
            "name": "Contracts and IP",
            "min_tier": 2,
            "below_tier": vocab.ON_REQUEST,
            "order": 3,
        },
        source=source,
        actor=actor,
    )
    engine.create_section(
        room_id,
        {
            "name": "Security and compliance",
            "min_tier": 3,
            "below_tier": vocab.HIDDEN,
            "summary": "Penetration test results and the SOC 2 report.",
            "order": 4,
        },
        source=source,
        actor=actor,
    )

    # A recipient left at tier zero, so the teaser and on-request states are shown.
    contoso = engine.create_recipient(
        room_id,
        {
            "org": "Contoso Health",
            "name": "Contoso diligence",
            "email": "procurement@contoso.example",
            "tier": 0,
        },
        source=source,
        actor=actor,
    )

    # A recipient unlocked to tier two, so a full view above the curated room shows.
    vantage = engine.create_recipient(
        room_id,
        {
            "org": "Vantage Partners",
            "name": "Vantage diligence",
            "tier": 0,
            "expires_at": (now + timedelta(days=30)).date().isoformat(),
        },
        source=source,
        actor=actor,
    )
    engine.unlock(vantage["id"], 2, source=source, actor=actor)

    # A section granted below that recipient's tier: section-level access.
    engine.grant_section(
        legal["id"], vantage["id"], note="Shared at the legal review.", source=source, actor=actor
    )

    # An open request for the on-request section, from the tier-zero recipient.
    engine.request_section(
        legal["id"],
        contoso["id"],
        note="Requested at kickoff.",
        source=source,
        actor=actor,
    )

    # A revoked link and an expired link: the two ways a room closes.
    revoked = engine.create_recipient(
        room_id,
        {"org": "Fabrikam Logistics", "name": "Fabrikam (withdrawn)"},
        source=source,
        actor=actor,
    )
    engine.revoke(revoked["id"], source=source, actor=actor)
    engine.create_recipient(
        room_id,
        {
            "org": "Adventure Works",
            "name": "Adventure (trial)",
            "expires_at": (now - timedelta(days=1)).date().isoformat(),
        },
        source=source,
        actor=actor,
    )

    # A second room, so the summary is not accidentally room-scoped wrong.
    engine.create_section(
        room_two,
        {"name": "Pilot results", "min_tier": 0, "summary": "The pilot readout.", "order": 1},
        source=source,
        actor=actor,
    )

    counted = engine.summary(room_id)
    return (
        f"{overview['name']} is full at tier 0 and {financials['name']} is a teaser "
        f"below tier 1. {counted['sections']} sections (1 teaser, 1 on request, "
        f"1 hidden), {counted['recipients']} links ({counted['active']} active, "
        f"{counted['revoked']} revoked, {counted['expired']} expired), "
        f"{counted['unlocked']} unlocked above tier 0, {counted['grants']} section grant, "
        f"{counted['open_requests']} open access request, {counted['events']} disclosure events"
    )
