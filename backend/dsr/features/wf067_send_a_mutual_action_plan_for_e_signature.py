"""WF-067: send a mutual action plan for e-signature approval and track the state.

The researched workflow, in full. A seller builds the MAP template (step 1),
creates the envelope with its recipients and their fields in one API call
(step 2), distributes it so the status goes ``DRAFT`` to ``PENDING`` and the
recipients get a signing link (step 3), and then the room lives off webhook
events: a recipient opens, signs and completes; the document completes; or a
recipient rejects, lets the deadline pass, or gets a reminder (steps 4 to 6).
"The sales room consumes ``DOCUMENT_COMPLETED`` ... to flip the MAP milestone to
*Approved*, advance the plan, and notify the owner."

What the contract meant for this build
--------------------------------------
**The prefix is ``/api/wf-067``, and everything is room-scoped.** The join key
the research names is ``externalId``, which maps a document back to a deal room,
so every route that can receive an event carries the room it belongs to.

**``source`` comes from the route.** Every write passes
``f"{router.prefix}..."``, so the audit row names the route that actually served
it. A URL string hardcoded inside a domain method is a defect, and the same class
of bug has shipped in this codebase before: a feature's audit log kept naming a
path the app had stopped serving. ``source`` is a *required* keyword on every
writing method of :class:`~dsr.scheduling_meetings.engine.MapEngine`, so omitting
it is a ``TypeError`` at the call site rather than an untraceable row.

**One handler for the whole error hierarchy.** ``MapError`` is the base of every
refusal in :mod:`dsr.scheduling_meetings`, and each carries its own ``status`` and
``code``, so one handler answers 400 for a malformed body, 401 for an event that
failed its secret check, 409 for one that conflicts with live state and 403 for a
recipient who is not an approver - without the handler knowing which is which.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
404, and two handlers for one type is a collision the host refuses.

**The webhook route reads the raw body.** Not because a signature covers it - the
research's check is a shared secret in a header, not a body signature - but
because the event name and the join key both have to be read exactly as they
arrived, and a re-serialised object is not what the vendor sent. The route takes
``await request.json()`` and hands the mapping on, so nothing is re-encoded.

**Nothing here can sign for anybody.** The research states it as a limit on the
vendor's API: "The API cannot: Sign documents on behalf of recipients". There is
no route that accepts a recipient status, and the only thing that moves a
recipient's status is a verified webhook event.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.scheduling_meetings import (
    EVENT_DOCUMENT_COMPLETED,
    MILESTONE_APPROVED,
    OUTCOME_DUPLICATE,
    WEBHOOK_SECRET_HEADER,
    DuplicateEvent,
    MapEngine,
    MapError,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-067-send-a-mutual-action-plan-for-e-signature",
    "ticket": "WF-067",
    "name": "Send a mutual action plan for e-signature approval",
    "description": (
        "Build a mutual action plan template with recipient roles and placed fields, send "
        "the envelope to named signers and approvers, and track the signing state from "
        "verified webhooks until the room milestone flips to Approved. An approver gates "
        "the signers, a retried event is applied once, and this product never signs for a "
        "recipient."
    ),
}

router = APIRouter(prefix="/api/wf-067", tags=["wf067"])


def get_engine(store: RecordStore = StoreDep) -> MapEngine:
    """A :class:`MapEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` the feature host exists to make unnecessary. Building it here
    also leaves the engine a plain object, which is what a test constructs.
    """
    return MapEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _map_error(request: Request, exc: MapError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``MapError`` is the base of every
    refusal in :mod:`dsr.scheduling_meetings` - a recipient with no address, a
    field past the edge of the page, a role the research does not list, a plan
    already distributed, an event that failed its secret check - and all of them
    are the caller's to fix.

    The status rides on the exception rather than being decided here, because a
    malformed body and an event that conflicts with live state are both this
    package's errors and only one of them conflicts with state that already
    exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {MapError: _map_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: MapEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The five recipient roles and the approver's one-line rule, the five envelope
    statuses, the two signing orders, the field types and the percentage
    coordinates, the fourteen events with their meanings, the seven milestones
    and which of them are refusals, and the invariants this product keeps. A
    client renders its signer list from this rather than from a list compiled into
    the page, so a role added server-side reaches every client at once - and the
    editor can never disagree with the validator about what is legal.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: MapEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the vendor's behaviour in unusual detail and says almost
    nothing about what the *room* should do with it. It does not say what format
    ``externalId`` takes here, does not say whether an approver's refusal differs
    from a signer's, and does not pick between the email invite, the redirect and
    the embed. Those are collected here - named, traceable, bounded, and served -
    rather than left as comments in function bodies.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Step 1: the template
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/templates")
def list_templates(room_id: str, engine: MapEngine = EngineDep) -> dict[str, Any]:
    """Every MAP template this room has built."""
    templates = engine.templates(room_id)
    return {"room_id": room_id, "count": len(templates), "templates": templates}


@router.post("/rooms/{room_id}/templates", status_code=201)
def create_template(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """Step 1: the seller-built template, with its recipient roles and fields.

    "Seller builds the MAP/agreement **Template** (a PDF) with recipient roles and
    fields." What the room stores is the field geometry and the roles, not the
    bytes: the PDF is the seller's to upload at the vendor, and what the room has
    to check is the shape of what will be placed on it.
    """
    return engine.create_template(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/templates"
    )


@router.get("/rooms/{room_id}/templates/{template_id}")
def read_template(room_id: str, template_id: str, engine: MapEngine = EngineDep) -> dict[str, Any]:
    """One template, by this room's record id."""
    return engine.template(room_id, template_id)


# --------------------------------------------------------------------------- #
# Steps 2 and 3: the envelope, then distribution
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/plans")
def list_plans(
    room_id: str,
    milestone: str | None = Query(default=None, description="filter by milestone"),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """Every plan this room has sent, newest first, with the milestone each is at."""
    plans = engine.plans(room_id, milestone=milestone)
    return {"room_id": room_id, "count": len(plans), "plans": plans}


@router.post("/rooms/{room_id}/plans", status_code=201)
def create_plan(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """Step 2: the envelope, its recipients and its fields, in one call.

    "Seller creates the envelope via the API in one call ... supplying
    ``recipients[]`` each with ``email``, ``name``, ``role`` and ``fields[]``."
    The research makes this one request, so this is one room action and it writes
    one plan plus its recipients.

    The plan is created ``DRAFT``, because "After distribution, recipients receive
    an email with a link to sign the document. The document status changes from
    ``DRAFT`` to ``PENDING``" - so a plan that exists has not gone anywhere yet,
    and nothing a webhook says about it can be believed until it has.
    """
    return engine.create_plan(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{{room_id}}/plans"
    )


@router.get("/rooms/{room_id}/plans/{plan_id}")
def read_plan(room_id: str, plan_id: str, engine: MapEngine = EngineDep) -> dict[str, Any]:
    """One plan with its recipients, its signing links, and its gate.

    The ``signing_unlocked`` flag and ``blocking_approvers`` are here rather than
    in the page, because "APPROVER | Must approve before signers can sign" is a
    rule about who still has to act, and only the room knows who that is.
    """
    return engine.plan_view(room_id, plan_id)


@router.post("/rooms/{room_id}/plans/{plan_id}/distribute")
def distribute_plan(
    room_id: str,
    plan_id: str,
    actor: str | None = Query(default=None),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """Step 3: distribute. The plan leaves ``DRAFT`` for ``PENDING``.

    "Seller distributes: ``POST /api/v2/envelope/distribute`` with
    ``{envelopeId}`` -> status ``DRAFT`` to ``PENDING``, recipients get a signing
    link."

    Distributing twice is refused rather than repeated, because the vendor would
    send the signing links again and a buyer would be asked to sign the same plan
    twice.
    """
    return engine.distribute(
        room_id,
        plan_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/plans/{{plan_id}}/distribute",
    )


@router.post("/rooms/{room_id}/plans/{plan_id}/cancel")
def cancel_plan(
    room_id: str,
    plan_id: str,
    actor: str | None = Query(default=None),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """The seller pulls the plan.

    "``POST /api/v2/envelope/cancel`` (used by ``DOCUMENT_CANCELLED``)". The
    vendor event is what actually stops the signing links; this records the
    room-side decision that asked for it.
    """
    return engine.cancel_plan(
        room_id,
        plan_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/plans/{{plan_id}}/cancel",
    )


@router.get("/rooms/{room_id}/plans/{plan_id}/can-sign")
def can_sign(
    room_id: str,
    plan_id: str,
    email: str = Query(description="the recipient asking"),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """May this person sign right now? The approver gate, read per person.

    "APPROVER | Must approve before signers can sign". A signer who arrives early
    is not forbidden - the plan is not ready - so this reports the decision
    rather than raising, and names the approvers still blocking.
    """
    return engine.can_sign(room_id, plan_id, email)


# --------------------------------------------------------------------------- #
# Steps 4 to 6: the verified webhook
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/webhook", status_code=201)
async def receive_event(
    room_id: str,
    request: Request,
    response: Response,  # type: ignore[assignment] - FastAPI injects the real object
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """One event from the vendor: verify it, resolve it, apply it once, react.

    The researched data flow end to end: *PDF template + field coordinates +
    recipient roles -> envelope -> distribute -> per-recipient tokenised signing
    URLs -> ``readStatus``/``signingStatus``/``sendStatus`` transitions -> webhook
    events -> sales-room MAP state + owner notification.*

    The order of the checks is the order of the risks:

    1. **The secret.** "Check the ``X-Documenso-Secret`` header matches your
       configured secret." A failure writes nothing at all - not the event, not
       the recipient, not the milestone - because an unauthenticated caller must
       not be able to mark a buyer's plan approved.
    2. **The join key.** "``externalId`` is the join key back to the deal room."
    3. **The retry.** "Webhooks may be retried, so handle duplicate events." A
       repeat is answered **200**, not 201, because nothing new happened and
       answering 201 would tell the vendor a state change it did not get.

    The body is read from the request rather than declared as a FastAPI model, so
    an event this build does not recognise is a domain refusal with a code and a
    sentence rather than a framework validation error.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is one refusal
        raise HTTPException(status_code=400, detail="the event body is not JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="the event body is not a JSON object")

    try:
        report = engine.receive_event(
            room_id,
            headers=dict(request.headers),
            payload=payload,
            actor="vendor",
            source=f"POST {router.prefix}/rooms/{{room_id}}/webhook",
        )
    except DuplicateEvent as duplicate:
        # A retry is not a fault. It is answered 200 so the vendor stops, and the
        # attempt is counted on the row that was kept.
        response.status_code = 200
        report = {
            "outcome": OUTCOME_DUPLICATE,
            "event": str(duplicate).split(" ")[1] if " " in str(duplicate) else "",
            "detail": str(duplicate),
        }
        return report

    if report.get("new_milestone") == MILESTONE_APPROVED:
        report["advanced_to"] = EVENT_DOCUMENT_COMPLETED
    return report


@router.get("/rooms/{room_id}/events")
def list_events(
    room_id: str,
    plan_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """The event log for a room, newest first.

    Retries are here with their attempt count rather than collapsed away, because
    "Webhooks may be retried, so handle duplicate events" is only demonstrably true
    if the repeat is visible.
    """
    events = engine.events(room_id, plan_id=plan_id, limit=limit)
    return {"room_id": room_id, "count": len(events), "events": events}


@router.get("/rooms/{room_id}/notices")
def list_notices(
    room_id: str,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """What an event told the owner, newest first.

    One notice per event that changed something a seller would want to know, and
    none for a repeat - so this list is a list of things that actually happened
    rather than a list of webhook calls.
    """
    notices = engine.notices(room_id, unread_only=unread_only, limit=limit)
    return {
        "room_id": room_id,
        "count": len(notices),
        "unread": sum(1 for row in notices if not row.get("read")),
        "notices": notices,
    }


@router.post("/rooms/{room_id}/notices/acknowledge")
def acknowledge_notices(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MapEngine = EngineDep,
) -> dict[str, Any]:
    """Mark notices read, by id or all of them.

    "Notify the owner" is half a feature if a notification cannot be dealt with.
    It is a write, so it is audited under this route rather than folded into a read.
    """
    return engine.acknowledge(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/notices/acknowledge",
    )


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: MapEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room's header, and the invariants beside them.

    Counted over this room's own plans and events, so a header says what happened
    in that room. The two invariants are here because they are the two things a
    reader looks for and does not find on a page: this product never signs for a
    recipient, and the signed document is not retrievable before every recipient
    has finished.
    """
    return engine.summary(room_id)


@router.get("/whoami")
def webhook_contract() -> dict[str, Any]:
    """What the vendor needs to configure, in one place.

    The header name, the room-scoped path to aim at, and the fourteen event names
    worth subscribing to. A seller configuring a webhook needs these three things
    and nothing else, and none of them are something they should have to read off
    this module's source.
    """
    from dsr.scheduling_meetings import EVENTS

    return {
        "header": WEBHOOK_SECRET_HEADER,
        "path_template": f"POST {router.prefix}/rooms/{{room_id}}/webhook",
        "events": list(EVENTS),
        "join_key": "externalId",
        "note": (
            "The secret is compared in constant time. An event without the right header "
            "writes nothing at all. A repeated event is answered 200 and applied once."
        ),
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The feature's own prefix, duplicated here so the demo data's audit sources name
#: the same routes the router serves. A change to the prefix has to be made
#: deliberately in both places, which is the point of writing it twice.
PREFIX = "/api/wf-067"

#: The secret the demo's webhooks carry. In a real room this is the seller's own
#: value from the vendor's dashboard.
DEMO_SECRET = "northwind-map-webhook-secret"

DEMO_SIGNER_EMAIL = "buyer@northwind.example"
DEMO_APPROVER_EMAIL = "legal@contoso.example"
DEMO_SELLER_EMAIL = "dana@northwind.example"


def _room(store: RecordStore, name: str, account: str) -> str:
    return store.create("room", {"name": name, "account": account}, actor="dana")["id"]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three rooms, and the states that are not all successes.

    The rows are produced by running the real :class:`MapEngine`, so the demo
    cannot show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only green teaches a
    reviewer nothing:

    * **one room** - a plan with a signer, an **approver**, a CC and a viewer, sent
      and **fully approved**. Its event log holds the researched sequence:
      ``DOCUMENT_OPENED``, ``DOCUMENT_SIGNED``, ``DOCUMENT_RECIPIENT_COMPLETED``
      and then ``DOCUMENT_COMPLETED``, which is what "flip the MAP milestone to
      *Approved*" hangs on;
    * **the same room** - a second plan whose **approver refused**, so
      ``refused_by_approver`` is a row rather than a claim, and a third whose
      **signer refused**;
    * **a second room** - a plan **out for signature** with the approver still
      pending, so ``signing_unlocked`` is false and the page shows who is holding
      it up;
    * **a third room** - a plan **never distributed**, so an event that claims a
      signature on it is refused as ``event_before_distribution`` rather than
      believed;
    * a **duplicate delivery** of one event on the first plan, counted and applied
      once, because "Webhooks may be retried" is the rule a demo should show;
    * a **wrong secret** on one delivery, refused with nothing written, because the
      vendor's own instruction is to verify the header and a demo that never shows
      a failure is not showing the check.

    The seeder hands over ``[(room_id, account), ...]``. It is topped up with rooms
    this seed creates, because the states above are per-room. The return string
    says how many rooms came from the seeder, and every character in it is
    encodable by cp1252.
    """
    store = RecordStore(db)
    engine = MapEngine(store)
    given: list[str] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]

    names = [
        ("Northwind Traders - MAP approved", "Northwind Traders"),
        ("Contoso Health - MAP out for signature", "Contoso Health"),
        ("Fabrikam Logistics - MAP never distributed", "Fabrikam Logistics"),
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:3]
    created = 0
    for index in range(3):
        if index < len(room_ids):
            continue
        name, account = names[index]
        room_ids.append(_room(store, name, account))
        created += 1
    approved_room, pending_room, undelivered_room = room_ids

    create_source = f"POST {PREFIX}/rooms/{{room_id}}/plans"
    distribute_source = f"POST {PREFIX}/rooms/{{room_id}}/plans/{{plan_id}}/distribute"
    template_source = f"POST {PREFIX}/rooms/{{room_id}}/templates"
    webhook_source = f"POST {PREFIX}/rooms/{{room_id}}/webhook"
    headers = {WEBHOOK_SECRET_HEADER: DEMO_SECRET}

    def recipients(*, with_approver: bool = True) -> list[dict[str, Any]]:
        people = [
            {
                "email": DEMO_SIGNER_EMAIL,
                "name": "Ada Byron",
                "role": "SIGNER",
                "party": "buyer",
                "signing_order": 1,
                "fields": [
                    {"type": "SIGNATURE", "positionX": 10, "positionY": 60, "width": 25},
                    {"type": "DATE", "positionX": 40, "positionY": 60, "width": 15},
                ],
            },
            {"email": DEMO_SELLER_EMAIL, "name": "Dana Reed", "role": "CC", "party": "seller"},
        ]
        if with_approver:
            people.insert(
                1,
                {
                    "email": DEMO_APPROVER_EMAIL,
                    "name": "Luis Ortega",
                    "role": "APPROVER",
                    "party": "buyer",
                    "signing_order": 1,
                },
            )
        people.append({"email": "ops@fabrikam.example", "name": "Ops", "role": "VIEWER"})
        return people

    def send(plan_id: str, event: str, **extra: Any) -> dict[str, Any]:
        plan = store.get(plan_id)
        assert plan is not None
        return engine.receive_event(
            plan["room_id"],
            headers=headers,
            payload={
                "event": event,
                "eventId": f"{plan_id}-{event}-{extra.get('recipientEmail', '')}",
                "externalId": plan["data"]["external_id"],
                "envelopeId": plan_id,
                **extra,
            },
            actor="vendor",
            source=webhook_source,
        )

    template_source_value = engine.create_template(
        approved_room,
        {
            "name": "Standard mutual action plan",
            "document_name": "northwind-mutual-action-plan.pdf",
            "roles": ["SIGNER", "APPROVER", "CC", "VIEWER"],
            "fields": [
                {"type": "SIGNATURE", "positionX": 10, "positionY": 60, "width": 25},
                {"type": "NAME", "positionX": 10, "positionY": 70, "width": 25},
                {"type": "DATE", "positionX": 40, "positionY": 60, "width": 15},
            ],
            "invite_path": "embed",
        },
        actor="dana",
        source=template_source,
    )

    # -- room one: a plan that reached Approved ---------------------------- #
    approved = engine.create_plan(
        approved_room,
        {
            "subject": "Mutual action plan - Northwind enterprise rollout",
            "message": "Three milestones. Both sides sign.",
            "template_id": template_source_value["id"],
            "webhook_secret": DEMO_SECRET,
            "invite_path": "embed",
            "recipients": recipients(),
            "owner": "dana",
        },
        actor="dana",
        source=create_source,
    )
    engine.distribute(approved_room, approved["id"], actor="dana", source=distribute_source)
    send(approved["id"], "DOCUMENT_OPENED", recipientEmail=DEMO_SIGNER_EMAIL)
    send(approved["id"], "DOCUMENT_SIGNED", recipientEmail=DEMO_APPROVER_EMAIL)
    send(approved["id"], "DOCUMENT_SIGNED", recipientEmail=DEMO_SIGNER_EMAIL)
    send(
        approved["id"],
        "DOCUMENT_RECIPIENT_COMPLETED",
        recipientEmail=DEMO_APPROVER_EMAIL,
        completedAt="2026-09-28T10:14:00Z",
    )
    # A retry of the approver's completion. Counted, applied once.
    duplicate_outcome = "not counted"
    try:
        send(approved["id"], "DOCUMENT_RECIPIENT_COMPLETED", recipientEmail=DEMO_APPROVER_EMAIL)
    except DuplicateEvent:
        duplicate_outcome = OUTCOME_DUPLICATE
    send(
        approved["id"],
        "DOCUMENT_RECIPIENT_COMPLETED",
        recipientEmail=DEMO_SIGNER_EMAIL,
        completedAt="2026-09-28T10:21:00Z",
    )
    completed = send(
        approved["id"],
        EVENT_DOCUMENT_COMPLETED,
        completedAt="2026-09-28T10:22:00Z",
    )

    # An approver refusal on a second plan in the same room, so the two refusals
    # are distinguishable rows rather than one "rejected".
    approver_refused = engine.create_plan(
        approved_room,
        {
            "subject": "Mutual action plan - Contoso security review",
            "webhook_secret": DEMO_SECRET,
            "recipients": recipients(),
            "owner": "dana",
        },
        actor="dana",
        source=create_source,
    )
    engine.distribute(approved_room, approver_refused["id"], actor="dana", source=distribute_source)
    send(
        approver_refused["id"],
        "DOCUMENT_REJECTED",
        recipientEmail=DEMO_APPROVER_EMAIL,
        rejectionReason="Indemnity clause 7.2 is not acceptable to our legal team.",
    )

    # A signer refusal on a third, which is a different milestone.
    signer_refused = engine.create_plan(
        approved_room,
        {
            "subject": "Mutual action plan - Contoso phased rollout",
            "webhook_secret": DEMO_SECRET,
            "recipients": recipients(),
            "owner": "dana",
        },
        actor="dana",
        source=create_source,
    )
    engine.distribute(approved_room, signer_refused["id"], actor="dana", source=distribute_source)
    send(
        signer_refused["id"],
        "DOCUMENT_REJECTED",
        recipientEmail=DEMO_SIGNER_EMAIL,
        rejectionReason="The rollout timeline does not match our procurement window.",
    )

    # -- room two: out for signature, approver still pending --------------- #
    pending = engine.create_plan(
        pending_room,
        {
            "subject": "Mutual action plan - Contoso onboarding",
            "webhook_secret": DEMO_SECRET,
            "recipients": recipients(),
            "owner": "sam",
        },
        actor="sam",
        source=create_source,
    )
    engine.distribute(pending_room, pending["id"], actor="sam", source=distribute_source)
    send(pending["id"], "DOCUMENT_OPENED", recipientEmail=DEMO_SIGNER_EMAIL)
    send(pending["id"], "DOCUMENT_REMINDER_SENT", recipientEmail=DEMO_APPROVER_EMAIL)
    # Read, not just called: the seeder's own summary reports whether the approver
    # gate is holding this plan, so the state is shown rather than asserted here.
    blocked = engine.can_sign(pending_room, pending["id"], DEMO_SIGNER_EMAIL)

    # -- room three: never distributed, so an event is refused ------------- #
    undelivered = engine.create_plan(
        undelivered_room,
        {
            "subject": "Mutual action plan - Fabrikam draft, not sent",
            "webhook_secret": DEMO_SECRET,
            "recipients": recipients(),
            "owner": "dana",
        },
        actor="dana",
        source=create_source,
    )
    early_refusal = "not refused"
    try:
        send(undelivered["id"], "DOCUMENT_SIGNED", recipientEmail=DEMO_SIGNER_EMAIL)
    except MapError as exc:
        early_refusal = exc.code

    # A delivery with the wrong secret. Refused, and nothing written.
    wrong_secret = "not refused"
    plan_row = store.get(undelivered["id"])
    assert plan_row is not None
    try:
        engine.receive_event(
            undelivered_room,
            headers={WEBHOOK_SECRET_HEADER: "not-the-secret"},
            payload={
                "event": "DOCUMENT_OPENED",
                "externalId": plan_row["data"]["external_id"],
                "recipientEmail": DEMO_SIGNER_EMAIL,
            },
            actor="vendor",
            source=webhook_source,
        )
    except MapError as exc:
        wrong_secret = exc.code

    summary = engine.summary(approved_room)
    pending_summary = engine.summary(pending_room)
    return (
        f"{summary['plans']} plan(s) in room one: "
        f"{summary['approved']} approved off a DOCUMENT_COMPLETED, "
        f"1 refused by the approver and 1 refused by a signer as separate milestones, "
        f"{summary['events']} event(s) with a retry counted as {duplicate_outcome}, "
        f"an event on an undelivered plan refused as {early_refusal} and a wrong-secret "
        f"delivery refused as {wrong_secret}; "
        f"room two has {pending_summary['plans']} plan(s) out for signature with signing "
        f"{'unlocked' if pending['signing_unlocked'] else 'blocked: ' + str(blocked['reason'])} and "
        f"{pending_summary['events']} event(s) including a reminder; "
        f"room three has 1 plan never distributed"
        + (f"; {created} room(s) created for the per-room states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
        + ("" if completed.get("new_milestone") == MILESTONE_APPROVED else "; NOT APPROVED")
    )
