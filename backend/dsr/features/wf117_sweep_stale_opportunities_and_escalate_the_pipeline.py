"""WF-117: sweep stale opportunities and escalate the pipeline.

The rules live in :mod:`dsr.stale_opportunities`, which knows nothing about
HTTP. This module is the four things a feature contributes, and the three things
it must never contribute.

What this module contributes
----------------------------
* The route table, under the prefix this feature owns (``/api/wf-117``).
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

Source: ``docs/research/digital-sales-room-workflows/wf/WF-117.md``; issue 169.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.stale_opportunities import rules, vocabulary as v
from dsr.stale_opportunities.engine import StaleOpportunitySweepEngine
from dsr.stale_opportunities.errors import SweepConflict, SweepNotFound, SweepValidationError
from dsr.stale_opportunities.inferences import describe_inferences
from dsr.store import RecordStore

FEATURE = {
    "id": v.FEATURE_ID,
    "ticket": v.TICKET,
    "name": v.NAME,
    "description": (
        "Sweeps opportunities that exceed an inactivity rule and escalates them with "
        "an opportunity write, an owner task and an internal notification. The stale "
        "rule reads the two fields the data flow names, and the escalation marks the "
        "opportunity so the sweep does not re-fire on it."
    ),
    "nav": [{"id": v.FEATURE_ID, "label": "Stale opportunities"}],
}

router = APIRouter(prefix="/api/wf-117", tags=["wf-117"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _validation_error(request: Request, exc: SweepValidationError) -> JSONResponse:
    """422, with one message beside each bad field."""

    return JSONResponse(status_code=422, content={"error": "sweep_invalid", **exc.as_dict()})


def _not_found(request: Request, exc: SweepNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(status_code=404, content={"error": "sweep_not_found", **exc.as_dict()})


def _conflict(request: Request, exc: SweepConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(status_code=409, content={"error": "sweep_conflict", **exc.as_dict()})


EXCEPTION_HANDLERS = {
    SweepValidationError: _validation_error,
    SweepNotFound: _not_found,
    SweepConflict: _conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> StaleOpportunitySweepEngine:
    """The sweep engine on the shared audited store, with the production clock."""

    return StaleOpportunitySweepEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every closed word, sourced rule and collection this workflow enforces."""

    return StaleOpportunitySweepEngine.vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call the research left open, with its basis."""

    entries = describe_inferences()
    return {"count": len(entries), "entries": entries}


@router.get("/opportunities")
def opportunities(
    room_id: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """The opportunity rows the data flow evaluates, normalised."""

    entries = engine.opportunities(room_id=room_id)
    return {"count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Sweeps
# --------------------------------------------------------------------------- #


@router.get("/sweeps")
def list_sweeps(
    room_id: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Every sweep declared for this room, newest first."""

    entries = engine.list_sweeps(room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.post("/sweeps", status_code=201)
def create_sweep(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a sweep: the stale rule, its filters and its cadence."""

    return engine.create_sweep(
        payload, source=_source("POST", "/sweeps"), actor=payload.get("actor")
    )


@router.get("/sweeps/{sweep_id}")
def read_sweep(sweep_id: str, engine: StaleOpportunitySweepEngine = EngineDep) -> dict[str, Any]:
    """One sweep, with its rule and its cadence."""

    return engine.get_sweep(sweep_id)


@router.patch("/sweeps/{sweep_id}")
def update_sweep(
    sweep_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Change the rule, the filters, the cadence or the actions."""

    return engine.update_sweep(
        sweep_id,
        payload,
        source=_source("PATCH", f"/sweeps/{sweep_id}"),
        actor=payload.get("actor"),
    )


@router.delete("/sweeps/{sweep_id}")
def delete_sweep(
    sweep_id: str,
    actor: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a sweep. Its runs stay, and keep what they did."""

    return engine.delete_sweep(
        sweep_id, source=_source("DELETE", f"/sweeps/{sweep_id}"), actor=actor
    )


@router.post("/sweeps/{sweep_id}/publish")
def publish_sweep(
    sweep_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Publish a sweep so it may run. The user flow's fourth step."""

    published = payload.get("published", True)
    return engine.set_published(
        sweep_id,
        bool(published),
        source=_source("POST", f"/sweeps/{sweep_id}/publish"),
        actor=payload.get("actor"),
    )


@router.post("/sweeps/{sweep_id}/evaluate")
def evaluate_sweep(
    sweep_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """What the sweep would do right now. Writes nothing."""

    at = rules.parse_timestamp(payload.get("at"), "at") if payload.get("at") else None
    return engine.evaluate(sweep_id, at_ms=at)


@router.post("/sweeps/{sweep_id}/run", status_code=201)
def run_sweep(
    sweep_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Run the sweep: escalate every matched opportunity, then record the run."""

    at = rules.parse_timestamp(payload.get("at"), "at") if payload.get("at") else None
    return engine.sweep(
        sweep_id,
        source=_source("POST", f"/sweeps/{sweep_id}/run"),
        actor=payload.get("actor"),
        at_ms=at,
    )


# --------------------------------------------------------------------------- #
# Runs, escalations and notifications
# --------------------------------------------------------------------------- #


@router.get("/runs")
def list_runs(
    sweep_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Every sweep run, newest first, with its counts and its per-row results."""

    entries = engine.list_runs(sweep_id=sweep_id, room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.get("/runs/{run_id}")
def read_run(run_id: str, engine: StaleOpportunitySweepEngine = EngineDep) -> dict[str, Any]:
    """One run in full: what it scanned, what it escalated and why anything was skipped."""

    return engine.get_run(run_id)


@router.get("/escalations")
def list_escalations(
    sweep_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Every escalation this workflow produced, newest first."""

    entries = engine.list_escalations(sweep_id=sweep_id, room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.get("/notifications")
def list_notifications(
    sweep_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """Every internal notification the sweep recorded, newest first."""

    entries = engine.list_notifications(sweep_id=sweep_id, room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: StaleOpportunitySweepEngine = EngineDep,
) -> dict[str, Any]:
    """The headline counts a seller needs before opening a sweep."""

    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says a stale-opportunity sweep must show.

    The sweep scans every opportunity row in the room, including the ones the
    WF-039 bundle writes, so the demo is deliberately mixed: an opportunity the
    sweep escalates, one it only nudges, one below the threshold, one filtered by
    stage, one filtered by value, one with no activity evidence, and a second run
    that escalates nothing because the first run marked the matches.

    Every character in the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature
    broke the whole seed.
    """

    from datetime import datetime, timedelta, timezone

    store = RecordStore(db)
    now = context.get("now") or datetime.now(timezone.utc)
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    engine = StaleOpportunitySweepEngine(store, now=lambda: now)

    rooms = context.get("room_ids") or []
    room_id = None
    if rooms:
        first = rooms[0]
        room_id = first[0] if isinstance(first, (list, tuple)) else first

    def ago(days: float) -> str:
        return (now - timedelta(days=days)).isoformat()

    specs = [
        {
            "name": "Northwind - Q4 enterprise",
            "stage": "Qualification",
            "stage_entered_at": ago(30),
            "last_activity_at": ago(25),
            "value": 120000,
            "owner": "dana",
        },
        {
            "name": "Contoso - security review",
            "stage": "Discovery",
            "stage_entered_at": ago(10),
            "last_activity_at": ago(9),
            "value": 45000,
            "owner": "sam",
        },
        {
            "name": "Fabrikam - renewal",
            "stage": "Negotiation",
            "stage_entered_at": ago(8),
            "last_activity_at": ago(2),
            "value": 80000,
            "owner": "dana",
        },
        {
            "name": "Adventure Works - pilot",
            "stage": "Closed Won",
            "stage_entered_at": ago(60),
            "last_activity_at": ago(55),
            "value": 20000,
            "owner": "sam",
        },
        {
            "name": "Litware - small deal",
            "stage": "Evaluation",
            "stage_entered_at": ago(40),
            "last_activity_at": ago(35),
            "value": 3000,
            "owner": "dana",
        },
        {
            "name": "Tailspin - regional",
            "stage": "Proposal",
            "value": 60000,
            "owner": "sam",
        },
    ]

    for spec in specs:
        db.create(
            v.OPPORTUNITY_COLLECTION,
            {"object": v.OPPORTUNITY_OBJECT, **spec},
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    sweep = engine.create_sweep(
        {
            "name": "Stale opportunity sweep (demo)",
            "room_id": room_id,
            "inactivity_basis": v.REFERENCE_LATEST_OF_BOTH,
            "threshold_days": v.NUDGE_AFTER_DAYS,
            "cadence": [dict(step) for step in v.DEFAULT_CADENCE],
            "filters": {
                "stages": ["Closed Won", "Closed Lost"],
                "stage_mode": "exclude",
                "value_min": 10000,
                "value_max": 500000,
            },
            "value_bump": 1000,
            "notify_team": "sales",
            "published": True,
        },
        source="seed",
        actor="dana",
    )

    first = engine.sweep(sweep["id"], source="seed", actor="dana")
    later = now + timedelta(hours=1)
    second = engine.sweep(
        sweep["id"], source="seed", actor="dana", at_ms=rules.parse_timestamp(later, "at")
    )

    return (
        f"WF-117 seeded: {len(specs)} opportunities and 1 published sweep "
        f"(threshold {v.NUDGE_AFTER_DAYS} days, cadence {v.NUDGE_AFTER_DAYS}/{v.ESCALATE_AFTER_DAYS}); "
        f"run 1 escalated {first['escalated']} and skipped {first['skipped']}; "
        f"run 2 escalated {second['escalated']} and reported "
        f"{second['by_reason'].get(v.REASON_ALREADY_ESCALATED, 0)} as already escalated"
    )
