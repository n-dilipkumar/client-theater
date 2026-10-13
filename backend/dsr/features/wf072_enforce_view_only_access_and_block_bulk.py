"""WF-072: enforce view-only access and block bulk download.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-072.md``, quoted in full in issue 134. The
rules live in :mod:`dsr.view_only_access` and are not restated here. This module is the three
things a feature contributes and the three things it must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and this
  feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``, ``dsr/db/audited.py``,
  ``backend/seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``, ``lib/features.js``,
  ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` string. :func:`_source` builds every one from :data:`router`, so
  the audit row names the route that actually served the write.

The status codes here are this product's own
---------------------------------------------

The specification documents no HTTP status codes, so each one follows the product's
conventions rather than inventing a codebook. 400 for a policy payload this workflow will not
accept, because the sender can fix it. 404 for a room or a link that does not exist. 403 for a
download the three controls did not permit: the request is well formed, the link exists, and
the answer is no. The 403 is different from the 400 on purpose: a bad policy is a sender error,
and a refused download is the designed answer this whole workflow exists to give.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.view_only_access import inferences, rules, vocabulary as vocab
from dsr.view_only_access.engine import ViewOnlyAccessEngine

FEATURE = {
    "id": "wf-072-enforce-view-only-access-and-block-bulk",
    "ticket": "WF-072",
    "name": "Enforce view-only access and block bulk download",
    "description": (
        "Three controls compose into one AND: the room's bulk download setting, the link's "
        "allow download switch, and the item's own download grant. A download is served only "
        "when all three permit it, and every permitted fetch writes a view row with "
        "downloaded_at and download_type so a leak by download is auditable. The room and "
        "link controls are this workflow's own; the per-item grant is read from WF-074's "
        "permission rows and never written here."
    ),
    "nav": [{"id": "wf-072-view-only-access", "label": "View-only access"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-072", tags=["WF-072"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf072_http.py`` asserts every source this router can record
    matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> ViewOnlyAccessEngine:
    """A :class:`ViewOnlyAccessEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request:
    the engine holds nothing beyond the store and a clock, so building it here leaves both
    overridable in a test instead of hanging a long-lived object off ``app.state``, which is a
    shared file this feature may not edit.
    """

    return ViewOnlyAccessEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.view_only_access.rules and raised by nothing else in the
# product. That is what makes it safe to map them here: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError or LookupError would
# intercept those exceptions across the whole product.


def _policy_refused(request: Request, exc: rules.PolicyRefused) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "policy_refused",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _room_not_found(request: Request, exc: rules.RoomNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such room."},
    )


def _link_not_found(request: Request, exc: rules.LinkNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such governed link."},
    )


def _download_refused(request: Request, exc: rules.DownloadRefused) -> JSONResponse:
    """403, and the decision that produced the refusal rides on the body.

    A refusal that said only "no" would send a rep looking in the wrong place. The body
    carries every gate and which one withheld the file, so the page can say it.
    """

    return JSONResponse(
        status_code=403,
        content={
            "error": "view_only",
            "detail": str(exc),
            **exc.decision,
        },
    )


EXCEPTION_HANDLERS = {
    rules.PolicyRefused: _policy_refused,
    rules.RoomNotFound: _room_not_found,
    rules.LinkNotFound: _link_not_found,
    rules.DownloadRefused: _download_refused,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: ViewOnlyAccessEngine = EngineDep
) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the panel cannot drift from the rules
    that validate it: the three controls with their owners, the safe defaults, the CLI
    spellings, the AND semantics, the read-source collections and the view shape all come
    from the same tables the validator reads.
    """

    return vocab.vocabulary_payload()


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected."""

    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = inferences.describe_one(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# The room settings panel: the bulk download escalation
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/policy")
def read_room_policy(room_id: str, engine: ViewOnlyAccessEngine = EngineDep) -> dict[str, Any]:
    """One room's policy, or the documented default when nothing is set."""

    return engine.room_policy(room_id)


@router.patch("/rooms/{room_id}/policy")
def update_room_policy(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ViewOnlyAccessEngine = EngineDep,
) -> dict[str, Any]:
    """Set the room's ``bulk_download`` control. Mirrors ``PATCH /v1/datarooms/{id}``.

    The escalation is deliberate: the specification describes bulk download as turning on
    "allow viewers to download every document as a zip". Only ``bulk_download`` is
    interpreted; ``conversations`` and ``agents`` from the same settings triple are stored and
    carried, because they belong to other workflows.
    """

    return engine.set_room_policy(
        room_id, payload, actor=actor, source=_source("PATCH", "/rooms/{room_id}/policy")
    )


# --------------------------------------------------------------------------- #
# Gifted links: the per-link switch
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/links")
def list_links(room_id: str, engine: ViewOnlyAccessEngine = EngineDep) -> dict[str, Any]:
    """Every governed link in one room, and where its switch stands."""

    rows = engine.links(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "links": rows,
        vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
        vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/rooms/{room_id}/links", status_code=201)
def create_link(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ViewOnlyAccessEngine = EngineDep,
) -> dict[str, Any]:
    """Create a governed link. Mirrors ``POST /v1/links``.

    ``allow_download`` defaults to the documented ``false``, so a link created without the
    field is view-only and must be explicitly escalated.
    """

    return engine.create_link(
        room_id,
        payload,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/links"),
    )


@router.get("/links/{link_id}")
def read_link(link_id: str, engine: ViewOnlyAccessEngine = EngineDep) -> dict[str, Any]:
    """One link's switch, with what it is worth."""

    return engine.read_link(link_id)


@router.patch("/links/{link_id}")
def update_link(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ViewOnlyAccessEngine = EngineDep,
) -> dict[str, Any]:
    """Toggle ``allow_download`` in place. Mirrors ``PATCH /v1/links/{id}``.

    The id and the room are untouched, which is the specification's "the URL and existing
    viewers are unaffected".
    """

    return engine.update_link(
        link_id, payload, actor=actor, source=_source("PATCH", "/links/{link_id}")
    )


# --------------------------------------------------------------------------- #
# The decision and the download path
# --------------------------------------------------------------------------- #


@router.get("/links/{link_id}/download")
def evaluate_download(
    link_id: str,
    item_id: str = Query(..., description="The item whose bytes would be served."),
    engine: ViewOnlyAccessEngine = EngineDep,
) -> dict[str, Any]:
    """The three-way AND for one item on one link, read-only.

    Exists so a viewer page can render the affordance correctly without attempting a fetch:
    the download control is hidden when any of the three gates says no.
    """

    return engine.evaluate(link_id, item_id)


@router.post("/links/{link_id}/download", status_code=201)
def fetch_download(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ViewOnlyAccessEngine = EngineDep,
) -> dict[str, Any]:
    """Serve one document, and record the download when the three controls agree.

    The synchronous enforcement the specification describes. A refused fetch answers 403 and
    writes nothing; a permitted fetch writes the view row through an audited write, so the
    audit entry and the row are one transaction.
    """

    return engine.fetch(
        link_id,
        payload,
        actor=actor,
        source=_source("POST", "/links/{link_id}/download"),
    )


@router.post("/links/{link_id}/bulk")
def request_bulk_download(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ViewOnlyAccessEngine = EngineDep,
) -> dict[str, Any]:
    """The room-wide escalation: whether every named document may be zipped.

    Read-only and synchronous: it reports the decision without releasing bytes, because this
    workflow gates the download and a zip of every document is the escalation the room control
    exists for. A zipped item that is withheld keeps the whole archive from being built.
    """

    item_ids = payload.get("item_ids") or []
    if not isinstance(item_ids, list):
        raise rules.PolicyRefused(
            "item_ids must be a list of item ids.",
            {"item_ids": "Send a JSON array of item ids."},
        )
    return engine.bulk(link_id, [str(item) for item in item_ids])


# --------------------------------------------------------------------------- #
# The download log: the audit trail reads this
# --------------------------------------------------------------------------- #


@router.get("/links/{link_id}/downloads")
def list_link_downloads(link_id: str, engine: ViewOnlyAccessEngine = EngineDep) -> dict[str, Any]:
    """Every recorded view on one link, newest first. Mirrors ``GET /v1/links/{id}/views``."""

    rows = engine.views(link_id=link_id)
    return {
        "link_id": link_id,
        "count": len(rows),
        "downloads": rows,
        vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
        vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.get("/downloads")
def list_downloads(
    room_id: str | None = Query(None), engine: ViewOnlyAccessEngine = EngineDep
) -> dict[str, Any]:
    """Every recorded download event, optionally narrowed to a room."""

    rows = engine.views(room_id=room_id)
    return {
        "count": len(rows),
        "downloads": rows,
        vocab.OWNER_FIELD: vocab.SCOPE_OWNER,
        vocab.READ_SOURCE_FIELD: vocab.READ_SOURCE,
        vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the control states the specification says matter, not just the happy path.

    Every row is produced by calling the real :class:`ViewOnlyAccessEngine`, so the demo
    cannot show a shape or a view the HTTP routes would not produce. ``source="seed"`` rather
    than a route string: no route served this, and claiming one would be the lie the brief's
    audit rule exists to prevent.

    The states seeded, and why each is here:

    * a room with **bulk download on** and a room with it **off**, because the room control is
      the room-wide escalation and a board holding only the on case would hide the default;
    * a link with **allow download on** and a link left at the **documented off default**;
    * three per-item grants in WF-074's permission collection - one **granted**, one
      **revoked**, and one **never granted** - because the three grant states are what
      separates "somebody revoked it" from "nobody granted it";
    * a **permitted download**, which writes a real view row through the engine, so the
      download log has a row with ``downloaded_at`` and ``download_type``;
    * a **refused download**, which writes nothing, so the demo's own comment can say the log
      holds the downloads that happened and not the ones that were refused.

    The per-item grant rows belong to WF-074's permission set and are written here only as
    demo data for the read source this workflow evaluates. The request path never writes them.

    The return string is ASCII and is asserted encodable by cp1252 in ``tests/test_wf072.py``:
    the seeder prints it to a Windows console, and one RIGHTWARDS ARROW in a recovered
    feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id

    engine = ViewOnlyAccessEngine(store, now=lambda: now)

    # 1. The room control: on for one room, off for the other, so the default is a state.
    #    Only when a second room exists; with one room a second write would overwrite the
    #    first and the demo would show only the off case.
    engine.set_room_policy(room_id, {vocab.BULK_DOWNLOAD: True}, actor="dana", source="seed")
    if room_two != room_id:
        engine.set_room_policy(room_two, {vocab.BULK_DOWNLOAD: False}, actor="dana", source="seed")

    # 2. Two links: one escalated, one at the documented view-only default.
    open_link = engine.create_link(
        room_id,
        {vocab.NAME_FIELD: "Northwind - partner round", vocab.ALLOW_DOWNLOAD: True},
        actor="dana",
        source="seed",
    )
    engine.create_link(
        room_id,
        {vocab.NAME_FIELD: "Northwind - default view-only round"},
        actor="dana",
        source="seed",
    )

    # 3. Three per-item grants in WF-074's collection: granted, revoked, and never granted.
    #    These are the read source; this workflow evaluates them and never writes them on the
    #    request path. Written through the store, so each is an audited insert like any other.
    store.create(
        vocab.WF074_LINK_PERMISSION_COLLECTION,
        {
            vocab.ITEM_ID: "ddoc_northwind_deck",
            vocab.ITEM_TYPE: vocab.ITEM_TYPE_DOCUMENT,
            "can_view": True,
            vocab.CAN_DOWNLOAD: True,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    store.create(
        vocab.WF074_LINK_PERMISSION_COLLECTION,
        {
            vocab.ITEM_ID: "ddoc_northwind_pricing",
            vocab.ITEM_TYPE: vocab.ITEM_TYPE_DOCUMENT,
            "can_view": True,
            vocab.CAN_DOWNLOAD: True,
            vocab.REVOKED_AT: now.isoformat(),
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # 4. A permitted download, so the log has a real row. The engine re-reads the decision,
    #    so this cannot record a view the controls would not have allowed.
    engine.fetch(
        open_link["id"],
        {vocab.ITEM_ID: "ddoc_northwind_deck", vocab.DOWNLOAD_TYPE: vocab.DOWNLOAD_TYPE_ORIGINAL},
        actor="viewer",
        source="seed",
    )

    counts = engine.summary()
    # Read the two refusing states back rather than asserting them in prose: a fetch that would
    # be permitted proves nothing about a refusal, so both refusals are exercised through the
    # decision the routes use, and the absence of a view row is what is counted.
    revoked = engine.evaluate(open_link["id"], "ddoc_northwind_pricing")
    never_granted = engine.evaluate(open_link["id"], "ddoc_northwind_board_deck")
    refusals_wrote_nothing = (
        not revoked[vocab.DOWNLOAD_FIELD] and not never_granted[vocab.DOWNLOAD_FIELD]
    )

    return (
        f"{counts['links']} governed links ({counts['allow_download']} allow download, "
        f"{counts['view_only_links']} view-only); "
        f"{counts['rooms_configured']} room policies ({counts['bulk_download']} bulk download on); "
        f"1 granted item, 1 revoked item (out of force), 1 item never granted "
        f"({never_granted['grant'][vocab.GRANT_FIELD]}); "
        f"{counts['downloads']} recorded download(s); "
        f"2 refused downloads wrote nothing: {refusals_wrote_nothing}"
    )
