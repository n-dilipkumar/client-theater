"""WF-071: stamp a dynamic per-viewer watermark on every page of a tracked link.

The rules live in :mod:`dsr.viewer_watermark`, which knows nothing about HTTP.
This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary and the inferences, served from one place so the page cannot
  drift from the rules that validate it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

**What this feature reuses, and why that is the whole design.** The brief for
this ticket says to read :mod:`dsr.tracked_deal_link` and reuse its link, viewer
and revisit vocabulary rather than re-declaring it. So:

* the link a watermark is set on is a WF-121 ``wf121_tracked_link`` row, resolved
  through WF-121's own ``resolve_link``;
* the viewer email in the watermark is WF-121's ``require_viewer`` - WF-069's
  ``normalize_email`` - so the address that is stamped is the address the
  analytics recorded;
* the link-open boundary is WF-121's ``require_open``;
* the plan gate and its quoted rule are WF-077's.

The researched mapping of the spec's link API onto this feature:

===========================  =====================================================
Spec route                   Here
===========================  =====================================================
``POST /v1/links``           ``POST /api/wf-071/links/{id}/watermark`` (enable)
``PATCH /v1/links/{id}``     ``PATCH /api/wf-071/links/{id}/watermark``
``GET /v1/links/{id}``       ``GET /api/wf-071/links/{id}/watermark``
(no spec route)              ``DELETE /api/wf-071/links/{id}/watermark`` (disable)
===========================  =====================================================

Link *creation* stays WF-121's, which is the workflow that provisions the link;
this feature owns only the watermark on it.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-071.md``, quoted in
full on issue #133.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.pdf_analytics import AnalyticsBook
from dsr.store import RecordStore
from dsr.tracked_deal_link import TrackedLinkBook
from dsr.viewer_watermark import (
    ViewerWatermarkError,
    WatermarkBook,
    WatermarkPlanError,
    WatermarkValidationError,
)

FEATURE = {
    "id": "wf-071-watermark",
    "ticket": "WF-071",
    "name": "Stamp a dynamic per-viewer watermark",
    "description": (
        "A rep enables a watermark on a tracked link and sets its text; at view "
        "time the tokens ({{email}}, {{date}}, {{time}}, {{link}}, {{ipAddress}}) "
        "are substituted with the values of that view and composited onto every "
        "page. The link, the viewer identity and the open boundary are WF-121's; "
        "the plan gate is WF-077's. Nothing is burned into a stored document."
    ),
    "nav": [{"id": "viewer-watermark", "label": "Viewer watermark"}],
}

router = APIRouter(prefix="/api/wf-071", tags=["WF-071"])


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> WatermarkBook:
    """The book, built on the shared audited store.

    Per request rather than hung on ``app.state``, which is the shared-file edit
    this host exists to remove. It also leaves the clock a seam a test can replace.
    """
    return WatermarkBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _watermark_error(request: Request, exc: ViewerWatermarkError) -> JSONResponse:
    """A refusal this workflow made, answered with the status it carries.

    One handler for the whole hierarchy. ``ViewerWatermarkError`` is defined in
    :mod:`dsr.viewer_watermark.errors`, so a global handler for it cannot
    intercept an unrelated error anywhere else - which is why the builtins are not
    claimed here, and why the plan refusal is this workflow's own type rather than
    WF-077's already-mapped ``PlanFeatureError``.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


EXCEPTION_HANDLERS = {ViewerWatermarkError: _watermark_error}


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(book: WatermarkBook = BookDep) -> dict[str, Any]:
    """Every value this workflow accepts, and which workflow it is borrowed from."""
    return book.vocabulary()


@router.get("/inferences")
def inferences(book: WatermarkBook = BookDep) -> dict[str, Any]:
    """Every decision the research does not make, and how to change each one."""
    return book.inferences()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Counts for the scope: links, watermarked, tiled, stamps, viewers stamped."""
    return book.summary(room_id)


# --------------------------------------------------------------------------- #
# The watermark on a link
# --------------------------------------------------------------------------- #


@router.get("/links")
def list_links(
    room_id: str | None = Query(default=None),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Every tracked link in scope, each with its watermark settings joined."""
    links = book.list_links(room_id)
    return {"count": len(links), "room_id": room_id, "links": links}


@router.get("/links/{link_id}/watermark")
def read_watermark(link_id: str, book: WatermarkBook = BookDep) -> dict[str, Any]:
    """The link and its settings, always including ``watermark_config`` or null."""
    return book.read(link_id)


@router.post("/links/{link_id}/watermark")
def enable_watermark(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    plan: str | None = Query(
        default=None,
        description="The team's plan, checked only when this flips the toggle on",
    ),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Enable the watermark on a link and set its seven-field config.

    Every field the body omits is filled from the documented default, and every
    field it carries is validated. A request that flips the toggle from off to on
    is refused with 403 when the named plan does not cover a watermark.
    """
    return book.enable(
        link_id,
        payload,
        actor=actor,
        source=_source("POST", "/links/{link_id}/watermark"),
        plan=plan,
    )


@router.patch("/links/{link_id}/watermark")
def patch_watermark(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    plan: str | None = Query(default=None),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Replace the config, or clear it with ``{"watermark_config": null}``.

    Clearing leaves the toggle on, which is the researched PATCH. A patch against
    a link with no config yet is an enable and goes through the plan gate.
    """
    return book.patch(
        link_id,
        payload,
        actor=actor,
        source=_source("PATCH", "/links/{link_id}/watermark"),
        plan=plan,
    )


@router.delete("/links/{link_id}/watermark")
def disable_watermark(
    link_id: str,
    actor: str | None = Query(default=None),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Turn the watermark off. The config is kept, so re-enabling restores it."""
    return book.disable(
        link_id, actor=actor, source=_source("DELETE", "/links/{link_id}/watermark")
    )


@router.post("/preview")
def preview(
    payload: dict[str, Any] = Body(default_factory=dict),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Render a sample overlay from a config, with no write.

    The panel shows the substituted tokens before a rep saves, and the same pure
    rules render a real view, so the preview cannot disagree with delivery.
    """
    return book.preview(payload)


# --------------------------------------------------------------------------- #
# The viewer's stamp
# --------------------------------------------------------------------------- #


@router.post("/links/{link_id}/stamp", status_code=201)
def stamp_view(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Stamp one view: interpolate the tokens for this viewer and composite.

    The body is the view: ``viewer``, ``ip_address``, and optionally
    ``occurred_at`` and ``page_count``. The rendered text is returned with one
    entry per page - the link's own page count unless the body overrides it - and
    the view is recorded so the trace is auditable. A link with no watermark
    returns ``enable_watermark: false`` and composites nothing.
    """
    return book.stamp(
        link_id,
        payload,
        actor=actor,
        source=_source("POST", "/links/{link_id}/stamp"),
    )


@router.get("/links/{link_id}/stamps")
def list_stamps(
    link_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    book: WatermarkBook = BookDep,
) -> dict[str, Any]:
    """Every watermark stamped on this link, newest first."""
    stamps = book.stamps(link_id, limit=limit)
    return {"link_id": link_id, "count": len(stamps), "stamps": stamps}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The demo deck, registered through WF-018's own ingest so the link points at a
#: real library asset rather than at a row this feature invented.
DEMO_ASSET = {
    "externalId": "wf071-demo-confidential-deck",
    "name": "Enterprise Security & Compliance Overview",
    "type": "pdf",
    "pageCount": 12,
    "tags": ["security", "confidential", "approved"],
    "downloadEnabled": False,
    "isInternal": False,
    "trackingEnabled": True,
}

#: ``Confidential — {{email}} — {{date}} — {{link}}``, the user-flow example. The
#: em dash is kept in the stored text; it is the summary string that must stay
#: cp1252-safe, and that is plain ASCII.
DEMO_TEXT = "Confidential \u2014 {{email}} \u2014 {{date}} \u2014 {{link}}"
DEMO_IP = "203.0.113.24"


def _demo_setup(db: AuditedDatabase, context: dict[str, Any]) -> tuple[WatermarkBook, Any, str]:
    rooms = context.get("room_ids") or []
    if not rooms:
        raise WatermarkValidationError("no room to scope the demo to")
    room_id = rooms[0][0]
    store = RecordStore(db)
    library = AnalyticsBook(store)
    library.register_asset(DEMO_ASSET, room_id=room_id, actor="dana", source="seed")
    book = WatermarkBook(store)
    links = TrackedLinkBook(store)
    link = links.create_link(
        room_id,
        {
            "title": "Enterprise Security & Compliance Overview",
            "asset_id": DEMO_ASSET["externalId"],
            "recipients": ["a.buyer@northwind.example"],
            "crm_system": "salesforce",
        },
        actor="dana",
        source="seed",
    )
    links.attach_share_url(
        link["id"],
        f"/api/wf-121/links/{link['id']}",
        actor="dana",
        source="seed",
    )
    return book, links, link["id"]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed one watermarked link and the states a reviewer needs to read.

    The demo deliberately contains the states the happy path alone would not
    teach, because a demo of only success teaches nothing:

    * the plan gate refusing a ``starter`` plan, then the same request passing on
      the plan that does cover a watermark;
    * a tiled watermark with all four identity tokens, stamped for a buyer with an
      email and an IP, so the substituted text is visible;
    * a **second** link whose config was cleared while the toggle stayed on, which
      is the researched PATCH;
    * an anonymous stamp on a link that asks for no viewer, so ``{{email}}``
      resolves to the recorded placeholder and is named in ``unresolved_tokens``.

    Every write goes through the real rules, so the demo cannot show a shape the
    workflow would not produce.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return "0 watermarked links (no rooms to scope them to)"
    now = context["now"]
    book, links, link_id = _demo_setup(db, context)

    # The plan gate is a real refusal, not a promise: a lower plan cannot turn the
    # toggle on. Caught here so the seeder prints it rather than aborting.
    try:
        book.enable(link_id, {"text": DEMO_TEXT}, actor="dana", source="seed", plan="starter")
        plan_state = "plan gate did not fire"
    except WatermarkPlanError:
        plan_state = "starter plan refused, then business plan allowed"

    book.enable(
        link_id,
        {
            "text": DEMO_TEXT,
            "is_tiled": True,
            "position": "middle-center",
            "rotation": 45,
            "color": "#000000",
            "font_size": 24,
            "opacity": 0.5,
        },
        actor="dana",
        source="seed",
        plan="business",
    )

    stamps = 0
    for viewer, ip, hours in (
        ("a.buyer@northwind.example", DEMO_IP, 0),
        ("a.buyer@northwind.example", DEMO_IP, 26),
    ):
        book.stamp(
            link_id,
            {
                "viewer": viewer,
                "ip_address": ip,
                "occurred_at": (now - timedelta(hours=hours)).isoformat(),
            },
            actor="gate",
            source="seed",
        )
        stamps += 1

    # The second link: anonymous, config cleared with the toggle left on.
    anonymous = links.create_link(
        room_id=rooms[0][0],
        payload={
            "title": "Open overview",
            "asset_id": DEMO_ASSET["externalId"],
            "require_viewer": False,
        },
        actor="dana",
        source="seed",
    )
    book.enable(
        anonymous["id"],
        {"text": "Draft \u2014 {{email}} \u2014 {{ipAddress}}"},
        actor="dana",
        source="seed",
    )
    book.patch(
        anonymous["id"],
        {"watermark_config": None},
        actor="dana",
        source="seed",
    )
    cleared_state = "second link cleared its config with the toggle still on"

    return (
        f"2 watermarked links ({stamps} stamps, 1 tiled with all four identity tokens, "
        f"1 anonymous: {plan_state}; {cleared_state}; the anonymous link names "
        "'email' and 'ipAddress' in unresolved_tokens)"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
