"""WF-123: in-room qualification capture.

The rules live in :mod:`dsr.qualification_capture`, which knows nothing about
HTTP. This module is the four things a feature contributes, and the three things
it must never contribute.

What this module contributes
----------------------------
* The route table, under the prefix this feature owns (``/api/wf-123``).
* The error-to-response mapping, exported as :data:`EXCEPTION_HANDLERS` because
  FastAPI accepts handlers on the app object only.
* The demo rows, as ``seed(db, context)`` rather than an edit to the shared
  ``backend/seed.py``.
* The vocabulary and the inference list the page renders.

What it must never contribute
-----------------------------
* No shared file: ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-123.md``; issue 160.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.qualification_capture import vocabulary as v
from dsr.qualification_capture.engine import QualificationEngine
from dsr.qualification_capture.errors import (
    CaptureConflict,
    CaptureNotFound,
    FormValidationError,
    QualificationError,
)
from dsr.qualification_capture.inferences import describe_inferences
from dsr.store import RecordStore

FEATURE = {
    "id": v.FEATURE_ID,
    "ticket": v.TICKET,
    "name": v.NAME,
    "description": (
        "Render a short qualifying form inside a room, capture the buyer's "
        "answers without leaving the room, attach them to the deal record, score "
        "a qualification band and route on it. The summary is seller-only."
    ),
    "nav": [{"id": v.FEATURE_ID, "label": "Qualification capture"}],
}

router = APIRouter(prefix=v.PREFIX, tags=[v.TICKET])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _validation_error(request: Request, exc: FormValidationError) -> JSONResponse:
    """422, with one message beside each bad field."""

    return JSONResponse(status_code=422, content={"error": "form_invalid", **exc.as_dict()})


def _not_found(request: Request, exc: CaptureNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(
        status_code=404, content={"error": "qualification_not_found", **exc.as_dict()}
    )


def _conflict(request: Request, exc: CaptureConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(
        status_code=409, content={"error": "qualification_conflict", **exc.as_dict()}
    )


def _qualification_error(request: Request, exc: QualificationError) -> JSONResponse:
    """The catch-all, for a refusal with no more specific mapping."""

    return JSONResponse(status_code=422, content={"error": "qualification_error", **exc.as_dict()})


EXCEPTION_HANDLERS = {
    FormValidationError: _validation_error,
    CaptureNotFound: _not_found,
    CaptureConflict: _conflict,
    QualificationError: _qualification_error,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> QualificationEngine:
    """The engine on the shared audited store, with the production clock."""

    return QualificationEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the route template this router serves.

    ``suffix`` is the path as declared (``/forms/{form_id}``), not the concrete
    request path. The audit row then names the route that will always serve that
    write - groupable across every id, and equal to a mounted route so the check
    that compares them is an equality rather than a pattern match.
    """

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every closed word, scale and sourced quote this workflow enforces."""

    return QualificationEngine.vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call the research left open, with its basis."""

    entries = describe_inferences()
    return {"count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Seller: forms
# --------------------------------------------------------------------------- #


@router.post("/forms", status_code=201)
def create_form(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Embed a qualifying form in a room."""

    target_room = str(payload.get("room_id") or room_id or "").strip()
    if not target_room:
        raise FormValidationError({"room_id": "a form needs a room to render in"})
    return engine.create_form(
        payload,
        room_id=target_room,
        source=_source("POST", "/forms"),
        actor=payload.get("actor"),
    )


@router.get("/forms")
def list_forms(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every form, newest first, with its respondent count."""

    entries = engine.list_forms(room_id=room_id, status=status)
    return {"count": len(entries), "entries": entries}


@router.get("/forms/{form_id}")
def read_form(form_id: str, engine: QualificationEngine = EngineDep) -> dict[str, Any]:
    """One form, with the configuration a seller reads."""

    return engine.get_form(form_id)


@router.patch("/forms/{form_id}")
def update_form(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Change the name, the intro, the dimensions, the required set or the routing."""

    return engine.update_form(
        form_id,
        payload,
        source=_source("PATCH", "/forms/{form_id}"),
        actor=payload.get("actor"),
    )


@router.post("/forms/{form_id}/publish")
def publish_form(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Make a form live so the buyer sees it. The room must be active."""

    return engine.publish_form(
        form_id,
        source=_source("POST", "/forms/{form_id}/publish"),
        actor=payload.get("actor"),
    )


@router.delete("/forms/{form_id}")
def delete_form(
    form_id: str,
    actor: str | None = Query(default=None),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a form. The row stays readable."""

    return engine.soft_delete_form(
        form_id, source=_source("DELETE", "/forms/{form_id}"), actor=actor
    )


@router.post("/forms/{form_id}/restore")
def restore_form(
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Restore a soft-deleted form."""

    return engine.restore_form(
        form_id,
        source=_source("POST", "/forms/{form_id}/restore"),
        actor=payload.get("actor"),
    )


# --------------------------------------------------------------------------- #
# Buyer: the in-room widget
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/form")
def room_form(room_id: str, engine: QualificationEngine = EngineDep) -> dict[str, Any]:
    """The live form rendered inside a room, with nothing scored."""

    return engine.room_form(room_id)


@router.post("/rooms/{room_id}/forms/{form_id}/autosave", status_code=201)
def autosave(
    room_id: str,
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Store the buyer's partial answers without leaving the room."""

    saved = engine.autosave(
        room_id,
        form_id,
        payload,
        source=_source("POST", "/rooms/{room_id}/forms/{form_id}/autosave"),
        actor=payload.get("actor"),
    )
    return {"submission": engine.buyer_view(saved), "events": saved.get("events", [])}


@router.post("/rooms/{room_id}/forms/{form_id}/submit", status_code=201)
def submit(
    room_id: str,
    form_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Complete the form. The response is the buyer's view - no score, no band."""

    saved = engine.submit(
        room_id,
        form_id,
        payload,
        source=_source("POST", "/rooms/{room_id}/forms/{form_id}/submit"),
        actor=payload.get("actor"),
    )
    return {
        "submission": engine.buyer_view(saved),
        "received": True,
        "message": "Thank you. Your answers were attached to this deal.",
    }


# --------------------------------------------------------------------------- #
# Seller: submissions, qualification and routing
# --------------------------------------------------------------------------- #


@router.get("/submissions")
def list_submissions(
    room_id: str | None = Query(default=None),
    form_id: str | None = Query(default=None),
    band: str | None = Query(default=None),
    status: str | None = Query(default=None),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every submission, newest first, with its score and band (seller only)."""

    entries = engine.list_submissions(room_id=room_id, form_id=form_id, band=band, status=status)
    return {"count": len(entries), "entries": entries, "seller_only": True}


@router.get("/submissions/{submission_id}")
def read_submission(submission_id: str, engine: QualificationEngine = EngineDep) -> dict[str, Any]:
    """One submission with its qualification, its routing and its events."""

    submission = engine.get_submission(submission_id)
    submission["events"] = engine.events(submission_id)
    return submission


@router.post("/submissions/{submission_id}/route")
def route_submission(
    submission_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Re-run the routing rule on a submission's band."""

    return engine.route_submission(
        submission_id,
        source=_source("POST", "/submissions/{submission_id}/route"),
        actor=payload.get("actor"),
    )


@router.get("/routes")
def list_routes(
    room_id: str | None = Query(default=None),
    engine: QualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every routing decision, newest first."""

    entries = engine.routes(room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.get("/rooms/{room_id}/qualification")
def qualification(room_id: str, engine: QualificationEngine = EngineDep) -> dict[str, Any]:
    """The seller-only qualification summary for a room."""

    return engine.qualification_summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says an in-room form must show.

    One live form in the first demo room and four submissions, and not all of
    them the happy path, because a demo containing only a qualified buyer teaches
    a reviewer nothing:

    * a qualified submission at the top of every ladder;
    * a developing submission with mixed answers;
    * an unqualified submission with nothing to route;
    * an in-progress draft, so the auto-save state is not empty.

    Every character in the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature
    broke the whole seed.
    """

    from datetime import datetime, timezone

    store = RecordStore(db)
    now = context.get("now") or datetime.now(timezone.utc)
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    engine = QualificationEngine(store, now=lambda: now)

    rooms = context.get("room_ids") or []
    if not rooms:
        return "WF-123: no room available, seeded nothing"
    first = rooms[0]
    if isinstance(first, (list, tuple)):
        room_id = first[0]
        account = first[1] if len(first) > 1 else None
    else:
        room_id, account = first, None

    form = engine.create_form(
        {
            "name": "Northwind qualification",
            "intro": "Four questions so we can prepare the right next step.",
            "deal_ref": "deal_northwind",
            "account_ref": account or "Northwind",
        },
        room_id=str(room_id),
        source="seed",
        actor="dana",
    )
    engine.publish_form(form["id"], source="seed", actor="dana")

    def send(token: str, answers: dict[str, str]) -> dict[str, Any]:
        return engine.submit(
            str(room_id),
            form["id"],
            {"answers": answers, "submission_token": token},
            source="seed",
            actor="dana",
        )

    first_qualified = send(
        "seed-buyer-hot",
        {
            v.DIMENSION_BUDGET: "funded",
            v.DIMENSION_TIMELINE: "now",
            v.DIMENSION_AUTHORITY: "decision_maker",
            v.DIMENSION_PROCUREMENT_PATH: "known_process",
        },
    )
    send(
        "seed-buyer-warm",
        {
            v.DIMENSION_BUDGET: "approved",
            v.DIMENSION_TIMELINE: "this_quarter",
            v.DIMENSION_AUTHORITY: "influencer",
            v.DIMENSION_PROCUREMENT_PATH: "unknown",
        },
    )
    send(
        "seed-buyer-cold",
        {
            v.DIMENSION_BUDGET: "no_budget",
            v.DIMENSION_TIMELINE: "later",
            v.DIMENSION_AUTHORITY: "unknown",
            v.DIMENSION_PROCUREMENT_PATH: "unknown",
        },
    )
    engine.autosave(
        str(room_id),
        form["id"],
        {
            "submission_token": "seed-buyer-draft",
            "answers": {v.DIMENSION_BUDGET: "needs_approval"},
        },
        source="seed",
        actor="dana",
    )

    summary = engine.qualification_summary(str(room_id))
    bands = summary["by_band"]
    return (
        "WF-123 seeded: 1 live form in the room; "
        f"{summary['submissions']} submissions "
        f"({bands[v.BAND_QUALIFIED]} qualified, {bands[v.BAND_DEVELOPING]} developing, "
        f"{bands[v.BAND_UNQUALIFIED]} unqualified) and {summary['in_progress']} draft; "
        f"the qualified submission routed to {first_qualified.get('route_target')}"
    )
