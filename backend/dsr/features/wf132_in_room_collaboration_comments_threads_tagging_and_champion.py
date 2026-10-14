"""WF-132: in-room conversation and champion self-service.

The rules live in :mod:`dsr.inroom_collaboration` and are not restated here. This
module is the three things a feature contributes, and the three things it must
never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
  A hundred workflow branches each editing those files is why none of the first
  hundred merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

Who an actor is
---------------

There is no session in this repository for a room member to borrow, so every write
carries the acting principal and the engine resolves that person's role and side
from the room's ``owner`` field and WF-004's ``room_access`` grants. That is the
same identity the Share dialog uses, which is what "reuse the roles vocabulary"
means in practice: one room role set, not two.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.inroom_collaboration import rules as conversation_rules, vocabulary as vocab
from dsr.inroom_collaboration.engine import InRoomEngine
from dsr.inroom_collaboration.errors import (
    InRoomDenied,
    InRoomError,
    InRoomNotFound,
    InRoomValidation,
)
from dsr.store import RecordStore

FEATURE = {
    "id": vocab.FEATURE_ID,
    "ticket": vocab.TICKET,
    "name": vocab.NAME,
    "description": (
        "Anchor comments and threads to a room object, tag colleagues into them, "
        "track mentions and resolution state, and let a buyer-side champion edit "
        "content themselves so a buyer never emails a zip of attachments."
    ),
    "nav": [{"id": "wf-132-conversations", "label": "Room conversations"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix=vocab.ROUTE_PREFIX, tags=["WF-132"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


def _engine(store: RecordStore) -> InRoomEngine:
    """The engine, with the clock left at its production default."""
    return InRoomEngine(store)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _in_room_error(request: Request, exc: InRoomError) -> JSONResponse:
    """One handler for the whole family, keyed on the class's own status.

    Registering the base type rather than each subclass is deliberate: Starlette
    resolves a handler by walking the exception's MRO, so a base handler catches
    every subclass, and the status lives on the class, which means there is one
    place that says a refusal is 403 and not a second table to drift.
    """
    if isinstance(exc, InRoomDenied):
        slug = "denied"
    elif isinstance(exc, InRoomNotFound):
        slug = "not_found"
    elif isinstance(exc, InRoomValidation):
        slug = "invalid"
    else:
        slug = "conflict"
    content: dict[str, Any] = {"error": slug, "detail": str(exc)}
    if exc.errors:
        content["errors"] = exc.errors
    reason = getattr(exc, "reason", None)
    if reason:
        content["reason"] = reason
    return JSONResponse(status_code=exc.status_code, content=content)


EXCEPTION_HANDLERS = {InRoomError: _in_room_error}


# --------------------------------------------------------------------------- #
# Vocabulary and summary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, including the contested flag."""
    return vocab.vocabulary()


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""
    return _engine(store).summary(room_id)


# --------------------------------------------------------------------------- #
# Threads
# --------------------------------------------------------------------------- #


@router.get("/threads")
def list_threads(
    room_id: str | None = Query(None),
    anchor_type: str | None = Query(None),
    anchor_id: str | None = Query(None),
    state: str | None = Query(None),
    principal: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every thread, or the threads in one room, newest activity first."""
    threads = _engine(store).list_threads(
        room_id=room_id,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        state=state,
        principal=principal,
    )
    return {"room_id": room_id, "count": len(threads), "threads": threads}


@router.get("/rooms/{room_id}/threads")
def list_room_threads(
    room_id: str,
    anchor_type: str | None = Query(None),
    anchor_id: str | None = Query(None),
    state: str | None = Query(None),
    principal: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The threads in one room, optionally filtered by anchor, state or watcher."""
    threads = _engine(store).list_threads(
        room_id=room_id,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        state=state,
        principal=principal,
    )
    return {"room_id": room_id, "count": len(threads), "threads": threads}


@router.post("/rooms/{room_id}/threads", status_code=201)
def open_thread(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Open a thread with its first comment, anchored to a room object.

    ``anchor_type`` is one of ``page``, ``section``, ``asset`` or ``map_task``, and
    ``anchor_id`` is the id of that object. The anchor is stored as a pair rather
    than resolved here: this workflow does not own the page or task collections.
    """
    return _engine(store).open_thread(
        room_id, payload, source=_source("POST", "/rooms/{room_id}/threads")
    )


@router.get("/threads/{thread_id}")
def read_thread(thread_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One thread with its comments, mentions and escalations."""
    return _engine(store).read_thread(thread_id)


@router.post("/threads/{thread_id}/comments", status_code=201)
def add_comment(
    thread_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Reply in a thread, or post a question into it.

    A question (``kind`` is ``question``) is the only kind that can escalate, so
    the kind is recorded rather than inferred from the text.
    """
    return _engine(store).add_comment(
        thread_id, payload, source=_source("POST", "/threads/{thread_id}/comments")
    )


@router.post("/threads/{thread_id}/resolve")
def resolve_thread(
    thread_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Resolve a thread. A resolved thread refuses further comments until reopened."""
    return _engine(store).resolve_thread(
        thread_id, payload, source=_source("POST", "/threads/{thread_id}/resolve")
    )


@router.post("/threads/{thread_id}/reopen")
def reopen_thread(
    thread_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Reopen a resolved thread, so the resolution is a step and not a dead end."""
    return _engine(store).reopen_thread(
        thread_id, payload, source=_source("POST", "/threads/{thread_id}/reopen")
    )


@router.post("/threads/{thread_id}/mentions", status_code=201)
def tag_colleagues(
    thread_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Tag colleagues into a thread without writing a comment.

    A handle that resolves to a live participant is notified and subscribed; one
    that resolves to nobody is recorded as unresolved, which is the visible
    consequence of a room with no grant for that address.
    """
    return _engine(store).tag(
        thread_id, payload, source=_source("POST", "/threads/{thread_id}/mentions")
    )


@router.post("/threads/{thread_id}/watch")
def watch_thread(
    thread_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Subscribe a principal to a thread."""
    return _engine(store).watch(
        thread_id, payload, source=_source("POST", "/threads/{thread_id}/watch")
    )


@router.post("/threads/{thread_id}/convert-to-task", status_code=201)
def convert_to_task(
    thread_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record the thread-to-MAP-task conversion the research names.

    The conversion row names the task the thread should become. The task row
    itself belongs to WF-124 and is not written here.
    """
    return _engine(store).convert_to_task(
        thread_id, payload, source=_source("POST", "/threads/{thread_id}/convert-to-task")
    )


# --------------------------------------------------------------------------- #
# Champion self-service
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/identity")
def identity(
    room_id: str,
    actor: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Who ``actor`` is in this room: role, side and champion status."""
    return _engine(store).identity(room_id, actor)


@router.post("/rooms/{room_id}/edits", status_code=201)
def record_edit(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record a champion editing a section, as a content delta in the change log.

    A buyer-side holder of a content-managing role passes the same check the
    seller does, so the champion can edit their own room without a rep. The
    before/after and the derived delta are kept; nothing is deleted.
    """
    return _engine(store).record_edit(
        room_id, payload, source=_source("POST", "/rooms/{room_id}/edits")
    )


@router.get("/rooms/{room_id}/edits")
def list_edits(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The append-only content change log for one room."""
    edits = _engine(store).list_edits(room_id)
    return {"room_id": room_id, "count": len(edits), "edits": edits}


@router.get("/rooms/{room_id}/champions")
def champion_activity(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The buyer-side champion's self-service edits, the outcome the flow targets."""
    return _engine(store).champion_activity(room_id)


# --------------------------------------------------------------------------- #
# Escalations
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/escalations/refresh", status_code=201)
def refresh_escalations(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Sweep the room for unanswered buyer questions and escalate the overdue ones.

    Idempotent and explicit: escalation depends on time passing, so a scheduler
    calls this rather than each request re-deriving it.
    """
    created = _engine(store).refresh_escalations(
        room_id, source=_source("POST", "/rooms/{room_id}/escalations/refresh")
    )
    return {"room_id": room_id, "created": len(created), "escalations": created}


@router.get("/rooms/{room_id}/escalations")
def list_escalations(
    room_id: str, thread_id: str | None = Query(None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """The escalations raised on a room's threads, newest first."""
    rows = _engine(store).escalations(room_id=room_id, thread_id=thread_id)
    return {"room_id": room_id, "count": len(rows), "escalations": rows}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _buyer_principals(store: RecordStore, room_id: str | None) -> list[dict[str, Any]]:
    """The room's live grant holders, most privileged first.

    Reads WF-004's ``room_access`` collection rather than re-declaring it: the
    grant list is the buyer/stakeholder list the data sources name, and a tag with
    no grant behind it is unresolved by design.
    """
    order = {vocab.ROLE_COLLABORATOR: 0, vocab.ROLE_CONTRIBUTOR: 1, vocab.ROLE_VIEWER: 2}
    grants = []
    for record in store.list(vocab.ACCESS_COLLECTION, room_id=room_id, limit=500):
        data = dict(record.get("data") or {})
        principal = conversation_rules.normalise_principal(data.get("principal"))
        if principal:
            grants.append({"principal": principal, "role": data.get("role") or vocab.ROLE_VIEWER})
    grants.sort(key=lambda grant: (order.get(grant["role"], 9), grant["principal"]))
    return grants


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the conversation states the research says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a shape, a mention
    state or an audit row the HTTP routes would not produce. ``source="seed"``
    rather than a route string: no route served this, and claiming one would be the
    lie the source rule exists to prevent.

    The states seeded, and why each is here:

    * an **open** thread where a buyer tags a colleague who resolves to a real
      grant, so a mention is notified and a watcher is subscribed;
    * a thread tagging an address nobody in the room holds, so the unresolved
      mention is visible rather than silent;
    * a buyer **question** left unanswered and then swept after the interval, so an
      escalation row exists in the demo;
    * a **resolved** thread, so the resolution state is on screen;
    * a **champion edit** by a buyer-side holder of a content-managing role, the
      self-service outcome the workflow exists to produce;
    * a **thread-to-task conversion**, so the fourth automation is visible.

    Every character of the returned string is ASCII, so the seeder can print it on
    a Windows console. A single non-cp1252 character in one feature broke the whole
    seeder once.
    """
    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room = store.get(room_id) or {"id": room_id, "data": {}}
    owner = conversation_rules.normalise_principal((room.get("data") or {}).get("owner")) or "dana"

    grants = _buyer_principals(store, room_id)
    champion = next((g["principal"] for g in grants if g["role"] == vocab.ROLE_COLLABORATOR), None)
    buyer = next((g["principal"] for g in grants if g["principal"] != champion), None)
    buyer = buyer or champion or owner
    champion = champion or owner
    stranger = "visitor@nowhere.example"

    # A clock that starts in the past and advances, so an unanswered question is
    # genuinely older than the escalation interval when the sweep runs.
    clock = {"now": now - timedelta(hours=vocab.UNANSWERED_QUESTION_HOURS + 6)}
    engine = InRoomEngine(store, now=lambda: clock["now"])
    source = "seed"

    # 1. Open thread, buyer tags the champion (resolved) on a page.
    tagged = engine.open_thread(
        room_id,
        {
            "actor": buyer,
            "anchor_type": vocab.ANCHOR_PAGE,
            "anchor_id": "page_pricing",
            "anchor_label": "Pricing",
            "body": f"Can we talk through the volume tiers? cc @{champion}",
        },
        source=source,
    )
    engine.add_comment(
        tagged["id"],
        {"actor": owner, "body": "Yes - the tiers step at 25 and 100 seats."},
        source=source,
    )

    # 2. A tag nobody in the room holds, so an unresolved mention is on screen.
    engine.open_thread(
        room_id,
        {
            "actor": buyer,
            "anchor_type": vocab.ANCHOR_SECTION,
            "anchor_id": "section_security",
            "anchor_label": "Security review",
            "body": f"Pulling in @{stranger} from our infosec team.",
        },
        source=source,
    )

    # 3. A buyer question, unanswered, then swept after the interval.
    question = engine.open_thread(
        room_id,
        {
            "actor": buyer,
            "anchor_type": vocab.ANCHOR_MAP_TASK,
            "anchor_id": "task_security_review",
            "anchor_label": "Buyer security review",
            "body": "Who signs the DPA on your side?",
            "kind": vocab.KIND_QUESTION,
        },
        source=source,
    )

    # 4. A resolved thread.
    resolved = engine.open_thread(
        room_id,
        {
            "actor": buyer,
            "anchor_type": vocab.ANCHOR_ASSET,
            "anchor_id": "asset_overview_deck",
            "anchor_label": "Enterprise Overview Deck",
            "body": "Sharing this with our platform team.",
        },
        source=source,
    )
    engine.resolve_thread(resolved["id"], {"actor": owner}, source=source)

    # 5. The champion edits a section themselves, with a real before/after delta.
    engine.record_edit(
        room_id,
        {
            "actor": champion,
            "section_ref": "page_pricing",
            "anchor_type": vocab.ANCHOR_SECTION,
            "summary": "Champion tailored the pricing summary for internal approval.",
            "before": {"headline": "Enterprise pricing", "tiers": "3"},
            "after": {"headline": "Pricing for the Northwind rollout", "tiers": "4"},
        },
        source=source,
    )

    # 6. Convert a thread to a MAP task link, the fourth automation.
    engine.convert_to_task(
        question["id"],
        {
            "actor": owner,
            "owner": owner,
            "owner_side": vocab.SIDE_SELLER,
            "title": "Answer the DPA question",
        },
        source=source,
    )

    # 7. Advance the clock and sweep, so the old question escalates.
    clock["now"] = now
    engine.refresh_escalations(room_id, source=source)

    totals = engine.summary(room_id)
    return (
        f"{totals['threads']} threads ({totals['open_threads']} open, "
        f"{totals['resolved_threads']} resolved), {totals['comments']} comments, "
        f"{totals['questions']} buyer question(s), {totals['unanswered_questions']} "
        f"unanswered, {totals['escalations']} escalation(s), {totals['mentions']} "
        f"mention(s) ({totals['unresolved_mentions']} unresolved), "
        f"{totals['champion_edits']} champion edit(s), {totals['conversions']} conversion(s)"
    )
