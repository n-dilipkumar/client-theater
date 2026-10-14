"""WF-130: generated, personalised collateral from deal data into the room.

The rules live in :mod:`dsr.collateral_generation`, which knows nothing about
HTTP. This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the room publish, which goes through the ``dsr.pages`` seam WF-002 already
  wraps rather than editing the publishing feature or re-implementing its
  page and revision writes.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-130.md``, quoted in
full on issue #142.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.collateral_generation import CollateralBook, CollateralError, CollateralRefused
from dsr.db.audited import AuditedDatabase, AuditError, RecordNotFound
from dsr.deps import StoreDep
from dsr.fragments import FragmentError
from dsr.pages import PageService, PermissionDenied
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-130-collateral-generation",
    "ticket": "WF-130",
    "name": "Generated, personalised collateral from deal data",
    "description": (
        "Assemble a personalised document or deck from a deal record and a "
        "template, with personalisation tokens, synced source sections and "
        "sections hidden until the deal reaches their stage. Review, edit, and "
        "publish it to the room through the pages seam."
    ),
    "nav": [{"id": "collateral-generation", "label": "Collateral generation"}],
}

router = APIRouter(prefix="/api/wf-130", tags=["WF-130"])

#: The slug a published room page gets. The collateral id is unique, so a second
#: publish of the same artefact re-uses its page instead of colliding.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> CollateralBook:
    """The book, built on the shared audited store, per request."""
    return CollateralBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _collateral_error(request: Request, exc: CollateralError) -> JSONResponse:
    """A refusal this workflow made, answered with the status it carries.

    One handler for the whole hierarchy. ``CollateralError`` is defined in
    ``dsr.collateral_generation.errors``, so a handler for it cannot intercept an
    unrelated error anywhere else.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


EXCEPTION_HANDLERS = {CollateralError: _collateral_error}


# --------------------------------------------------------------------------- #
# The room publish seam
# --------------------------------------------------------------------------- #


def _page_blocks(collateral: dict[str, Any]) -> list[dict[str, Any]]:
    """One text fragment per visible section, in the authored order."""
    return [
        {
            "fragment": "text",
            "config": {"heading": section.get("title"), "body": section.get("body")},
        }
        for section in collateral.get("sections") or []
        if section.get("visible")
    ]


def _page_slug(collateral: dict[str, Any]) -> str:
    raw = _SLUG_STRIP.sub("-", f"collateral-{collateral['id']}".lower()).strip("-")
    return raw or "collateral"


def _publish_room_page(
    store: RecordStore,
    collateral: dict[str, Any],
    *,
    actor: str | None,
    source: str,
) -> str | None:
    """Write the artefact into the room through the pages seam, and publish it.

    This is the one integration WF-130 has. The publishing feature already
    exists, so the page and its immutable revision are written by
    :class:`dsr.pages.PageService`, the seam WF-002 wraps, rather than by this
    workflow. A refusal from that seam is reported as this workflow's own 409 so
    the caller sees one error contract.
    """
    blocks = _page_blocks(collateral)
    if not blocks:
        return None
    room_id = str(collateral["room_id"])
    service = PageService(store)
    payload = {"title": str(collateral.get("title") or "Collateral"), "blocks": blocks}
    try:
        page_id = collateral.get("room_page_id")
        if page_id:
            service.update_page(room_id, str(page_id), payload, actor=actor, source=source)
            page = {"id": page_id}
        else:
            page = service.create_page(
                room_id, {**payload, "slug": _page_slug(collateral)}, actor=actor, source=source
            )
        service.publish(room_id, str(page["id"]), actor=actor, source=source)
    except (RecordNotFound, PermissionDenied, FragmentError, AuditError) as exc:
        raise CollateralRefused(
            f"the room's publish seam refused the page: {exc}", room_id=room_id
        ) from exc
    return str(page["id"])


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(book: CollateralBook = BookDep) -> dict[str, Any]:
    """Every value this workflow accepts, and which half is sourced."""
    return book.vocabulary()


@router.get("/decisions")
def decisions(book: CollateralBook = BookDep) -> dict[str, Any]:
    """Every decision the research does not make, with the alternative rejected."""
    return book.decisions()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Counts for the scope: artefacts by state and kind, sections, assets."""
    return book.summary(room_id)


# --------------------------------------------------------------------------- #
# The content library
# --------------------------------------------------------------------------- #


@router.get("/assets")
def list_assets(book: CollateralBook = BookDep) -> dict[str, Any]:
    """Every source asset, by key."""
    assets = book.list_assets()
    return {"count": len(assets), "assets": assets}


@router.post("/assets", status_code=201)
def create_asset(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Register a case study, a deck or a pricing entry as a source asset."""
    return book.register_asset(payload, actor=actor, source=_source("POST", "/assets"))


@router.patch("/assets/{asset_id}")
def update_asset(
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Change an asset and bump its version."""
    return book.update_asset(
        asset_id, payload, actor=actor, source=_source("PATCH", "/assets/{asset_id}")
    )


@router.post("/assets/{asset_id}/refresh")
def refresh_from_asset(
    asset_id: str,
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Push a changed asset across every live artefact that draws from it."""
    return book.refresh_from_asset(
        asset_id, actor=actor, source=_source("POST", "/assets/{asset_id}/refresh")
    )


# --------------------------------------------------------------------------- #
# The collateral
# --------------------------------------------------------------------------- #


@router.get("/collateral")
def list_collateral(
    room_id: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Every artefact in scope, newest first."""
    rows = book.list_collaterals(room_id)
    return {"count": len(rows), "room_id": room_id, "collateral": rows}


@router.post("/collateral", status_code=201)
def create_collateral(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str = Query(..., description="The room this artefact belongs to"),
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Assemble a personalised document or deck from a deal and a template.

    The body carries ``deal`` (the CRM record, supplied by the caller),
    ``template`` (a shipped key or a section list), and an optional ``title``.
    """
    return book.create_collateral(
        room_id, payload, actor=actor, source=_source("POST", "/collateral")
    )


@router.get("/collateral/{collateral_id}")
def read_collateral(collateral_id: str, book: CollateralBook = BookDep) -> dict[str, Any]:
    """One artefact, its sections and their visibility at the deal's stage."""
    return book.read(collateral_id)


@router.patch("/collateral/{collateral_id}")
def update_collateral(
    collateral_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Review and edit: the title, the deal stage, or the sections."""
    return book.update_collateral(
        collateral_id, payload, actor=actor, source=_source("PATCH", "/collateral/{collateral_id}")
    )


@router.post("/collateral/{collateral_id}/publish")
def publish_collateral(
    collateral_id: str,
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Publish the artefact, and write it into the room through the pages seam."""
    published = book.publish(
        collateral_id, actor=actor, source=_source("POST", "/collateral/{collateral_id}/publish")
    )
    page_id = _publish_room_page(
        store,
        published,
        actor=actor,
        source=_source("POST", "/collateral/{collateral_id}/publish"),
    )
    if page_id:
        published = book.attach_page(
            collateral_id,
            page_id,
            actor=actor,
            source=_source("POST", "/collateral/{collateral_id}/publish"),
        )
    return published


@router.post("/collateral/{collateral_id}/withdraw")
def withdraw_collateral(
    collateral_id: str,
    actor: str | None = Query(default=None),
    book: CollateralBook = BookDep,
) -> dict[str, Any]:
    """Withdraw the artefact from the room. It accepts no further edit."""
    return book.withdraw(
        collateral_id, actor=actor, source=_source("POST", "/collateral/{collateral_id}/withdraw")
    )


@router.get("/collateral/{collateral_id}/events")
def collateral_events(collateral_id: str, book: CollateralBook = BookDep) -> dict[str, Any]:
    """The artefact's own trail: generated, edited, revealed, published, refreshed."""
    book.read(collateral_id)
    events = book.list_events(collateral_id)
    return {"collateral_id": collateral_id, "count": len(events), "events": events}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The content-library entry the proposal's synced section draws from. A change
#: to it re-renders every live artefact that references it.
DEMO_ASSET_KEY = "northwind-case-study"
DEMO_ASSET_BODY_V1 = (
    "Northwind cut onboarding time by 38 percent in the first quarter after adopting the room."
)
DEMO_ASSET_BODY_V2 = (
    "Northwind cut onboarding time by 38 percent in the first quarter and renewed early."
)

#: The deal the demo document is personalised from. The stage is ``discovery``,
#: so the pricing section is present but hidden until the deal reaches
#: ``procurement`` - the reveal the evidence describes.
DEMO_DEAL: dict[str, Any] = {
    "account": "Northwind",
    "contact": "Ava Chen",
    "product": "Digital Sales Room",
    "stage": "discovery",
    "seller": "dana",
    "deal_id": "sf-op-0001",
}


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed two artefacts, a refreshed synced section and a refused edit.

    The demo deliberately contains the states the happy path alone would not
    teach, because a demo of only success teaches nothing:

    * a published proposal document personalised from a deal, whose pricing
      section is hidden until ``procurement``;
    * a synced case-study section, refreshed after its source asset changes;
    * a withdrawn deck, which refuses a further edit - the non-success state the
      seeder prints.

    Every write goes through the audited store, so the demo cannot show a shape
    the workflow would not produce.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return "0 generated collateral (no rooms to scope them to)"
    room_id = rooms[0][0]
    book = CollateralBook(RecordStore(db))

    book.register_asset(
        {
            "key": DEMO_ASSET_KEY,
            "kind": "case_study",
            "title": "Northwind onboarding case study",
            "body": DEMO_ASSET_BODY_V1,
        },
        actor="dana",
        source="seed",
    )
    duplicate_refused = 0
    try:
        book.register_asset({"key": DEMO_ASSET_KEY, "body": "again"}, actor="dana", source="seed")
    except CollateralError:
        duplicate_refused += 1

    proposal = book.create_collateral(
        room_id,
        {"deal": DEMO_DEAL, "template_key": "proposal_document"},
        actor="dana",
        source="seed",
    )
    book.publish(proposal["id"], actor="dana", source="seed")

    # The source asset changes; the synced section is pushed into the live
    # artefact.
    assets = {asset["key"]: asset for asset in book.list_assets()}
    asset_id = assets[DEMO_ASSET_KEY]["id"]
    book.update_asset(asset_id, {"body": DEMO_ASSET_BODY_V2}, actor="dana", source="seed")
    refreshed = book.refresh_from_asset(asset_id, actor="dana", source="seed")

    # A second artefact the seller withdrew, which then refuses an edit.
    deck = book.create_collateral(
        room_id,
        {"deal": DEMO_DEAL, "template_key": "discovery_deck", "title": "Northwind discovery deck"},
        actor="dana",
        source="seed",
    )
    book.publish(deck["id"], actor="dana", source="seed")
    book.withdraw(deck["id"], actor="dana", source="seed")
    withdrawn_refused = 0
    try:
        book.update_collateral(deck["id"], {"title": "Nope"}, actor="dana", source="seed")
    except CollateralError:
        withdrawn_refused += 1

    listing = book.list_collaterals(room_id)
    published = [item for item in listing if item["state"] == "published"]
    withdrawn = [item for item in listing if item["state"] == "withdrawn"]
    hidden = sum(int(item.get("hidden_count") or 0) for item in published)
    assets_now = book.list_assets()
    return (
        f"{len(listing)} collateral ({len(published)} published, "
        f"{len(withdrawn)} withdrawn) from 2 templates; "
        f"{hidden} hidden section (revealed at procurement); "
        f"{refreshed['refreshed']} live artefact refreshed to case study "
        f"v{refreshed['version']}; {len(assets_now)} source asset "
        f"({duplicate_refused} duplicate refused); "
        f"{withdrawn_refused} withdrawn artefact refused an edit"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
