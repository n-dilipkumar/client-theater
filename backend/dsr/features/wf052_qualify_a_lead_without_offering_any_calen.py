"""WF-052: qualify a lead without offering any calendar.

The researched workflow in full. An admin or engineer calls the Concierge router
Edge API **without** an ``interval`` in the body. The answer carries the resolved
assignee (``assignment.userId``, ``assignment.type``), the ``schedulingAllowed``
signal and a ``routingLink`` the lead can be redirected to later. The caller then
decides whether to surface a scheduler at all, or simply writes the qualification
result into the CRM.

The domain logic is in :mod:`dsr.lead_qualification`, which this module does not
own. What lives here is the three things a workflow has to take out of shared
files: the HTTP surface, the mapping from domain errors to responses, and the
demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-052``, and the module name is the ticket.** The host
discovers it, so nothing in ``dsr/api.py`` learns this workflow exists.

**``qualify`` writes nothing, and that is the feature.** "No routing session is
consumed until scheduling occurs." The route answers from a router read and an
in-memory chain walk, and the ``routeId`` it returns is a digest rather than a
stored row, so the guarantee is an absence a caller can count rather than a flag
a caller has to trust. The response carries the counters, so a reader sees
``routing_sessions_consumed: 0`` next to the answer.

**A body carrying an ``interval`` is refused, not ignored.** "The difference is
whether you pass an ``interval``." Answering half of the booking workflow would
hand back a ``routingId`` the caller cannot use and slots this workflow never
computed, so the body is refused with 400 and the researched sentence in the
message.

**One handler for the whole error hierarchy.**
:class:`~dsr.lead_qualification.errors.QualificationError` is the base of every
refusal in the package, and each subclass carries its own ``status`` and
``code``, so one handler answers 400 for a body with an interval, 404 for a
router that is not declared and 409 for a duplicate slug - which are not the same
kind of problem. ``RecordNotFound`` is deliberately *not* claimed: the core app
already maps it to 404, and two handlers for one type is a collision the host
refuses.

**The recorded path is separate on purpose.** The research names two caller
paths and chooses neither, so both are served: ``POST /qualify`` is path (a),
the caller keeps the ``routingLink``, and ``POST /rooms/{room_id}/verdicts`` is
path (b), which writes one audited row with the CRM writeback staged inside it
and marks it ``applied: false``. No automation fires beyond rule evaluation -
deliberately - so nothing is pushed anywhere.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.lead_qualification import (
    ASSIGNEE_COLLECTION,
    ROUTER_COLLECTION,
    VERDICT_COLLECTION,
    LeadQualificationEngine,
    QualificationError,
    describe,
    published_vocabulary,
    require_room,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-052-qualify-a-lead-without-offering-any-calendar",
    "ticket": "WF-052",
    "name": "Qualify a lead without offering any calendar",
    "description": (
        "Runs a Concierge router's rules against a form or lead payload and answers with the "
        "proposed owner, the schedulingAllowed signal and a routingLink the caller may redirect "
        "to later. It refuses a body carrying an interval, queries no availability, computes no "
        "slot list, consumes no routing session, and fires no automation."
    ),
    "nav": [{"id": "lead-qualification", "label": "Lead qualification"}],
}

router = APIRouter(prefix="/api/wf-052", tags=["wf052"])


def get_engine(store: RecordStore = StoreDep) -> LeadQualificationEngine:
    """A :class:`LeadQualificationEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a clock, and an ``app.state`` entry is exactly the edit
    to the shared ``dsr/api.py`` that the feature host exists to make
    unnecessary. Building it here also leaves the clock a plain constructor
    argument, which is what lets a test drive the whole workflow with a clock it
    controls.
    """
    return LeadQualificationEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _qualification_error(request: Request, exc: QualificationError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy, and the body comes from the error itself
    so no route can answer a given refusal differently from another.
    """
    return JSONResponse(status_code=exc.status, content=exc.as_response())


EXCEPTION_HANDLERS = {QualificationError: _qualification_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the two access patterns, the verdicts, the rule kinds.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a term added in one place reaches every
    client at once.
    """
    return published_vocabulary()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research for WF-052 is precise about the absent ``interval`` and silent
    about the rule language, the verdict vocabulary, what an unroutable lead
    answers with, and where the CRM values in a payload come from. Those gaps are
    product behaviour rather than comments, so they are collected here for a
    reviewer to disagree with by name. A read with no side effect, so it needs no
    store.
    """
    return describe()


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Routers, assignees, recorded verdicts, and the verdicts by name.

    Computed over exactly the rows the same filters would return, so a
    room-scoped total above an unscoped list cannot be misread as a product-wide
    one.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #


@router.get("/routers", summary="List qualification routers")
def list_routers(
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """The declared routers, newest first, with their whole chain.

    The full envelope per router, because an administrator configuring one needs
    the record id to patch it and the chain it will run.
    """
    records = engine.routers(enabled=enabled, limit=limit)
    return {"count": len(records), "routers": records, "collection": ROUTER_COLLECTION}


@router.post("/routers", status_code=201, summary="Declare a router")
def create_router(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a Concierge router: a slug, a name, and an ordered chain.

    The chain must end with a catch-all, because "Each router must end with a
    'Catch All' path to make sure you define the routing and acknowledge all
    inbound Leads." A chain that does not is refused before any row is created,
    so a draft that would strand a lead cannot leave a router behind that looks
    usable.
    """
    return engine.create_router(payload, actor=actor, source=f"POST {router.prefix}/routers")


@router.post("/routers/preview", summary="Check a draft router without saving it")
def preview_router(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Report what is wrong with a draft, and which rule a sample lead would hit.

    The advisory half of the save. An admin sees that a draft has no catch-all,
    and sees which rule a sample lead matches, before anything is published.
    Declared before ``/routers/{router_slug}`` so the concrete path is never
    shadowed by the template. Writes nothing, which the answer says.
    """
    return engine.preview_router(payload)


@router.get("/routers/{router_slug}", summary="Read one router")
def read_router(router_slug: str, engine: LeadQualificationEngine = EngineDep) -> dict[str, Any]:
    """One router and its chain."""
    return engine.require_router(router_slug)


@router.patch("/routers/{router_slug}", summary="Patch a router")
def update_router(
    router_slug: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Patch a router, re-validating the whole merged result.

    Re-validated rather than patched, because every field this workflow checks
    interacts: adding a second catch-all has to fail the way a fresh declaration
    would, and reordering the chain must not leave the floor somewhere no lead
    can reach.
    """
    return engine.update_router(
        router_slug, payload, actor=actor, source=f"PATCH {router.prefix}/routers/{{router_slug}}"
    )


@router.delete("/routers/{router_slug}", status_code=204, summary="Delete a router")
def delete_router(
    router_slug: str,
    actor: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> Response:
    """Soft-delete a router. 204.

    Its recorded verdicts stay readable, which is the point: the history of who a
    lead was proposed to outlives the router that proposed them.
    """
    engine.delete_router(
        router_slug, actor=actor, source=f"DELETE {router.prefix}/routers/{{router_slug}}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Assignees: the users a rule may propose
# --------------------------------------------------------------------------- #


@router.get("/assignees", summary="List the users a rule may propose")
def list_assignees(
    limit: int = Query(default=200, ge=1, le=1000),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every declared assignee.

    A rep whose row is missing is a lead that answers ``unroutable``, so this
    list is the page a rep reads before a router starts refusing leads.
    """
    records = engine.assignees(limit=limit)
    return {"count": len(records), "assignees": records, "collection": ASSIGNEE_COLLECTION}


@router.post("/assignees", status_code=201, summary="Declare an assignee")
def create_assignee(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a user a rule may name as an owner.

    The id is the one a rule carries in ``assign_user_id``, which is the
    ``assignment.userId`` the researched response names.
    """
    return engine.create_assignee(payload, actor=actor, source=f"POST {router.prefix}/assignees")


@router.delete("/assignees/{user_id}", status_code=204, summary="Delete an assignee")
def delete_assignee(
    user_id: str,
    actor: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> Response:
    """Soft-delete an assignee row. 204.

    Routers that name the id keep working and their leads answer ``unroutable``,
    which is the state an operator needs to see rather than a save-time refusal
    that would only surface later.
    """
    engine.delete_assignee(
        user_id, actor=actor, source=f"DELETE {router.prefix}/assignees/{{user_id}}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The researched call
# --------------------------------------------------------------------------- #


@router.post("/qualify", summary="Qualify a lead without offering any calendar")
def qualify(
    payload: dict[str, Any] = Body(default_factory=dict),
    router_slug: str | None = Query(
        default=None, description="which router to run; may also come in the body"
    ),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 1 to 3 of the researched flow: evaluate the rules and answer.

    Writes nothing. Not a routing session, not a verdict row, not the router's
    history. It is the path an assistant or a backend agent calls to screen and
    rank leads cheaply, before any scheduler is opened.

    The answer carries the researched fields in both spellings - ``routeId`` and
    ``route_id``, ``routingLink`` and ``routing_link``, ``schedulingAllowed`` and
    ``scheduling_allowed`` - because the researched payload is camelCase and this
    product's API is snake_case.
    """
    slug = str(router_slug or payload.get("router_slug") or "").strip()
    if not slug:
        raise HTTPException(
            status_code=400,
            detail="router_slug is required; it is the researched path segment",
        )
    return engine.qualify(slug, payload)


@router.post(
    "/rooms/{room_id}/verdicts",
    status_code=201,
    summary="Qualify a lead and keep the answer",
)
def record_verdict(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    router_slug: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """The second caller path: write the qualification result into the CRM.

    "Backend decides whether to surface a scheduler at all - or simply writes the
    qualification result into the CRM." This is that second branch. It qualifies
    exactly as ``/qualify`` does and then keeps one row, with the CRM writeback
    staged inside it and marked ``applied: false``.

    Room-scoped because a qualification is read by the team that owns the lead's
    room, and checked here rather than trusted from the body.
    """
    require_room(engine, room_id)
    slug = str(router_slug or payload.get("router_slug") or "").strip()
    if not slug:
        raise HTTPException(
            status_code=400,
            detail="router_slug is required; it is the researched path segment",
        )
    return engine.record_verdict(
        slug,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/verdicts",
    )


# --------------------------------------------------------------------------- #
# Recorded verdicts
# --------------------------------------------------------------------------- #


@router.get("/verdicts", summary="List recorded verdicts")
def list_verdicts(
    room_id: str | None = Query(default=None),
    router_slug: str | None = Query(default=None),
    verdict: str | None = Query(default=None, description="qualified | not_scheduled | unroutable"),
    scheduling_allowed: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: LeadQualificationEngine = EngineDep,
) -> dict[str, Any]:
    """Every recorded verdict, newest first.

    A verdict records which rule held, what it proposed, and whether a scheduler
    is allowed - which is the answer to "why was this lead not offered a
    calendar", asked often enough to deserve rows rather than a re-derivation.
    """
    records = engine.verdicts(
        room_id=room_id,
        router_slug=router_slug,
        verdict=verdict,
        scheduling_allowed=scheduling_allowed,
        limit=limit,
    )
    return {"count": len(records), "verdicts": records, "collection": VERDICT_COLLECTION}


@router.get("/verdicts/{verdict_id}", summary="Read one recorded verdict")
def read_verdict(verdict_id: str, engine: LeadQualificationEngine = EngineDep) -> dict[str, Any]:
    """One recorded verdict, or 404 if it does not exist."""
    return engine.require_verdict(verdict_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The users the demo routers may propose. Three rows, because a demo of only
#: clean resolutions would teach nothing about the states this workflow exists to
#: name: a catch-all that resolves, and a rule that refuses a scheduler.
DEMO_ASSIGNEES: tuple[dict[str, Any], ...] = (
    {
        "user_id": "005-nadia",
        "name": "Nadia A. Farouk",
        "email": "nadia@soluspring.example",
        "team": "Enterprise",
    },
    {
        "user_id": "005-desk",
        "name": "SoluSpring Deal Desk",
        "email": "desk@soluspring.example",
        "team": "Commercial",
    },
    {
        "user_id": "005-priya",
        "name": "Priya Raman",
        "email": "priya@soluspring.example",
        "team": "Commercial",
    },
)

#: The demo routers. The third is deliberately broken: its catch-all names a rep
#: this workspace has not been given, which is the one state that is legal to
#: declare and impossible to route to.
DEMO_ROUTERS: tuple[dict[str, Any], ...] = (
    {
        "router_slug": "demo-request",
        "name": "Demo requests from the web form",
        "tenant": "soluspring.chilipiper.example",
        "notes": "Enterprise goes to the AE, a hot contact is handled without a scheduler.",
        "rules": [
            {
                "kind": "data_field",
                "name": "Enterprise seat count",
                "conditions": [
                    {
                        "kind": "data_field",
                        "source": "form",
                        "field": "seats",
                        "operator": "gte",
                        "value": 200,
                    }
                ],
                "assign_user_id": "005-nadia",
            },
            {
                "kind": "crm_field",
                "name": "Hot contact is followed up by email",
                "conditions": [
                    {
                        "kind": "crm_field",
                        "source": "lead",
                        "field": "rating",
                        "operator": "equals",
                        "value": "hot",
                    }
                ],
                "assign_user_id": "005-priya",
                "scheduling_allowed": False,
                "crm_writeback": {"rating": "hot", "disqualification_reason": "Follow up by email"},
            },
            {"kind": "catch_all", "name": "Deal desk takes the rest", "assign_user_id": "005-desk"},
        ],
    },
    {
        "router_slug": "partner-intake",
        "name": "Partner intake, accepted without a screen",
        "tenant": "soluspring.chilipiper.example",
        "notes": "A chain with only a floor, which is legal and useful.",
        "rules": [{"kind": "catch_all", "name": "Deal desk", "assign_user_id": "005-desk"}],
    },
    {
        "router_slug": "unprovisioned-desk",
        "name": "A catch-all naming a rep who has not joined",
        "tenant": "soluspring.chilipiper.example",
        "notes": "Legal to declare, impossible to route to.",
        "rules": [
            {
                "kind": "catch_all",
                "name": "Former employee's desk",
                "assign_user_id": "005-former-employee",
            }
        ],
    },
)

#: The leads the demo runs, one per state worth showing.
DEMO_LEADS: tuple[dict[str, Any], ...] = (
    {
        "router_slug": "demo-request",
        "form": {
            "email": "m.oyelaran@northwind.example",
            "company": "Northwind Traders",
            "seats": 400,
        },
        "room_index": 0,
        "record": True,
    },
    {
        "router_slug": "demo-request",
        "form": {"email": "d.rao@contoso.example", "company": "Contoso Health", "seats": 40},
        "crm": {"lead": {"rating": "hot"}},
        "room_index": 1,
        "record": True,
    },
    {
        "router_slug": "demo-request",
        "form": {"email": "ops@fabrikam.example", "company": "Fabrikam Logistics", "seats": 25},
        "room_index": 2,
        "record": True,
    },
    {
        "router_slug": "partner-intake",
        "form": {"email": "buyer@adventure.example", "company": "Adventure Works", "seats": 8},
        "room_index": 3,
        "record": False,
    },
    {
        "router_slug": "unprovisioned-desk",
        "form": {"email": "stranger@newco.example", "company": "Newco", "seats": 2},
        "room_index": 0,
        "record": False,
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the assignees, the routers, and a recorded verdict per interesting state.

    The verdicts come from running the real engine over the real rules, so the
    demo cannot show a shape the workflow would not produce, and seeding opens no
    socket because there is no CRM here: the CRM values ride in the lead payload.

    Deliberately mixed. A demo of only qualified leads would teach nothing about
    the two states this workflow exists for: a matched rule that refuses a
    scheduler, and a catch-all naming a rep who does not exist.

    Returns a description the seeder prints. It is ASCII on purpose: the seeder
    prints it to a Windows console, and a single character outside cp1252 broke
    the whole seeder once.
    """
    store = RecordStore(db)
    engine = LeadQualificationEngine(store, clock=lambda: context["now"].isoformat())
    source = "seed"
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])

    for assignee in DEMO_ASSIGNEES:
        engine.create_assignee(assignee, actor="dana", source=source)
    for spec in DEMO_ROUTERS:
        engine.create_router(spec, actor="dana", source=source)

    verdicts: dict[str, int] = {}
    recorded = 0
    for lead in DEMO_LEADS:
        payload = {"form": lead["form"], "crm": lead.get("crm") or {}}
        answer = engine.qualify(str(lead["router_slug"]), payload)
        verdicts[str(answer["verdict"])] = verdicts.get(str(answer["verdict"]), 0) + 1
        if lead.get("record") and rooms:
            index = int(lead.get("room_index", 0))
            engine.record_verdict(
                str(lead["router_slug"]),
                payload,
                room_id=rooms[index % len(rooms)][0],
                actor="dana",
                source=source,
            )
            recorded += 1

    tally = ", ".join(f"{count} {name}" for name, count in sorted(verdicts.items()))
    return (
        f"{len(DEMO_ASSIGNEES)} assignees, {len(DEMO_ROUTERS)} routers, "
        f"{len(DEMO_LEADS)} qualifications ({tally}), {recorded} recorded"
    )
