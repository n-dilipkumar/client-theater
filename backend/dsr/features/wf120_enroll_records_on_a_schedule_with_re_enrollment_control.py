"""WF-120: enroll records on a schedule with re-enrollment control.

The rules live in :mod:`dsr.schedule_enrollment`, which knows nothing about HTTP.
This module is the four things a feature contributes, and the three things it
must never contribute.

What this module contributes
----------------------------
* The route table, under the prefix this feature owns (``/api/wf-120``).
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

Source: ``docs/research/digital-sales-room-workflows/wf/WF-120.md``; issue 170.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.schedule_enrollment import rules, vocabulary as v
from dsr.schedule_enrollment.engine import ScheduleEnrollmentEngine
from dsr.schedule_enrollment.errors import (
    EnrollConflict,
    EnrollNotFound,
    EnrollValidationError,
)
from dsr.schedule_enrollment.inferences import describe_inferences
from dsr.store import RecordStore

FEATURE = {
    "id": v.FEATURE_ID,
    "ticket": v.TICKET,
    "name": v.NAME,
    "description": (
        "Scans CRM records on a schedule, enrolls the records that match the enrollment "
        "filter, controls re-enrollment, and backfills the records that already match at "
        "publish time. Each enrolled record is held in an enrollment queue for the "
        "configured delay, then its actions fire and the enrollment history is written to "
        "the workflow log."
    ),
    "nav": [{"id": v.FEATURE_ID, "label": "Scheduled enrollment"}],
}

router = APIRouter(prefix="/api/wf-120", tags=["wf-120"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _validation_error(request: Request, exc: EnrollValidationError) -> JSONResponse:
    """422, with one message beside each bad field."""

    return JSONResponse(status_code=422, content={"error": "enrollment_invalid", **exc.as_dict()})


def _not_found(request: Request, exc: EnrollNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(status_code=404, content={"error": "enrollment_not_found", **exc.as_dict()})


def _conflict(request: Request, exc: EnrollConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(status_code=409, content={"error": "enrollment_conflict", **exc.as_dict()})


EXCEPTION_HANDLERS = {
    EnrollValidationError: _validation_error,
    EnrollNotFound: _not_found,
    EnrollConflict: _conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> ScheduleEnrollmentEngine:
    """The enrollment engine on the shared audited store, with the production clock."""

    return ScheduleEnrollmentEngine(store)


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

    return ScheduleEnrollmentEngine.vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call the research left open, with its basis."""

    entries = describe_inferences()
    return {"count": len(entries), "entries": entries}


@router.get("/records")
def records(
    room_id: str | None = Query(default=None),
    object: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """The CRM record rows the schedules scan, normalised."""

    entries = engine.records(room_id=room_id, object=object)
    return {"count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #


@router.get("/schedules")
def list_schedules(
    room_id: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Every schedule declared for this room, newest first."""

    entries = engine.list_schedules(room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.post("/schedules", status_code=201)
def create_schedule(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a schedule: the object, the enrollment filter and the settings."""

    return engine.create_schedule(
        payload, source=_source("POST", "/schedules"), actor=payload.get("actor")
    )


@router.get("/schedules/{schedule_id}")
def read_schedule(schedule_id: str, engine: ScheduleEnrollmentEngine = EngineDep) -> dict[str, Any]:
    """One schedule, with its filter, its settings and its next run."""

    return engine.get_schedule(schedule_id)


@router.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Change the filter, the settings or the actions."""

    return engine.update_schedule(
        schedule_id,
        payload,
        source=_source("PATCH", f"/schedules/{schedule_id}"),
        actor=payload.get("actor"),
    )


@router.delete("/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: str,
    actor: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a schedule. Its enrollments stay, and keep what they did."""

    return engine.delete_schedule(
        schedule_id, source=_source("DELETE", f"/schedules/{schedule_id}"), actor=actor
    )


@router.post("/schedules/{schedule_id}/publish")
def publish_schedule(
    schedule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Publish or unpublish, taking the Review-and-publish backfill choice."""

    published = payload.get("published", True)
    backfill = payload.get("backfill")
    return engine.set_published(
        schedule_id,
        bool(published),
        backfill=str(backfill) if backfill not in (None, "") else None,
        source=_source("POST", f"/schedules/{schedule_id}/publish"),
        actor=payload.get("actor"),
    )


@router.post("/schedules/{schedule_id}/evaluate")
def evaluate_schedule(
    schedule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """What the schedule would do right now. Writes nothing."""

    at = rules.parse_timestamp(payload.get("at"), "at") if payload.get("at") else None
    return engine.evaluate(schedule_id, at_ms=at)


@router.post("/schedules/{schedule_id}/run", status_code=201)
def run_schedule(
    schedule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Run one scheduled scan: unenroll, enroll the matches, then release the queue."""

    at = rules.parse_timestamp(payload.get("at"), "at") if payload.get("at") else None
    return engine.run(
        schedule_id,
        source=_source("POST", f"/schedules/{schedule_id}/run"),
        actor=payload.get("actor"),
        at_ms=at,
    )


# --------------------------------------------------------------------------- #
# Enrollments, runs, the workflow log and notifications
# --------------------------------------------------------------------------- #


@router.get("/enrollments")
def list_enrollments(
    schedule_id: str | None = Query(default=None),
    record_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Every enrollment, newest first, with its state and its history links."""

    entries = engine.enrollments(
        schedule_id=schedule_id, record_id=record_id, room_id=room_id, status=status
    )
    return {"count": len(entries), "entries": entries}


@router.get("/enrollments/{enrollment_id}")
def read_enrollment(
    enrollment_id: str, engine: ScheduleEnrollmentEngine = EngineDep
) -> dict[str, Any]:
    """One enrollment: its queue delay, its state and its fired actions."""

    return engine.get_enrollment(enrollment_id)


@router.get("/runs")
def list_runs(
    schedule_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Every run, newest first, with its counts and its per-record results."""

    entries = engine.list_runs(schedule_id=schedule_id, room_id=room_id, kind=kind)
    return {"count": len(entries), "entries": entries}


@router.get("/runs/{run_id}")
def read_run(run_id: str, engine: ScheduleEnrollmentEngine = EngineDep) -> dict[str, Any]:
    """One run in full: what it scanned, enrolled, released and why anything was skipped."""

    return engine.get_run(run_id)


@router.get("/events")
def list_events(
    schedule_id: str | None = Query(default=None),
    record_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """The workflow log: enrollment history, oldest first, as the research records it."""

    entries = engine.events(
        schedule_id=schedule_id, record_id=record_id, room_id=room_id, kind=kind
    )
    return {"count": len(entries), "entries": entries}


@router.get("/notifications")
def list_notifications(
    schedule_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """Every internal notification the actions recorded, newest first."""

    entries = engine.notifications(schedule_id=schedule_id, room_id=room_id)
    return {"count": len(entries), "entries": entries}


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: ScheduleEnrollmentEngine = EngineDep,
) -> dict[str, Any]:
    """The headline counts a seller needs before opening a schedule."""

    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


#: The contacts the demo scans. Deliberately mixed, because a demo of only green
#: teaches a reviewer nothing: two match the qualification filter, two are
#: customers, and two carry a high risk band.
DEMO_CONTACTS: tuple[dict[str, Any], ...] = (
    {
        "name": "Northwind - Ada Lovelace",
        "stage": "Qualification",
        "lifecycle_stage": "Lead",
        "risk": "high",
        "owner": "dana",
    },
    {
        "name": "Northwind - Grace Hopper",
        "stage": "Qualification",
        "lifecycle_stage": "Lead",
        "risk": "low",
        "owner": "dana",
    },
    {
        "name": "Contoso - Alan Turing",
        "stage": "Discovery",
        "lifecycle_stage": "Customer",
        "risk": "low",
        "owner": "sam",
    },
    {
        "name": "Contoso - Katherine Johnson",
        "stage": "Discovery",
        "lifecycle_stage": "Customer",
        "risk": "medium",
        "owner": "sam",
    },
    {
        "name": "Fabrikam - Tim Berners-Lee",
        "stage": "Proposal",
        "lifecycle_stage": "Lead",
        "risk": "low",
        "owner": "dana",
    },
    {
        "name": "Fabrikam - Margaret Hamilton",
        "stage": "Negotiation",
        "lifecycle_stage": "Lead",
        "risk": "high",
        "owner": "dana",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says a scheduled enrollment must show.

    Four schedules, because the four interesting states need one each: a backfill
    that enrols the records that already match, a future-only publish whose
    matches are deferred and then enrolled on the next run, a queued enrollment
    whose delay holds it and whose unenrollment criteria remove one record while
    the other completes, and a re-enrollment schedule whose completed record
    enrols a second time.

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
    engine = ScheduleEnrollmentEngine(store, now=lambda: now)

    rooms = context.get("room_ids") or []
    room_id = None
    if rooms:
        first = rooms[0]
        room_id = first[0] if isinstance(first, (list, tuple)) else first

    for spec in DEMO_CONTACTS:
        db.create(
            v.CRM_RECORD_COLLECTION,
            {"object": v.DEFAULT_OBJECT, **spec},
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    def make(payload: dict[str, Any]) -> dict[str, Any]:
        return engine.create_schedule(
            {"room_id": room_id, "object": v.DEFAULT_OBJECT, **payload},
            source="seed",
            actor="dana",
        )

    def publish(schedule_id: str, backfill: str) -> dict[str, Any]:
        return engine.set_published(
            schedule_id,
            True,
            backfill=backfill,
            source="seed",
            actor="dana",
            at_ms=rules.parse_timestamp(now, "now"),
        )

    later = rules.parse_timestamp(now + timedelta(hours=2), "at")

    # 1. A backfill publish: the two Qualification leads enrol at once, then a
    #    second run refuses both because the mode is single enrollment.
    qualify = make(
        {
            "name": "Qualify new leads (demo)",
            "filters": [{"field": "stage", "op": v.OP_EQ, "value": "Qualification"}],
            "re_enroll": v.REENROLL_SINGLE,
            "backfill": v.BACKFILL_EXISTING,
        }
    )
    publish(qualify["id"], v.BACKFILL_EXISTING)
    engine.run(qualify["id"], source="seed", actor="dana")

    # 2. A future-only publish: the two customers are deferred at activation and
    #    enrolled by the next run.
    welcome = make(
        {
            "name": "Welcome customers (demo)",
            "filters": [{"field": "lifecycle_stage", "op": v.OP_EQ, "value": "Customer"}],
            "backfill": v.BACKFILL_FUTURE_ONLY,
        }
    )
    publish(welcome["id"], v.BACKFILL_FUTURE_ONLY)
    engine.run(welcome["id"], source="seed", actor="dana")

    # 3. A delayed queue with unenrollment criteria: the two high-risk contacts
    #    queue, one is removed by the criteria, the other's actions fire.
    review = make(
        {
            "name": "High-risk review (demo)",
            "filters": [{"field": "risk", "op": v.OP_EQ, "value": "high"}],
            "unenroll_filter": [{"field": "stage", "op": v.OP_EQ, "value": "Qualification"}],
            "delay_minutes": 60,
            "backfill": v.BACKFILL_EXISTING,
        }
    )
    publish(review["id"], v.BACKFILL_EXISTING)
    engine.run(review["id"], source="seed", actor="dana", at_ms=later)

    # 4. Turn Re-enroll on for the first schedule and run it again: the two
    #    completed Qualification leads enrol a second time. This is the toggle
    #    the Settings tab offers, exercised on the schedule that already refused
    #    them in step 1.
    engine.update_schedule(
        qualify["id"],
        {"re_enroll": v.REENROLL_AFTER_COMPLETION},
        source="seed",
        actor="dana",
    )
    engine.run(qualify["id"], source="seed", actor="dana", at_ms=later)

    # 5. Leave one enrollment held in the queue: a two-hour delay, enrolled by
    #    its own backfill and not yet released.
    drip = make(
        {
            "name": "Onboarding drip (demo)",
            "filters": [{"field": "stage", "op": v.OP_EQ, "value": "Proposal"}],
            "delay_minutes": 120,
            "backfill": v.BACKFILL_EXISTING,
        }
    )
    publish(drip["id"], v.BACKFILL_EXISTING)

    enrollments = store.list(v.ENROLLMENT_COLLECTION, limit=200)
    by_status: dict[str, int] = {}
    for enrollment in enrollments:
        status = str(enrollment["data"].get("status"))
        by_status[status] = by_status.get(status, 0) + 1
    re_enrolled = len([row for row in enrollments if row["data"].get("re_enrollment")])
    tasks = len([row for row in enrollments if row["data"].get("task_id")])

    return (
        f"WF-120 seeded: {len(DEMO_CONTACTS)} CRM contacts and 4 schedules "
        f"(backfill, deferred-then-enrolled, queued with unenrollment, re-enrollment); "
        f"{len(enrollments)} enrollments ({by_status.get(v.STATUS_QUEUED, 0)} queued, "
        f"{by_status.get(v.STATUS_COMPLETED, 0)} completed, "
        f"{by_status.get(v.STATUS_UNENROLLED, 0)} unenrolled), {re_enrolled} re-enrollments, "
        f"{tasks} tasks created through WF-116"
    )
