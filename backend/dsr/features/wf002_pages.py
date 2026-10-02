"""WF-002: build the room's buyer-facing pages from DSR fragments.

Ported from ``feature/WF-002-build-the-room-s-buyer-facing-pages-from`` onto the
plugin host. That branch shipped its routes in a second module
(``dsr/routes_pages.py``) and mounted them from ``dsr/api.py``; the routes are
folded into this module's own ``router`` here, so there is one prefix to reason
about and one place the host mounts. The domain logic is untouched in
:mod:`dsr.fragments` and :mod:`dsr.pages`.

What changed in the port, and why
----------------------------------
* **Prefix.** ``/api/wf-002``, not the branch's ``/api``. The branch also claimed
  ``/api/rooms/{room_id}/...``, which is core-shaped: WF-001 and WF-003 have a
  legitimate interest in the same path, and the contract asks for a
  ticket-derived prefix so a collision is visible at load time rather than
  decided by whichever module the host walked first. The branch was never
  merged, so no client depends on the old path.
* **Dependencies.** ``StoreDep`` from :mod:`dsr.deps` instead of a private
  ``get_store`` reimplementation, and nothing from :mod:`dsr.api`.
* **Audit ``source``.** The branch hardcoded its write sources in the domain
  layer (``"POST fragment-set"``, ``f"create page in {room_id}"``). Every route
  here now passes ``f"{METHOD} {router.prefix}/..."`` down, so the audit row
  names the route that actually served the write. This is the bug the contract
  calls out by name: an audit log that kept recording a path the app had
  stopped serving.
* **Demo data.** ``seed(db, context)`` below, rather than the branch's edit to
  ``backend/seed.py``.

What the routes are for
-----------------------
``/fragment-sets``, ``/fragments``
    The catalogue. Readable by anyone; writable by anyone, because registering
    your own fragment set is the documented extension mechanism. The write is
    audited like every other one.

``/rooms/{room_id}/...``
    The editor. Writes require the Room Collaborator role, resolved from the
    room's own payload (see :mod:`dsr.pages`).

``/rooms/{room_id}/view``
    The buyer read path. Published pages only, resolved through the revision a
    page points at, so an unpublished edit is not on the path a buyer reads at
    all rather than merely hidden by the UI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditError, RecordNotFound
from dsr.deps import StoreDep
from dsr.fragments import FragmentError, normalise_fragment, normalise_set
from dsr.pages import PageService, PermissionDenied
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-002-buyer-pages",
    "ticket": "WF-002",
    "name": "Build the room's buyer-facing pages from DSR fragments",
    "description": (
        "A fragment catalogue, a drag-and-drop page editor with a configuration "
        "panel, an explicit publish step that writes an immutable revision, and "
        "the buyer read path that serves published revisions only."
    ),
    "nav": [{"id": "pages", "label": "Buyer pages"}],
}

router = APIRouter(prefix="/api/wf-002", tags=["wf002"])


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def get_pages(store: RecordStore = StoreDep) -> PageService:
    """The page service over the process-wide audited store."""
    return PageService(store)


PagesDep = Depends(get_pages)


def _actor() -> Any:
    return Query(
        default=None, description="Who is acting; checked against the room's collaborators"
    )


def _expected_revision() -> Any:
    return Query(
        default=None, description="The page revision the caller last read; a mismatch is a 409"
    )


# --------------------------------------------------------------------------- #
# Errors
#
# The branch registered these on the shared app in api.py, because an APIRouter
# cannot register exception handlers of its own. The host does that for a
# feature that exports EXCEPTION_HANDLERS, so no shared file is touched here.
# --------------------------------------------------------------------------- #


def _invalid_fragment(request: Request, exc: FragmentError) -> JSONResponse:
    # 400: the request was well-formed but names a fragment, a field or a field
    # value that cannot be accepted. The message is written for the person
    # editing the page.
    return JSONResponse(status_code=400, content={"error": "invalid_fragment", "detail": str(exc)})


def _forbidden(request: Request, exc: PermissionDenied) -> JSONResponse:
    # 403: the request was well-formed and the room exists, but the actor is not
    # a Room Collaborator on it.
    return JSONResponse(status_code=403, content={"error": "forbidden", "detail": str(exc)})


EXCEPTION_HANDLERS = {FragmentError: _invalid_fragment, PermissionDenied: _forbidden}


# --------------------------------------------------------------------------- #
# Fragment catalogue
# --------------------------------------------------------------------------- #


@router.get("/fragment-sets", summary="Fragment sets, with their fragments")
def list_fragment_sets(
    set_key: str | None = Query(default=None, alias="set", description="Only this set's fragments"),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """The merged catalogue: the three shipped sets plus any a team has added.

    This is the discovery endpoint the editor builds its palette from, so a
    client never hard-codes a fragment list.
    """
    catalogue = pages.catalogue()
    if set_key:
        catalogue = {
            **catalogue,
            "sets": [item for item in catalogue["sets"] if item["key"] == set_key],
            "fragments": [item for item in catalogue["fragments"] if item["set"] == set_key],
        }
        catalogue["counts"] = {
            **catalogue["counts"],
            "sets": len(catalogue["sets"]),
            "fragments": len(catalogue["fragments"]),
        }
    return catalogue


@router.post("/fragment-sets", status_code=201, summary="Register a custom fragment set")
def create_fragment_set(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Add a fragment set. The documented third-party extension point."""
    try:
        return pages.add_fragment_set(
            payload, actor=actor, source=f"POST {router.prefix}/fragment-sets"
        )
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/fragments", status_code=201, summary="Define a custom fragment")
def create_fragment(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Define a fragment inside an existing set."""
    try:
        return pages.add_fragment(payload, actor=actor, source=f"POST {router.prefix}/fragments")
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Pages: the editor
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/pages", summary="List a room's pages, drafts included")
def list_pages(room_id: str, pages: PageService = PagesDep) -> dict[str, Any]:
    room = pages.require_room(room_id)
    return {
        "room_id": room_id,
        "room_name": (room.get("data") or {}).get("name"),
        "permission_source": pages.permission_source(room),
        "pages": pages.list_pages(room_id),
    }


@router.post("/rooms/{room_id}/pages", status_code=201, summary="Create a page")
def create_page(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    try:
        return pages.create_page(
            room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/pages"
        )
    except AuditError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/pages/{page_id}", summary="One page, draft and publish state")
def get_page(room_id: str, page_id: str, pages: PageService = PagesDep) -> dict[str, Any]:
    return pages.get_page(room_id, page_id)


@router.patch("/rooms/{room_id}/pages/{page_id}", summary="Edit a page's draft")
def update_page(
    room_id: str,
    page_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    return pages.update_page(
        room_id,
        page_id,
        payload,
        actor=actor,
        expected_revision=expected_revision,
        source=f"PATCH {router.prefix}/rooms/{room_id}/pages/{page_id}",
    )


@router.delete("/rooms/{room_id}/pages/{page_id}", summary="Delete a page")
def delete_page(
    room_id: str,
    page_id: str,
    hard: bool = Query(default=False),
    actor: str | None = _actor(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    return pages.delete_page(
        room_id,
        page_id,
        actor=actor,
        hard=hard,
        source=f"DELETE {router.prefix}/rooms/{room_id}/pages/{page_id}",
    )


# -- blocks: the drag-and-drop step ----------------------------------------- #


@router.post("/rooms/{room_id}/pages/{page_id}/blocks", status_code=201, summary="Place a fragment")
def add_block(
    room_id: str,
    page_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Place a fragment onto the page. "Drag a fragment onto the page."

    A ``fragment`` key names the palette entry and ``config`` seeds it. Declared
    fields are validated; undeclared keys are stored exactly as authored.
    """
    return pages.add_block(
        room_id,
        page_id,
        payload,
        actor=actor,
        index=payload.get("index"),
        expected_revision=expected_revision,
        source=f"POST {router.prefix}/rooms/{room_id}/pages/{page_id}/blocks",
    )


@router.patch(
    "/rooms/{room_id}/pages/{page_id}/blocks/{block_id}", summary="Configure a placed fragment"
)
def update_block(
    room_id: str,
    page_id: str,
    block_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Set a fragment's fields in the configuration panel.

    The patch merges into the existing config, so one field can be set without
    resending the rest, and any undeclared key it carries is preserved.
    """
    return pages.update_block(
        room_id,
        page_id,
        block_id,
        payload,
        actor=actor,
        expected_revision=expected_revision,
        source=f"PATCH {router.prefix}/rooms/{room_id}/pages/{page_id}/blocks/{block_id}",
    )


@router.delete(
    "/rooms/{room_id}/pages/{page_id}/blocks/{block_id}", summary="Remove a placed fragment"
)
def remove_block(
    room_id: str,
    page_id: str,
    block_id: str,
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    return pages.remove_block(
        room_id,
        page_id,
        block_id,
        actor=actor,
        expected_revision=expected_revision,
        source=f"DELETE {router.prefix}/rooms/{room_id}/pages/{page_id}/blocks/{block_id}",
    )


@router.put("/rooms/{room_id}/pages/{page_id}/blocks/order", summary="Reorder placed fragments")
def reorder_blocks(
    room_id: str,
    page_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Set the block order. Every block on the page must appear exactly once."""
    order = payload.get("order")
    if not isinstance(order, list):
        raise HTTPException(status_code=400, detail="'order' must be a list of block ids")
    return pages.reorder_blocks(
        room_id,
        page_id,
        [str(item) for item in order],
        actor=actor,
        expected_revision=expected_revision,
        source=f"PUT {router.prefix}/rooms/{room_id}/pages/{page_id}/blocks/order",
    )


# -- publish: the step that reaches the buyer --------------------------------- #


@router.post("/rooms/{room_id}/pages/{page_id}/publish", summary="Publish the draft to buyers")
def publish_page(
    room_id: str,
    page_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Click *Publish*.

    "The fragment appears on the page the next time a member opens the room."
    Writes an immutable revision and points the page at it, so a buyer reads
    the published blocks and never the draft.
    """
    return pages.publish(
        room_id,
        page_id,
        actor=actor,
        note=payload.get("note"),
        expected_revision=expected_revision,
        source=f"POST {router.prefix}/rooms/{room_id}/pages/{page_id}/publish",
    )


@router.post("/rooms/{room_id}/pages/{page_id}/unpublish", summary="Withdraw a page from buyers")
def unpublish_page(
    room_id: str,
    page_id: str,
    actor: str | None = _actor(),
    expected_revision: int | None = _expected_revision(),
    pages: PageService = PagesDep,
) -> dict[str, Any]:
    """Take the page out of the buyer view, keeping the draft and its history."""
    return pages.unpublish(
        room_id,
        page_id,
        actor=actor,
        expected_revision=expected_revision,
        source=f"POST {router.prefix}/rooms/{room_id}/pages/{page_id}/unpublish",
    )


@router.get("/rooms/{room_id}/pages/{page_id}/revisions", summary="A page's published revisions")
def page_revisions(room_id: str, page_id: str, pages: PageService = PagesDep) -> dict[str, Any]:
    revisions = pages.revisions(room_id, page_id)
    return {
        "room_id": room_id,
        "page_id": page_id,
        "count": len(revisions),
        "revisions": [
            {
                "id": revision["id"],
                "number": (revision.get("data") or {}).get("number"),
                "published_at": (revision.get("data") or {}).get("published_at"),
                "published_by": (revision.get("data") or {}).get("published_by"),
                "note": (revision.get("data") or {}).get("note"),
                "digest": (revision.get("data") or {}).get("digest"),
                "block_count": len((revision.get("data") or {}).get("blocks") or []),
            }
            for revision in revisions
        ],
    }


# --------------------------------------------------------------------------- #
# The buyer read path
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/view", summary="Buyer view: every published page in the room")
def room_view(room_id: str, pages: PageService = PagesDep) -> dict[str, Any]:
    """What a member sees when they open the room.

    Published pages only, in page order, each carrying the blocks from the
    revision it points at and the documents those blocks select, resolved from
    the room's documents. A draft edit cannot appear here, because this path
    never reads the draft.
    """
    published = pages.published_pages(room_id)
    return {"room_id": room_id, "count": len(published), "pages": published}


@router.get("/rooms/{room_id}/view/{slug}", summary="Buyer view: one published page")
def published_page(room_id: str, slug: str, pages: PageService = PagesDep) -> dict[str, Any]:
    try:
        return pages.published_page(room_id, slug)
    except RecordNotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"no published page at {slug!r} in this room"
        ) from exc


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

# A feature whose page is empty in the demo is a feature nobody can review. The
# demo rooms are created by the shared seeder and its documents are created
# before features are seeded, so this seeds pages, drafts and one published
# revision, and wires document selectors to documents that already exist.

#: One custom set, so the catalogue's merge path and the "your own fragment set"
#: extension point are both visible in the demo rather than only in the tests.
DEMO_FRAGMENT_SET = {
    "key": "account-team",
    "name": "Account Team",
    "summary": "Blocks an account team can add without changing this codebase.",
}

DEMO_FRAGMENT = {
    "key": "account-owner",
    "name": "Account Owner",
    "set": "account-team",
    "summary": "Who to contact, and how.",
    "icon": "our-team",
    "fields": [
        {"key": "name", "label": "Name", "type": "text", "help": "The buyer's named contact."},
        {
            "key": "seniority",
            "label": "Seniority",
            "type": "select",
            "options": ["Director", "VP", "Head of", "C-level"],
            "help": "Optional.",
        },
    ],
}

PAGE_SEEDS = [
    {
        "title": "Welcome",
        "slug": "welcome",
        "is_home": True,
        "order": 0,
        "blocks": [
            {"fragment": "header-main", "config": {"heading": "Your digital sales room"}},
            {
                "fragment": "welcome",
                "config": {
                    "heading": "Welcome",
                    "body": "Everything your team needs to evaluate us, in one place.",
                },
            },
            {
                "fragment": "text",
                "config": {"body": "Reply to your account manager with any questions."},
            },
            # The Document Gallery Block, on the page buyers actually read, so
            # the published view exercises the room's documents.
            {"fragment": "document-gallery", "config": {}},
        ],
        "publish": True,
    },
    {
        "title": "Evaluation plan",
        "slug": "evaluation-plan",
        "order": 1,
        # A draft, deliberately left unpublished. "Buyers cannot see this" is the
        # state the whole workflow exists to make checkable, so it is in the demo
        # rather than only in the tests.
        "blocks": [
            {
                "fragment": "timeline",
                "config": {
                    "number_of_steps": 4,
                    "current_step": 2,
                    "step_1_title": "Discovery",
                    "step_1_estimate": "Complete",
                    "step_2_title": "Technical validation",
                    "step_2_estimate": "In progress",
                    "step_3_title": "Security review",
                    "step_3_estimate": "Week of the 14th",
                    "step_4_title": "Commercials",
                    "step_4_estimate": "Target end of month",
                },
            },
            {"fragment": "pdf-preview", "config": {}},
            {
                "fragment": "video",
                "config": {
                    "url": "https://example.com/walkthrough.mp4",
                    "width": 640,
                    "height": 360,
                },
            },
            {"fragment": "question-and-answer", "config": {}},
        ],
        "publish": False,
    },
]

#: Placed on the first demo room's welcome page, so the custom-fragment render
#: and configuration path is something a reviewer can see rather than infer.
CUSTOM_BLOCK = {
    "fragment": "account-owner",
    "config": {"name": "Dana Okonkwo", "seniority": "Director"},
}


def _with_documents(blocks: list[dict[str, Any]], document_ids: list[str]) -> list[dict[str, Any]]:
    """Point the document selectors at documents that really exist.

    A selector is validated against the room's own documents, so a seeded page
    naming a made-up id would be rejected outright. The Document Gallery Block
    takes up to four selectors and the PDF Preview Block one, which is what the
    documentation describes for each.
    """
    result: list[dict[str, Any]] = []
    for block in blocks:
        config = dict(block.get("config") or {})
        if block["fragment"] == "document-gallery":
            for slot in range(1, 5):
                key = f"document_{slot}"
                if key not in config and slot - 1 < len(document_ids):
                    config[key] = document_ids[slot - 1]
        if block["fragment"] == "pdf-preview" and "document" not in config and document_ids:
            config["document"] = document_ids[0]
        result.append({**block, "config": config})
    return result


def seed(db, context: dict[str, Any]) -> str:
    """Seed the catalogue additions, two pages per demo room, and a publish.

    Pages are written through the same :class:`PageService` the routes use, so
    the demo cannot drift from what a collaborator can actually do and every row
    is audited the way a live edit would be. The room is also given a
    collaborator grant, which makes the documented Room Collaborator requirement
    visible in the demo rather than only in the tests.
    """
    room_ids = context["room_ids"]
    if not room_ids:
        return ""

    store = RecordStore(db)
    service = PageService(store)

    db.create(
        "fragment_set",
        normalise_set(DEMO_FRAGMENT_SET, "custom"),
        record_id=f"fragment_set_{DEMO_FRAGMENT_SET['key']}",
        actor="dana",
        source="seed",
    )
    db.create(
        "fragment",
        normalise_fragment(DEMO_FRAGMENT, "custom"),
        record_id=f"fragment_{DEMO_FRAGMENT['key']}",
        actor="dana",
        source="seed",
    )

    pages = 0
    revisions = 0
    for index, (room_id, _account) in enumerate(room_ids):
        if store.get(room_id) is None:
            continue
        store.update(
            room_id,
            {"collaborators": ["dana"], "viewers": ["buyer@example.com"]},
            actor="dana",
            source="seed",
        )

        document_ids = [
            record["id"] for record in store.list("document", room_id=room_id, limit=50)
        ]

        for spec in PAGE_SEEDS:
            blocks = _with_documents(spec["blocks"], document_ids)
            if index == 0 and spec["slug"] == "welcome":
                blocks = [*blocks, dict(CUSTOM_BLOCK)]
            page = service.create_page(
                room_id,
                {
                    "title": spec["title"],
                    "slug": spec["slug"],
                    "is_home": spec.get("is_home", False),
                    "order": spec["order"],
                    "blocks": blocks,
                    # A team-owned field, to show that an undeclared key is
                    # stored, returned and published without a migration.
                    "campaign_id": "q4-enterprise",
                },
                actor="dana",
                source="seed",
            )
            pages += 1
            if spec["publish"]:
                service.publish(room_id, page["id"], actor="dana", note="seeded", source="seed")
                revisions += 1

    return f"1 fragment set, 1 fragment, {pages} pages, {revisions} published revisions"
