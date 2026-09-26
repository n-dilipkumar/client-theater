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

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dsr.db.audited import AuditedDatabase, AuditError, RecordNotFound
from dsr.domain_service import DomainConflict, DomainService, HostNotServed
from dsr.domains import (
    DomainError,
    cname_target,
    default_base_url,
    link_secret_from_path,
    resolver_from_env,
)
from dsr.store import RecordStore, parse_where

_ROOT = Path(__file__).resolve().parents[2]


def _db_path() -> str:
    """Resolve the database path at call time, not import time.

    Reading the environment lazily matters for test isolation: a module-level
    constant would be captured on first import and every test would silently
    share one database.
    """
    return os.environ.get("DSR_DB_PATH", str(_ROOT / "data" / "dsr.db"))


def _mirror_dir() -> str:
    return os.environ.get("DSR_AUDIT_DIR", str(_ROOT / "data" / "audit"))


# Static file mounting is inherently import-time, so this one stays a constant.
FRONTEND_DIST = Path(os.environ.get("DSR_FRONTEND_DIST", str(_ROOT / "frontend" / "dist")))


def get_store(request: Request) -> RecordStore:
    """FastAPI dependency yielding the process-wide store."""
    return request.app.state.store


StoreDep = Depends(get_store)


def get_domain_service(request: Request) -> DomainService:
    """FastAPI dependency yielding the domain service.

    Held on app state rather than built per request so the resolver is created
    once: constructing it reads the deployment's DNS configuration, and a new
    one per request would re-read configuration for every call.
    """
    return request.app.state.domain_service


DomainDep = Depends(get_domain_service)


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    db = AuditedDatabase(path, mirror_dir=_mirror_dir(), actor="api")
    app.state.db = db
    app.state.store = RecordStore(db)
    app.state.domain_service = DomainService(app.state.store, resolver=resolver_from_env())
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


@app.exception_handler(DomainError)
async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
    # 422: the value was well-formed HTTP but not a usable domain or brand token.
    return JSONResponse(status_code=422, content={"error": "invalid_domain", "detail": str(exc)})


@app.exception_handler(HostNotServed)
async def _host_not_served(request: Request, exc: HostNotServed) -> JSONResponse:
    # 404: the host is not routed to this deployment, so nothing here can be
    # served. Kept as 404 rather than 421 to avoid advertising that the secret
    # is real.
    return JSONResponse(
        status_code=404,
        content={"error": "host_not_served", "detail": f"{exc.host} is not served by this deployment"},
    )


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
    """Merge a partial payload into ``data`` and audit the change.

    For ``room`` records the product's own fields are filtered out first. The
    share-link secret is documented as a "non-removable identifier", so the
    generic route must not be a back door for clearing it; the dedicated
    endpoints under ``/api/rooms`` are the only writer for those fields.
    """
    existing = store.get(record_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"record {record_id} not found")
    if existing["collection"] != collection:
        raise HTTPException(status_code=404, detail=f"record {record_id} is not in {collection}")

    patch = payload
    if collection == "room":
        patch = DomainService.strip_reserved(payload)

    return store.update(
        record_id,
        patch,
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
# White-label rooms on a custom domain (WF-017)
# --------------------------------------------------------------------------- #
#
# These routes are the researched flow, in the researched order: configure DNS
# with a CNAME, wait for it to propagate, enter the domain, let the service
# verify it, then save. They sit on top of the same schema-flexible record
# store as everything else -- no table, column, or migration was added.


@app.get("/api/white-label/config", tags=["white-label"])
def white_label_config() -> dict[str, Any]:
    """Deployment facts the setup screen needs.

    The CNAME target is the deployment's own edge hostname, surfaced from the
    server so the UI never has to guess or hard-code it.
    """
    return {
        "cname_target": cname_target(),
        "base_url": default_base_url(),
        "record_type": "CNAME",
        "propagation_note": (
            "A new CNAME can take up to 24 hours to propagate. You can keep sharing the default-host "
            "links while you wait; they will continue to work."
        ),
        "cloudflare_note": (
            "If this domain is proxied by Cloudflare, set the CNAME to DNS only (proxy off). "
            "A proxied record will not verify and slows page loads."
        ),
        "format_note": "The domain must be in subdomain format, for example proposals.acme.com.",
    }


@app.get("/api/rooms/{room_id}/white-label", tags=["white-label"])
def room_white_label(
    room_id: str, service: DomainService = DomainDep
) -> dict[str, Any]:
    """A room's public identity: domain state, share links, brand tokens."""
    try:
        room = service.get_room(room_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return service.describe(room)


@app.post("/api/rooms/{room_id}/white-label/link-secret", tags=["white-label"])
def room_link_secret(
    room_id: str,
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Mint the room's share-link secret, if it has none."""
    try:
        room = service.ensure_link_secret(room_id, actor=actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return service.describe(room)


@app.post("/api/white-label/verify", tags=["white-label"])
def verify_domain(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Check a candidate domain. Claims nothing.

    Separate from the claim endpoint because the researched flow is verify-then-
    save: the operator needs to see what is wrong before committing to a change.
    """
    domain = payload.get("domain")
    if domain is None:
        raise HTTPException(status_code=400, detail="domain is required")
    return service.verify(domain)


@app.post("/api/rooms/{room_id}/white-label/domain", tags=["white-label"])
def claim_domain(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    force: bool = Query(default=False, description="Save even if the CNAME has not propagated yet"),
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Attach a verified custom domain to a room."""
    domain = payload.get("domain")
    if domain is None:
        raise HTTPException(status_code=400, detail="domain is required")
    try:
        service.claim(room_id, domain, force=force, actor=actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    except DomainConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.describe(service.get_room(room_id))


@app.delete("/api/rooms/{room_id}/white-label/domain", tags=["white-label"])
def release_domain(
    room_id: str,
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Detach the custom domain. Share links keep working on the default host."""
    try:
        service.release(room_id, actor=actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return service.describe(service.get_room(room_id))


@app.post("/api/rooms/{room_id}/white-label/recheck", tags=["white-label"])
def recheck_domain(
    room_id: str,
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Re-run DNS verification for the domain the room already holds."""
    try:
        service.recheck(room_id, actor=actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return service.describe(service.get_room(room_id))


@app.patch("/api/rooms/{room_id}/white-label/branding", tags=["white-label"])
def update_branding(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Merge brand tokens into the room's open ``branding`` object.

    Colours and font stacks are validated because they are rendered into inline
    styles. Every other key passes through untouched: a team can add
    ``branding.email_footer`` without coordinating with anyone.
    """
    try:
        service.update_branding(room_id, payload, actor=actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return service.describe(service.get_room(room_id))


@app.get("/api/white-label/links/{secret}", tags=["white-label"])
def resolve_link(
    secret: str,
    host: str | None = Query(default=None, description="The Host header the request arrived on"),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Resolve a share-link secret to the room it points at.

    Identity is the secret, not the host, so the same secret resolves on the
    default host and on every verified custom domain. That is what stops a
    domain change from breaking links the customer already shared.
    """
    try:
        resolved = service.resolve(secret, host=host)
    except KeyError:
        raise HTTPException(status_code=404, detail="no room matches that link secret") from None
    room = resolved["room"]
    return {
        "room_id": room["id"],
        "name": (room.get("data") or {}).get("name"),
        "served_on_custom_domain": resolved["served_on_custom_domain"],
        "white_label": service.describe(room),
    }


@app.get("/api/white-label/resolve", tags=["white-label"])
def resolve_link_path(
    path: str = Query(description="The request path, e.g. /r/Proposal-Name-aB3xY9zK1q"),
    host: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Resolve a full room path to its secret and room.

    The inverse of link construction: reads the trailing secret out of the path
    so an edge proxy can route a white-labelled request without reimplementing
    the slug rules.
    """
    secret = link_secret_from_path(path)
    if not secret:
        raise HTTPException(status_code=404, detail="path carries no link secret")
    return resolve_link(secret=secret, host=host, service=service)


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
