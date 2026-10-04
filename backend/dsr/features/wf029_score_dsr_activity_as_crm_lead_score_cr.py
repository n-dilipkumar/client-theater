"""WF-029: score DSR activity as CRM lead-score criteria.

The domain logic is in :mod:`dsr.lead_score`, which this module does not own. What
lives here is the three things a workflow has to take out of shared files: the HTTP
surface, the mapping from domain errors to responses, and the demo data.

Three properties of the specification shaped the routes
-------------------------------------------------------

**No outbound CRM call is made.** The research names four HubSpot endpoints and two
scopes as its API surface, and this build holds no credential for either scope. Each
run therefore carries the request bodies those endpoints would receive, marked
``executed: false``. A network call inside a route that has to be audited and
deterministic would make the audit row depend on a third party. The decision is
recorded as ``no-outbound-crm-call`` in :mod:`dsr.lead_score.inferences` and was taken
by Jev at audit ``jev-20261004T024259-6152-79173``.

**The score is recomputed from the contact's history, not accumulated.** The research
says the rule "is continuous - every matching Dock activity event re-evaluates the
score without user action", and *re-evaluates* is the operative word. Jev chose this
over accumulation at audit ``jev-20261004T024258-6152-78859``, which is why
``POST /rooms/{room_id}/score`` is a single-contact recomputation and why
``GET /rooms/{room_id}/history`` can return runs whose number did not move.

**Nothing needs a publish step.** The researched flow ends at "Save; subsequent buyer
activity in the DSR moves the contact's score automatically", so a saved criterion is
armed. The sibling workflow WF-030 does describe a publish step, and its absence here
is a decision rather than an oversight.

``source=`` comes from the route
--------------------------------
Every write below passes ``f"{router.prefix}..."`` so the audit row names the route
that actually served it. A hardcoded string inside a domain method is a defect, and the
same class of bug has shipped in this codebase before: a feature's audit log kept
naming a path the app had stopped serving.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.lead_score import names
from dsr.lead_score.engine import LeadScoreEngine
from dsr.lead_score.errors import LeadScoreError
from dsr.lead_score.vocabulary import (
    BUCKET_LABEL,
    FAMILY_LABEL,
    FILTER_FAMILIES,
    REQUIRED_SCOPES,
    SCORE_PROPERTY,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-029-score-dsr-activity-as-crm-lead-score",
    "ticket": "WF-029",
    "name": "Score DSR activity as CRM lead-score criteria",
    "description": (
        "Hold the lead-score criteria a seller adds against the HubSpot Score contact "
        "property: one criterion per Dock activity property, its filters, and a score value "
        "in a positive or negative bucket. Every matching DSR event re-evaluates a contact's "
        "score from its whole history, and each run records exactly what the researched CRM "
        "endpoints would have received."
    ),
    "nav": [{"id": "lead-score", "label": "Lead score"}],
}

router = APIRouter(prefix="/api/wf-029", tags=["WF-029"])


def get_engine(store: RecordStore = StoreDep) -> LeadScoreEngine:
    """A :class:`LeadScoreEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but the
    store handle and a clock, and an ``app.state`` entry is exactly the edit to the
    shared ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return LeadScoreEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _lead_score_error(request: Request, exc: LeadScoreError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``LeadScoreError`` is the base of every
    refusal in :mod:`dsr.lead_score` - a criterion on a sixth Dock property, a filter
    the property does not publish, a score that is not a positive whole number, a
    switched-off integration, a token without the two scopes - and all of them are the
    caller's to fix.

    ``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
    404, and two handlers for one type is a collision the host refuses.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {LeadScoreError: _lead_score_error}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: LeadScoreEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The five Dock activity properties and the filters each publishes, the two buckets
    and their signs, the score property, the two required scopes, the matcher's
    reasons, the lifecycle-stage constraint, and the four CRM endpoints. A client
    renders its pickers from this rather than from a list compiled into the page.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: LeadScoreEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the score property, the buckets, the five Dock properties, the
    filters worth setting, the scopes and the endpoints. It does not say whether a
    score accumulates or is recomputed, whether it has a floor, or what a criterion
    against an unprovisioned property does. The edges are collected here - named,
    traceable, and served - rather than left as comments in function bodies.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Step 1: the CRM organisation
# --------------------------------------------------------------------------- #


@router.get("/integrations")
def list_integrations(engine: LeadScoreEngine = EngineDep) -> dict[str, Any]:
    """The registered CRM organisations, each with the scopes it still needs."""
    return engine.integrations()


@router.post("/integrations", status_code=201)
def register_integration(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """ "Confirm the Dock and HubSpot integration is enabled."

    The two researched scopes ride on the row, because they decide whether a criterion
    can be armed at all. ``deal_connections`` maps a room id onto the deal that room is
    connected to, which is the second half of the same step.
    """
    return engine.register_integration(
        payload, actor=actor or "system", source=f"POST {router.prefix}/integrations"
    )


@router.patch("/integrations/{integration_id}")
def amend_integration(
    integration_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Switch the integration on or off, add a scope, or connect a room to a deal.

    A null ``deal_connections`` entry removes that room, which is how step 1's
    "connected to a deal/account" is able to become untrue.
    """
    return engine.amend_integration(
        integration_id,
        payload,
        actor=actor or "system",
        source=f"PATCH {router.prefix}/integrations/{{integration_id}}",
    )


# --------------------------------------------------------------------------- #
# Step 1b: the Dock activity properties
# --------------------------------------------------------------------------- #


@router.get("/properties")
def list_properties(engine: LeadScoreEngine = EngineDep) -> dict[str, Any]:
    """The Dock activity contact properties a criterion can score against."""
    return engine.properties()


@router.post("/properties", status_code=201)
def provision_property(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Record one Dock activity contact property as provisioned.

    Recorded, not created: the research's write path for a property is
    ``POST /crm/v3/properties`` and this build makes no outbound call. The row is the
    assertion a criterion is armed against.
    """
    if not str(payload.get("family") or "").strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "a Dock activity property must name one of the five published properties: "
                f"{', '.join(FILTER_FAMILIES)}"
            ),
        )
    return engine.provision_property(
        payload, actor=actor or "system", source=f"POST {router.prefix}/properties"
    )


# --------------------------------------------------------------------------- #
# Steps 2 to 5: the criteria
# --------------------------------------------------------------------------- #


@router.get("/criteria")
def list_criteria(
    bucket: str | None = Query(default=None, description="positive or negative"),
    family: str | None = Query(default=None, description="one of the five Dock properties"),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Every saved criterion, each with its lint and whether it can score anything."""
    return engine.criteria(bucket=bucket, family=family)


@router.post("/criteria", status_code=201)
def create_criterion(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """ "Click Add criteria for either positive or negative scores."

    Steps 3 to 5 in one call: the bucket, the Dock property to score against, the
    filters, and the score value. The score is a magnitude and the bucket carries the
    sign, so a negative number is refused rather than read as a bucket. The response
    carries the lint, which reports what the criterion scores before a buyer has done
    anything.
    """
    return engine.add_criterion(
        payload, actor=actor or "system", source=f"POST {router.prefix}/criteria"
    )


@router.get("/criteria/{criterion_id}")
def read_criterion(criterion_id: str, engine: LeadScoreEngine = EngineDep) -> dict[str, Any]:
    """One saved criterion."""
    criterion = engine.criterion(criterion_id)
    if criterion is None:
        raise HTTPException(status_code=404, detail=f"criterion {criterion_id} not found")
    return criterion


@router.patch("/criteria/{criterion_id}")
def amend_criterion(
    criterion_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Change a saved criterion in place.

    Amendable while armed, and that follows from the score being derived rather than
    accumulated: an edit takes effect at the next event with nothing to republish.
    """
    return engine.amend_criterion(
        criterion_id,
        payload,
        actor=actor or "system",
        source=f"PATCH {router.prefix}/criteria/{{criterion_id}}",
    )


@router.delete("/criteria/{criterion_id}")
def withdraw_criterion(
    criterion_id: str,
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Withdraw a criterion, so it stops scoring.

    Soft-deleted rather than destroyed, because the runs it produced name it. Because
    the score is recomputed from history, its points come off the contact at that
    contact's next event.
    """
    return engine.withdraw_criterion(
        criterion_id,
        actor=actor or "system",
        source=f"DELETE {router.prefix}/criteria/{{criterion_id}}",
    )


@router.post("/preview")
def preview(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Would this criterion match this event? Nothing is saved and nothing is sent.

    ``{"criterion": {...}, "event": {...}}``. The researched flow is a UI flow, so the
    editor needs to answer this without a buyer having to do anything first, and the
    verdict carries the same per-filter reasoning a real run does.
    """
    return engine.preview(payload)


# --------------------------------------------------------------------------- #
# The source side: DSR activity
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/activity")
def list_activity(
    room_id: str,
    contact: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """The DSR activity rows this workflow has read for a room."""
    return engine.activities(room_id, contact=contact, limit=limit)


@router.post("/rooms/{room_id}/activity", status_code=201)
def record_activity(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """One DSR activity event, and the continuous rule that follows it.

    "DSR engagement (Views, Clicks, Downloads, Interactions, MAP task activity)" is the
    source side. The event is stored and the score is recomputed in the same call, which
    is what "every matching Dock activity event re-evaluates the score without user
    action" means from a server's point of view. An ``idempotency_key`` makes a retried
    webhook a counter rather than a second event.

    The response is the run as well as the event, findings included. A room with no deal
    connected still stores the event and still reports a score; it does not fail the
    request, because the event happened and only its attribution is missing.
    """
    result = engine.record_activity(
        room_id,
        payload,
        actor=actor or "system",
        source=f"POST {router.prefix}/rooms/{{room_id}}/activity",
    )
    return result


# --------------------------------------------------------------------------- #
# The continuous rule
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/score")
def score(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Re-evaluate one contact's score from its whole history, and record the write.

    The researched rule is continuous, so this route exists for the moment somebody
    needs to see it fire rather than to make it fire. The response carries the total,
    the per-criterion contributions, every run finding, and the three CRM requests the
    research's endpoints would have received.
    """
    return engine.score(
        room_id,
        payload,
        actor=actor or "system",
        source=f"POST {router.prefix}/rooms/{{room_id}}/score",
    )


@router.get("/rooms/{room_id}/scores")
def list_scores(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Every contact's score in a room, highest first, with its contributions."""
    return engine.scores(room_id, limit=limit)


@router.get("/rooms/{room_id}/scores/{contact_id}")
def read_contact_score(
    room_id: str, contact_id: str, engine: LeadScoreEngine = EngineDep
) -> dict[str, Any]:
    """One contact's score, with the runs that last produced it."""
    row = engine.contact_score(room_id, contact_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"contact {contact_id} has no score in room {room_id}",
        )
    return row


@router.get("/rooms/{room_id}/history")
def history(
    room_id: str,
    contact: str | None = Query(default=None),
    moved_only: bool = Query(default=False, description="only the runs whose number moved"),
    limit: int = Query(default=50, ge=1, le=500),
    engine: LeadScoreEngine = EngineDep,
) -> dict[str, Any]:
    """Every scoring run, or only the runs whose number moved.

    Both, because the researched rule is continuous: a run that moved nothing is still
    the record of the rule having fired, and a reader asking "why is this score what it
    is" wants the ones that moved.
    """
    return engine.history(room_id, contact=contact, moved_only=moved_only, limit=limit)


@router.get("/rooms/{room_id}/summary")
def summary(room_id: str, engine: LeadScoreEngine = EngineDep) -> dict[str, Any]:
    """What a page needs above the fold, including the states that are not successes."""
    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The two score properties in the source's own words, restated next to the demo so a
#: reader can see which sentence each row came from.
DEMO_BUCKET_NOTES = tuple(BUCKET_LABEL.values())

#: The Dock activity properties the demo provisions. MAP activity is deliberately
#: **absent**, so the page shows the state the issue describes: a criterion on a
#: property nobody provisioned saves, matches nothing, and is reported every run.
DEMO_PROVISIONED_PROPERTIES: tuple[str, ...] = (
    "views",
    "clicks",
    "downloads",
    "interactions",
)

#: The criteria the demo saves, in the researched flow's order: bucket, Dock property,
#: filters, score value.
DEMO_CRITERIA: tuple[dict[str, Any], ...] = (
    {
        "label": "Read the pricing pack",
        "family": "downloads",
        "bucket": "positive",
        "score": 20,
        "refinements": {"file_name": "Pricing One-Pager"},
    },
    {
        "label": "Signed up for a free account",
        "family": "map_activity",
        "bucket": "positive",
        "score": 50,
        "refinements": {"task_name": "Sign up for free account"},
    },
    {
        "label": "Bounced off the careers page",
        "family": "clicks",
        "bucket": "negative",
        "score": 10,
        "refinements": {"link_name": "https://careers.northwind.example/engineering"},
    },
    {
        "label": "Came back to the room this week",
        "family": "views",
        "bucket": "positive",
        "score": 5,
        "refinements": {},
    },
)

#: One contact's whole history, expressed as events. Written as data rather than
#: generated so a reviewer can read exactly which state each row is here for,
#: including the states that are *not* successes.
#:
#: ``days_ago`` is relative to the seeder's clock. The pricing-pack criterion has no
#: ``Occurred`` filter of its own in this demo, so the demo adds one, and the row
#: below is inside it.
DEMO_ACTIVITY: tuple[dict[str, Any], ...] = (
    # Priya: the score a seller wants to see move. A download worth 20 and two views
    # worth 5 each.
    {
        "room": 0,
        "contact": "priya.raman@northwind.example",
        "account": "Northwind Traders",
        "action": "downloaded",
        "file_name": "Pricing One-Pager",
        "days_ago": 3,
    },
    {
        "room": 0,
        "contact": "priya.raman@northwind.example",
        "account": "Northwind Traders",
        "action": "viewed",
        "target": "Enterprise Overview Deck",
        "days_ago": 2,
    },
    {
        "room": 0,
        "contact": "priya.raman@northwind.example",
        "account": "Northwind Traders",
        "action": "viewed",
        "target": "Contract Draft",
        "days_ago": 1,
    },
    # Dana: the negative bucket made into a row. A careers-page click and nothing
    # else, so her score is below zero - which is also the state that proves no floor
    # is applied unless a CRM organisation sets one.
    {
        "room": 0,
        "contact": "dana.kelly@northwind.example",
        "account": "Northwind Traders",
        "action": "opened_link",
        "link_url": "https://careers.northwind.example/engineering",
        "days_ago": 2,
    },
    # Lukas: a small positive score from views alone, on the second connected room.
    {
        "room": 2,
        "contact": "lukas.weber@fabrikam.example",
        "account": "Fabrikam Logistics",
        "action": "viewed",
        "target": "Security and Compliance Pack",
        "days_ago": 4,
    },
    # Ops: a download in a room with **no deal connected**, so the run reports
    # room_not_deal_connected and the score has no account to be attributed to. The
    # researched step 1 pairs the integration with the deal connection, and this row is
    # what the missing half looks like.
    {
        "room": 1,
        "contact": "procurement@contoso.example",
        "account": "Contoso Health",
        "action": "downloaded",
        "file_name": "Pricing One-Pager",
        "days_ago": 1,
    },
)


def _seed_now(context: dict[str, Any]) -> datetime:
    value = context.get("now")
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """The criteria, the properties, the activity, and the states that are not successes.

    The rows are produced by running the real :class:`LeadScoreEngine`, so the demo
    cannot show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only green teaches a reviewer
    nothing. What is here on purpose:

    * **A score that moved up** - Priya at 25 from one pricing-pack download and two
      views, which is the whole workflow working.
    * **A score below zero** - Dana from a single careers-page click, so the negative
      bucket is a row rather than a claim and the "no floor" inference is visible.
    * **A room with no deal connected** - Ops in Contoso Health, so the run reports
      ``room_not_deal_connected`` instead of silently attributing a score to nobody.
    * **A criterion on a Dock property nobody provisioned** - the MAP-activity row,
      saved and armed-looking but matching nothing, which is the dependency the issue
      records and the reason ``awaiting_provisioning`` is not an empty list.
    * **A criterion with no second filter** - the views row, which carries only the
      recommended ``Occurred`` baseline and therefore warns rather than refuses.
    * **A criterion writing to a third-party contact property**, so the extensibility
      note is a stored row marked unresolved rather than a sentence in a document.

    Every summary number is counted from the store rather than written out, so the
    string the seeder prints cannot drift from what the demo produced.
    """
    room_ids = list(context.get("room_ids") or [])
    if not room_ids:
        return "no demo rooms, so no lead-score demo data"

    context.get("rng") or random.Random("wf029")
    base = _seed_now(context)
    actor = "dana"
    source = "seed"
    engine = LeadScoreEngine(RecordStore(db), now=lambda: base.isoformat())

    def at(days_ago: float) -> str:
        return (base - timedelta(days=days_ago)).isoformat()

    # Step 1: the integration is on, carries both researched scopes, and is connected
    # to two of the four demo rooms. The two rooms left out are what makes the
    # "no deal connected" finding a row rather than a claim.
    connected = {room_ids[index][0]: {"deal_id": f"deal_{index + 1}"} for index in (0, 2)}
    engine.register_integration(
        {
            "vendor": "hubspot",
            "label": "HubSpot portal",
            "portal_id": "hs_demo_portal",
            "enabled": True,
            "scopes": list(REQUIRED_SCOPES),
            "deal_connections": connected,
        },
        actor="sam",
        source=source,
    )

    # Step 1b: four of the five Dock activity properties. MAP activity is left out on
    # purpose, so the page has a gap to show.
    engine.seed_properties(DEMO_PROVISIONED_PROPERTIES, actor=actor, source=source)

    # Steps 2 to 5: the criteria. The MAP-activity one and the third-party-property
    # one both save, and both are reported as scoring nothing.
    engine.add_criterion(
        {
            **DEMO_CRITERIA[0],
            "refinements": {**DEMO_CRITERIA[0]["refinements"], "occurred": {"from": at(90)}},
        },
        actor=actor,
        source=source,
    )
    engine.add_criterion(
        {
            **DEMO_CRITERIA[1],
            "refinements": {**DEMO_CRITERIA[1]["refinements"], "occurred": {"from": at(90)}},
        },
        actor=actor,
        source=source,
    )
    engine.add_criterion(
        {
            **DEMO_CRITERIA[2],
            "refinements": {**DEMO_CRITERIA[2]["refinements"], "occurred": {"from": at(90)}},
        },
        actor=actor,
        source=source,
    )
    engine.add_criterion(
        {**DEMO_CRITERIA[3], "refinements": {"occurred": {"from": at(7)}}},
        actor=actor,
        source=source,
    )
    engine.add_criterion(
        {
            "label": "Scored on a third-party property",
            "family": "interactions",
            "bucket": "positive",
            "score": 15,
            "refinements": {"occurred": {"from": at(90)}},
            "score_property": "Showing SMB Intent",
        },
        actor=actor,
        source=source,
    )

    # The source side, and the continuous rule that follows each event.
    for event in DEMO_ACTIVITY:
        index = int(event["room"])
        if index >= len(room_ids):
            continue
        room_id, account = room_ids[index]
        payload = {key: value for key, value in event.items() if key not in ("room", "days_ago")}
        payload["account"] = account
        payload["occurred_at"] = at(float(event["days_ago"]))
        engine.record_activity(room_id, payload, actor=actor, source=source)

    criteria = engine.criteria()
    properties = engine.properties()
    room_summary = engine.summary(room_ids[0][0])
    scores = engine.scores(room_ids[0][0])
    history = engine.history(room_ids[0][0], limit=100)
    armed = sum(1 for entry in criteria["criteria"] if entry["armed"])
    by_bucket = room_summary["criteria_by_bucket"]
    below = sum(1 for entry in scores["scores"] if entry["score"] < 0)

    return (
        f"{criteria['count']} criteria ({armed} armed, "
        f"{by_bucket['positive']} positive, {by_bucket['negative']} negative), "
        f"{len(properties['provisioned'])} of {len(FILTER_FAMILIES)} Dock properties provisioned, "
        f"{len(DEMO_ACTIVITY)} activity events, {scores['total']} contacts scored on room 1, "
        f"{history['count']} runs, {below} score below zero"
    )


#: Names re-exported for a caller that reads this module rather than the package.
__all__ = [
    "DEMO_ACTIVITY",
    "DEMO_BUCKET_NOTES",
    "DEMO_CRITERIA",
    "DEMO_PROVISIONED_PROPERTIES",
    "EXCEPTION_HANDLERS",
    "FEATURE",
    "SCORE_PROPERTY",
    "get_engine",
    "router",
    "seed",
]

#: The collections this feature owns, published so a reader of the registry can see
#: what a WF-029 record looks like without opening the domain package.
COLLECTIONS = (
    names.INTEGRATIONS,
    names.PROPERTIES,
    names.CRITERIA,
    names.ACTIVITY,
    names.CONTACTS,
    names.RUNS,
)

#: Kept beside the criteria list so a client that renders the pickers has the vendor's
#: own sentence for each option rather than a paraphrase.
FAMILY_SENTENCES = FAMILY_LABEL
