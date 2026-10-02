"""WF-008: auto-sync an external cloud file into the content library.

Ported from ``feature/WF-008-auto-sync-an-external-cloud-file-into-the`` onto
the plugin host. The rules live in :mod:`dsr.external_library`, which the port
took over unchanged apart from one defect; this module is the three things that
used to be edits to shared files:

* the route table, which was appended to the single ``app`` in ``dsr/api.py``
  and is now an ``APIRouter`` this feature owns;
* the error mapping, which was one ``@app.exception_handler`` block inside
  ``dsr/library_api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host
  attaches;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

What that buys is the property the host exists for. On the original branch this
workflow could not be merged without resolving a conflict against the other
eleven workflows, because all twelve appended to the same three files.

Four deliberate departures from the branch, each required by the contract or by
a measured collision:

* **The audit ``source`` is threaded, not hard-coded.** The branch defaulted
  ``source_of_record="POST /api/library/external"`` in the service constructor
  and wrote ``source="POST /api/library/external/resync"`` literally inside
  ``resync``. That is the defect the port brief calls out by name: an audit row
  naming a path the app might have stopped serving is worse than none, because
  it looks authoritative. Both write methods now require a ``source``, and
  :func:`_source` builds it from ``router.prefix`` so the two cannot drift.
* **The service is built from ``StoreDep`` rather than from app state.** The
  branch added ``app.state.library = default_library(app.state.store)`` to the
  lifespan in ``dsr/api.py``, which is a shared-file edit. The dependency here
  derives the service from the store the host already injects, so nothing in
  ``api.py`` learns this feature exists.
* **The prefix is ``/api/library``, as the brief for this workflow measured.**
  Two features sit under it today. Sharing a prefix is safe only when the
  concrete paths are disjoint, which was measured rather than assumed: this
  feature owns ``/connections``, ``/external``, ``/external/resync``,
  ``/external/{content_id}``, ``/external/{content_id}/sync-status``,
  ``/folders`` and ``/sources``; WF-007 owns ``/vocabulary``,
  ``/rooms/{room_id}/documents``, ``/rooms/{room_id}/folders``,
  ``/rooms/{room_id}/usage`` and ``/documents/{document_id}`` with its
  ``/content`` and ``/thumbnail`` children. That is 20 ``(method, path)`` pairs
  under the prefix and no duplicates, and the host refuses a real collision at
  mount time, so the guarantee does not rest on this paragraph.
* **The domain package is ``dsr/external_library/``, not ``dsr/library/``.** The
  branch's package was ``dsr/library/``, which is now a module on main:
  WF-007's ``ContentLibrary``. Python resolves a package before a module of the
  same name on the same path, so the directory silently hid
  ``dsr.library.ContentLibrary`` and WF-007's feature failed to import with no
  error pointing here. The name is this feature's to choose and the collision is
  not something to resolve by editing the other feature, so the package moved.

The domain package stays self-contained. A cross-feature import would recreate
exactly the coupling the host removes, so if WF-007 and this workflow should
share a library domain, that is a decision about the product's shape and belongs
to a human, not to a worktree.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.external_library.errors import ExternalSyncError
from dsr.external_library.sources import default_registry
from dsr.external_library.sync import ExternalLibrarySync
from dsr.store import RecordStore, parse_where

FEATURE = {
    "id": "wf-008-external-sync",
    "ticket": "WF-008",
    "name": "Auto-sync an external cloud file into the content library",
    "description": (
        "Add a file that lives in someone else's cloud to a room's library as a "
        "link rather than a copy, with autoSync deciding whether that link "
        "follows the source or is a one-time snapshot, and a re-sync pass that "
        "applies what the source has moved on to."
    ),
}

router = APIRouter(prefix="/api/library", tags=["wf008"])


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, so the audit row and the
    route table cannot drift apart. A domain function that hard-codes its own
    path is the defect this fixes: the audit log would keep naming a route the
    app had stopped serving, which is worse than no audit row because it looks
    authoritative.
    """
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #

#: One service per store, rebuilt if the store itself changes.
#:
#: It is cached rather than rebuilt per request for a reason that is a rule, not
#: an optimisation: the add operation is documented as rate-limited to one
#: request per second per token, and that limit is a property of the process,
#: not of a request. A service built fresh on every call would hand every
#: request a full allowance and the documented limit would not exist at all.
#:
#: Keying on the store rather than caching one forever also keeps tests honest:
#: a new ``TestClient`` builds a new store, so a suite that opens several never
#: reaches through a service bound to a database that has been closed.
_SERVICE: ExternalLibrarySync | None = None


def get_library(store: RecordStore = StoreDep) -> ExternalLibrarySync:
    """The external library service, derived from the shared audited store.

    Composes the documented dependency seam rather than reading
    ``request.app.state``, so this feature imports nothing from ``dsr.api``.

    It is built lazily on the first request rather than in a lifespan hook,
    which is also why a real source adapter registered at import time is picked
    up: by the time a request arrives, every feature module has been imported.
    """
    global _SERVICE
    if _SERVICE is None or _SERVICE.store is not store:
        # The adapter registry is passed in rather than reached for globally, so
        # a deployment swaps the simulated drive for a real one here and nowhere
        # else.
        _SERVICE = ExternalLibrarySync(store, adapters=default_registry())
    return _SERVICE


LibraryDep = Depends(get_library)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# ``ExternalSyncError`` is raised by nothing else in the product, which is what
# makes it safe to hand to the host: a handler for ``ValueError`` or
# ``PermissionError`` would let this feature intercept exceptions raised anywhere
# in the app. Two features may not map the same type, and the host refuses the
# second rather than letting load order decide.


def _external_sync_error(request: Request, exc: ExternalSyncError) -> JSONResponse:
    """Render the documented error payload.

    The research is explicit that *every* error carries a remediation and a
    correlation id, so both are on the wire rather than in a log line the caller
    never sees.

    ``detail`` is the one awkward part. The shared ``apiRequest`` in
    ``frontend/src/lib/api.js`` keeps only ``body.detail`` (and the status) and
    discards the rest of the body, so a client built on it can only ever show
    ``detail``. Rather than let the remedy and the correlation id be silently
    dropped on the way to the screen, the sentence in ``detail`` carries them.
    The structured fields are still on the wire verbatim for any other client.

    If ``apiRequest`` ever keeps the parsed body, as ``lib/api.js`` would if it
    set ``error.body``, this composition can be dropped and the UI can read the
    fields again. That edit is a shared-file change and so is not made here.
    """
    payload = exc.to_payload()
    payload["detail"] = (
        f"{exc.code}: {exc.detail} {exc.remediation} [correlation id {exc.correlation_id}]"
    )
    return JSONResponse(status_code=exc.status, content=payload)


EXCEPTION_HANDLERS = {ExternalSyncError: _external_sync_error}


# --------------------------------------------------------------------------- #
# Request validation
# --------------------------------------------------------------------------- #
#
# Fields the add operation understands. Anything else the caller wants to keep
# belongs in ``metadata``, so a typo cannot silently become a stored field --
# which matters more than usual here, because the whole project is
# schema-flexible and a misspelled field would otherwise be stored and indexed
# as though it were real.

_ADD_FIELDS = frozenset(
    {
        "externalSource",
        "externalContentId",
        "parentFolderId",
        "autoSync",
        "roomId",
        "title",
        "metadata",
    }
)

_REQUIRED_ADD_FIELDS = ("externalSource", "externalContentId", "roomId")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
#
# Every route here is a thin translation of an HTTP request into an
# ``ExternalLibrarySync`` call. Request bodies use the researched field names
# (``externalSource``, ``externalContentId``, ``parentFolderId``,
# ``autoSync``) so the documented flow can be followed against the API directly.
# What comes back uses this project's snake_case and the fixed envelope, like
# every other route here.


@router.get("/sources", summary="Supported external sources and their connection state")
def list_sources(library: ExternalLibrarySync = LibraryDep) -> dict[str, Any]:
    """Every registered source, and whether a usable connection exists.

    The research says only GoogleDrive is supported "at this time" and that more
    may be added later. This reports the registry rather than hard-coding that
    sentence into the product, which is what makes "may be added" true of the
    code and not only of the documentation.
    """
    sources = library.sources()
    return {"count": len(sources), "sources": sources}


@router.get("/connections", summary="Connection records with their lineage")
def list_connections(library: ExternalLibrarySync = LibraryDep) -> dict[str, Any]:
    """Configured source accounts, with the mapping back to each connection.

    ``externalSystemConnectionName`` and the mapping are the lineage the
    reporting API surfaces for a linked item, so an operator can answer "which
    account did this come from" without leaving the app.
    """
    connections = library.connections()
    return {"count": len(connections), "connections": connections}


@router.post("/external", status_code=201, summary="Add an external file to a room's library")
def add_external_content(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    library: ExternalLibrarySync = LibraryDep,
) -> dict[str, Any]:
    """Add an external cloud file to a room's library.

    Creates a library item **linked to** the external file, not a copy of it.
    ``autoSync`` decides whether the link is living (``true``) or a one-time
    snapshot (``false``, which is also the default when the field is omitted).

    201 with the ``contentId`` to persist on a first add; 200 with
    ``created: false`` when that file is already linked into that folder.
    """
    unexpected = sorted(set(payload) - _ADD_FIELDS)
    if unexpected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unexpected field(s) {', '.join(unexpected)}; "
                "put anything extra inside metadata so it is stored on purpose"
            ),
        )

    missing = [name for name in _REQUIRED_ADD_FIELDS if not str(payload.get(name) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"missing required field(s): {', '.join(missing)}"
        )

    auto_sync = payload.get("autoSync")
    if auto_sync is not None and not isinstance(auto_sync, bool):
        raise HTTPException(status_code=400, detail="autoSync must be true or false when present")

    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")

    result = library.add(
        external_source=str(payload["externalSource"]),
        external_content_id=str(payload["externalContentId"]),
        room_id=str(payload["roomId"]),
        parent_folder_id=str(payload.get("parentFolderId") or "root"),
        auto_sync=auto_sync,
        title=str(payload["title"]) if payload.get("title") else None,
        metadata=metadata,
        actor=actor,
        # The rate limit is per token, and the caller's identity is the actor. A
        # caller who does not say who they are is one anonymous bucket, not
        # exempt.
        token=actor,
        source=_source("POST", "/external"),
    )
    # A re-link is not a creation, so it must not claim 201.
    body = {
        "contentId": result["content_id"],
        "record": result["item"],
        "status": result["status"],
        "created": result["created"],
    }
    return JSONResponse(status_code=201 if result["created"] else 200, content=body)


@router.get("/external", summary="List externally sourced library items")
def list_external_content(
    room_id: str | None = Query(default=None, alias="roomId"),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    library: ExternalLibrarySync = LibraryDep,
) -> dict[str, Any]:
    """Every externally sourced item, with the sync state derived for each.

    ``where`` filters on any JSON path in the payload, so a team that added a
    field can ask for it on the same call without this route knowing it exists.
    """
    try:
        filters = parse_where(where)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "source.kind" in filters and filters["source.kind"] != "external":
        raise HTTPException(status_code=400, detail="source.kind must be 'external' on this route")

    items = library.list_items(room_id=room_id, where=filters, limit=limit)
    return {
        "collection": library.item_collection,
        "count": len(items),
        "items": items,
        "statuses": [library.status_of(item) for item in items],
    }


@router.post("/external/resync", summary="Run the auto-sync pass")
def resync_external_content(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    library: ExternalLibrarySync = LibraryDep,
) -> dict[str, Any]:
    """Re-fetch every auto-synced item and apply what the source has moved on to.

    This is the automation the research describes ("will automatically re-sync
    whenever the linked source file is updated"), made callable so a scheduler
    can drive it and an operator can run it now.

    An item whose source has not moved is not written, so a pass that discovers
    nothing produces no audit rows.
    """
    room_id = payload.get("roomId") or payload.get("room_id")
    return library.resync(
        room_id=str(room_id) if room_id else None,
        actor=actor,
        source=_source("POST", "/external/resync"),
    )


@router.get("/external/{content_id}", summary="One linked item")
def get_external_content(
    content_id: str, library: ExternalLibrarySync = LibraryDep
) -> dict[str, Any]:
    """One linked item, addressed by the ``contentId`` the add returned."""
    item = library.get_item(content_id)
    return {"record": item, "status": library.status_of(item)}


@router.get("/external/{content_id}/sync-status", summary="Sync state for one item")
def get_sync_status(content_id: str, library: ExternalLibrarySync = LibraryDep) -> dict[str, Any]:
    """Sync state for one item.

    Implements the companion operation the research references as
    ``GetContentSyncStatus`` without publishing a reference page for. The shape
    is therefore a design inference, and it is documented as one on
    :meth:`dsr.external_library.sync.ExternalLibrarySync.status_of`.
    """
    return library.sync_status(content_id)


@router.get("/folders", summary="Folders a caller may target with parentFolderId")
def list_folders(
    room_id: str | None = Query(default=None, alias="roomId"),
    library: ExternalLibrarySync = LibraryDep,
) -> dict[str, Any]:
    """Target folders for ``parentFolderId``, with ``root`` offered first.

    The research reserves the keyword ``root`` for a teamsite's top-level library
    folder, and a client picking a target needs it offered rather than typed.
    """
    folders = library.folders(room_id=room_id)
    return {"count": len(folders), "folders": folders}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: Connections a demo starts with. Ordinary schema-flexible records, written
#: through the same audited store as everything else.
CONNECTIONS = [
    {
        "source": "GoogleDrive",
        "name": "Dana's Google Drive",
        "account": "dana@northwind.example",
        "status": "connected",
        "scopes": ["drive.readonly"],
    },
    {
        "source": "GoogleDrive",
        "name": "Sam's Google Drive",
        "account": "sam@contoso.example",
        "status": "connected",
        "scopes": ["drive.readonly"],
    },
]

FOLDERS = [("Q2 Decks", 0), ("Security & Compliance", 1)]

#: (room index, folder index or None for root, file id, auto_sync, applied version)
#:
#: The first entry is deliberately seeded holding an older version than the
#: simulated source reports, which is the state a room is really in between an
#: edit landing upstream and a sync pass running. It means the very first "Run
#: re-sync" in the UI has genuine drift to apply, so the automation is visible
#: rather than theoretical.
LINKED_FILES = [
    (0, 0, "1XK_AinrjCzyylNGaH6oX9MUfY5-G-Y54", True, "rev-6"),
    (0, None, "1Bv2Tn9yQKc0HqZ7mLpR4sWdXaE", False, "rev-3"),
    (1, 1, "1XK_AinrjCzyylNGaH6oX9MUfY5-G-Y54", True, "rev-7"),
]


def seed(db, context: dict[str, Any]) -> str:
    """Give the demo rooms a connected source and some linked files.

    The branch did this by editing ``backend/seed.py``, which is a shared file:
    ten of the first twelve workflows rewrote it purely to add their own rows.
    The seeder calls this hook instead, so the demo data travels with the
    feature that needs it.

    The linkage is created through the real service rather than written by hand,
    so a seeded item is exactly what the workflow would have produced -- same
    source block, same idempotence, same audit shape.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    if len(room_ids) < 2:
        return ""

    for spec in CONNECTIONS:
        db.create("external_connection", spec, actor=spec["account"], source="seed")

    folder_ids = [
        db.create(
            "library_folder",
            {"name": name},
            room_id=room_ids[room_index][0],
            actor="seed",
            source="seed",
        )["id"]
        for name, room_index in FOLDERS
    ]

    library = ExternalLibrarySync(RecordStore(db), adapters=default_registry())
    for index, (room_index, folder_index, file_id, auto_sync, applied) in enumerate(LINKED_FILES):
        added = library.add(
            external_source="GoogleDrive",
            external_content_id=file_id,
            room_id=room_ids[room_index][0],
            parent_folder_id=folder_ids[folder_index] if folder_index is not None else "root",
            auto_sync=auto_sync,
            actor="seed",
            # One token per item: the add operation allows one request per
            # second per token, and a seed run is not a user clicking slowly.
            token=f"seed-{index}",
            source="seed",
        )
        if applied:
            # Roll the recorded version back to the seeded one, as if the file
            # had last been applied at `applied` and has moved on since. Written
            # through the audited store like any other change.
            db.update(
                added["content_id"],
                {"source": {**added["item"]["data"]["source"], "source_version": applied}},
                actor="seed",
                source="seed",
            )

    return (
        f"{len(CONNECTIONS)} connections, {len(folder_ids)} folders, "
        f"{len(LINKED_FILES)} linked files (1 behind its source)"
    )
