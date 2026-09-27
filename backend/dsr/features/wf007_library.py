"""WF-007: ingest a document or deck into the content library.

Ported from ``feature/WF-007-ingest-a-document-or-deck-into-the-content``. The
rules live in :mod:`dsr.library`, which the port took over essentially
unchanged; this module is the three things that used to be edits to shared
files:

* the route table, which the branch registered on the single ``app`` in
  ``dsr/api.py`` by hand and is now an ``APIRouter`` this feature owns;
* the error mapping, which was an ``@app.exception_handler`` block in
  ``dsr/api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host attaches;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

The branch's ``backend/dsr/library_api.py`` is gone rather than kept beside
this module. It was the branch's own router, and the host only mounts what
lives in ``dsr/features/``, so a copy of the routes left behind would be dead
code that looks alive.

The prefix stays ``/api/library`` so the public URL is identical to the
branch's. It is shared with WF-010, which owns ``/api/library/contract``,
``/fields``, ``/search``, ``/assemble`` and ``/searches``; no concrete path
overlaps, and the host allows two features to share a prefix. That was believed
to be a collision and held this workflow back, which is the cost of guessing
instead of measuring: the check is ``(method, path)``, not prefix.

Every write is handed the path this router actually serves. The branch wrote
the source into the domain as literal strings, which is the defect the port
brief calls out -- a feature's audit log ends up naming a route the app has
stopped serving, and the log is the product promise. ``_source`` below builds
the value from ``router.prefix``, and :class:`~dsr.library.ContentLibrary` is
handed the same prefix for the ``thumbnailUrl`` it writes, so the audit trail
and the stored URL cannot drift from the routes.

A dependency, and it is a real one
----------------------------------

The researched ingest is ``multipart/form-data`` with a JSON ``metadata`` part
and a binary ``content`` part, and FastAPI cannot even *define* a route with a
``File`` or ``Form`` parameter unless ``python-multipart`` is installed. This
module does not add that dependency to ``pyproject.toml`` -- that file is
shared and a human adds it once, deliberately -- so the check below turns
FastAPI's terse ``RuntimeError`` into a message that names the fix. Without
it the module fails to import, and the host records the failure at
``/api/features`` and carries on serving everything else, which is the designed
behaviour but is not what this feature wants.
"""

from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from dsr.deps import ROOT, StoreDep
from dsr.library import (
    ContentLibrary,
    LibraryError,
    UploadTooLarge,
    content_root_from_env,
    max_upload_bytes_from_env,
    parse_metadata_part,
)
from dsr.store import RecordStore, parse_where

FEATURE = {
    "id": "wf-007-content-library",
    "ticket": "WF-007",
    "name": "Ingest a document or deck into the content library",
    "description": (
        "Multipart ingest of a binary plus a schema-flexible metadata part: "
        "required name, derived format, the root folder keyword, name "
        "de-collision, rollback on a failed binary, add-a-version rather than "
        "replace-the-binary, and an asynchronous thumbnail."
    ),
    "nav": [{"id": "library", "label": "Content library"}],
}

#: The researched upload ceiling, its chunk size, and the collections this
#: workflow owns. Re-exported from the domain module so a client can discover
#: the vocabulary over HTTP without importing Python.
WORKFLOW = {
    "folder_root_keyword": "root",
    "initial_version": "0.1",
    "thumbnail_statuses": ["pending", "ready", "revoked"],
    "ingest_states": ["pending", "complete", "failed"],
    "document_collection": "document",
    "folder_collection": "documentFolder",
}

router = APIRouter(prefix="/api/library", tags=["wf007"])


# --------------------------------------------------------------------------- #
# A declared dependency
# --------------------------------------------------------------------------- #
#
# The researched ingest is multipart, and FastAPI raises at *route definition*
# time -- that is, at import time -- if the parser is missing. The resulting
# traceback names a pip command but not this feature, and a reader has no way to
# tell which of a hundred features wanted it. Checked explicitly so the failure
# says what is missing and who needs it. See the module docstring.


def _require_multipart() -> None:
    # Both module names, newest first: python-multipart renamed its importable
    # module, and FastAPI's own check accepts either. Importing only the old
    # name emits a PendingDeprecationWarning on a fresh install.
    try:
        import python_multipart  # noqa: F401 - the presence check is the point
    except ModuleNotFoundError:
        try:
            import multipart  # noqa: F401
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "WF-007 (wf-007-content-library) needs the 'python-multipart' package to "
                "serve its multipart ingest routes. It is not declared in "
                "backend/pyproject.toml; add it once, deliberately: "
                '"python-multipart>=0.0.9"'
            ) from exc


_require_multipart()


# --------------------------------------------------------------------------- #
# Dependency
# --------------------------------------------------------------------------- #


def get_library(store: RecordStore = StoreDep) -> ContentLibrary:
    """The content library, built on the shared audited store.

    The branch's router read ``request.app.state.library``, an object the core
    ``lifespan`` had to be edited to construct. That is exactly the coupling
    this host removes: the library is derived here, from the same
    ``StoreDep`` every other reader uses, so the feature needs nothing in
    ``api.py``.

    The content root and the size ceiling are resolved at call time rather than
    import time, for the reason ``dsr.deps.db_path`` documents: a module-level
    constant is captured on first import and every test silently shares one
    blob store.
    """
    return ContentLibrary(
        store,
        content_root_from_env(ROOT / "data" / "content"),
        max_upload_bytes=max_upload_bytes_from_env(),
        # The stored thumbnailUrl points at a route this router serves, so the
        # prefix is the single source of truth for it.
        thumbnail_url_base=f"{router.prefix}/documents",
    )


LibraryDep = Depends(get_library)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because a domain
    function must never hard-code a path the app might have stopped serving.
    """
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# One handler, for the base class. Starlette resolves a handler by walking the
# exception's MRO, so this covers ValidationError, NotFound, Conflict,
# UploadTooLarge and BlobStorageError too, and each keeps the status its own
# class declares.
#
# ``LibraryError`` is defined in ``dsr.library`` and raised by nothing else, so
# mapping it here cannot intercept an exception from another workflow. Handing
# the host ``Exception`` or ``ValueError`` would be the opposite.


def _library_error(request: Request, exc: LibraryError) -> JSONResponse:
    """Library failures carry their own status: 400, 404, 409, 413 or 500."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": str(exc)})


EXCEPTION_HANDLERS = {LibraryError: _library_error}


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/usage")
def usage(room_id: str, library: ContentLibrary = LibraryDep) -> dict[str, Any]:
    """Counts for the library header: documents, folders, bytes, pending work."""
    body = library.usage(room_id)
    body["max_upload_bytes"] = library.max_upload_bytes
    return body


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The fixed vocabulary of this workflow, for a client to render a form.

    Deliberately small. It is the words the code understands, not a schema:
    everything else a document carries is free-form and indexed on arrival.
    """
    return {**WORKFLOW, "max_upload_bytes": max_upload_bytes_from_env()}


# --------------------------------------------------------------------------- #
# Folders
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/folders", status_code=201)
def create_folder(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    library: ContentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Create a library folder. Extra payload keys are stored, not rejected."""
    known = {"name", "parentFolderId"}
    metadata = {k: v for k, v in (payload or {}).items() if k not in known}
    return library.create_folder(
        room_id,
        name=str(payload.get("name") or ""),
        parent_folder_id=str(payload.get("parentFolderId") or "root"),
        actor=actor,
        source=_source("POST", f"/rooms/{room_id}/folders"),
        **metadata,
    )


@router.get("/rooms/{room_id}/folders")
def list_folders(room_id: str, library: ContentLibrary = LibraryDep) -> dict[str, Any]:
    """List the folder hierarchy of a room, with materialized paths."""
    folders = library.list_folders(room_id)
    return {"room_id": room_id, "count": len(folders), "folders": folders}


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/documents", status_code=201)
async def ingest_document(
    room_id: str,
    content: UploadFile = File(..., description="The binary. Rejected if empty or over the limit."),
    metadata: str = Form(..., description="JSON object: name, format, parentFolderId, and any extra fields."),
    resolveNameCollision: bool = Form(default=True),
    rollbackOnError: bool = Form(default=True),
    actor: str | None = Query(default=None),
    request_id: str | None = Query(default=None, alias="requestId"),
    library: ContentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Ingest a document or deck into the room's content library.

    Returns the stored record. The document is a ``Draft`` at version 0.1 with
    an empty ``thumbnailUrl``, because thumbnail rendering lags the upload; poll
    the thumbnail route to pick it up.
    """
    fields = parse_metadata_part(metadata)
    if content.size and content.size > library.max_upload_bytes:
        # Fail before touching the record when the size is already known. The
        # streaming check in the domain is the real guard; this just avoids
        # creating a draft we know we are going to roll back.
        raise UploadTooLarge(
            f"upload is {content.size} bytes, over the {library.max_upload_bytes} byte limit"
        )
    return library.ingest(
        room_id,
        content.file,
        content.filename or "",
        metadata=fields,
        resolve_name_collision=resolveNameCollision,
        rollback_on_error=rollbackOnError,
        actor=actor,
        request_id=request_id,
        source=_source("POST", f"/rooms/{room_id}/documents"),
    )


@router.get("/rooms/{room_id}/documents")
def list_documents(
    room_id: str,
    parentFolderId: str | None = Query(default=None, description="'root' or a folder id"),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2" over arbitrary fields'),
    limit: int = Query(default=100, ge=1, le=1000),
    library: ContentLibrary = LibraryDep,
) -> dict[str, Any]:
    """List a room's documents, filtered through the dynamic index.

    ``where`` resolves dotted JSON paths, so a filter on a field this code has
    never heard of works without a migration.
    """
    try:
        filters = parse_where(where)
    except ValueError as exc:
        raise LibraryError(str(exc)) from exc
    records = library.list_documents(
        room_id, parent_folder_id=parentFolderId, where=filters, limit=limit
    )
    return {"room_id": room_id, "count": len(records), "documents": records}


# --------------------------------------------------------------------------- #
# A single document
# --------------------------------------------------------------------------- #


@router.get("/documents/{document_id}")
def get_document(document_id: str, library: ContentLibrary = LibraryDep) -> dict[str, Any]:
    """File information and properties for one document."""
    return library.get_document(document_id)


@router.put("/documents/{document_id}")
async def add_version(
    document_id: str,
    content: UploadFile = File(..., description="The new binary. The previous version is kept."),
    actor: str | None = Query(default=None),
    request_id: str | None = Query(default=None, alias="requestId"),
    library: ContentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Add a new version of a document.

    Deliberately not a binary replacement: the researched API is explicit that
    overwriting a file's binary is the wrong operation, so this appends and the
    earlier bytes stay retrievable by version id.
    """
    return library.add_version(
        document_id,
        content.file,
        content.filename or "",
        actor=actor,
        request_id=request_id,
        source=_source("PUT", f"/documents/{document_id}"),
    )


@router.get("/documents/{document_id}/content")
def download_document(
    document_id: str,
    versionId: str | None = Query(default=None, description="Defaults to the current version"),
    library: ContentLibrary = LibraryDep,
) -> FileResponse:
    """Stream the stored binary for a document version."""
    path, filename, size = library.read_version(document_id, versionId)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"X-Content-Size": str(size or "")},
    )


@router.get("/documents/{document_id}/thumbnail")
def get_thumbnail(document_id: str, library: ContentLibrary = LibraryDep) -> Response:
    """The derived preview, or 404 while it is still pending.

    Polling this is how a client discovers the thumbnail, mirroring the
    documented behaviour that rendering is asynchronous and may lag the upload.
    A pure read: it never derives, because a read that writes is a mutation the
    audit log would have to record.
    """
    markup = library.read_thumbnail(document_id)
    return Response(
        markup,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-cache", "X-Thumbnail-Status": "ready"},
    )


@router.post("/documents/{document_id}/thumbnail")
def derive_thumbnail(
    document_id: str,
    actor: str | None = Query(default=None),
    library: ContentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Render the preview now and record it on the document."""
    return library.derive_thumbnail(
        document_id,
        actor=actor,
        source=_source("POST", f"/documents/{document_id}/thumbnail"),
    )


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    hard: bool = Query(default=False),
    actor: str | None = Query(default=None),
    library: ContentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Remove a document from the library view. The stored bytes are retained."""
    return library.delete(
        document_id,
        hard=hard,
        actor=actor,
        source=_source("DELETE", f"/documents/{document_id}"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: Ingested through the real ingest path rather than written as records, because
#: the ingest path is the thing worth demonstrating: bytes on disk, audited
#: metadata, a materialized path, and the thumbnail lag the researched API
#: documents. Only some rows get a preview, so "pending" is visible in the demo
#: instead of implied.
LIBRARY_DEMO = [
    {
        "room": 0,
        "folder": "Sales Enablement",
        "name": "Enterprise Overview Deck.pptx",
        "description": "The deck we walk an enterprise buyer through in the first call.",
        "externalId": "ext-12345",
        "properties": {"audience": "enterprise", "region": "global"},
        "thumbnail": True,
    },
    {
        "room": 0,
        "folder": "Sales Enablement",
        "name": "Security & Compliance Pack.pdf",
        "description": "SOC 2, ISO 27001 and the data residency annex.",
        "externalId": "ext-12346",
        "properties": {"audience": "security-review", "region": "emea"},
        "thumbnail": True,
    },
    {
        "room": 1,
        "folder": None,
        "name": "Implementation Roadmap.pdf",
        "description": "Milestones, owners and the integration cutover plan.",
        "properties": {"audience": "operations"},
        "thumbnail": False,
    },
]


def seed(db, context: dict[str, Any]) -> str:
    """Ingest a few documents so the library page is not empty in the demo.

    The branch did this by editing ``backend/seed.py``, which is a shared file:
    ten of the first twelve workflows rewrote it purely to add their own rows.
    The seeder calls this hook instead.

    Re-seeding is a no-op rather than a duplicate: the seeder is documented as
    safe to run twice, and a second copy of every document would make the demo
    page lie about what the product does.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    if not room_ids:
        return "no rooms in the core dataset; nothing to ingest into"

    library = ContentLibrary(
        RecordStore(db),
        content_root_from_env(ROOT / "data" / "content"),
        max_upload_bytes=max_upload_bytes_from_env(),
        thumbnail_url_base=f"{router.prefix}/documents",
    )

    target_room = room_ids[0][0]
    if library.list_documents(target_room, limit=1):
        return "library already holds documents; left the existing rows alone"

    folders: dict[tuple[str, str], str] = {}
    ingested = 0
    derived = 0
    for spec in LIBRARY_DEMO:
        index = min(spec["room"], len(room_ids) - 1)
        room_id = room_ids[index][0]
        parent = "root"
        if spec["folder"]:
            cache_key = (room_id, spec["folder"])
            if cache_key not in folders:
                folder = library.create_folder(
                    room_id, spec["folder"], actor="dana", source="seed"
                )
                folders[cache_key] = folder["id"]
            parent = folders[cache_key]

        name = spec["name"]
        record = library.ingest(
            room_id,
            io.BytesIO(f"seeded demo bytes for {name}\n".encode() * 512),
            name,
            metadata={
                "name": name,
                "parentFolderId": parent,
                "description": spec["description"],
                "externalId": spec.get("externalId"),
                "properties": spec["properties"],
                "language": "en-GB",
                "experts": [{"type": "group", "id": "solutions-engineering"}],
            },
            actor="dana",
            source="seed",
        )
        if spec["thumbnail"]:
            library.derive_thumbnail(
                record["id"], actor="dana", source="seed"
            )
            derived += 1
        ingested += 1

    return (
        f"{ingested} documents ingested through the real path, "
        f"{len(folders)} folder(s), {derived} thumbnail(s) rendered"
    )
