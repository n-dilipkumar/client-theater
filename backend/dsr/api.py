"""HTTP API for the Digital Sales Room.

Every route is schema-flexible: record payloads are arbitrary JSON, so a team
can ship a new field without a migration, a redeploy, or a change to this file.
The only fixed vocabulary is the envelope around the payload (id, collection,
revision, timestamps) plus the audit trail.

The API is also the integration surface other teams are expected to use, so it
is documented in OpenAPI and returns stable, listable envelopes rather than
positional shapes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dsr.db.audited import AuditedDatabase, AuditError, RecordNotFound
from dsr.deps import FRONTEND_DIST, StoreDep, db_path as _db_path, get_store, mirror_dir as _mirror_dir
from dsr.features import load_features
from dsr.store import RecordStore, parse_where

__all__ = ["app", "get_store", "StoreDep", "FRONTEND_DIST"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    db = AuditedDatabase(path, mirror_dir=_mirror_dir(), actor="api")
    app.state.db = db
    app.state.store = RecordStore(db)
    yield
    db.close()


app = FastAPI(
    title="Digital Sales Room API",
    version="0.1.0",
    description=(
        "Schema-flexible API over an audited SQLite store. Every mutation is "
        "recorded in the audit log in the same transaction as the change."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


@app.exception_handler(RecordNotFound)
async def _not_found(request: Request, exc: RecordNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc), "id": str(exc)})


@app.exception_handler(AuditError)
async def _audit_error(request: Request, exc: AuditError) -> JSONResponse:
    # 409: the request was well-formed but conflicts with current state.
    status = 409 if "conflict" in str(exc).lower() or "exists" in str(exc).lower() else 400
    return JSONResponse(status_code=status, content={"error": "audit_error", "detail": str(exc)})


# --------------------------------------------------------------------------- #
# Health and stats
# --------------------------------------------------------------------------- #


@app.get("/api/health", tags=["system"])
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "digital-sales-room"}


@app.get("/api/stats", tags=["system"])
def stats(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Counts for the dashboard header: records, deletions, audit volume."""
    return store.stats()


@app.get("/api/collections", tags=["schema"])
def collections(store: RecordStore = StoreDep) -> dict[str, Any]:
    """List collections and the JSON fields actually in use in each.

    The ``fields`` map is the discovery endpoint that lets clients adapt to
    schema-flexible payloads without hard-coding a schema.
    """
    result = []
    for row in store.collections():
        entry = dict(row)
        entry["fields"] = store.fields(row["collection"])
        result.append(entry)
    return {"collections": result, "count": len(result)}


# --------------------------------------------------------------------------- #
# Records: schema-flexible CRUD
# --------------------------------------------------------------------------- #


@app.post("/api/records/{collection}", status_code=201, tags=["records"])
def create_record(
    collection: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create a record from an arbitrary JSON payload."""
    if not collection.strip():
        raise HTTPException(status_code=400, detail="collection is required")
    return store.create(collection, payload, room_id=room_id, actor=actor, source=f"POST /api/records/{collection}")


@app.get("/api/records/{collection}", tags=["records"])
def list_records(
    collection: str,
    room_id: str | None = Query(default=None),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    order_by: str = Query(default="updated_at"),
    descending: bool = Query(default=True),
    include_deleted: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """List or filter records. ``where`` matches arbitrary JSON paths in ``data``."""
    try:
        filters = parse_where(where)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if filters:
        records = store.find(
            collection, filters, limit=limit, include_deleted=include_deleted
        )
    else:
        records = store.list(
            collection,
            room_id=room_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
            descending=descending,
            include_deleted=include_deleted,
        )
    return {"collection": collection, "count": len(records), "records": records}


@app.post("/api/records/{collection}/bulk", status_code=201, tags=["records"])
def bulk_create(
    collection: str,
    items: list[dict[str, Any]] = Body(default_factory=list),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create many records in one transaction, producing one audit entry."""
    created = store.bulk_create(collection, items, room_id=room_id, actor=actor, source="POST bulk")
    return {"collection": collection, "count": len(created), "records": created}


@app.get("/api/records/{collection}/{record_id}", tags=["records"])
def get_record(collection: str, record_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    record = store.require(record_id)
    if record["collection"] != collection:
        raise HTTPException(status_code=404, detail=f"record {record_id} is not in {collection}")
    return record


@app.patch("/api/records/{collection}/{record_id}", tags=["records"])
def update_record(
    collection: str,
    record_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    expected_revision: int | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Merge a partial payload into ``data`` and audit the change."""
    existing = store.get(record_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"record {record_id} not found")
    if existing["collection"] != collection:
        raise HTTPException(status_code=404, detail=f"record {record_id} is not in {collection}")
    return store.update(
        record_id,
        payload,
        actor=actor,
        source=f"PATCH /api/records/{collection}/{record_id}",
        expected_revision=expected_revision,
    )


@app.delete("/api/records/{collection}/{record_id}", tags=["records"])
def delete_record(
    collection: str,
    record_id: str,
    hard: bool = Query(default=False),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Soft-delete by default; ``hard=true`` removes the row entirely."""
    existing = store.get(record_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"record {record_id} not found")
    return store.delete(
        record_id, hard=hard, actor=actor, source=f"DELETE /api/records/{collection}/{record_id}"
    )


@app.post("/api/records/{collection}/{record_id}/restore", tags=["records"])
def restore_record(
    collection: str,
    record_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Undo a soft delete."""
    return store.restore(record_id, actor=actor, source="POST restore")


# --------------------------------------------------------------------------- #
# Audit trail: the UI wrapper reads this
# --------------------------------------------------------------------------- #


@app.get("/api/audit", tags=["audit"])
def audit_log(
    collection: str | None = Query(default=None),
    record_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Read the audit trail, newest first, with before/after state and diffs."""
    entries = store.audit(
        collection=collection,
        record_id=record_id,
        actor=actor,
        action=action,
        since=since,
        limit=limit,
        offset=offset,
    )
    return {"count": len(entries), "entries": entries}


@app.get("/api/audit/{seq}", tags=["audit"])
def audit_entry(seq: int, store: RecordStore = StoreDep) -> dict[str, Any]:
    for entry in store.audit(limit=1000, offset=0):
        if entry["seq"] == seq:
            return entry
    raise HTTPException(status_code=404, detail=f"audit entry {seq} not found")


# --------------------------------------------------------------------------- #
# Feature plugins: one module per workflow, discovered on the next line.
#
# This must run before the SPA catch-all below, because that catch-all matches
# every path that has not already been claimed. A feature added under
# dsr/features/ is mounted here without this file being touched, which is the
# whole reason a hundred features can be built in parallel and merged.
# --------------------------------------------------------------------------- #

load_features(app)


# --------------------------------------------------------------------------- #
# Static frontend (served once built)
# --------------------------------------------------------------------------- #

if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Any:
        """Serve the SPA, falling back to index.html for client-side routes."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="unknown API route")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

# probe: this line exists only to prove the CI guard blocks a shared-file edit
