"""WF-115: publish custom workflow triggers and actions to an app marketplace.

A third-party builder authors a workflow trigger or action -- its immutable key,
sample JSON payload, filters, custom variables, and the URLs the platform will
call -- submits it for review with a changelog, and once it is approved and
published it becomes a reusable primitive for every sub-account.

The domain lives in :mod:`dsr.marketplace_primitives`: the vocabulary, the
validation rules, the review state machine, and :class:`Marketplace` over the
record store. This module is the HTTP surface and nothing more. It declares its
own prefix, takes its dependency from ``dsr.deps`` (never ``dsr.api``), and lets
the plugin host mount it by discovery, so no shared file is edited to add it.

One handler covers the whole refusal hierarchy because FastAPI accepts exception
handlers on the application only, and the host attaches what this module exports.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.marketplace_primitives import Marketplace, vocabulary
from dsr.marketplace_primitives.engine import seed  # re-exported for the seeder
from dsr.marketplace_primitives.errors import REFUSAL_STATUS, MarketplaceRefusal
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-115-marketplace-primitives",
    "ticket": "WF-115",
    "prefix": "/api/wf-115",
    "name": "Publish custom workflow triggers and actions to an app marketplace",
    "description": (
        "Author a marketplace trigger or action with its immutable key, sample "
        "JSON, filters and endpoint URLs, submit it for review with a changelog, "
        "and publish an approved version to every sub-account. The review step is "
        "a state machine; deletion is a soft delete so a workflow that references "
        "a deleted key can still be told the trigger was deleted."
    ),
}

#: Owned by this ticket and therefore unique. Every concrete path below is
#: under it, so nothing here can shadow another feature's route or a core one.
router = APIRouter(prefix="/api/wf-115", tags=["WF-115"])


def _refusal(request: Request, exc: MarketplaceRefusal) -> JSONResponse:
    """One handler for the family, dispatched on the concrete subclass."""
    status, code = REFUSAL_STATUS.get(type(exc), (422, "marketplace_refused"))
    return JSONResponse(
        status_code=status,
        content={
            "error": code,
            "detail": str(exc),
            "problems": list(getattr(exc, "problems", []) or []),
        },
    )


EXCEPTION_HANDLERS = {MarketplaceRefusal: _refusal}

__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]


def engine(store: RecordStore) -> Marketplace:
    return Marketplace(store)


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Actor") or "dana")


def _source(request: Request) -> str:
    """The audit ``source`` for a write: the route that actually served it.

    Using the live request path rather than a hand-written string means the
    audit row always names a route the app serves, including when a path
    parameter carried a real id.
    """
    return f"{request.method} {request.url.path}"


# --------------------------------------------------------------------------- #
# Vocabulary and decisions
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route() -> dict[str, Any]:
    """The rules, states, transitions, decisions and sources, without a store."""
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
    """Counts per review state and per kind."""
    return engine(store).summary()


@router.get("/modules")
def list_modules(
    kind: str | None = Query(default=None, description="trigger or action"),
    review_state: str | None = Query(default=None, description="Comma-separated review states"),
    q: str | None = Query(default=None, description="Substring of name or key"),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every module, newest first, filtered in memory over the small set."""
    modules = engine(store).modules(
        kind=kind,
        review_state=review_state,
        q=q,
        include_deleted=include_deleted,
        limit=limit,
    )
    return {"count": len(modules), "modules": modules}


@router.get("/modules/{module_id}")
def get_module(
    module_id: str,
    include_deleted: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One module with its version, review, and delivery history."""
    return engine(store).detail(module_id, include_deleted=include_deleted)


@router.get("/keys/{key}")
def get_by_key(
    key: str,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Resolve a module by its immutable key, the reference a workflow holds.

    The key resolves even when the module is not published, so a caller can be
    told the difference between "no such key" and "that trigger is not live".
    A deleted module resolves with ``deleted`` true, because a workflow that
    references a deleted trigger must be able to learn that it was deleted.
    """
    view = engine(store).by_key(key, include_deleted=True)
    if view is None:
        from dsr.marketplace_primitives.errors import ModuleNotFound

        raise ModuleNotFound(f"no marketplace module with key {key!r}")
    view = dict(view)
    view["resolvable"] = view["review_state"] == "published"
    return view


# --------------------------------------------------------------------------- #
# Authoring
# --------------------------------------------------------------------------- #


@router.post("/modules", status_code=201)
def create_module(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Author a new module in the draft state."""
    body = dict(payload)
    fields = {key: value for key, value in body.items() if key not in {"kind", "key", "name"}}
    return engine(store).create(
        kind=body.get("kind"),
        key=body.get("key"),
        name=body.get("name"),
        actor=_actor(request),
        source=_source(request),
        fields=fields,
    )


@router.patch("/modules/{module_id}")
def update_module(
    module_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit a module. The key is immutable; a published module is not editable."""
    return engine(store).update(
        module_id,
        dict(payload),
        actor=_actor(request),
        source=_source(request),
    )


# --------------------------------------------------------------------------- #
# The review state machine
# --------------------------------------------------------------------------- #


@router.post("/modules/{module_id}/submit")
def submit_module(
    module_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Submit a module for review with a changelog, incrementing its version."""
    return engine(store).submit(
        module_id,
        changelog=payload.get("changelog"),
        actor=_actor(request),
        source=_source(request),
    )


@router.post("/modules/{module_id}/review")
def review_module(
    module_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Take a review decision: start_review, approve, request_changes, or reject."""
    return engine(store).review(
        module_id,
        decision=payload.get("decision"),
        reviewer=str(payload.get("reviewer") or _actor(request)),
        note=payload.get("note"),
        actor=_actor(request),
        source=_source(request),
    )


@router.post("/modules/{module_id}/publish")
def publish_module(
    module_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Publish an approved module live to every sub-account."""
    return engine(store).publish(module_id, actor=_actor(request), source=_source(request))


@router.post("/modules/{module_id}/withdraw")
def withdraw_module(
    module_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Withdraw a submission, or unpublish a published module before editing."""
    return engine(store).withdraw(module_id, actor=_actor(request), source=_source(request))


@router.delete("/modules/{module_id}")
def delete_module(
    module_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Soft-delete a module. The row and its immutable key survive."""
    return engine(store).delete(module_id, actor=_actor(request), source=_source(request))


@router.post("/modules/{module_id}/restore")
def restore_module(
    module_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Undo a soft delete."""
    return engine(store).restore(module_id, actor=_actor(request), source=_source(request))


# --------------------------------------------------------------------------- #
# Subscription deliveries
# --------------------------------------------------------------------------- #


@router.post("/modules/{module_id}/deliveries", status_code=201)
def record_delivery(
    module_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record and evaluate an inbound subscription delivery for a published module.

    The room does not own the Subscription URL and never calls it. This route
    models the platform's POST so a builder can see the delivery resolve against
    the module's own filters and sample payload.
    """
    return engine(store).record_delivery(
        module_id,
        event_type=payload.get("event_type"),
        target_url=payload.get("target_url"),
        filters=payload.get("filters"),
        extras=payload.get("extras"),
        actor=_actor(request),
        source=_source(request),
    )
