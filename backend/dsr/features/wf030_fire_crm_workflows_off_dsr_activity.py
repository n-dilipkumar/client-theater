"""WF-030: fire CRM workflows off DSR activity.

The researched workflow, in full. A seller verifies the integration is on and the
workspace is connected to a deal (step 1), writes a **contact based** workflow
(step 3) whose trigger is **"When filter criteria is met"** on the integration
(step 4), picks one of the **five filter families** and refines it (steps 5 and 6),
adds actions (step 7), and **publishes** it (step 8) - after which DSR activity
drives it with no further setup. Each of those steps is a refusal or a rule in
:mod:`dsr.crm_workflows`, and the whole flow is a handful of routes below.

The domain logic is in :mod:`dsr.crm_workflows`, which this module does not own
and which no other feature could have written into its own path. What lives here is
the three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-030``, and room scoping is real.** The research's trigger
evaluates a *contact's* activity in a *workspace*, so anything scoped to one is
served under ``/rooms/{room_id}/...``. The workflow library and the integration
registry are not room-scoped: several workflows share one integration, and a
workflow may cover several rooms, so putting a room in either key would make two
things that are legitimately shared into per-room objects.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served it.
A hardcoded string inside a domain method is a defect, and the same class of bug has
shipped in this codebase before: a feature's audit log kept naming a path the app
had stopped serving. ``source`` is a *required* keyword on every writing method of
:class:`~dsr.crm_workflows.engine.WorkflowEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.** ``WorkflowError`` is the base of every
refusal in :mod:`dsr.crm_workflows`, and each carries its own ``status`` and
``code``, so one handler can answer 400 for a malformed workflow and 409 for one
that conflicts with state that already exists without being told which. It is a
domain type, so registering it globally cannot intercept anything unrelated
elsewhere in the product. ``RecordNotFound`` is deliberately *not* claimed: the core
app already maps it to 404, and two handlers for one type is a collision the host
refuses.

**Two routes for one decision, on purpose.** ``/activity`` and ``/evaluate`` are
different questions: one records the event and reports how it was classified, the
other asks whether it enrols anybody. ``POST .../evaluate`` reports every decision
rather than raising for a workflow that did not fire, because a buyer's activity
failing to match a filter is a fact about the buyer and a caller who got a 400 for
it would retry forever.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm_workflows import ACTIONABILITY_NOTE, WorkflowEngine, WorkflowError
from dsr.crm_workflows.actions import resolve_actions
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-030-fire-crm-workflows-off-dsr-activity",
    "ticket": "WF-030",
    "name": "Fire CRM workflows off DSR activity",
    "description": (
        "Write a contact-based CRM workflow whose trigger is 'When filter criteria is "
        "met' on one of the five published DSR filter families, refine it by the keys "
        "that family publishes, attach the four researched actions, and publish it so "
        "DSR activity drives it with no further setup. Nothing happens on the seller's "
        "screen, and every action is recorded rather than executed."
    ),
    "nav": [{"id": "crm-workflows", "label": "CRM workflows"}],
}

router = APIRouter(prefix="/api/wf-030", tags=["wf030"])


def get_engine(store: RecordStore = StoreDep) -> WorkflowEngine:
    """A :class:`WorkflowEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return WorkflowEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _workflow_error(request: Request, exc: WorkflowError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``WorkflowError`` is the base of every
    refusal in :mod:`dsr.crm_workflows` - an unknown filter family, an unsupported
    refinement, a workflow that is not contact-based, a trigger that is not "When
    filter criteria is met", a workflow with no actions, an integration that is not
    registered or is switched off, a published definition that cannot be amended -
    and all of them are the caller's to fix. The status rides on the exception
    rather than being decided here, because a missing name and a conflict with a
    live rule are both this package's errors and only one of them conflicts with
    state that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {WorkflowError: _workflow_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: WorkflowEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The five filter families and which refinements each one publishes, the four
    action kinds, the trigger modes, the contact-based constraint, the stage
    constraint, the matcher's reasons, and this product's action-to-family table. A
    client renders its filter editor from this rather than from a list compiled into
    the page, so a family or a refinement added server-side reaches every client at
    once - and the editor can never disagree with the validator about what is legal.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: WorkflowEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the five families, the refinements, the contact-based rule,
    the trigger, the four actions and the silence on the seller's screen. It does not
    say what happens on the edges of that, so the edges are collected here - named,
    traceable, and served - rather than left as comments in function bodies. The
    sourced half comes back beside the inferred half, because the point of the
    endpoint is to see where the line falls.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Step 1: the integration and the workspace-to-deal link
# --------------------------------------------------------------------------- #


@router.get("/integrations")
def list_integrations(engine: WorkflowEngine = EngineDep) -> dict[str, Any]:
    """The integrations a workflow's trigger can filter on.

    Step 1 of the researched flow is a check a person performs in the CRM's own UI.
    It is here as a record because several workflows share one integration, and
    because a workflow whose integration is off should be able to say so.
    """
    listed = engine.integrations()
    return {
        "count": len(listed),
        "integrations": listed,
        "checked": "enabled and the workspace connected to a deal/account",
    }


@router.post("/integrations", status_code=201)
def register_integration(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Register the integration, with the rooms connected to a deal or account.

    ``connections`` accepts either an object keyed by room id or a list of
    ``{room_id, deal_id}`` rows, because that is the shape a client building from a
    table of rooms produces. The whole map is one JSON value: adding
    ``opportunity_id`` or ``contact_ids`` to a connection needs no migration.
    """
    return engine.register_integration(
        payload, actor=actor, source=f"POST {router.prefix}/integrations"
    )


@router.patch("/integrations/{integration_id}")
def amend_integration(
    integration_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Turn the integration on or off, or (un)link a room.

    Turning it off, or unlinking a connected room, is refused with 409 while a
    published workflow depends on it, naming the workflows that would stop firing.
    Step 1 is a check a person performs before building a workflow, so disabling the
    integration underneath a live rule would make the check meaningless and the
    failure invisible.
    """
    return engine.amend_integration(
        integration_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/integrations/{{integration_id}}",
    )


# --------------------------------------------------------------------------- #
# Steps 2 to 8: the workflow library
# --------------------------------------------------------------------------- #


@router.get("/workflows")
def list_workflows(
    status: str | None = Query(default=None, description="draft | published"),
    integration: str | None = Query(default=None),
    include_withdrawn: bool = Query(default=False),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """The library, with every row's lint findings beside it.

    A workflow nobody re-reads is where an unrefined filter goes to live, so the
    findings are on the listing and not only on a detail route. Every filter is a
    JSON path in the definition's own payload, resolved through the dynamic index,
    so a field a team added is queryable without a change to this route.
    """
    listed = engine.workflows(
        status=status, integration=integration, include_withdrawn=include_withdrawn
    )
    return {
        "count": len(listed),
        "flagged": sum(1 for row in listed if row.get("flagged")),
        "actionability_note": ACTIONABILITY_NOTE,
        "workflows": listed,
    }


@router.post("/workflows", status_code=201)
def create_workflow(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Write a workflow definition. It does not fire until it is published.

    Steps 2 to 7. The definition is validated in the order the flow describes it -
    name, contact-based, trigger, the five families and their refinements, then at
    least one action - so the refusal a caller gets is about the first thing that is
    wrong, which is the one they are looking at.

    The integration is only resolved, not required: a draft may legitimately be
    written before step 1 is finished, and the lint says so. Publishing is where it
    becomes a hard prerequisite, which is what makes it visible when it matters.
    """
    return engine.create(payload, actor=actor, source=f"POST {router.prefix}/workflows")


@router.get("/workflows/{workflow_id}")
def read_workflow(workflow_id: str, engine: WorkflowEngine = EngineDep) -> dict[str, Any]:
    """One workflow: its trigger, its refinements, its actions, its findings."""
    definition = engine.workflow(workflow_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not found")
    return definition


@router.patch("/workflows/{workflow_id}")
def amend_workflow(
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Change a draft. A published workflow must be unpublished first.

    A published definition is the thing already firing, and contacts may already have
    been enrolled by the version being edited, so editing it in place would rewrite
    what those enrollments claim happened. The whole merged definition is
    re-validated rather than field by field, so an amendment cannot smuggle a
    non-contact-based workflow in by changing one nested key.
    """
    return engine.amend(
        workflow_id, payload, actor=actor, source=f"PATCH {router.prefix}/workflows/{{workflow_id}}"
    )


@router.delete("/workflows/{workflow_id}")
def withdraw_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Withdraw a workflow, published or not. A soft delete, always.

    A published workflow's enrollments name it, so the row is soft-deleted rather
    than destroyed: the enrollments and the audit trail must not be left pointing at
    something the API no longer serves, which is the exact defect the audit-source
    rule exists to prevent. Unpublishing is the off switch for a rule you still want;
    this retires the definition.
    """
    return engine.withdraw(
        workflow_id, actor=actor, source=f"DELETE {router.prefix}/workflows/{{workflow_id}}"
    )


@router.post("/workflows/{workflow_id}/publish")
def publish_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Step 8: publish, so DSR activity drives the workflow with no further setup.

    The integration must be registered and on. This is the moment step 1's check
    becomes load-bearing, which is why it is enforced here rather than at creation.
    """
    return engine.publish(
        workflow_id, actor=actor, source=f"POST {router.prefix}/workflows/{{workflow_id}}/publish"
    )


@router.post("/workflows/{workflow_id}/unpublish")
def unpublish_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Stop a published workflow firing, without retiring it.

    The definition and its enrollments survive, and publishing again resumes exactly
    where it left off. Withdrawing is the other act, and this is deliberately not it.
    """
    return engine.unpublish(
        workflow_id, actor=actor, source=f"POST {router.prefix}/workflows/{{workflow_id}}/unpublish"
    )


# --------------------------------------------------------------------------- #
# The source side: DSR activity, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/activity")
def list_activity(
    room_id: str,
    contact: str | None = Query(default=None, description="one buyer, by email or alias"),
    family: str | None = Query(default=None, description="one of the five filter families"),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """The room's DSR activity, oldest first, each row classified into its family.

    Oldest first, deliberately: an evaluation reads the events in the order they
    happened, so a listing that reordered them would make the match count depend on
    the sort. ``?family=views`` is the fastest way to see what a filter on that
    family would actually be looking at.
    """
    rows = engine.activity(room_id=room_id, contact=contact, family=family)
    by_family: dict[str, int] = {}
    for row in rows:
        key = str(row.get("action_family") or "unclassified")
        by_family[key] = by_family.get(key, 0) + 1
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_family": [
            {"family": name, "count": count} for name, count in sorted(by_family.items())
        ],
        "activity": rows,
    }


@router.post("/rooms/{room_id}/activity", status_code=201)
def record_activity(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """Record one DSR activity event - the webhook end of the researched source.

    The research's source side is Dock ``workspace.*`` / ``workspace.plan.task.*``
    webhooks and ``GET /v1/workspace-plan-tasks``. This writes to the product's own
    ``activity`` stream, the same rows the other analytics pages read, so this
    feature's page and theirs cannot disagree about what a buyer did.

    A repeated ``idempotency_key`` answers 200 with ``outcome: duplicate`` and the
    event that was kept. 201 would tell a sender a new event was recorded when
    nothing was, and a retried webhook must not inflate an enrollment's match count.
    """
    result = engine.record_activity(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/activity"
    )
    if result["outcome"] == "duplicate" and response is not None:
        response.status_code = 200
    return result


# --------------------------------------------------------------------------- #
# The automation: evaluate a contact's activity
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/evaluate")
def evaluate_room(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    contact: str | None = Query(default=None, description="the buyer; or send it in the body"),
    actor: str | None = Query(default=None),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """The researched data flow for one contact in one room, with every decision shown.

    "DSR activity ... -> the filter evaluates criteria per contact -> workflow
    enrollment -> emails, slack notifications, field writes, stage changes". Three
    decisions, and the response separates them:

    * ``skipped`` - workflows that could not be considered at all: a draft, an
      integration that is off or unregistered, or a room with no deal connection.
      Step 1's two conditions, reported per workflow rather than refused globally.
    * ``misses`` - workflows the contact's activity did not satisfy, with a sample of
      per-event reasons, so a rule that enrolled nobody can say which filter fell
      short instead of returning an empty answer.
    * ``enrollments`` - what fired, each with its resolved action plan.

    Nothing is raised for a workflow that did not fire. Also carries
    ``actionable: 0`` and the research's own sentence, because the research is
    explicit that nothing happens on the seller's screen.
    """
    who = payload.get("contact") if isinstance(payload, dict) else None
    only = payload.get("workflow_ids") if isinstance(payload, dict) else None
    return engine.evaluate(
        room_id,
        str(who or contact or ""),
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/evaluate",
        workflow_ids=[str(entry) for entry in only] if isinstance(only, list) else None,
    )


@router.get("/rooms/{room_id}/enrollments")
def list_enrollments(
    room_id: str,
    workflow_id: str | None = Query(default=None),
    contact: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: WorkflowEngine = EngineDep,
) -> dict[str, Any]:
    """The room's enrollments, newest first, with the action plan on each.

    Every filter is a JSON path in the enrollment's own payload and is resolved by
    ``find()`` through the dynamic index, so a field a team added is queryable the
    moment it is written, with no migration and no change to this route.
    """
    listed = engine.enrollments(
        room_id=room_id, workflow_id=workflow_id, contact=contact, limit=limit
    )
    return {
        "room_id": room_id,
        "count": len(listed),
        "executed": 0,
        "actionable": 0,
        "actionability_note": ACTIONABILITY_NOTE,
        "enrollments": listed,
    }


@router.get("/rooms/{room_id}/enrollments/{enrollment_id}")
def read_enrollment(
    room_id: str, enrollment_id: str, engine: WorkflowEngine = EngineDep
) -> dict[str, Any]:
    """One enrollment, with the workflow it followed.

    A withdrawn workflow still reads. Its enrollments name it, and hiding the
    workflow would leave the history pointing at nothing - the exact defect the
    audit-source rule exists to prevent. ``workflow_missing`` says when that has
    happened so the reader is not left guessing.
    """
    row = engine.enrollment(room_id, enrollment_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"enrollment {enrollment_id} not found in room {room_id}"
        )
    return row


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: WorkflowEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room's header, and the actionability note beside them.

    Counted over this room's enrollments and this room's activity rather than the
    whole collections, so a room's header says what happened in that room.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The published workflows the demo creates, one per researched example.
#:
#: "You can create HubSpot workflows based on Dock activity filters. There are a few
#: examples for when this might be helpful: To trigger emails and/or slack
#: notifications based on Dock activity. Change stages in HubSpot based on onboarding
#: or mutual action plan tasks. Update HubSpot fields based on Dock activity." The
#: first three below are those three, in that order.
#:
#: The third carries an action kind outside the four on purpose. The source says
#: "and more!", and a demo containing only kinds this build resolves would not show
#: that an unfamiliar kind is stored and reported rather than dropped. It also
#: refines nothing at all, because the lint says a `views` filter with no refinement
#: matches every view - a thing worth seeing reported rather than reading about.
DEMO_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "name": "Pricing pack downloaded: follow up in Slack",
        "description": (
            "The first researched example: a Slack notification and an email off a "
            "download filter, which is also the 'Downloads: filter by date and/or file "
            "name' refinement used exactly as documented."
        ),
        "trigger": {
            "integration": "hubspot",
            "criteria": {
                "family": "downloads",
                "refinements": {"file_name": "Pricing One-Pager"},
            },
        },
        "actions": [
            {"kind": "slack_notification", "channel": "#deals", "text": "Pricing pack downloaded."},
            {
                "kind": "send_email",
                "template": "pricing-follow-up",
                "subject": "Pricing, and a next step",
            },
        ],
    },
    {
        "name": "Onboarding task done: move the deal to proposal",
        "description": (
            "The second researched example: 'Change stages in HubSpot based on "
            "onboarding or mutual action plan tasks'. A deal stage, so the forward-only "
            "lifecycle rule does not apply to it - the two are kept apart because the "
            "source's constraint is about the lifecyclestage property."
        ),
        "trigger": {
            "integration": "hubspot",
            "criteria": {
                "family": "map_activity",
                "refinements": {"activity_text": 'completed task "Sign up for free account"'},
            },
        },
        "actions": [
            {"kind": "change_stage", "stage": "proposal", "stage_kind": "deal_stage"},
            {
                "kind": "update_field",
                "field": "onboarding_task_done",
                "value": "Sign up for free account",
            },
        ],
    },
    {
        "name": "Any view: flag the contact for review",
        "description": (
            "The third researched example, 'Update HubSpot fields based on Dock "
            "activity', with no refinement at all - which the lint reports, because a "
            "views filter that nothing narrows matches every view. It also carries an "
            "action kind outside the four, to show that such a kind is stored and "
            "reported unresolved rather than dropped."
        ),
        "trigger": {"integration": "hubspot", "criteria": {"family": "views"}},
        "actions": [
            {"kind": "update_field", "field": "dsr_activity_seen", "value": True},
            {"kind": "enroll_in_sequence", "sequence": "dsr-nurture"},
        ],
    },
)

#: A fourth published workflow, refined by link URL, that this buyer does not
#: satisfy. It exists so the ``misses`` branch of the evaluation is a row rather
#: than a claim, and it is the only thing in the demo that exercises the
#: "Clicks/Interactions by date or link URL" refinement - so the two ways a
#: link-URL criterion can decline are both visible: a different URL
#: (``link_url_mismatch``) and no URL at all (``refinement_unverifiable``).
DEMO_UNMATCHED_NAME = "Pricing page clicked: send the comparison one-pager"

DEMO_UNMATCHED: dict[str, Any] = {
    "name": DEMO_UNMATCHED_NAME,
    "description": (
        "Refined by link URL, which the buyer never clicks and one of their clicks "
        "carries no URL for. The evaluation reports both reasons rather than a single "
        "'did not match'."
    ),
    "trigger": {
        "integration": "hubspot",
        "criteria": {
            "family": "clicks",
            "refinements": {"link_url": "https://northwind.example/pricing"},
        },
    },
    "actions": [
        {"kind": "send_email", "template": "pricing-comparison", "subject": "Side by side"},
    ],
}

#: A draft, so the library shows a workflow before step 8 and the evaluation reports
#: it as one. Named as a constant because it is never published and therefore
#: produces no enrollment: a reviewer looking for it in the database finds it in the
#: library, and in the evaluation's ``skipped`` list naming it a draft.
DEMO_DRAFT_NAME = "Intro call completed: advance the lifecycle stage"

#: The two lifecycle stages that make the forward-only constraint visible. The
#: contact is already at ``DEMO_LIFECYCLE_CURRENT``; an action naming
#: ``DEMO_LIFECYCLE_REFUSED`` is behind it, so it is refused - and only it, because
#: the constraint is about the property rather than about the workflow.
DEMO_LIFECYCLE_CURRENT = "marketingqualifiedlead"
DEMO_LIFECYCLE_REFUSED = "lead"

#: The link the unmatched criteria names, and the one the buyer actually clicked.
DEMO_WANTED_URL = "https://northwind.example/pricing"
DEMO_CLICKED_URL = "https://northwind.example/roadmap"

#: An activity word in no published family, so "reported unclassified and kept" is a
#: row rather than a claim. The research enumerates five families, and this product's
#: own ``shared`` word is not in the table, so the event belongs to none of them.
DEMO_UNCLASSIFIED_ACTION = "shared"


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Four published workflows, a draft, and the states that are not all successes.

    The rows are produced by running the real :class:`WorkflowEngine`, so the demo
    cannot show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only green teaches a
    reviewer nothing:

    * three workflows that fire for one buyer, and one whose filter matches nothing,
      so the ``misses`` branch of the evaluation is a row;
    * the second run of the same evaluation, so "fires continuously" is visible as a
      counter on the enrollment already there rather than a second row;
    * a **lifecycle stage that cannot move forward**, refused for that action alone
      while the ``update_field`` beside it still applies - the sourced constraint
      showing up as a per-action refusal;
    * a link-URL criterion declining two different ways: a different URL, and an
      event with no URL at all;
    * an action kind outside the four, reported unresolved on its enrollment;
    * a room with no deal connection in the integration, so ``room_not_connected`` is
      a row (when the seeder was given more than two rooms);
    * a draft, so ``not_published`` is a row;
    * one activity event sent twice under one ``idempotency_key``, so the duplicate
      that was *not* stored is visible as a counter;
    * an activity word in no family, so ``unclassified`` is a row.
    """
    store = RecordStore(db)
    engine = WorkflowEngine(store)
    rng: random.Random = context.get("rng") or random.Random("wf030")
    # ``backend/seed.py`` passes ``[(room_id, account), ...]``. A bare id is
    # accepted too, because a caller assembling a context by hand should not have to
    # know the tuple shape to seed a feature.
    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    source = "seed"

    connections: list[dict[str, Any]] = []
    for index, (candidate, account) in enumerate(rooms[:2]):
        connections.append(
            {"room_id": candidate, "deal_id": f"deal{index:03d}", "account": account}
        )
    engine.register_integration(
        {
            "name": "hubspot",
            "label": "HubSpot",
            "enabled": True,
            "connections": connections,
        },
        actor="dana",
        source=source,
    )

    published = [
        engine.publish(
            engine.create(spec, actor="dana", source=source)["id"], actor="dana", source=source
        )["id"]
        for spec in (*DEMO_WORKFLOWS, DEMO_UNMATCHED)
    ]
    engine.create(
        {
            "name": DEMO_DRAFT_NAME,
            "description": (
                "The research's second example again, on the forward-only lifecycle "
                "property. A draft, so the library shows a workflow before step 8 and "
                "the evaluation reports it as one rather than ignoring it."
            ),
            "trigger": {
                "integration": "hubspot",
                "criteria": {
                    "family": "map_activity",
                    "refinements": {"activity_text": 'completed task "Intro call"'},
                },
            },
            "actions": [
                {
                    "kind": "change_stage",
                    "stage": "salesqualifiedlead",
                    "stage_kind": "lifecyclestage",
                },
                {"kind": "update_field", "field": "intro_call_completed", "value": True},
            ],
        },
        actor="dana",
        source=source,
    )["id"]

    if not rooms:
        return (
            f"1 integration, {len(published)} published workflows, 1 draft, "
            f"0 activity events (no rooms to scope them to)"
        )

    def at(minutes_ago: int) -> str:
        return (base - timedelta(minutes=minutes_ago)).isoformat()

    room_id, account = rooms[0]
    other_room = rooms[1][0] if len(rooms) > 1 else room_id
    buyer = "a.buyer@northwind.example"
    sent: list[dict[str, Any]] = []

    def send(
        target_room: str,
        action: str,
        target: str,
        *,
        minutes_ago: int,
        contact: str = buyer,
        **extra: Any,
    ) -> dict[str, Any]:
        """Record one event, in this product's own activity vocabulary.

        ``person`` rather than ``contact``, ``url`` rather than ``link_url``: the
        alias table in the vocabulary is what makes both this product's shape and a
        vendor webhook payload resolve to the same event, and seeding through the
        aliases keeps that path exercised rather than only the canonical spelling.
        """
        payload: dict[str, Any] = {
            "person": contact,
            "account": account,
            "action": action,
            "target": target,
            "occurred_at": at(minutes_ago),
            "delivery": "webhook",
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        result = engine.record_activity(target_room, payload, actor="dana", source=source)
        sent.append(result)
        return result

    # The download that fires the Slack-and-email workflow: the file name is the one
    # the criteria names.
    send(room_id, "downloaded", "Pricing One-Pager", minutes_ago=12, file_name="Pricing One-Pager")
    # The MAP task that fires the stage workflow, phrased exactly as the research's
    # own worked example.
    send(
        room_id,
        "completed_section",
        "Mutual Action Plan",
        minutes_ago=47,
        activity_text='completed task "Sign up for free account"',
    )
    # The view that fires the unrefined workflow.
    send(room_id, "viewed", "Enterprise Overview Deck", minutes_ago=4)
    # A second room with a download of its own, so room scoping is a row.
    send(
        other_room,
        "downloaded",
        "Security & Compliance Pack",
        minutes_ago=30,
        file_name="Security & Compliance Pack",
    )
    # An activity word in no published family: reported unclassified, kept, not dropped.
    send(room_id, DEMO_UNCLASSIFIED_ACTION, "Contract Draft", minutes_ago=8)
    # A click to a URL the unmatched criteria does not name: link_url_mismatch.
    send(room_id, "opened_link", "Implementation Roadmap", minutes_ago=20, url=DEMO_CLICKED_URL)
    # A click that carries no URL at all, against the same criteria:
    # refinement_unverifiable. Not the same reason, and the evaluation says so.
    send(room_id, "opened_link", "Pricing One-Pager", minutes_ago=90)
    # The same event twice under one key: a retried webhook must not be stored
    # twice, and the counter is what makes that visible.
    retry_key = f"evt_wf030_{rng.randrange(10**6):06d}"
    send(
        room_id,
        "opened_link",
        "Implementation Roadmap",
        minutes_ago=20,
        url=DEMO_CLICKED_URL,
        idempotency_key=retry_key,
    )
    duplicate = send(
        room_id,
        "opened_link",
        "Implementation Roadmap",
        minutes_ago=20,
        url=DEMO_CLICKED_URL,
        idempotency_key=retry_key,
    )

    evaluated = engine.evaluate(room_id, buyer, actor="dana", source=source)
    again = engine.evaluate(room_id, buyer, actor="dana", source=source)

    # The lifecycle stage that cannot move forward. Applied to the enrollment the
    # evaluation just took, so the sourced constraint is a row on a row rather than a
    # claim in a docstring.
    refused = 0
    live = engine.enrollments(room_id=room_id, workflow_id=published[1], limit=10)
    if live:
        plan = resolve_actions(
            [
                {
                    "kind": "change_stage",
                    "stage": DEMO_LIFECYCLE_REFUSED,
                    "stage_kind": "lifecyclestage",
                    "index": 0,
                    "resolved": True,
                },
                {
                    "kind": "update_field",
                    "field": "stage_review_requested",
                    "value": True,
                    "index": 1,
                    "resolved": True,
                },
            ],
            contact={"contact": buyer},
            room={"id": room_id},
            lifecycle_stage=DEMO_LIFECYCLE_CURRENT,
        )
        refused = sum(1 for action in plan if action["status"] == "refused")
        store.update(
            live[0]["id"],
            {
                "action_plan": plan,
                "action_summary": {
                    "actions": len(plan),
                    "planned": sum(1 for action in plan if action["status"] == "planned"),
                    "refused": refused,
                    "unresolved": sum(1 for action in plan if action["status"] == "unresolved"),
                    "executed": 0,
                },
                "lifecycle_stage": DEMO_LIFECYCLE_CURRENT,
            },
            actor="dana",
            source=source,
        )

    # A room the integration has no connection for, so ``room_not_connected`` is a
    # row. Only meaningful when the seeder was given a third room.
    isolated_skips = 0
    if len(rooms) > 2:
        send(
            rooms[2][0],
            "downloaded",
            "Pricing One-Pager",
            minutes_ago=15,
            file_name="Pricing One-Pager",
        )
        isolated = engine.evaluate(rooms[2][0], buyer, actor="dana", source=source)
        isolated_skips = sum(
            1 for row in isolated["skipped"] if row["reason"] == "room_not_connected"
        )

    missed = evaluated["misses"]
    reasons = sorted({row["reason"] for entry in missed for row in entry["sample"]})
    return (
        f"1 integration ({len(connections)} room(s) connected), {len(published)} published workflows, "
        f"1 draft, {len(sent)} activity event(s) sent, "
        f"{evaluated['enrolled']} workflow(s) fired for {buyer} "
        f"({again['enrolled']} already enrolled on the second run), "
        f"{refused} action refused for a lifecycle stage that cannot move forward "
        f"(the update_field beside it still applied), "
        f"{evaluated['not_enrolled']} matched nothing ({', '.join(reasons) or 'no sample'}), "
        f"{len(evaluated['skipped'])} skipped (1 draft), "
        f"{duplicate['duplicate_attempts']} duplicate webhook not stored, "
        f"1 event in no filter family"
        + (
            f", {isolated_skips} workflow(s) skipped for an unconnected room"
            if isolated_skips
            else ""
        )
    )
