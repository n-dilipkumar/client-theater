"""WF-137: version control, brand governance and post-sale room continuity.

The rules live in :mod:`dsr.competitive_baseline.governance`, which knows nothing
about HTTP. This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns on ``/api/wf-137``;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary and the inferences, served from one place so the page cannot
  drift from the rules that validate it.

A publish is snapshotted into an immutable version; a room is brought onto the
brand token set and reports every off-brand finding; and at closed-won the room
is repurposed, not rebuilt, into a phased onboarding portal. The room's own
retention policy lives in ``records.data``, so a team sets a window with no
migration and no coordination with anyone.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-137.md``, quoted in
full on issue #185.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.competitive_baseline import governance as rules
from dsr.competitive_baseline.governance import (
    GovernanceBook,
    GovernanceError,
    GovernanceRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-137-governance",
    "ticket": "WF-137",
    "name": "Version control, brand governance and post-sale room continuity",
    "description": (
        "Every publish is snapshotted into an immutable version a seller can preview, "
        "restore or branch. The brand token set is applied to each room so an off-brand "
        "room is a named finding. At closed-won the room is repurposed, not rebuilt, "
        "into a phased onboarding portal with its own retention policy."
    ),
    "nav": [{"id": "governance", "label": "Governance"}],
}

router = APIRouter(prefix="/api/wf-137", tags=["WF-137"])


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> GovernanceBook:
    """The book, built on the shared audited store, per request."""
    return GovernanceBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _governance_error(request: Request, exc: GovernanceError) -> JSONResponse:
    """A refusal this workflow made, answered with the status it carries.

    One handler for the whole hierarchy. ``GovernanceError`` is defined in
    :mod:`dsr.competitive_baseline.governance`, so a handler for it cannot
    intercept an unrelated error anywhere else - which is why the builtins are
    not claimed.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


EXCEPTION_HANDLERS = {GovernanceError: _governance_error}


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every value this workflow accepts, and the decision behind each."""
    return rules.vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every decision the research does not make, and how to change each one."""
    return rules.inferences()


@router.get("/summary")
def summary(book: GovernanceBook = BookDep) -> dict[str, Any]:
    """Counts for the whole governance surface."""
    return book.summary()


# --------------------------------------------------------------------------- #
# Brand governance
# --------------------------------------------------------------------------- #


@router.get("/brand")
def brand_board(book: GovernanceBook = BookDep) -> dict[str, Any]:
    """The brand token set and every governed room's compliance against it."""
    return book.brand_board()


@router.post("/brand")
def write_brand(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Replace the brand token set. Each write is a new version."""
    return book.write_brand_set(payload, actor=actor, source=_source("POST", "/brand"))


@router.get("/rooms/{room_id}/brand")
def room_brand(room_id: str, book: GovernanceBook = BookDep) -> dict[str, Any]:
    """One room's branding, and every finding that keeps it off brand."""
    return book.brand_state(room_id)


@router.post("/rooms/{room_id}/brand/apply")
def apply_room_brand(
    room_id: str,
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Apply the approved tokens to a room. Idempotent, so a reload writes nothing."""
    return book.apply_brand(
        room_id, actor=actor, source=_source("POST", "/rooms/{room_id}/brand/apply")
    )


# --------------------------------------------------------------------------- #
# Versioning: publish, checkpoint, preview, restore, branch
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/history")
def room_history(room_id: str, book: GovernanceBook = BookDep) -> dict[str, Any]:
    """Every version of a room, oldest first, with the head marked."""
    return book.history(room_id)


@router.post("/rooms/{room_id}/snapshots", status_code=201)
def snapshot_room(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    trigger: str = Query(default="publish", description="publish or session"),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Snapshot a publish (automatic) or an editing-session checkpoint."""
    source = _source("POST", "/rooms/{room_id}/snapshots")
    if trigger == "session":
        return book.checkpoint(room_id, payload, actor=actor, source=source)
    return book.publish(room_id, payload, actor=actor, source=source)


@router.get("/rooms/{room_id}/snapshots/{version}")
def read_snapshot(room_id: str, version: int, book: GovernanceBook = BookDep) -> dict[str, Any]:
    """Preview what restoring a version would change, field by field."""
    return book.preview(room_id, version)


@router.post("/rooms/{room_id}/snapshots/{version}/restore")
def restore_snapshot(
    room_id: str,
    version: int,
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Restore a version by writing it as the next version, never editing one."""
    return book.restore(
        room_id,
        version,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/snapshots/{version}/restore"),
    )


@router.post("/rooms/{room_id}/snapshots/{version}/branch", status_code=201)
def branch_snapshot(
    room_id: str,
    version: int,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Seed a new room from an earlier version, so the two lines stay separate."""
    return book.branch(
        room_id,
        version,
        payload,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/snapshots/{version}/branch"),
    )


# --------------------------------------------------------------------------- #
# Plans and the closed-won handoff
# --------------------------------------------------------------------------- #


@router.get("/plans")
def list_plans(book: GovernanceBook = BookDep) -> dict[str, Any]:
    """Every onboarding plan template, with its phases."""
    plans = book.list_plans()
    return {"count": len(plans), "plans": plans}


@router.post("/plans", status_code=201)
def write_plan(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Create or replace an onboarding plan template by its key."""
    return book.write_plan(payload, actor=actor, source=_source("POST", "/plans"))


@router.get("/rooms/{room_id}/onboarding")
def room_onboarding(room_id: str, book: GovernanceBook = BookDep) -> dict[str, Any]:
    """The phased onboarding plan instantiated on a room, and its progress."""
    return book.plan_state(room_id)


@router.post("/rooms/{room_id}/repurpose")
def repurpose_room(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Repurpose the room at closed-won: keep the record, add the phases."""
    return book.repurpose(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/repurpose")
    )


@router.post("/rooms/{room_id}/onboarding/advance")
def advance_phase(
    room_id: str,
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Close the active phase and start the next, if its milestones are done."""
    return book.advance_phase(
        room_id, actor=actor, source=_source("POST", "/rooms/{room_id}/onboarding/advance")
    )


@router.post("/rooms/{room_id}/onboarding/milestones/complete")
def complete_milestone(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Close one milestone. The body names ``phase_key`` and ``milestone_key``."""
    return book.complete_milestone(
        room_id,
        _text(payload.get("phase_key")),
        _text(payload.get("milestone_key")),
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/onboarding/milestones/complete"),
    )


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


@router.get("/retention")
def retention_board(book: GovernanceBook = BookDep) -> dict[str, Any]:
    """Every retention policy and what the schedule owes it."""
    return book.retention_board()


@router.get("/rooms/{room_id}/retention")
def room_retention(room_id: str, book: GovernanceBook = BookDep) -> dict[str, Any]:
    """One room's retention window and whether it owes an archive or a delete."""
    return book.retention_state(room_id)


@router.post("/rooms/{room_id}/retention")
def set_retention(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Set or override a room's retention windows, stored as ordinary JSON."""
    return book.write_retention(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/retention")
    )


@router.post("/rooms/{room_id}/retention/apply")
def apply_room_retention(
    room_id: str,
    actor: str | None = Query(default=None),
    book: GovernanceBook = BookDep,
) -> dict[str, Any]:
    """Apply what the schedule owes: archive, then a later soft delete."""
    return book.apply_retention(
        room_id, actor=actor, source=_source("POST", "/rooms/{room_id}/retention/apply")
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: How far before the seed clock the demo deal closed. Far enough back that the
#: derived archive window has already opened, so the seeder can exercise it.
CLOSED_DAYS_AGO = 200

#: How long before the seed clock a second room's lifecycle ended, so a short
#: retention window on it is already due.
RETENTION_ENDED_DAYS_AGO = 60


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the governance surface with the states a happy path would not teach.

    The demo deliberately carries the states the seeder exists to print:

    * a room brought onto the brand set, after it started off brand;
    * a history of two publishes and one editing-session checkpoint - the two
      distinct triggers the evidence names - with an earlier version restored;
    * a room repurposed into a four-phase onboarding portal, then advanced from
      its first phase;
    * a retention policy on a second room whose archive window has opened, then
      the archive it owes;
    * two refused actions, printed as non-successes rather than hidden.

    Every write goes through the audited store, so the demo cannot show a shape
    the workflow would not produce.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return "0 governance rows (no rooms to govern)"
    book = GovernanceBook(RecordStore(db))
    now = context["now"]
    room_a = rooms[0][0]
    room_b = rooms[2][0] if len(rooms) > 2 else (rooms[1][0] if len(rooms) > 1 else room_a)

    # 1. The room starts off brand. Applying the set is the state that clears it.
    findings_before = book.brand_state(room_a)["findings"]
    applied = book.apply_brand(room_a, actor="enablement", source="seed")

    # 2. A publish, an editing-session checkpoint, a second publish, a restore.
    book.publish(
        room_a,
        {
            "label": "Overview deck shared",
            "summary": "First publish of the room.",
            "content": {"sections": ["overview"]},
        },
        actor="dana",
        source="seed",
    )
    book.checkpoint(
        room_a,
        {
            "label": "Editing session",
            "summary": "A checkpoint a seller took on purpose.",
            "content": {"sections": ["overview", "pricing"]},
        },
        actor="dana",
        source="seed",
    )
    book.publish(
        room_a,
        {
            "label": "Pricing one-pager added",
            "summary": "Second publish of the room.",
            "content": {"sections": ["overview", "pricing", "security"]},
        },
        actor="dana",
        source="seed",
    )
    book.restore(room_a, 1, actor="dana", source="seed")

    # 3. Closed-won: repurpose the room, then advance from the first phase.
    closed_at = (now - timedelta(days=CLOSED_DAYS_AGO)).isoformat()
    plan = book.repurpose(room_a, {"closed_at": closed_at}, actor="dana", source="seed")
    book.complete_milestone(room_a, "kickoff", "introductions", actor="dana", source="seed")
    book.complete_milestone(room_a, "kickoff", "success_criteria", actor="cs", source="seed")
    advanced = book.advance_phase(room_a, actor="cs", source="seed")

    # 4. Retention: a second room with a window that has already opened.
    book.write_retention(
        room_b,
        {
            "archive_after_days": 30,
            "delete_after_days": 365,
            "lifecycle_ended_at": (now - timedelta(days=RETENTION_ENDED_DAYS_AGO)).isoformat(),
            "legal_basis": "signed contract",
        },
        actor="cs",
        source="seed",
    )
    archived = book.apply_retention(room_b, actor="cs", source="seed")

    # 5. The non-success states this seeder exists to print.
    refused = 0
    try:
        book.advance_phase(room_a, actor="cs", source="seed")
    except GovernanceRefused:
        refused += 1
    try:
        book.apply_retention(room_b, actor="cs", source="seed")
    except GovernanceRefused:
        refused += 1

    history = book.history(room_a)
    counts = book.summary()
    active = advanced["active_phase"]
    active_key = active["phase_key"] if active else "none"
    return (
        f"{history['count']} versions on room A (head v{history['head_version']}, 1 restored), "
        f"{counts['snapshots']} snapshot(s) across {counts['rooms_tracked']} governed room(s); "
        f"brand set v{counts['brand_set_version']} cleared {len(findings_before)} finding(s), "
        f"room A compliant {applied['compliant']}; "
        f"repurposed into {len(plan['phases'])} phases, active {active_key} "
        f"({advanced['lifecycle_state']}); "
        f"retention applied on room B ({archived['applied']}); "
        f"{refused} refused action(s) printed"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
