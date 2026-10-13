"""WF-118: order, throttle and repeat in-app guide prompts.

Rank guides in an application's order list, apply an app-level throttle between
two automatic guides, and relaunch a guide after a visitor dismisses it on an
interval the activation card sets. Guides that ignore throttling sort first, so
the order list is two-tier rather than flat.

The domain lives in :mod:`dsr.guide_prompts`: the vocabulary and the sourced
rules, the refusal hierarchy, the pure ordering and eligibility rules, and
:class:`GuidePrompts` over the record store. This module is the HTTP surface and
nothing more. It declares its own prefix, takes its dependency from ``dsr.deps``
(never ``dsr.api``), and lets the plugin host mount it by discovery, so no shared
file is edited to add it.

One handler covers the whole refusal hierarchy because FastAPI accepts exception
handlers on the application only, and the host attaches what this module exports.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.guide_prompts import GuidePrompts, vocabulary
from dsr.guide_prompts.engine import seed  # re-exported for the seeder
from dsr.guide_prompts.errors import REFUSAL_STATUS, GuidePromptsRefusal
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-118-guide-prompts",
    "ticket": "WF-118",
    "prefix": "/api/wf-118",
    "name": "Order, throttle and repeat in-app guide prompts",
    "description": (
        "Rank guides in an application order list, apply an app-level throttle "
        "between two automatic guides, and relaunch a guide after a visitor "
        "dismisses it on an interval the activation card sets. The throttle "
        "countdown starts when the previous guide is dismissed or completed, and "
        "a dismissal restarts both the repeat clock and the throttle clock."
    ),
}

#: Owned by this ticket and therefore unique. Every concrete path below is under
#: it, so nothing here can shadow another feature's route or a core one.
router = APIRouter(prefix="/api/wf-118", tags=["WF-118"])


def _refusal(request: Request, exc: GuidePromptsRefusal) -> JSONResponse:
    """One handler for the family, dispatched on the concrete subclass."""
    status, code = REFUSAL_STATUS.get(type(exc), (422, "guide_prompts_refused"))
    return JSONResponse(
        status_code=status,
        content={
            "error": code,
            "detail": str(exc),
            "problems": list(getattr(exc, "problems", []) or []),
        },
    )


EXCEPTION_HANDLERS = {GuidePromptsRefusal: _refusal}

__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]


def engine(store: RecordStore) -> GuidePrompts:
    return GuidePrompts(store)


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Actor") or "dana")


def _source(request: Request) -> str:
    """The audit ``source`` for a write: the route that actually served it.

    Using the live request path rather than a hand-written string means the audit
    row always names a route the app serves, including when a path parameter
    carried a real value.
    """
    return f"{request.method} {request.url.path}"


# --------------------------------------------------------------------------- #
# Vocabulary and decisions
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route() -> dict[str, Any]:
    """The rules, units, decisions and sources, without a store."""
    return vocabulary()


@router.get("/decisions")
def decisions_route() -> dict[str, Any]:
    """Every judgement this build recorded, with its audit id and the alternative."""
    return {"count": len(vocabulary()["decisions"]), "decisions": vocabulary()["decisions"]}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Counts the ordering page leads with."""
    return engine(store).summary()


@router.get("/guides")
def list_guides(
    status: str | None = Query(default=None, description="staging or public"),
    q: str | None = Query(default=None, description="Substring of name or key"),
    product_area: str | None = Query(default=None),
    category: str | None = Query(default=None),
    page_location: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every guide, filtered the way the ordering table filters."""
    guides = engine(store).guides(
        status=status,
        q=q,
        product_area=product_area,
        category=category,
        page_location=page_location,
        include_deleted=include_deleted,
        limit=limit,
    )
    return {"count": len(guides), "guides": guides}


@router.get("/guides/{key}")
def get_guide(
    key: str,
    include_deleted: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One guide with its activation and its derived stop-after label."""
    guide = engine(store).require_guide(key, include_deleted=include_deleted)
    return engine(store).guide_view(guide)


# --------------------------------------------------------------------------- #
# Authoring
# --------------------------------------------------------------------------- #


@router.post("/guides", status_code=201)
def create_guide(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Author a guide in the staging state."""
    body = dict(payload)
    fields = {k: v for k, v in body.items() if k not in {"key", "name"}}
    return engine(store).create(
        key=body.get("key"),
        name=body.get("name"),
        actor=_actor(request),
        source=_source(request),
        fields=fields,
    )


@router.patch("/guides/{key}")
def update_guide(
    key: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit a guide's activation or metadata. The key is immutable."""
    return engine(store).update(key, dict(payload), actor=_actor(request), source=_source(request))


@router.delete("/guides/{key}")
def delete_guide(
    key: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Soft-delete a guide. The row and its key survive."""
    return engine(store).delete(key, actor=_actor(request), source=_source(request))


@router.post("/guides/{key}/restore")
def restore_guide(
    key: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Undo a soft delete."""
    return engine(store).restore(key, actor=_actor(request), source=_source(request))


# --------------------------------------------------------------------------- #
# Ordering and throttling
# --------------------------------------------------------------------------- #


@router.get("/applications/{app_id}/order")
def get_order(app_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The order list and throttle for an application."""
    return engine(store).order_view(app_id)


@router.put("/applications/{app_id}/order")
def set_order(
    app_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Replace the order list and the throttle in one write."""
    return engine(store).set_order(
        app_id,
        guide_keys=payload.get("guide_keys"),
        throttling=payload.get("throttling"),
        actor=_actor(request),
        source=_source(request),
    )


@router.post("/applications/{app_id}/order/move")
def move_guide(
    app_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Apply one row arrow (top, up, down, bottom) to the order list."""
    return engine(store).move_guide(
        app_id,
        str(payload.get("key")),
        str(payload.get("move")),
        actor=_actor(request),
        source=_source(request),
    )


# --------------------------------------------------------------------------- #
# Eligibility and the session simulator
# --------------------------------------------------------------------------- #


@router.get("/applications/{app_id}/ranking")
def ranking(
    app_id: str,
    user_id: str = Query(default="visitor", description="The visitor whose session to read"),
    at: str | None = Query(default=None, description="ISO 8601 evaluation moment"),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The two-tier order list with each guide's eligibility at ``at``."""
    return engine(store).ranking(app_id, user_id, at)


@router.post("/applications/{app_id}/next")
def next_guide(
    app_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The next automatic guide to launch, or why none can."""
    return engine(store).next_guide(
        app_id, str(payload.get("user_id") or "visitor"), payload.get("at")
    )


@router.get("/applications/{app_id}/sessions/{user_id}")
def get_session(
    app_id: str,
    user_id: str,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """A visitor's stored clocks and per-guide counters."""
    return engine(store).session_view(app_id, user_id)


@router.post("/applications/{app_id}/events", status_code=201)
def record_event(
    app_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record an interaction: shown, dismissed, advanced or completed.

    A dismissal, a step advance and a completion all restart both clocks. A
    ``programmatic`` interaction models a Web SDK ``showGuideById`` launch, which
    bypasses the throttle gate and does not consume the interval.
    """
    return engine(store).record_event(
        app_id,
        str(payload.get("user_id") or "visitor"),
        event=payload.get("event"),
        key=payload.get("key"),
        at=payload.get("at"),
        cause=payload.get("cause"),
        programmatic=bool(payload.get("programmatic")),
        actor=_actor(request),
        source=_source(request),
    )
