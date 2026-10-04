"""WF-124: run a Mutual Action Plan inside the room as a task graph.

The rules live in :mod:`dsr.mutual_action.plan` and are not restated here. This
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

A note on error types
---------------------

Three types are mapped here, all declared by :mod:`dsr.mutual_action.plan` and
raised by nothing else in the product. None of them is a builtin, deliberately: a
handler registered for ``ValueError`` or ``PermissionError`` would intercept those
exceptions across the whole product.

A note on the seller's and the buyer's routes
---------------------------------------------

The seller board and the buyer's plan are separate routes, and the audience is a
parameter rather than an authenticated identity. The research describes the plan
as "a shared roadmap" inside the room, and the repository has no session concept
for a room member to borrow. What protects an internal-only task is therefore not
who asked but which surface they asked for: :func:`PlanEngine.visible_plan`
builds the buyer's list through :func:`~dsr.mutual_action.plan.visible_tasks`, and
a task marked ``internal`` is absent from that response rather than hidden by the
UI. Recording the audience on the row means the decision is auditable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.mutual_action import plan as mutual
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-124-native-mutual-action-plan-execution",
    "ticket": "WF-124",
    "name": "Native Mutual Action Plan execution",
    "description": (
        "Run a mutual action plan in the room as a task graph with named owners on "
        "both sides, due dates, dependencies and status. Internal-only tasks never "
        "reach the buyer's view, overdue items escalate, and a closed plan carries "
        "its tasks into the implementation plan rather than ending the deal."
    ),
    "nav": [{"id": "wf-124-map", "label": "Action plan"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-124", tags=["WF-124"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _plan_error(request: Request, exc: mutual.PlanError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "plan_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _plan_blocked(request: Request, exc: mutual.PlanBlocked) -> JSONResponse:
    """409 for a change the graph will not accept.

    409 rather than 400 because nothing about the request is malformed. The graph
    is in a state that makes the change wrong, which is a conflict with the plan's
    current shape, and a client that retries the same call will get the same answer.
    ``reason`` is a stable token the UI branches on.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "plan_conflict", "reason": exc.reason, "detail": str(exc)},
    )


def _plan_not_found(request: Request, exc: mutual.PlanNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a buyer may not see.

    An internal-only task read by the buyer returns this rather than a 403, because
    a 403 would confirm that something exists behind the id. "No such task" is the
    only answer that does not leak.
    """
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such plan or task."},
    )


EXCEPTION_HANDLERS = {
    mutual.PlanError: _plan_error,
    mutual.PlanBlocked: _plan_blocked,
    mutual.PlanNotFound: _plan_not_found,
}


def _engine(store: RecordStore) -> mutual.PlanEngine:
    """The engine, with the clock left at its production default.

    The real clock here rather than a request-supplied value: a caller that could
    pass its own ``now`` could pass a past one and make an overdue task look
    not-yet-due. Tests inject the clock into the engine directly.
    """
    return mutual.PlanEngine(store)


def _audience(value: Any) -> str:
    """Read the audience, defaulting to the buyer side.

    The default is the buyer because that is the audience the visibility rule
    protects. See :func:`dsr.mutual_action.plan.as_audience` for the derivation.
    """
    return mutual.as_audience(value)


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""
    return _engine(store).summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, so the UI need not hard-code it.

    Includes what is deliberately absent, under ``not_implemented``. A reviewer who
    cannot tell "we decided not to" from "we forgot to" has to read the source, and
    that is a cost paid every time the question comes up.
    """
    return mutual.vocabulary()


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


@router.post("/templates")
def create_template(
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Write a reusable task template.

    A template carries the task rows that get copied onto a plan when it is
    instantiated, which is the "auto-generation of the plan from a template"
    automation the research names.
    """
    return _engine(store).create_template(
        payload, source=_source("POST", "/templates"), actor="rep"
    )


@router.get("/templates")
def list_templates(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every template, with its task rows."""
    return {"templates": _engine(store).list_templates()}


@router.get("/templates/{template_id}")
def read_template(template_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One template, with its task rows and the refs its edges name."""
    return _engine(store).read_template(template_id)


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/plans")
def create_plan(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create a plan in a room, from a template or from its own task rows.

    Instantiating a template copies its task rows onto real tasks and rewrites the
    template's edges onto this plan's own task ids, so a dependency between two
    template rows becomes a dependency between the two copies.
    """
    return _engine(store).create_plan(
        room_id, payload, source=_source("POST", "/rooms/{room_id}/plans"), actor="rep"
    )


@router.get("/rooms/{room_id}/plans")
def list_plans(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every plan in a room, with its tasks and the derived states."""
    return {"room_id": room_id, "plans": _engine(store).list_plans(room_id)}


@router.get("/plans")
def list_all_plans(
    room_id: str | None = Query(None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Every plan, or the plans in one room."""
    return {"room_id": room_id, "plans": _engine(store).list_plans(room_id)}


@router.get("/plans/{plan_id}")
def read_plan(plan_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One plan, with its tasks, escalations and dangling dependency edges."""
    return _engine(store).read_plan(plan_id)


@router.patch("/plans/{plan_id}")
def update_plan(
    plan_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Rename a plan or move its phase.

    The selling plan and the implementation plan it becomes are different rows, so
    the phase is a field rather than a rename.
    """
    return _engine(store).update_plan(
        plan_id, payload, source=_source("PATCH", "/plans/{plan_id}"), actor="rep"
    )


@router.post("/plans/{plan_id}/close-won")
def close_won(plan_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Mark a plan closed-won and carry it into the implementation plan.

    The conversion runs here rather than as a step a seller must remember, because
    the research calls it an automation. The agreed tasks are carried over, not
    replaced, so the record of what the parties agreed to survives the transition.
    """
    return _engine(store).close_as_won(
        plan_id, source=_source("POST", "/plans/{plan_id}/close-won"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# The buyer's view of a plan
# --------------------------------------------------------------------------- #


@router.get("/plans/{plan_id}/shared")
def shared_plan(
    plan_id: str,
    audience: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """A plan as ``audience`` may see it.

    This is the route that keeps an internal-only task out of the client's hands.
    The task is absent from the response rather than hidden by the UI, so a buyer
    cannot recover it by reading the network tab.
    """
    return _engine(store).visible_plan(plan_id, audience=_audience(audience))


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


@router.get("/plans/{plan_id}/tasks")
def list_tasks(
    plan_id: str,
    audience: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The tasks on a plan, or only the ones ``audience`` may see."""
    engine = _engine(store)
    tasks = engine.tasks_of(plan_id, audience=audience)
    return {"plan_id": plan_id, "audience": _audience(audience), "tasks": tasks}


@router.post("/plans/{plan_id}/tasks")
def add_task(
    plan_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add a task with a named owner and a side.

    A task added by hand cannot declare a dependency, because the engine cannot know
    which of the plan's existing tasks the seller meant. Dependencies are set on the
    task afterwards, once the seller can see both ids.
    """
    return _engine(store).add_task(
        plan_id, payload, source=_source("POST", "/plans/{plan_id}/tasks"), actor="rep"
    )


@router.get("/tasks/{task_id}")
def read_task(
    task_id: str,
    audience: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One task, with its derived status.

    An internal-only task read with the buyer audience answers 404 rather than 403,
    because a 403 would confirm that something exists behind the id.
    """
    return _engine(store).read_task(task_id, audience=audience)


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Move a task, or change its owner, date, visibility or dependency edges.

    A change of status writes a status event, so a task's history is answerable
    without reading the audit log. A change that puts a task out of time writes an
    escalation row.
    """
    return _engine(store).update_task(
        task_id, payload, source=_source("PATCH", "/tasks/{task_id}"), actor="rep"
    )


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Remove a task, unless a task that is still open depends on it.

    Deleting a predecessor would leave its dependents on an edge pointing at
    nothing, which the graph reads as satisfied. That would tell a buyer a task can
    start when its owner says it cannot, so the delete is refused and the response
    names the reason.
    """
    return _engine(store).delete_task(
        task_id, source=_source("DELETE", "/tasks/{task_id}"), actor="rep"
    )


@router.get("/plans/{plan_id}/events")
def list_events(plan_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The status events on a plan, oldest first."""
    return {"plan_id": plan_id, "events": _engine(store).events_of(plan_id)}


@router.get("/plans/{plan_id}/escalations")
def list_escalations(plan_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The escalations raised on a plan's tasks.

    One row per task per state, so a task that was due soon and then went overdue
    has both. The rows are not deleted when a task comes back on time: the record of
    what was owed is history.
    """
    return {"plan_id": plan_id, "escalations": _engine(store).escalations_of(plan_id)}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the plan states the research says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a shape, an event
    or an audit row the HTTP routes would not produce. ``source="seed"`` rather than
    a route string: no route served this, and claiming one would be the lie hard
    rule 4 exists to prevent.

    The states seeded, and why each is here:

    * a plan **in progress** with a dependency edge, so the graph shows a blocked
      task and an open one, which is the state a MAP is in most of the time;
    * a task that is **overdue**, so the escalation rule has something to report;
    * an **internal-only** task, so the buyer's view and the seller's view differ;
    * a plan that has already been **closed-won**, so the implementation plan it
      becomes is visible and the agreed tasks are shown to have survived.

    Every character of the returned string is ASCII, so the seeder can print it on
    a Windows console. A single non-cp1252 character in one feature broke the
    whole seeder once.
    """
    store = RecordStore(db)
    now: datetime = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id
    engine = mutual.PlanEngine(store, now=lambda: now)
    source, actor = "seed", "dana"

    template = engine.create_template(
        {
            "name": "Standard close plan",
            "description": "The four steps every Northwind close runs through.",
            "tasks": [
                {
                    "title": "Buyer security review",
                    "owner": "Buyer IT",
                    "owner_side": mutual.SIDE_BUYER,
                    "due_date": (now.date()).isoformat(),
                    mutual.TEMPLATE_REF: "security",
                },
                {
                    "title": "Sign the order form",
                    "owner": "Dana",
                    "owner_side": mutual.SIDE_SELLER,
                    "due_date": (now.date()).isoformat(),
                    "depends_on": ["security"],
                    mutual.TEMPLATE_REF: "orderform",
                },
            ],
        },
        source=source,
        actor=actor,
    )

    # 1. In progress, with an edge, an overdue task and an internal task.
    live = engine.create_plan(
        room_id,
        {"name": "Northwind mutual action plan", "template_id": template["id"]},
        source=source,
        actor=actor,
    )
    tasks = {task["title"]: task for task in live["tasks"]}

    security = tasks["Buyer security review"]
    engine.update_task(
        security["id"],
        {"status": mutual.STATUS_IN_PROGRESS},
        source=source,
        actor=actor,
    )
    engine.add_task(
        live["id"],
        {
            "title": "Confirm the discount floor with finance",
            "owner": "Dana",
            "owner_side": mutual.SIDE_SELLER,
            "visibility": mutual.VISIBILITY_INTERNAL,
        },
        source=source,
        actor=actor,
    )

    # 2. A plan that is behind, so the board shows the escalation state.
    behind = engine.create_plan(
        room_two,
        {
            "name": "Vantage mutual action plan",
            "tasks": [
                {
                    "title": "Return the procurement questionnaire",
                    "owner": "Vantage Ops",
                    "owner_side": mutual.SIDE_BUYER,
                    "due_date": now.date().isoformat(),
                },
                {
                    "title": "Schedule the technical deep dive",
                    "owner": "Dana",
                    "owner_side": mutual.SIDE_SELLER,
                    # Two days past, so the escalation rule has a genuinely overdue
                    # task to report rather than only one that is merely due today.
                    "due_date": (now - timedelta(days=2)).date().isoformat(),
                },
            ],
        },
        source=source,
        actor=actor,
    )
    behind_tasks = {task["title"]: task for task in engine.read_plan(behind["id"])["tasks"]}
    engine.update_task(
        behind_tasks["Return the procurement questionnaire"]["id"],
        {"due_date": now.date().isoformat(), "status": mutual.STATUS_IN_PROGRESS},
        source=source,
        actor=actor,
    )

    # 3. A closed-won plan, so the implementation plan it becomes is visible.
    closed = engine.create_plan(
        room_id,
        {
            "name": "Halcyon mutual action plan",
            "tasks": [
                {
                    "title": "Hand over the signed order form",
                    "owner": "Dana",
                    "owner_side": mutual.SIDE_SELLER,
                    "due_date": now.date().isoformat(),
                },
                {
                    "title": "Schedule the kickoff workshop",
                    "owner": "Buyer Success",
                    "owner_side": mutual.SIDE_BUYER,
                    "depends_on": [],
                },
            ],
        },
        source=source,
        actor=actor,
    )
    closed_tasks = {task["title"]: task for task in engine.read_plan(closed["id"])["tasks"]}
    engine.update_task(
        closed_tasks["Hand over the signed order form"]["id"],
        {"status": mutual.STATUS_DONE},
        source=source,
        actor=actor,
    )
    outcome = engine.close_as_won(closed["id"], source=source, actor=actor)

    counted = engine.summary()
    return (
        f"{counted['plans']} plans (1 closed-won, carrying "
        f"{outcome['carried_tasks']} agreed task(s) into the implementation plan), "
        f"{counted['tasks']} tasks across them, {counted['done']} done, "
        f"{counted['blocked']} blocked by a dependency, {counted['internal_tasks']} "
        f"internal-only and invisible to the client, {counted['overdue']} overdue and "
        f"{counted['due_soon']} due soon"
    )
