"""WF-135: capture the deal conversation into the CRM through one entry.

The rules live in :mod:`dsr.competitive_baseline.conversation_intelligence` and are
not restated here. This module is the three things a feature contributes, and the
three things it must never contribute.

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
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

Transcription and extraction arrive as data
-------------------------------------------

The research is explicit that this is a glue problem with no single open-source
answer. The transcription (self-hosted Whisper) and the extraction (an LLM) are
not built here: the caller posts the transcript reference and the four extraction
outputs, and this feature validates, stores, fans out and reports agreement. The
domain module therefore imports and tests with no network.

Route parameter names the verifier knows
----------------------------------------

``tools/verify_all_routes.py`` substitutes a value for every path placeholder it
knows and leaves an unknown one in the URL, which then fails as a request that
never answered. So this feature uses ``room_id``, ``interaction_id`` and
``row_id``, all of which the verifier knows, rather than inventing a name it
would leave in place.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.competitive_baseline import conversation_intelligence as ci
from dsr.competitive_baseline.conversation_intelligence import (
    CaptureError,
    CaptureNotFound,
    CaptureRefused,
    ConversationIntelligenceEngine,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-135-conversation-intelligence",
    "ticket": "WF-135",
    "name": "Conversation intelligence",
    "description": (
        "Capture a recorded deal conversation into the CRM through one entry. The "
        "caller posts the transcript reference and the four extraction outputs (a "
        "summary, the next steps, the requirements and the MEDDICC evidence); one "
        "write fans them out to the room's notes, a handoff packet and the CRM "
        "custom fields, and the workflow reports whether the three destinations "
        "agree."
    ),
    "nav": [{"id": "wf-135-conversation-intelligence", "label": "Conversation intelligence"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-135", tags=["WF-135"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


def _engine(store: RecordStore) -> ConversationIntelligenceEngine:
    """The engine, with the clock left at its production default."""
    return ConversationIntelligenceEngine(store)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _capture_error(request: Request, exc: CaptureError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "capture_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _capture_refused(request: Request, exc: CaptureRefused) -> JSONResponse:
    """409 for a change the capture's current state will not accept.

    409 rather than 400 because nothing about the request is malformed: an
    incomplete fan-out is a state, and a client that retries the same call gets
    the same answer. ``reason`` is a stable token the page branches on.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "capture_conflict", "reason": exc.reason, "detail": str(exc)},
    )


def _capture_not_found(request: Request, exc: CaptureNotFound) -> JSONResponse:
    """404 for a capture or a fan-out row that does not exist."""
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such capture or destination."},
    )


EXCEPTION_HANDLERS = {
    CaptureError: _capture_error,
    CaptureRefused: _capture_refused,
    CaptureNotFound: _capture_not_found,
}


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The headline numbers. Reads only."""
    return _engine(store).summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it."""
    return ci.vocabulary()


@router.get("/coverage")
def coverage(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """MEDDICC coverage across the captures in scope, with no score."""
    return _engine(store).coverage(room_id)


# --------------------------------------------------------------------------- #
# Captures: one entry
# --------------------------------------------------------------------------- #


@router.get("/captures")
def list_captures(
    room_id: str | None = Query(None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Every capture, newest state included, optionally scoped to a room."""
    captures = _engine(store).list_captures(room_id)
    return {"room_id": room_id, "captures": captures, "count": len(captures)}


@router.post("/captures")
def create_capture(
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record one conversation and fan it out to all three destinations.

    The extraction arrives in the body. The fan-out writes the room's notes, the
    handoff packet and the CRM custom fields, then reads them back.
    """
    room_id = str(payload.get("room_id") or payload.get(ci.ROOM_REF) or "").strip()
    return _engine(store).create_capture(
        room_id, payload, source=_source("POST", "/captures"), actor="rep"
    )


@router.get("/rooms/{room_id}/captures")
def list_room_captures(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every capture attached to one room."""
    captures = _engine(store).list_captures(room_id)
    return {"room_id": room_id, "captures": captures, "count": len(captures)}


@router.get("/captures/{interaction_id}")
def read_capture(interaction_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One capture, with its extraction, its fan-out rows and whether they agree."""
    return _engine(store).read_capture(interaction_id)


@router.get("/captures/{interaction_id}/destinations")
def list_destinations(interaction_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The fan-out rows one capture wrote: the room's notes, packet and CRM fields."""
    engine = _engine(store)
    capture = engine.read_capture(interaction_id)
    return {
        "capture_id": interaction_id,
        "destinations": capture["destinations"],
        "agreement": capture["agreement"],
    }


# --------------------------------------------------------------------------- #
# Destinations: reporting the outbound outcome
# --------------------------------------------------------------------------- #


@router.get("/destinations/{row_id}")
def read_destination(row_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One fan-out row."""
    return _engine(store).read_destination(row_id)


@router.post("/destinations/{row_id}/report")
def report_destination(
    row_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Report the outbound outcome of one destination and read it back.

    A CRM field reports a CRM outcome (created, updated, submitted, failed,
    rejected, rolled_back); the two local destinations report written or failed.
    A failed outcome makes the capture partial.
    """
    return _engine(store).report_destination(
        row_id, payload, source=_source("POST", "/destinations/{row_id}/report"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the capture states the researched flow says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a capture, a
    fan-out row or an agreed state the HTTP routes would not produce.
    ``source="seed"`` rather than a route string: no route served this, and
    claiming one would be the lie the contract's write rule exists to prevent.

    The states seeded, and why each is here:

    * a **complete** capture whose three destinations all agree, with the CRM row
      reported ``updated``, so the one-entry fan-out is visible end to end;
    * a **second** capture whose CRM row was reported ``failed``, so a partial
      capture is visible rather than only the happy path;
    * a **third** capture from an email thread with thinner MEDDICC evidence, so
      the coverage report has both covered and missing letters;
    * a **plan reference** on the first capture, so the MAP is on the record.

    Every character of the returned string is ASCII, so the seeder can print it on
    a Windows console. A single non-cp1252 character in one feature broke the
    whole seeder once.
    """
    store = RecordStore(db)
    now = context["now"]
    room_ids = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id
    engine = ConversationIntelligenceEngine(store, now=lambda: now)
    source, actor = "seed", "dana"

    # One complete capture, fanned out to all three destinations.
    complete = engine.create_capture(
        room_id,
        {
            "source_kind": "call",
            "occurred_at": now.isoformat(),
            "transcript_ref": "store://transcripts/northwind-discovery-2026-05",
            "plan_ref": "wf124_plan_northwind",
            "summary": (
                "Northwind confirmed a Q3 rollout, asked for a security review and named "
                "their CFO as the budget holder."
            ),
            "next_steps": [
                {"text": "Send the SOC 2 report", "owner": "dana"},
                {"text": "Book the security review", "owner": "buyer"},
            ],
            "requirements": [
                "SOC 2 Type II report",
                "Single sign-on through their identity provider",
            ],
            "meddicc": {
                "metrics": "A 30 percent cut in onboarding time is the headline number.",
                "economic_buyer": "Their CFO signs anything above 50 thousand.",
                "decision_criteria": "Security, then price, then the rollout plan.",
                "decision_process": "Security review, then legal, then signature.",
                "identify_pain": "Onboarding takes eleven days today.",
                "champion": "Priya sponsors the rollout internally.",
                "competition": "One incumbent is being evaluated in parallel.",
            },
        },
        source=source,
        actor=actor,
    )
    crm_row = next(row for row in complete["destinations"] if row["kind"] == ci.CRM_FIELD)
    engine.report_destination(
        crm_row["id"],
        {"outcome": "updated", "note": "The CRM accepted the field update."},
        source=source,
        actor=actor,
    )

    # A second capture whose CRM row failed, so a partial capture is visible.
    partial = engine.create_capture(
        room_id,
        {
            "source_kind": "meeting",
            "occurred_at": now.isoformat(),
            "summary": "A technical deep dive; the security answer is still open.",
            "next_steps": ["Answer the retention question"],
            "requirements": ["Data retention of 90 days or less"],
            "meddicc": {"identify_pain": "Retention policy blocks the pilot."},
        },
        source=source,
        actor=actor,
    )
    partial_crm = next(row for row in partial["destinations"] if row["kind"] == ci.CRM_FIELD)
    engine.report_destination(
        partial_crm["id"],
        {"outcome": "failed", "note": "The CRM refused: a required field was empty."},
        source=source,
        actor=actor,
    )

    # A third capture, from an email thread and with thinner evidence.
    third = engine.create_capture(
        room_two,
        {
            "source_kind": "email_thread",
            "occurred_at": now.isoformat(),
            "summary": "The buyer restated the two must-have requirements by email.",
            "next_steps": ["Confirm the seat count"],
            "requirements": ["Role-based access control"],
            "meddicc": {"metrics": "They measure onboarding in working days."},
        },
        source=source,
        actor=actor,
    )
    counted = engine.summary()
    return (
        f"{counted['captures']} captures "
        f"({counted['agreed']} agreed, {counted['partial']} partial), "
        f"{counted['destinations']} fan-out rows "
        f"({counted['room_note']} notes, {counted['handoff_packet']} packets, "
        f"{counted['crm_field']} CRM field rows), {counted['failed']} failed, "
        f"{counted['plan_linked']} with a plan reference, "
        f"{len(ci.covered_letters(third['extraction']))} of "
        f"{len(ci.MEDDICC_KEYS)} MEDDICC letters on the last capture"
    )
