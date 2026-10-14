"""WF-128: in-room live presentation / co-browsing.

The rules live in :mod:`dsr.live_sessions`, which knows nothing about HTTP. This
module is the four things a feature contributes, and the three things it must
never contribute.

What this module contributes
----------------------------
* The route table, under the prefix this feature owns (``/api/wf-128``).
* The error-to-response mapping, exported as :data:`EXCEPTION_HANDLERS` because
  FastAPI accepts handlers on the app object only.
* The demo rows, as ``seed(db, context)`` rather than an edit to the shared
  ``backend/seed.py``.
* The vocabulary, the inferences and the researched user flow the page renders.

What it must never contribute
-----------------------------
* No shared file: ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-128.md``; issue #138.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.live_sessions import vocabulary as v
from dsr.live_sessions.engine import LiveSessionBook
from dsr.live_sessions.errors import (
    SessionConflict,
    SessionNotFound,
    SessionValidationError,
)
from dsr.store import RecordStore

FEATURE = {
    "id": v.FEATURE_ID,
    "ticket": v.TICKET,
    "name": v.NAME,
    "description": (
        "A rep starts a live session from the room. The buying committee joins, each with "
        "its own link, and follows the presenter through the room's pages as the reveal "
        "order is respected. The session is recorded and published to the room as a replay, "
        "and the questions asked live are logged against the deal. No realtime transport "
        "exists in this build: the session is state and events, and a follower polls."
    ),
    "nav": [{"id": v.FEATURE_ID, "label": "Live presentation"}],
}

router = APIRouter(prefix=v.PREFIX, tags=["wf-128"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _validation_error(request: Request, exc: SessionValidationError) -> JSONResponse:
    """422, with one message beside each bad field."""

    return JSONResponse(status_code=422, content={"error": "live_session_invalid", **exc.as_dict()})


def _not_found(request: Request, exc: SessionNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(
        status_code=404, content={"error": "live_session_not_found", **exc.as_dict()}
    )


def _conflict(request: Request, exc: SessionConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(
        status_code=409, content={"error": "live_session_conflict", **exc.as_dict()}
    )


EXCEPTION_HANDLERS = {
    SessionValidationError: _validation_error,
    SessionNotFound: _not_found,
    SessionConflict: _conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> LiveSessionBook:
    """The session book on the shared audited store, with the production clock."""

    return LiveSessionBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every closed word, sourced rule and stack decision this workflow records."""

    return LiveSessionBook.vocabulary()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """The headline counts a seller needs before opening a room's live sessions."""

    return book.summary(room_id=room_id)


@router.get("/recipients")
def recipients(
    room_id: str = Query(...),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """The room's buyers, reused from WF-004's grants and invitations."""

    entries = book.recipients(room_id)
    return {"room_id": room_id, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


@router.get("/sessions")
def list_sessions(
    room_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Every live session, newest first, scoped to a room when one is given."""

    entries = book.sessions(room_id=room_id, state=state)
    return {"count": len(entries), "entries": entries}


@router.post("/sessions", status_code=201)
def create_session(
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Create a session from the room, plan the reveal order and invite people."""

    return book.create_session(
        payload, source=_source("POST", "/sessions"), actor=payload.get("actor")
    )


@router.get("/sessions/{session_id}")
def read_session(session_id: str, book: LiveSessionBook = BookDep) -> dict[str, Any]:
    """One session, with its agenda, its cursor and its recording reference."""

    return book.get_session(session_id)


@router.post("/sessions/{session_id}/start")
def start_session(
    session_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Start the live session: begin recording and notify every invitee."""

    return book.start_session(
        session_id,
        source=_source("POST", "/sessions/{session_id}/start"),
        actor=payload.get("actor"),
    )


@router.post("/sessions/{session_id}/end")
def end_session(
    session_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """End the session, publish the replay and close every attendance row."""

    return book.end_session(
        session_id,
        source=_source("POST", "/sessions/{session_id}/end"),
        actor=payload.get("actor"),
    )


@router.post("/sessions/{session_id}/reveal")
def reveal(
    session_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Move the presenter's cursor one step forward, or back to any earlier step."""

    return book.reveal(
        session_id,
        payload,
        source=_source("POST", "/sessions/{session_id}/reveal"),
        actor=payload.get("actor"),
    )


@router.get("/sessions/{session_id}/state")
def session_state(
    session_id: str,
    viewer: str | None = Query(default=None, description="A participant's email"),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """The step a follower polls. Read-only, so a poll never writes."""

    return book.state(session_id, viewer=viewer)


@router.post("/sessions/{session_id}/sync")
def sync(
    session_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """A follower acknowledges the presenter's cursor, the stand-in for a stream."""

    return book.sync(
        session_id,
        payload,
        source=_source("POST", "/sessions/{session_id}/sync"),
        actor=payload.get("actor"),
    )


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #


@router.post("/sessions/{session_id}/invite", status_code=201)
def invite(
    session_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Add one participant and issue that participant's own link."""

    return book.invite(
        session_id,
        payload,
        source=_source("POST", "/sessions/{session_id}/invite"),
        actor=payload.get("actor"),
    )


@router.get("/sessions/{session_id}/participants")
def list_participants(
    session_id: str,
    status: str | None = Query(default=None),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Everyone invited to this session, with their role and their link."""

    entries = book.participants(session_id=session_id, status=status)
    return {"session_id": session_id, "count": len(entries), "entries": entries}


@router.get("/sessions/{session_id}/events")
def session_events(
    session_id: str,
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """The session's event log, newest first. This is what a replay reads."""

    entries = book.events(session_id=session_id)
    return {"session_id": session_id, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #


@router.post("/sessions/{session_id}/questions", status_code=201)
def ask_question(
    session_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Log a question asked live against the session, and therefore the deal."""

    return book.ask(
        session_id,
        payload,
        source=_source("POST", "/sessions/{session_id}/questions"),
        actor=payload.get("actor"),
    )


@router.get("/sessions/{session_id}/questions")
def list_questions(
    session_id: str,
    status: str | None = Query(default=None),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Every question logged against this session, oldest first."""

    entries = book.questions(session_id=session_id, status=status)
    return {"session_id": session_id, "count": len(entries), "entries": entries}


@router.post("/questions/{question_id}/answer")
def answer_question(
    question_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Answer a logged question, in the session or after it."""

    return book.answer(
        question_id,
        payload,
        source=_source("POST", "/questions/{question_id}/answer"),
        actor=payload.get("actor"),
    )


# --------------------------------------------------------------------------- #
# Output: attendance, the replay and the notifications
# --------------------------------------------------------------------------- #


@router.get("/sessions/{session_id}/attendance")
def session_attendance(
    session_id: str,
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """Per-participant attendance, written to the room record."""

    entries = book.attendance(session_id=session_id)
    return {"session_id": session_id, "count": len(entries), "entries": entries}


@router.get("/sessions/{session_id}/recordings")
def session_recordings(
    session_id: str,
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """The session's recording, and its stand-in replay URL."""

    entries = book.recordings(session_id=session_id)
    return {"session_id": session_id, "count": len(entries), "entries": entries}


@router.get("/sessions/{session_id}/notifications")
def session_notifications(
    session_id: str,
    book: LiveSessionBook = BookDep,
) -> dict[str, Any]:
    """The invitee notifications written for this session."""

    entries = book.notifications(session_id=session_id)
    return {"session_id": session_id, "count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: Two rooms get a session so the demo shows both an ended replay and a session
#: that leaves states a reviewer should see. A feature whose page is empty in the
#: demo is a feature nobody can review, and this one has several ways to be
#: empty: no session, no join, no question, no replay.
DEMO_PAGE_TITLES: tuple[str, ...] = (
    "Welcome and agenda",
    "How the platform fits your team",
    "Security and rollout",
)


def _demo_pages(db: AuditedDatabase, room_id: str) -> int:
    """Give the first demo room an agenda of pages, if it has fewer than two.

    WF-002 owns the page collection. Seeding pages here gives the presenter
    something to reveal; a room with no pages would produce a single screen step
    and the reveal order would have nothing to order.
    """

    existing = db.list(v.PAGE_COLLECTION, room_id=room_id, limit=50)
    if len(existing) >= 2:
        return 0
    added = 0
    for index, title in enumerate(DEMO_PAGE_TITLES):
        db.create(
            v.PAGE_COLLECTION,
            {
                "title": title,
                "slug": f"live-{index + 1}",
                "position": index,
                "is_home": index == 0,
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        added += 1
    return added


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the researched states so a reviewer can see the workflow work.

    The states are chosen so the demo is not all one shape:

    * the first room gets an agenda of pages, a session that runs live: the
      presenter reveals step one and step two, one follower joins and syncs while
      a second invitee never joins (so an ``invited_only`` attendance row
      exists), a question is asked and answered and a second is left open, then
      the session ends and publishes a replay;
    * the second room gets a scheduled session that never starts, so the
      ``scheduled`` state and its unused links are visible.

    Every character in the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature
    broke the whole seed.
    """

    store = RecordStore(db)
    now = context.get("now")
    book = LiveSessionBook(store, now=(lambda: now) if now is not None else None)

    room_ids = context.get("room_ids") or []
    if not room_ids:
        return "no rooms to attach to"

    first_room = room_ids[0][0]
    _demo_pages(db, first_room)

    sessions = 0
    questions_answered = 0
    questions_open = 0

    session = book.create_session(
        {
            "room_id": first_room,
            "presenter": "dana@northwind.example",
            "title": "Live walkthrough",
            "invitees": ["a.buyer@northwind.example", "b.buyer@northwind.example"],
        },
        source="seed",
        actor="dana",
    )
    sessions += 1
    session_id = session["id"]

    book.start_session(session_id, source="seed", actor="dana")
    if session["step_count"] > 1:
        book.reveal(session_id, {"position": 1}, source="seed", actor="dana@northwind.example")

    participants = book.participants(session_id=session_id)
    followers = [row for row in participants if row["role"] == v.ROLE_FOLLOWER]
    if followers:
        book.sync(
            session_id,
            {"as": followers[0]["email"]},
            source="seed",
            actor=followers[0]["email"],
        )
    if len(followers) > 1:
        book.invite(
            session_id,
            {"email": "late.joiner@northwind.example", "role": v.ROLE_FOLLOWER},
            source="seed",
            actor="dana",
        )

    first_question = book.ask(
        session_id,
        {
            "asker": (followers[0]["email"] if followers else "a.buyer@northwind.example"),
            "text": "How does SSO map onto our Okta groups?",
        },
        source="seed",
        actor="buyer",
    )
    book.answer(
        first_question["id"],
        {"answer": "Groups map by SCIM attribute, and I will send the mapping."},
        source="seed",
        actor="dana",
    )
    questions_answered += 1
    book.ask(
        session_id,
        {
            "asker": (followers[-1]["email"] if followers else "b.buyer@northwind.example"),
            "text": "Can we see the audit log export before we sign?",
        },
        source="seed",
        actor="buyer",
    )
    questions_open += 1
    book.end_session(session_id, source="seed", actor="dana")

    if len(room_ids) > 1:
        second_room = room_ids[1][0]
        book.create_session(
            {
                "room_id": second_room,
                "presenter": "sam@contoso.example",
                "title": "Security review live",
            },
            source="seed",
            actor="sam",
        )
        sessions += 1

    attendance = book.attendance()
    attended = len([row for row in attendance if row["attended"]])
    invited_only = len([row for row in attendance if not row["attended"]])
    replays = len([row for row in book.recordings() if row["status"] == v.RECORDING_PUBLISHED])

    return (
        f"WF-128 seeded: {sessions} live sessions, {len(book.participants())} participants, "
        f"{attended} attended, {invited_only} invited only, "
        f"{questions_answered} questions answered, {questions_open} questions open, "
        f"{replays} replays published"
    )
