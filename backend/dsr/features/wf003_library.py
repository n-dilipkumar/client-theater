"""WF-003: populate and govern the room's document library.

Ported from ``feature/WF-003-populate-and-govern-the-room-s-document-library``
onto the plugin host. The rules live in :mod:`dsr.documents` and
:mod:`dsr.permissions`, which the port took over unchanged; this module is the
three things that used to be edits to shared files:

* the route table, which was appended to the single ``app`` in ``dsr/api.py``
  and is now an ``APIRouter`` this feature owns;
* the error mapping, which was five ``@app.exception_handler`` blocks in
  ``dsr/api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host attaches;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

What that buys is the property the host exists for. On the original branch this
workflow could not be merged without resolving a conflict against the other
eleven workflows, because all twelve appended to the same three files.

Two deliberate departures from the branch, both required by the contract:

* the prefix is ``/api/wf-003``, not the branch's ``/api/rooms/...`` and
  ``/api/document-workflow``. The branch's paths are still reachable in shape
  under the prefix; only the namespace changed, and the original branch was
  never merged, so nothing external depended on the old paths;
* every write is handed the path this router actually serves. The branch
  hard-coded ``source="POST /api/rooms/{room_id}/documents"`` inside the domain
  functions, which is the defect the port brief calls out: a feature's audit log
  ended up naming a route the app had stopped serving. ``source`` is now a
  required argument on the write methods and it is built from ``router.prefix``
  here, so the two cannot drift.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.documents import (
    FOLDER,
    GALLERY_SLOTS,
    DocumentLibrary,
    DocumentNotFound,
    InvalidDocument,
    InvalidTransition,
    PermissionDenied,
    RoomNotFound,
)
from dsr.permissions import role_vocabulary
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-003-document-library",
    "ticket": "WF-003",
    "name": "Populate and govern the room's document library",
    "description": (
        "The room's documents folder as one canonical library: upload, workflow "
        "status with a transition graph, role gates for every write, and the "
        "four-slot Document Gallery Block."
    ),
    "nav": [{"id": "documents", "label": "Documents"}],
}

router = APIRouter(prefix="/api/wf-003", tags=["wf003"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# The library refuses work with its own error types so the HTTP status is a fact
# about the rule that was broken, not a guess made at the edge. All five are
# this feature's own classes, which is what makes it safe to hand them to the
# host: registering a handler for ``PermissionError`` or ``ValueError`` would let
# one workflow intercept exceptions raised anywhere in the product, but these
# types are raised by nothing else. Two features may not map the same type, and
# the host refuses the second rather than letting load order decide.


def _room_not_found(request: Request, exc: RoomNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "room_not_found", "detail": str(exc)})


def _document_not_found(request: Request, exc: DocumentNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "document_not_found", "detail": str(exc)}
    )


def _permission_denied(request: Request, exc: PermissionDenied) -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": "forbidden", "detail": str(exc)})


def _invalid_document(request: Request, exc: InvalidDocument) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "invalid_document", "detail": str(exc)})


def _invalid_transition(request: Request, exc: InvalidTransition) -> JSONResponse:
    # 409: the document exists, the requested change just is not available.
    return JSONResponse(
        status_code=409, content={"error": "invalid_transition", "detail": str(exc)}
    )


EXCEPTION_HANDLERS = {
    RoomNotFound: _room_not_found,
    DocumentNotFound: _document_not_found,
    PermissionDenied: _permission_denied,
    InvalidDocument: _invalid_document,
    InvalidTransition: _invalid_transition,
}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
#
# Every route here is a thin translation of an HTTP request into a
# ``DocumentLibrary`` call. The rules live in one place so the HTTP surface
# cannot drift from them, and so the schema-flexible ``/api/records`` surface is
# still available for anything this workflow does not model.


def get_library(store: RecordStore = StoreDep) -> DocumentLibrary:
    """The document library, built on the shared audited store.

    This composes the documented dependency seam rather than reaching for
    ``request.app.state`` directly, so the library is derived from the same
    store every other reader uses. A feature never opens the database itself,
    and the audit row is written in the same transaction as the change.
    """
    return DocumentLibrary(store)


LibraryDep = Depends(get_library)


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because the whole
    point of the port brief's hard rule 4 is that a domain function must never
    hard-code a path the app might have stopped serving.
    """
    return f"{verb} {router.prefix}{suffix}"


@router.get("/rooms/{room_id}/documents")
def list_room_documents(
    room_id: str,
    search: str = Query(default="", description="Free-text match on name, title, description"),
    status: str | None = Query(default=None, description="Workflow status filter"),
    include_expired: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None, description="Room role of the caller"),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """The Documents view for a room: the files in its documents folder."""
    return library.list_documents(
        room_id,
        search=search,
        status=status,
        include_expired=include_expired,
        limit=limit,
        offset=offset,
        role=role,
        actor=actor,
    )


@router.post("/rooms/{room_id}/documents", status_code=201)
def create_room_document(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Add a file to the room's documents folder (the *New* button).

    403 when the caller's role cannot upload. The UI hides the button in that
    case, but the API refuses it too: a hidden control is not access control.
    """
    return library.add_document(
        room_id,
        payload,
        actor=actor,
        role=role,
        source=_source("POST", f"/rooms/{room_id}/documents"),
    )


@router.get("/rooms/{room_id}/documents/{document_id}")
def get_room_document(
    room_id: str,
    document_id: str,
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """One row of the Documents view."""
    return library.get_document(room_id, document_id, actor=actor, role=role)


@router.patch("/rooms/{room_id}/documents/{document_id}")
def update_room_document(
    room_id: str,
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Merge a partial payload. Fields the library does not own are accepted."""
    return library.update_document(
        room_id,
        document_id,
        payload,
        actor=actor,
        role=role,
        source=_source("PATCH", f"/rooms/{room_id}/documents/{document_id}"),
    )


@router.put("/rooms/{room_id}/documents/{document_id}/status")
def set_room_document_status(
    room_id: str,
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Move a document's workflow status. 409 on a disallowed transition."""
    return library.set_status(
        room_id,
        document_id,
        str(payload.get("status") or ""),
        actor=actor,
        role=role,
        source=_source("PUT", f"/rooms/{room_id}/documents/{document_id}/status"),
    )


@router.delete("/rooms/{room_id}/documents/{document_id}")
def delete_room_document(
    room_id: str,
    document_id: str,
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Remove a document. Soft delete, so the audit trail keeps the history."""
    return library.remove_document(
        room_id,
        document_id,
        actor=actor,
        role=role,
        source=_source("DELETE", f"/rooms/{room_id}/documents/{document_id}"),
    )


@router.get("/rooms/{room_id}/document-gallery")
def list_gallery_blocks(
    room_id: str,
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Document Gallery Blocks placed on the room's pages."""
    return library.list_galleries(room_id, actor=actor, role=role)


@router.post("/rooms/{room_id}/document-gallery", status_code=201)
@router.put("/rooms/{room_id}/document-gallery/{block_id}")
def save_gallery_block(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    block_id: str | None = None,
    actor: str | None = Query(default=None),
    role: str | None = Query(default=None),
    library: DocumentLibrary = LibraryDep,
) -> dict[str, Any]:
    """Create or replace a block's Document 1-4 selectors.

    A block holds at most four documents, and each one must already be in this
    room's documents folder.
    """
    # One route answers both verbs, so it also names the path it is serving:
    # a replaced block must not be audited as though it had just been created.
    suffix = (
        f"/rooms/{room_id}/document-gallery/{block_id}"
        if block_id
        else f"/rooms/{room_id}/document-gallery"
    )
    return library.save_gallery(
        room_id,
        payload,
        block_id=block_id,
        actor=actor,
        role=role,
        source=_source("PUT" if block_id else "POST", suffix),
    )


@router.get("/document-workflow")
def document_workflow(library: DocumentLibrary = LibraryDep) -> dict[str, Any]:
    """The status vocabulary, transitions, and role list, for clients to render."""
    vocabulary = library.workflow_vocabulary()
    vocabulary["roles"] = role_vocabulary()
    vocabulary["gallery_slots"] = GALLERY_SLOTS
    vocabulary["folder"] = FOLDER
    return vocabulary


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: One status per seeded document, so the Documents view shows the status
#: column with more than one value. ``draft`` and ``published`` are the two
#: states the primary sources document; ``in_review`` is a design inference.
DOCUMENT_STATUSES = ["published", "draft", "in_review", "published", "draft", "in_review"]

FILE_NAME_SUFFIX = {"deck": "pptx", "video": "mp4", "sheet": "xlsx"}


def seed(db, context: dict[str, Any]) -> str:
    """Make the core demo dataset readable as a document library.

    The branch did this by editing ``backend/seed.py``, which is a shared file:
    ten of the first twelve workflows rewrote it purely to add their own rows.
    The seeder calls this hook instead, so the demo data travels with the
    feature that needs it.

    Two things are fixed up rather than created, because the core dataset is not
    this feature's to rewrite:

    * the closed room is marked ``archived``, so the archived-room read-only
      behaviour is visible in the UI without archiving one by hand. Which room
      that is is read from each room's own ``stage``, not from its position in
      the core list;
    * the core ``document`` rows are given the library's own fields - folder,
      file name, format, workflow status, uploader, timestamps. They are already
      documents, so adding rows would have shown the same files twice.

    A gallery block is then placed on the first room, because the buyer-facing
    Document Gallery Block is half of this workflow and an empty section is not
    something a reviewer can check.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    now = context["now"]

    archived = 0
    for room_id, _account in room_ids:
        room = db.get(room_id)
        if room is None:
            continue
        data = room.get("data", {})
        # Only ``archived`` is meaningful to the gates; any other status is
        # treated as active, so an unrelated core field cannot lock the room.
        if str(data.get("status") or "").lower() == "archived":
            continue
        if str(data.get("stage") or "").lower() != "closed":
            continue
        db.update(room_id, {"status": "archived"}, actor="dana", source="seed")
        archived += 1

    documents = db.list("document", limit=200, order_by="created_at", descending=False)
    filled = 0
    for index, record in enumerate(documents):
        data = record.get("data", {})
        title = str(data.get("title") or data.get("name") or record["id"])
        kind = str(data.get("format") or data.get("kind") or "pdf").lower()
        room_id = record.get("room_id")
        account = next((a for r, a in room_ids if r == room_id), "")
        uploader = "dana" if index % 2 == 0 else "sam"
        modified_at = (now - timedelta(days=index * 3, hours=index)).isoformat(
            timespec="milliseconds"
        )
        db.update(
            record["id"],
            {
                "folder": FOLDER,
                "name": data.get("name") or f"{title}.{FILE_NAME_SUFFIX.get(kind, kind or 'pdf')}",
                "description": data.get("description") or f"{title} shared with {account}.",
                "format": kind,
                "status": DOCUMENT_STATUSES[index % len(DOCUMENT_STATUSES)],
                "uploaded_by": data.get("uploaded_by") or uploader,
                "uploaded_at": data.get("uploaded_at") or modified_at,
                "last_modified_by": data.get("last_modified_by")
                or ("sam" if index % 3 == 0 else "dana"),
                "last_modified_at": data.get("last_modified_at") or modified_at,
                # One document is left without a thumbnail on purpose: rendering
                # is asynchronous upstream, so "not rendered yet" is a real state
                # the view has to render honestly rather than a missing value.
                "thumbnail_url": data.get("thumbnail_url")
                or (f"https://cdn.example.com/thumbs/{record['id']}.png" if index % 3 else ""),
            },
            actor=uploader,
            source="seed",
        )
        filled += 1

    blocks = 0
    if room_ids:
        room_id = room_ids[0][0]
        picked = [r["id"] for r in documents if r.get("room_id") == room_id][:GALLERY_SLOTS]
        if picked and not db.list("document_gallery", room_id=room_id, limit=1):
            db.create(
                "document_gallery",
                {
                    "label": "Document Gallery Block",
                    "page": "overview",
                    "documents": picked,
                    "open_in_new_tab": True,
                },
                room_id=room_id,
                actor="dana",
                source="seed",
            )
            blocks += 1

    return (
        f"{filled} documents given library metadata, {archived} room(s) archived, "
        f"{blocks} gallery block"
    )
