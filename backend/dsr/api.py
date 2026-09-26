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
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from dsr.access import AccessDenied, AccessGate, PolicyError
from dsr.db.audited import AuditedDatabase, AuditError, RecordNotFound
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    db = AuditedDatabase(path, mirror_dir=_mirror_dir(), actor="api")
    app.state.db = db
    app.state.store = RecordStore(db)
    app.state.access = AccessGate(app.state.store)
    yield
    db.close()


def get_access(request: Request) -> AccessGate:
    """The WF-015 identity gate, over the process-wide store."""
    return request.app.state.access


AccessDep = Depends(get_access)


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


@app.exception_handler(PolicyError)
async def _policy_error(request: Request, exc: PolicyError) -> JSONResponse:
    """400 with a field-keyed map, so the UI can put each message next to the
    input that caused it rather than showing one combined string."""
    return JSONResponse(
        status_code=400,
        content={"error": "policy_invalid", "detail": str(exc), "errors": exc.errors},
    )


@app.exception_handler(AccessDenied)
async def _access_denied(request: Request, exc: AccessDenied) -> JSONResponse:
    """403 for a buyer the gate will not let through.

    The attempt is always recorded on a session; only the attribution is
    withheld, so the response carries the reason and the session id and nothing
    about who the buyer is.
    """
    return JSONResponse(
        status_code=403,
        content={"error": exc.reason, "detail": str(exc), "session_id": exc.session_id},
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
# WF-015: buyer identity verification and email-domain restriction
# --------------------------------------------------------------------------- #
#
# The policy itself is a record, so it is also reachable through the generic
# /api/records/access_policy endpoints. These routes exist because the workflow
# has behaviour (resolve a tier, run a gate, verify a token), not because the
# data needs a schema.


def _base_url(request: Request) -> str:
    """Absolute base for links we hand a buyer.

    An operator behind a proxy sets DSR_PUBLIC_URL; otherwise the request's own
    host is used, which is right for a local install and for a single-host
    deployment.
    """
    return os.environ.get("DSR_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


@app.get("/api/rooms/{room_id}/access", tags=["access"])
def get_room_access(
    room_id: str, access: AccessGate = AccessDep, store: RecordStore = StoreDep
) -> dict[str, Any]:
    """The policy in force for a room, and the level it resolved at.

    `level` matters: a control inherited from a template and shown without its
    origin is indistinguishable from one the seller set themselves.
    """
    _require_room(store, room_id)
    return access.resolve(room_id).as_dict()


@app.put("/api/rooms/{room_id}/access", tags=["access"])
def put_room_access(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Validate, normalise, and store the room's own policy.

    The whole policy is one payload, because a seller filling in a form is not
    making a sequence of PATCHes. `PolicyError` becomes a 400 with a
    field-keyed error map.
    """
    _require_room(store, room_id)
    return access.set_policy("room", room_id, payload, actor=actor)


@app.delete("/api/rooms/{room_id}/access", tags=["access"])
def delete_room_access(
    room_id: str,
    actor: str | None = Query(default=None),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Drop the room's own policy so it falls back to whatever it inherits."""
    _require_room(store, room_id)
    return access.clear_policy("room", room_id, actor=actor)


@app.get("/api/templates/{template_id}/access", tags=["access"])
def get_template_access(template_id: str, access: AccessGate = AccessDep) -> dict[str, Any]:
    """The default policy a template applies to the rooms created from it.

    No store dependency and no 404: a template id is a free string a team chose,
    and "no policy set yet" is the honest answer rather than a missing record.
    """
    policy = access.template_policy(template_id)
    if policy is None:
        return {"policy": None, "level": "default", "source": None, "template_id": template_id}
    return {"policy": policy, "level": "template", "source": None, "template_id": template_id}


@app.put("/api/templates/{template_id}/access", tags=["access"])
def put_template_access(
    template_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    access: AccessGate = AccessDep,
) -> dict[str, Any]:
    """Set the template default. Rooms that inherit pick it up on their next read."""
    return access.set_policy("template", template_id, payload, actor=actor)


@app.delete("/api/templates/{template_id}/access", tags=["access"])
def delete_template_access(
    template_id: str,
    actor: str | None = Query(default=None),
    access: AccessGate = AccessDep,
) -> dict[str, Any]:
    return access.clear_policy("template", template_id, actor=actor)


@app.get("/api/rooms/{room_id}/access/requirements", tags=["access"])
def access_requirements(
    room_id: str, access: AccessGate = AccessDep, store: RecordStore = StoreDep
) -> dict[str, Any]:
    """What the buyer must do to get in.

    The allowlist is deliberately absent: a form that says "only foo.example is
    accepted" is a free allowlist oracle.
    """
    _require_room(store, room_id)
    return access.requirements(room_id)


@app.post("/api/rooms/{room_id}/access/sessions", status_code=201, tags=["access"])
def create_access_session(
    room_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Run a buyer's form submission through the gate.

    The tier decides everything downstream: `open` grants and records nothing,
    `identify` records the identity and grants without sending mail, and
    `verify_email` leaves the room closed until the emailed link is followed. A
    domain that is not on the list is refused with 403 and the attempt is
    recorded without being attributed to anybody.
    """
    _require_room(store, room_id)
    return access.open_session(
        room_id,
        payload,
        headers=dict(request.headers),
        method=str(payload.get("method") or "identified"),
        base_url=_base_url(request),
        deliver=os.environ.get("DSR_ACCESS_DELIVER", "1") not in ("0", "false", "no"),
    )


@app.get("/api/rooms/{room_id}/access/verify", tags=["access"])
def verify_access_session(
    room_id: str,
    request: Request,
    token: str = Query(default=""),
    redirect: bool = Query(default=False),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> Any:
    """The link from the verification email.

    Idempotent, because a buyer who clicks twice should not be punished, but
    every presentation is counted and audited. The allowlist is re-checked here
    as well as at the form, so a link that has been sitting in an inbox cannot
    outlive a policy the seller has since tightened.

    With ``redirect=1`` — the form the emailed link carries — a successful
    verification sends the buyer on to the room in the app instead of returning
    JSON to a human. A refusal still returns 403 rather than redirecting, so a
    dead link tells the buyer why.
    """
    _require_room(store, room_id)
    result = access.verify(room_id, token)
    if redirect:
        target = f"/#/view/{urllib.parse.quote(room_id)}?token={urllib.parse.quote(token)}"
        return RedirectResponse(url=target, status_code=303)
    return result


@app.get("/api/rooms/{room_id}/access/session", tags=["access"])
def check_access_session(
    room_id: str,
    token: str | None = Query(default=None),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """What a token is worth right now, and what is still owed.

    A status endpoint rather than a command: it answers 200 with a body for every
    state the buyer can legitimately be in, and reserves 4xx for an unknown token
    or a refusal. `token` is optional, so this also answers "what does this room
    need?" for a buyer who has no token yet.
    """
    _require_room(store, room_id)
    return access.check(room_id, token)


@app.get("/api/rooms/{room_id}/access/sessions", tags=["access"])
def list_access_sessions(
    room_id: str,
    include_bots: bool = Query(default=False),
    include_refused: bool = Query(default=False),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Who has been let in, and who has been turned away.

    Bot-flagged attempts are excluded by default: the research is explicit that
    scanners "pollute analytics", and a flag is only useful if it changes what
    the seller actually sees.
    """
    _require_room(store, room_id)
    return access.sessions(
        room_id, include_bots=include_bots, include_refused=include_refused
    )


@app.get("/api/rooms/{room_id}/access/outbox", tags=["access"])
def list_access_outbox(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    access: AccessGate = AccessDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The delivery seam, so the verification link is reachable in a local
    install that has no mail server."""
    _require_room(store, room_id)
    messages = access.outbox(room_id, limit=limit)
    return {"room_id": room_id, "count": len(messages), "messages": messages}


def _require_room(store: RecordStore, room_id: str) -> None:
    """404 unless the id names a live record in the `room` collection."""
    record = store.get(room_id)
    if record is None or record["collection"] != "room":
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")


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
