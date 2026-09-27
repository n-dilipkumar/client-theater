"""WF-010: search the content library to assemble a room.

Ported from ``feature/WF-010-search-the-content-library-to-assemble-a-room``
onto the plugin host. The query contract, the matcher, the cursor codec and the
assembler live in :mod:`dsr.search`, which the port took over unchanged apart
from the three shared-file adaptations documented there and below. This module is
the four things that used to be edits to shared files:

* the route table, which the branch appended to the single ``app`` in
  ``dsr/api.py`` and which is now an ``APIRouter`` this feature owns;
* the error mapping, which was an ``@app.exception_handler`` for
  :class:`~dsr.search.SearchError` in ``dsr/api.py`` and is now an
  ``EXCEPTION_HANDLERS`` export the host attaches;
* the wiring, which the branch did in ``lifespan`` by assigning four services
  onto ``app.state`` and which is now three dependencies built from ``StoreDep``,
  so a feature needs no startup hook in a shared file;
* the demo rows, which were an edit to ``backend/seed.py`` and are now a
  ``seed(db, context)`` hook the seeder calls.

What that buys is the property the host exists for. On the original branch this
workflow could not be merged without resolving a conflict against the other
eleven, because all twelve appended to the same three files.

Three deliberate departures from the branch, each required by the contract:

* **The prefix stays ``/api/library``.** This workflow was originally held back
  believing it collided with WF-007 on ``/api/library``. It does not. The
  concrete paths here are ``/contract``, ``/fields``, ``/search``, ``/assemble``
  and ``/searches``; WF-007 owns ``/rooms/{room_id}/documents`` and
  ``/documents/{document_id}``. Zero concrete paths overlap, which is exactly the
  case a prefix-sharing host is built to allow, so the namespace is unchanged from
  the branch and nothing a client would have called has moved.
* **Every write is handed the path this router actually serves.** The branch
  hard-coded ``source="POST /api/library/assemble"`` inside
  ``LibraryAssembler.assemble`` and built ``DELETE /api/library/searches/{id}``
  inside ``SavedSearches.delete``. That is the defect the port brief calls out -
  a feature's audit log kept naming a path the app had stopped serving. ``source``
  is now a required argument on every write and is built from ``router.prefix``
  here, so the two cannot drift.
* **The two shared-file read helpers moved into the domain.** The branch added
  ``RecordStore.scan``/``count``/``audit_count`` and a ``context=`` keyword on
  ``bulk_create``. ``dsr/store.py`` and ``dsr/db/audited.py`` are shared, so the
  port reconstructs the reads in :mod:`dsr.search.reads` and drops the keyword.
  Both are raised in this port's report.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.search import (
    CursorCodec,
    LibraryAssembler,
    LibrarySchema,
    LibrarySearch,
    SavedSearches,
    SearchError,
    SearchQuery,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-010-library-search",
    "ticket": "WF-010",
    "name": "Search the content library to assemble a room",
    "description": (
        "Search the content library with a real query - term, fields, filters, "
        "sort, paged - then attach the chosen documents to a room in one audited "
        "write."
    ),
    "nav": [{"id": "library", "label": "Library search"}],
}

router = APIRouter(prefix="/api/library", tags=["wf010"])


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
#
# The store declares no schema, so this mapping is how a deployment says where its
# content fields live. Anything not listed resolves to itself, which is what keeps
# a team that adds ``data.region`` able to filter and sort on ``region`` the same
# day, with no migration. Overridable from the environment so one deployment can
# point the same code at a different shape.
#
# The branch held these as private helpers in ``dsr/api.py``. They belong to this
# workflow, so they are here.


def _library_fields() -> dict[str, str]:
    """Logical content field name -> dotted JSON path in ``data``."""
    default = {
        # The store's library records call their title `title` and their format
        # `kind`. Everything else keeps the name the search contract uses.
        "name": "title",
        "format": "kind",
    }
    override = os.environ.get("DSR_LIBRARY_FIELDS")
    if override:
        default.update(json.loads(override))
    return default


def _library_collection() -> str:
    return os.environ.get("DSR_LIBRARY_COLLECTION", "document")


def _room_content_collection() -> str:
    return os.environ.get("DSR_ROOM_CONTENT_COLLECTION", "room_content")


_PROCESS_SECRET: bytes | None = None
"""Fallback signing key, generated once per process.

Caching it matters and the distinction is deliberate. A paging token is only
meaningful to the process that issued it, so the default is a random per-process
key: tokens do not survive a restart, which is the honest default rather than a
silent promise of a stable page. But the key must be *the same* on every request
within that process or paging a result set would never work at all - so it is
generated once and reused, not drawn per request.

A configured key is returned as-is and is not cached, so rotating the environment
variable invalidates outstanding tokens immediately. That is the property the
expiry test depends on, and it is also what a real rotation needs.
"""


def _search_token_secret() -> bytes:
    global _PROCESS_SECRET
    configured = os.environ.get("DSR_SEARCH_TOKEN_SECRET")
    if configured:
        return configured.encode("utf-8")
    if _PROCESS_SECRET is None:
        _PROCESS_SECRET = base64.urlsafe_b64encode(os.urandom(32))
    return _PROCESS_SECRET


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _invalid_search(request: Request, exc: SearchError) -> JSONResponse:
    """A query the caller must fix.

    The message is the contract's own wording - term length, page size, filter
    depth, expired cursor - because a caller that hits one of these limits needs to
    know which limit it hit, not that something was wrong.
    """
    return JSONResponse(status_code=400, content={"error": "invalid_search", "detail": str(exc)})


#: ``SearchError`` is this feature's own class, raised by nothing else in the
#: product, so registering a handler for it cannot intercept anyone else's
#: exceptions. Two features may not map the same type and the host refuses the
#: second rather than letting load order decide.
EXCEPTION_HANDLERS = {SearchError: _invalid_search}


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
#
# The branch built these in the app's ``lifespan`` and stashed them on
# ``app.state``. A feature cannot edit that function, and it does not need to: the
# services are cheap value objects, and building them per request from the store
# the ``StoreDep`` seam provides is both simpler and more honest - the
# configuration is read when the request is served rather than frozen at startup,
# so a rotated token secret or a changed field map takes effect without a restart.


def _schema() -> LibrarySchema:
    return LibrarySchema(fields=_library_fields())


def get_library_search(store: RecordStore = StoreDep) -> LibrarySearch:
    """The search service, on the shared audited store."""
    return LibrarySearch(
        store,
        schema=_schema(),
        codec=CursorCodec(
            _search_token_secret(),
            ttl_seconds=int(os.environ.get("DSR_SEARCH_TOKEN_TTL", "900")),
        ),
        collection=_library_collection(),
    )


def get_assembler(store: RecordStore = StoreDep) -> LibraryAssembler:
    """The room-assembly service, on the shared audited store."""
    return LibraryAssembler(store, content_collection=_room_content_collection())


def get_saved_searches(store: RecordStore = StoreDep) -> SavedSearches:
    """Saved searches, on the shared audited store."""
    return SavedSearches(store, schema=_schema())


LibraryDep = Depends(get_library_search)
AssemblerDep = Depends(get_assembler)
SavedSearchesDep = Depends(get_saved_searches)


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because the point of
    the port brief's hard rule 4 is that a domain function must never hard-code a
    path the app might have stopped serving.
    """
    return f"{verb} {router.prefix}{suffix}"


def _client_details(raw: str | None) -> dict[str, Any] | None:
    """Decode the caller-attribution header.

    Modelled on the researched ``X-Seismic-Client-Details`` header, which exists
    so "the search activity data can be available to customers for reporting and
    insight analytics purposes": base64 of a small JSON object naming the calling
    application. Attribution is echoed back on the response and recorded on the
    assembly, so a room's contents can be traced to the app that put them there.

    A malformed header is an error rather than a shrug. Silently dropping
    attribution would mean the analytics are quietly wrong, which is worse than a
    rejected request.
    """
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        parsed = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail='X-Client-Details must be base64 of a JSON object, e.g. base64 of {"application":"my-app"}',
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="X-Client-Details must decode to a JSON object")
    return parsed


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


@router.get("/contract")
def library_contract(library: LibrarySearch = LibraryDep) -> dict[str, Any]:
    """The limits, field vocabulary and operators a client must build a valid query.

    Served rather than hard-coded in the UI so there is one source of truth for
    the term length, the page-size range, the filter depth and the token lifetime.
    """
    return library.contract()


@router.get("/fields")
def library_fields(library: LibrarySearch = LibraryDep) -> dict[str, Any]:
    """Which fields actually carry a value in the library right now.

    Discovery, like the schema explorer: a team that stored ``properties.Region``
    yesterday can filter on it today without changing any code.
    """
    fields = library.fields_in_use()
    return {"count": len(fields), "fields": fields, "collection": library.collection}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@router.post("/search")
def library_search(
    payload: dict[str, Any] = Body(default_factory=dict),
    continuation_token: str | None = Query(
        default=None,
        alias="continuationToken",
        description="Token from a previous page's continuationToken",
    ),
    x_client_details: str | None = Header(default=None, alias="X-Client-Details"),
    library: LibrarySearch = LibraryDep,
) -> dict[str, Any]:
    """Search the content library.

    Send a query body with ``term``, ``options.searchFields``,
    ``options.returnFields``, ``options.pageSize``,
    ``options.enableSuggestedQueryResults``, ``filter`` and ``sort``. An empty
    body queries all content. Page with ``?continuationToken=``; the token is bound
    to the query that issued it and expires, so a stale one returns 400 rather
    than a page of the wrong documents.

    Deployment note: front this route with whatever your platform uses for
    authorization. The researched operation requires a token carrying the search
    scope; this open-source app runs unauthenticated and does not fake a check it
    cannot perform.
    """
    query = SearchQuery.parse(payload, library.schema)
    return library.run(
        query,
        continuation_token=continuation_token or payload.get("continuationToken"),
        client_details=_client_details(x_client_details),
    )


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


@router.post("/assemble", status_code=201)
def library_assemble(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    x_client_details: str | None = Header(default=None, alias="X-Client-Details"),
    assembler: LibraryAssembler = AssemblerDep,
) -> dict[str, Any]:
    """Attach library documents to a room.

    One transaction, one audit row, whichever way it goes: a half-assembled room
    is worse than a rejected one, because the seller cannot tell from the room
    which documents actually made it in. Documents already in the room are
    reported as skipped rather than duplicated.
    """
    room_id = str(payload.get("room_id") or "").strip()
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items must be a non-empty list of library documents")

    try:
        return assembler.assemble(
            room_id,
            items,
            source=_source("POST", "/assemble"),
            actor=actor,
            request_id=request_id,
            search_id=payload.get("search_id"),
            client_details=_client_details(x_client_details),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from exc


# --------------------------------------------------------------------------- #
# Saved searches
# --------------------------------------------------------------------------- #


@router.get("/searches")
def list_saved_searches(
    limit: int = Query(default=100, ge=1, le=1000),
    saved: SavedSearches = SavedSearchesDep,
) -> dict[str, Any]:
    """Saved searches, most recently updated first."""
    records = saved.list(limit=limit)
    return {"collection": saved.collection, "count": len(records), "records": records}


@router.post("/searches", status_code=201)
def create_saved_search(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    x_client_details: str | None = Header(default=None, alias="X-Client-Details"),
    saved: SavedSearches = SavedSearchesDep,
) -> dict[str, Any]:
    """Save a named query so a team's searching is reportable.

    The query is validated on the way in, so a saved search is never something
    that fails when someone re-runs it. This is one audited write; searching
    itself is a read and leaves nothing in the trail.
    """
    try:
        return saved.save(
            str(payload.get("name") or ""),
            payload.get("query") or {},
            source=_source("POST", "/searches"),
            actor=actor,
            client_details=_client_details(x_client_details),
        )
    except ValueError as exc:
        # SearchError is a ValueError, so the registered handler would answer 400
        # for the query itself; this is the route's own validation for a missing
        # name, and both are 400 either way.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/searches/{record_id}")
def get_saved_search(record_id: str, saved: SavedSearches = SavedSearchesDep) -> dict[str, Any]:
    try:
        return saved.get(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"saved search {record_id} not found") from exc


@router.delete("/searches/{record_id}")
def delete_saved_search(
    record_id: str,
    actor: str | None = Query(default=None),
    saved: SavedSearches = SavedSearchesDep,
) -> dict[str, Any]:
    try:
        saved.get(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"saved search {record_id} not found") from exc
    return saved.delete(record_id, source=_source("DELETE", f"/searches/{record_id}"), actor=actor)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

REGIONS = ["APAC", "EMEA", "AMER"]
OWNERS = ["dana", "sam"]

#: The text the search actually reads, per title. The core dataset ships a title,
#: a kind and a page count; without a summary and a body there is nothing to
#: match a term against, so the search page would open empty. This is the data
#: the branch added to ``backend/seed.py``, moved here.
SEARCHABLE_TEXT: dict[str, dict[str, str]] = {
    "Enterprise Overview Deck": {
        "profile": "deck",
        "description": "Company overview for enterprise buyers, with the platform architecture.",
        "body": (
            "Covers the platform, the teams, and the security model in depth. Includes the "
            "implementation timeline and the reference architecture an IT team asks for first."
        ),
    },
    "Security & Compliance Pack": {
        "profile": "pack",
        "description": "SOC 2 Type II, ISO 27001 and the most recent penetration test summary.",
        "body": (
            "Every security question a buyer's review team asks, answered: encryption at rest "
            "and in transit, single sign-on, data residency, and incident response commitments."
        ),
    },
    "Pricing One-Pager": {
        "profile": "onepager",
        "description": "What it costs, with no asterisks and no 'contact sales' on the last line.",
        "body": (
            "Per-seat pricing with an enterprise band, annual and monthly terms, and the "
            "implementation fee broken down line by line."
        ),
    },
    "Implementation Roadmap": {
        "profile": "guide",
        "description": "A twelve-week plan from kickoff to first buyer-facing room.",
        "body": (
            "Week one is discovery, weeks two to six are configuration, weeks seven to twelve "
            "are content migration and enablement. Named owner for every phase."
        ),
    },
    "Customer Reference — Northwind": {
        "profile": "reference",
        "description": "A recorded walkthrough of a live enterprise room, with the buyer's own commentary.",
        "body": (
            "Transcribed: the Northwind evaluation team walk through their security room and "
            "explain why they cut the review from six weeks to two."
        ),
    },
    "API Integration Guide": {
        "profile": "guide",
        "description": "REST and webhook reference for connecting a CRM to a room.",
        "body": (
            "Authentication, rate limits, and every endpoint. Webhook retries are exponential "
            "with a signed payload so a CRM can trust what it receives."
        ),
    },
    "Contract Draft": {
        "profile": "contract",
        "description": "Standard terms with the procurement redlines already accepted.",
        "body": (
            "Not for the buyer's legal review. Contains the liability cap and the renewal "
            "mechanics that procurement usually negotiates."
        ),
    },
    "Mutual Action Plan": {
        "profile": "plan",
        "description": "The shared plan for this deal: who does what, by when.",
        "body": (
            "Lists the security review, the pilot scope, the success criteria, and the named "
            "owner on both sides for each milestone."
        ),
    },
}

#: Days before "now" each document was published, so `publishDate` is a usable
#: range filter and a sort rather than eight identical dates.
PUBLISHED_DAYS_AGO: dict[str, int] = {
    "Enterprise Overview Deck": 240,
    "Security & Compliance Pack": 178,
    "Pricing One-Pager": 129,
    "Implementation Roadmap": 196,
    "Customer Reference — Northwind": 88,
    "API Integration Guide": 227,
    "Contract Draft": 31,
    "Mutual Action Plan": 12,
}


def seed(db, context: dict[str, Any]) -> str:
    """Give the core demo documents the fields the content search reads.

    The branch did this by editing ``backend/seed.py``, which is a shared file:
    ten of the first twelve workflows rewrote it purely to add their own rows. The
    seeder calls this hook instead, so the demo data travels with the feature that
    needs it.

    Two things happen, both on the existing core ``document`` rows rather than on
    new ones. The core dataset already is the content library - creating a second
    set would show the same eight files twice - so the searchable fields are added
    to those. And a couple of saved searches are created, so the saved-search
    panel is not an empty section a reviewer has to imagine the contents of.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    now = context["now"]
    rng = context["rng"]

    documents = db.list("document", limit=200, order_by="created_at", descending=False)
    enriched = 0
    for index, record in enumerate(documents):
        data = record.get("data", {})
        title = str(data.get("title") or data.get("name") or record["id"])
        extra = SEARCHABLE_TEXT.get(title)
        if extra is None:
            continue
        age = PUBLISHED_DAYS_AGO.get(title, 30 * (index + 1))
        db.update(
            record["id"],
            {
                **extra,
                "publishDate": (now - timedelta(days=age)).date().isoformat(),
                "versionId": f"v{rng.randint(1, 4)}.{index}",
                "majorVersion": rng.randint(1, 4),
                "minorVersion": rng.randint(0, 9),
                "applicationUrls": {
                    "Workspace Share Link": f"https://rooms.example/share/{record['id'][:8]}",
                },
                "thumbnailUrl": f"https://cdn.example/thumbs/{record['id'][:8]}.png?token=demo",
                # Nested custom properties, because `custom.Region` filterable by
                # name is half the extension contract and a flat field would not
                # show it.
                "properties": {
                    "Region": REGIONS[index % len(REGIONS)],
                    "Owner": OWNERS[index % len(OWNERS)],
                    "Renewal Year": 2027,
                },
            },
            actor="dana",
            source="seed",
        )
        enriched += 1

    saved = 0
    if not db.list("library_search", limit=1):
        for name, query, actor in (
            (
                "Security material for a review",
                {
                    "term": "security",
                    "options": {
                        "searchFields": ["name", "description", "body", "properties"],
                        "returnFields": ["id", "name", "format", "publishDate", "downloadUrl"],
                        "pageSize": 20,
                    },
                },
                "dana",
            ),
            (
                "Short decks, APAC, recent",
                {
                    "options": {"returnFields": ["id", "name", "format", "pages"], "pageSize": 10},
                    "filter": {
                        "and": [
                            {"field": "custom.Region", "value": "APAC"},
                            {"field": "pages", "operator": "lessThanOrEqual", "value": 24},
                        ]
                    },
                    "sort": [{"field": "publishDate", "direction": "desc"}],
                },
                "sam",
            ),
        ):
            db.create(
                "library_search",
                {"name": name, "query": query, "client_application": "seed"},
                actor=actor,
                source="seed",
            )
            saved += 1

    return f"{enriched} library documents made searchable, {saved} saved searches"
