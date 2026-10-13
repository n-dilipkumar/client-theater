"""WF-116: create an automated seller follow-up task with a reminder.

The rules live in :mod:`dsr.seller_tasks`, which knows nothing about HTTP. This
module is the four things a feature contributes, and the three things it must
never contribute.

What this module contributes
----------------------------
* The route table, under the prefix this feature owns (``/api/wf-116``).
* The error-to-response mapping, exported as :data:`EXCEPTION_HANDLERS` because
  FastAPI accepts handlers on the app object only.
* The demo rows, as ``seed(db, context)`` rather than an edit to the shared
  ``backend/seed.py``.
* The vocabulary and the inference list the page renders.

What it must never contribute
-----------------------------
* No shared file: ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``seed.py``, ``App.jsx``, ``main.jsx`` ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-116.md``; issue 186.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.seller_tasks import rules, vocabulary as v
from dsr.seller_tasks.engine import SellerTaskEngine
from dsr.seller_tasks.errors import TaskConflict, TaskNotFound, TaskValidationError
from dsr.seller_tasks.inferences import describe_inferences
from dsr.store import RecordStore

FEATURE = {
    "id": v.FEATURE_ID,
    "ticket": v.TICKET,
    "name": v.NAME,
    "description": (
        "Create a follow-up task with a due time and a reminder. The task attaches "
        "to a contact or stands alone as an internal task. Its lifecycle fires the "
        "Task Added, Task Reminder and Task Completed triggers."
    ),
    "nav": [{"id": v.FEATURE_ID, "label": "Follow-up tasks"}],
}

router = APIRouter(prefix="/api/wf-116", tags=["WF-116"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _validation_error(request: Request, exc: TaskValidationError) -> JSONResponse:
    """422, with one message beside each bad field."""

    return JSONResponse(status_code=422, content={"error": "task_invalid", **exc.as_dict()})


def _not_found(request: Request, exc: TaskNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""

    return JSONResponse(status_code=404, content={"error": "task_not_found", **exc.as_dict()})


def _conflict(request: Request, exc: TaskConflict) -> JSONResponse:
    """409, with a stable reason token the page branches on."""

    return JSONResponse(status_code=409, content={"error": "task_conflict", **exc.as_dict()})


EXCEPTION_HANDLERS = {
    TaskValidationError: _validation_error,
    TaskNotFound: _not_found,
    TaskConflict: _conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> SellerTaskEngine:
    """The engine on the shared audited store, with the production clock."""

    return SellerTaskEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every closed word, signal rule and sourced quote this workflow enforces."""

    return SellerTaskEngine.vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call the research left open, with its basis."""

    entries = describe_inferences()
    return {"count": len(entries), "entries": entries}


@router.get("/contacts")
def contacts(
    room_id: str | None = Query(default=None),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Contacts a task may attach to, read from WF-039's collection."""

    entries = engine.list_contacts(room_id=room_id)
    return {"count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """The headline counts a seller needs before opening a task."""

    return engine.summary(room_id=room_id)


@router.get("/tasks")
def list_tasks(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    assignee: str | None = Query(default=None),
    contact_id: str | None = Query(default=None),
    attachment: str | None = Query(default=None),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Every task, newest first, with its derived facts."""

    entries = engine.list_tasks(
        room_id=room_id,
        status=status,
        assignee=assignee,
        contact_id=contact_id,
        attachment=attachment,
    )
    return {"count": len(entries), "entries": entries}


@router.get("/tasks/{task_id}")
def read_task(task_id: str, engine: SellerTaskEngine = EngineDep) -> dict[str, Any]:
    """One task with its lifecycle events and its notifications."""

    task = engine.get(task_id)
    task["events"] = engine.events(task_id)
    task["notifications"] = engine.notifications(task_id)
    return task


@router.get("/tasks/{task_id}/events")
def task_events(
    task_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """The Task Added, Task Reminder and Task Completed trail, oldest first."""

    engine.require(task_id)
    entries = engine.events(task_id, limit=limit)
    return {"count": len(entries), "entries": entries}


@router.get("/reminders/due")
def reminders_due(
    at: int | None = Query(default=None, description="Unix epoch milliseconds"),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Open tasks whose reminder time has been reached and not yet fired."""

    entries = engine.due_reminders(at)
    return {"count": len(entries), "entries": entries}


@router.get("/bin")
def recycling_bin(
    room_id: str | None = Query(default=None),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """The soft-deleted tasks. A delete is soft, so the row stays readable."""

    entries = engine.bin(room_id=room_id)
    return {"count": len(entries), "entries": entries}


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


@router.post("/tasks", status_code=201)
def create_task(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Create a task, attached to a contact or standalone, with its reminder."""

    return engine.create(payload, source=_source("POST", "/tasks"), actor=payload.get("actor"))


@router.post("/signals/{signal}", status_code=201)
def create_from_signal(
    signal: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """The workflow input: one engagement signal creates one deterministic task."""

    return engine.create_from_signal(
        signal,
        payload,
        source=_source("POST", f"/signals/{signal}"),
        actor=payload.get("actor"),
    )


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Change the subject, body, due time, reminder, priority, type or assignee."""

    return engine.update(
        task_id,
        payload,
        source=_source("PATCH", f"/tasks/{task_id}"),
        actor=payload.get("actor"),
    )


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Mark a task completed. This fires the Task Completed trigger."""

    return engine.complete(
        task_id,
        source=_source("POST", f"/tasks/{task_id}/complete"),
        actor=payload.get("actor"),
    )


@router.post("/tasks/{task_id}/reminder")
def fire_reminder(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Fire a task's reminder. This writes the Task Reminder trigger event."""

    at = rules.parse_timestamp(payload.get("at"), "at") if payload.get("at") else None
    return engine.fire_reminder(
        task_id,
        source=_source("POST", f"/tasks/{task_id}/reminder"),
        actor=payload.get("actor"),
        at_ms=at,
    )


@router.post("/tasks/{task_id}/attach")
def attach_contact(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Attach a standalone task to a contact."""

    contact_id = str(payload.get("contact_id") or "").strip()
    if not contact_id:
        raise TaskValidationError({"contact_id": "a contact is required"})
    return engine.attach(
        task_id,
        contact_id,
        source=_source("POST", f"/tasks/{task_id}/attach"),
        actor=payload.get("actor"),
    )


@router.post("/tasks/{task_id}/detach")
def detach_contact(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Turn an attached task into a standalone internal task."""

    return engine.detach(
        task_id,
        source=_source("POST", f"/tasks/{task_id}/detach"),
        actor=payload.get("actor"),
    )


@router.post("/tasks/{task_id}/pin")
def pin_task(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Record the pin intent on the task row. No foreign object is written."""

    engagement_id = str(payload.get("engagement_id") or "").strip()
    if not engagement_id:
        raise TaskValidationError({"engagement_id": "an engagement id is required"})
    return engine.pin(
        task_id,
        engagement_id,
        source=_source("POST", f"/tasks/{task_id}/pin"),
        actor=payload.get("actor"),
    )


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    actor: str | None = Query(default=None),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a task into the recycling bin. The row stays readable."""

    return engine.soft_delete(task_id, source=_source("DELETE", f"/tasks/{task_id}"), actor=actor)


@router.post("/tasks/{task_id}/restore")
def restore_task(
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Restore a task from the recycling bin."""

    return engine.restore(
        task_id,
        source=_source("POST", f"/tasks/{task_id}/restore"),
        actor=payload.get("actor"),
    )


@router.post("/bin/purge")
def purge_bin(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SellerTaskEngine = EngineDep,
) -> dict[str, Any]:
    """Hard-delete binned rows past the retention window."""

    now_ms = rules.parse_timestamp(payload.get("now"), "now") if payload.get("now") else None
    return engine.purge_bin(
        source=_source("POST", "/bin/purge"),
        actor=payload.get("actor"),
        now_ms=now_ms,
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says a follow-up task must show.

    Five tasks, and not all of them succeed, because a demo containing only the
    happy path teaches a reviewer nothing:

    * a contact-attached task with a future reminder;
    * a standalone task with an internal notification;
    * a completed task, so the Task Completed trigger is visible;
    * an overdue task whose reminder time has passed, so it appears in the due
      list and can fire the Task Reminder trigger;
    * a binned task, so the recycling bin is not empty.

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
    engine = SellerTaskEngine(store, now=lambda: now)

    rooms = context.get("room_ids") or []
    room_id = None
    if rooms:
        first = rooms[0]
        room_id = first[0] if isinstance(first, (list, tuple)) else first

    contact = db.create(
        rules.CONTACT_COLLECTION,
        {
            "object": rules.CONTACT_OBJECT,
            "reference_id": "seed-contact",
            "body": {"email": "dana@northwind.example"},
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    def iso(delta: timedelta) -> str:
        return (now + delta).isoformat()

    engine.create(
        {
            "subject": "Email Dana the revised pricing",
            "body": "Attach the updated price book and confirm the volume tier.",
            "due_at": iso(timedelta(days=2)),
            "reminder_at": iso(timedelta(days=1)),
            "priority": v.PRIORITY_HIGH,
            "task_type": v.TYPE_EMAIL,
            "contact_id": contact["id"],
            "assignee": "dana",
            "room_id": room_id,
        },
        source="seed",
        actor="dana",
    )
    engine.create(
        {
            "subject": "Internal review of the Northwind deal",
            "body": "No contact. This is a standalone internal task.",
            "due_at": iso(timedelta(days=3)),
            "reminder_at": iso(timedelta(days=2)),
            "priority": v.PRIORITY_MEDIUM,
            "task_type": v.TYPE_TODO,
            "assignee": "sam",
            "notify": True,
            "room_id": room_id,
        },
        source="seed",
        actor="dana",
    )
    done = engine.create(
        {
            "subject": "Call Northwind about the pilot",
            "due_at": iso(timedelta(days=1)),
            "priority": v.PRIORITY_LOW,
            "task_type": v.TYPE_CALL,
            "contact_id": contact["id"],
            "assignee": "dana",
            "room_id": room_id,
        },
        source="seed",
        actor="dana",
    )
    engine.complete(done["id"], source="seed", actor="dana")
    overdue = engine.create(
        {
            "subject": "Chase the signed order form",
            "due_at": iso(timedelta(hours=-1)),
            "reminder_at": iso(timedelta(hours=-2)),
            "priority": v.PRIORITY_HIGH,
            "task_type": v.TYPE_TODO,
            "assignee": "sam",
            "room_id": room_id,
        },
        source="seed",
        actor="dana",
    )
    engine.fire_reminder(overdue["id"], source="seed", actor="dana")
    binned = engine.create(
        {
            "subject": "Duplicate follow-up (binned)",
            "due_at": iso(timedelta(days=4)),
            "assignee": "dana",
            "room_id": room_id,
        },
        source="seed",
        actor="dana",
    )
    engine.soft_delete(binned["id"], source="seed", actor="dana")

    counted = engine.summary(room_id)
    return (
        "WF-116 seeded: 5 tasks (1 attached with a reminder, 1 standalone with a "
        "notification, 1 completed, 1 overdue with a fired reminder, 1 binned); "
        f"{counted['open']} open and {counted['reminders_due']} reminders due in the room"
    )
