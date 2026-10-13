"""WF-110: escalate a converted conversation into an owned ticket.

The researched workflow, in full. A conversation is converted to a ticket, or a
ticket is created via the REST API or inbound. The ticket carries a ticket type, a
state and, when converted, a linked conversation. The ``A ticket is created``
trigger fires and its actions run: notify the owning team in Slack, change the
assigned team, apply an SLA, tag the linked conversation. A second trigger on
``Teammate changes the state of a ticket`` fires when a teammate changes the
state, and the state transitions emit ``ticket.state.updated`` /
``ticket.resolved`` / ``ticket.closed`` webhooks for downstream systems.

The domain logic is in :mod:`dsr.conversation_tickets`. What lives here is the
three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-110``.** A ticket-derived prefix cannot collide with a
feature-shaped one by construction, and the host refuses a colliding
``(method, path)`` rather than shadowing it.

**``source=`` comes from the route.** Every write below passes a string built
from ``router.prefix``, so the audit row and the route table cannot drift. A
hardcoded string inside a domain method is a defect, and the same class of bug has
shipped in this codebase before: a feature's audit log kept naming a path the app
had stopped serving. ``source`` is a *required* keyword on every writing method of
:class:`~dsr.conversation_tickets.engine.TicketEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.**
:class:`~dsr.conversation_tickets.errors.TicketError` is the base of every
refusal in the package, and each subclass carries its own ``status`` and ``code``,
so one handler answers 400 for a malformed payload, 404 for a missing ticket and
409 for a ticket that conflicts with current state - which are not the same kind
of problem.

**A skip is an outcome, not an error.** A ticket outside a trigger's conditions,
a tracker-created ticket, and an action the converted-conversation gate blocks are
all outcomes the evaluation records rather than errors it raises. A data-driven
automation skips far more often than it fires, and turning a skip into an HTTP
error would make a correctly-behaving workflow look broken.

**Two events, two payload shapes, one reader.** ``ticket.resolved`` is a
state-based event and ``ticket.closed`` is separate from state transitions. They
are two topics with two payload shapes, and the domain's one reader handles both.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.conversation_tickets import inferences as inference_register, vocabulary as vocab
from dsr.conversation_tickets.engine import TicketEngine
from dsr.conversation_tickets.errors import TicketError, TicketRefused
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-110-conversation-ticket",
    "ticket": "WF-110",
    "name": "Escalate a converted conversation into an owned ticket",
    "description": (
        "A converted conversation becomes a ticket that an owning team holds. The "
        "'A ticket is created' trigger fires its background actions - notify the owning "
        "team in Slack, change the assigned team, apply an SLA, tag the linked conversation - "
        "and a second trigger fires when a teammate changes the state. Tag conversation and "
        "Apply SLA run only when the ticket was converted from a conversation. A tracker-created "
        "ticket never fires the trigger. ticket.resolved is a state transition and ticket.closed "
        "is a separate event with its own payload shape."
    ),
}

router = APIRouter(prefix="/api/wf-110", tags=["wf110"])

PREFIX = router.prefix


def get_engine(store: RecordStore = StoreDep) -> TicketEngine:
    """A :class:`TicketEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a clock, and an ``app.state`` entry is exactly the edit
    to the shared ``dsr/api.py`` that the feature host exists to make
    unnecessary. Building it here also leaves the clock a plain constructor
    argument, which is what lets a test drive the whole workflow with rows of its
    own and a clock it controls.
    """

    return TicketEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _ticket_error(request: Request, exc: TicketError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. The status rides on the exception rather
    than being decided here, because a malformed request, a missing row and a
    conflict with state that already exists are different problems and answering
    all three 400 would hide two of them.
    """

    to_dict = getattr(exc, "to_dict", None)
    body = to_dict() if callable(to_dict) else {"error": exc.code, "detail": str(exc)}
    return JSONResponse(status_code=getattr(exc, "status", 400), content=body)


EXCEPTION_HANDLERS = {TicketError: _ticket_error}


# --------------------------------------------------------------------------- #
# Audit sources
# --------------------------------------------------------------------------- #
#
# Every one built from ``router.prefix``, so the audit row names a path the host
# actually mounted. ``test_wf110_http.py`` asserts each of these against the live
# registry.

TICKET_CREATE_SOURCE = f"POST {PREFIX}/tickets"
TICKET_REPLY_SOURCE = f"POST {PREFIX}/tickets/{{ticket_id}}/reply"
TICKET_TAG_SOURCE = f"POST {PREFIX}/tickets/{{ticket_id}}/tags"
TICKET_UNTAG_SOURCE = f"DELETE {PREFIX}/tickets/{{ticket_id}}/tags/{{tag_name}}"
TICKET_LINK_SOURCE = f"POST {PREFIX}/tickets/{{ticket_id}}/link_conversation"
TICKET_UNLINK_SOURCE = f"DELETE {PREFIX}/tickets/{{ticket_id}}/unlink_conversation"
TICKET_TYPE_SOURCE = f"POST {PREFIX}/tickets/change_type"
TICKET_STATE_SOURCE = f"POST {PREFIX}/tickets/{{ticket_id}}/state"
TICKET_CLOSE_CONVERSATION_SOURCE = f"POST {PREFIX}/tickets/{{ticket_id}}/close_conversation"
TAG_SOURCE = f"POST {PREFIX}/tags"
SLA_SOURCE = f"POST {PREFIX}/sla-policies"
TRIGGER_SOURCE = f"POST {PREFIX}/triggers"
TRIGGER_PATCH_SOURCE = f"PATCH {PREFIX}/triggers/{{trigger_id}}"
TRIGGER_GO_LIVE_SOURCE = f"POST {PREFIX}/triggers/{{trigger_id}}/go-live"
TRIGGER_DELETE_SOURCE = f"DELETE {PREFIX}/triggers/{{trigger_id}}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every researched term this workflow enforces")
def vocabulary() -> dict[str, Any]:
    """The terms, the labels and the refusal codes, as data.

    The page renders its pickers from this and the validator raises from the same
    tuples, so a term added in one place reaches every client at once.
    """

    return vocab.published_vocabulary()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inferences() -> dict[str, Any]:
    """The decisions the specification left open, and the alternative each rejected."""

    return inference_register.describe()


@router.get("/decisions/{decision_id}", summary="One recorded decision")
def read_decision(decision_id: str) -> dict[str, Any]:
    entry = inference_register.by_id(decision_id)
    if entry is None:
        raise TicketRefused(
            f"No recorded decision {decision_id!r}; this workflow recorded "
            f"{len(inference_register.INFERENCES)}.",
            {"decision_id": "unknown"},
        )
    return entry


@router.get("/teams", summary="The teams a ticket can be assigned to")
def teams() -> dict[str, Any]:
    """The known teams, with their labels. The Slack channel is named per trigger."""

    return {
        "count": len(vocab.TEAMS),
        "teams": [{"name": name, "label": vocab.TEAM_LABELS[name]} for name in vocab.TEAMS],
    }


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Tickets, states, owning teams, notifications and the un-notified count.

    The summary is computed over exactly the rows the same filters would return,
    so a room-scoped total above an unscoped list cannot be misread as a
    product-wide one. ``tickets_without_a_notification`` is the non-success state
    the workflow exists to make visible.
    """

    return engine.summary(room_id=room_id)


@router.get("/catalog", summary="The rows the workflow reads")
def catalog(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Everything a reviewer needs to understand why a trigger fired or did not."""

    return engine.catalog(room_id=room_id, limit=limit)


# --------------------------------------------------------------------------- #
# Tags: the shared workspace vocabulary
# --------------------------------------------------------------------------- #


@router.get("/tags", summary="List the workspace tag vocabulary")
def list_tags(
    limit: int = Query(default=1000, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """The tags a tag action may select. The action cannot create one."""

    records = engine.tags(limit=limit)
    return {"count": len(records), "tags": records}


@router.post("/tags", status_code=201, summary="Register a workspace tag")
def create_tag(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Register a tag in the workspace vocabulary.

    The "tag the linked conversation" action selects from this vocabulary, so a
    tag has to exist before a trigger can name it. A duplicate name is refused
    rather than silently merged, because two tags that print the same are one tag
    to every reader.
    """

    return engine.create_tag(payload, actor=actor, source=TAG_SOURCE)


# --------------------------------------------------------------------------- #
# SLA policies
# --------------------------------------------------------------------------- #


@router.get("/sla-policies", summary="List the SLA policies")
def list_sla_policies(
    limit: int = Query(default=1000, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """The policies an apply-SLA action may select."""

    records = engine.sla_policies(limit=limit)
    return {"count": len(records), "policies": records}


@router.post("/sla-policies", status_code=201, summary="Declare an SLA policy")
def create_sla_policy(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a policy: a name, a metric and a target in hours.

    WF-108 owns SLA timers and may not be present, so this build stores the policy
    and a computed due instant and depends on nothing outside the module.
    """

    return engine.create_sla_policy(payload, actor=actor, source=SLA_SOURCE)


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


@router.get("/triggers", summary="List the triggers")
def list_triggers(
    kind: str | None = Query(default=None, description="ticket_created | ticket_state_changed"),
    room_id: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Every trigger, oldest first, with the live count and both known kinds."""

    if kind is not None and kind not in vocab.TRIGGER_KINDS:
        raise TicketRefused(
            f"kind must be one of {', '.join(vocab.TRIGGER_KINDS)}", {"kind": "unknown"}
        )
    records = engine.triggers(kind=kind, room_id=room_id)
    return {
        "count": len(records),
        "live": sum(1 for r in records if str(r["data"].get("state")) == "live"),
        "kinds": list(vocab.TRIGGER_KINDS),
        "actions": list(vocab.ACTION_KINDS),
        "triggers": records,
    }


@router.post("/triggers", status_code=201, summary="Build a trigger")
def create_trigger(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Build a trigger from a kind, conditions, actions and an optional channel.

    A trigger is a draft until it is set live, because step 5 of the user flow is
    "Save and set live". A draft fires nothing.
    """

    room_id = str(payload.get("room_id") or "").strip() or None
    return engine.create_trigger(payload, actor=actor, source=TRIGGER_SOURCE, room_id=room_id)


@router.get("/triggers/{trigger_id}", summary="One trigger")
def read_trigger(trigger_id: str, engine: TicketEngine = EngineDep) -> dict[str, Any]:
    return engine.require_trigger(trigger_id)


@router.patch("/triggers/{trigger_id}", summary="Edit a trigger")
def patch_trigger(
    trigger_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Edit the conditions, actions, channel or name. Every field is revalidated."""

    return engine.update_trigger(trigger_id, payload, actor=actor, source=TRIGGER_PATCH_SOURCE)


@router.post("/triggers/{trigger_id}/go-live", summary="Set a trigger live")
def go_live(
    trigger_id: str,
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Step 5: "Save and set live". A draft fires nothing until this call."""

    return engine.go_live(trigger_id, actor=actor, source=TRIGGER_GO_LIVE_SOURCE)


@router.delete("/triggers/{trigger_id}", summary="Remove a trigger")
def delete_trigger(
    trigger_id: str,
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a trigger. The evaluations it produced stay, because they
    explain why a ticket was or was not escalated."""

    return engine.delete_trigger(trigger_id, actor=actor, source=TRIGGER_DELETE_SOURCE)


# --------------------------------------------------------------------------- #
# Tickets
# --------------------------------------------------------------------------- #


@router.get("/tickets", summary="List tickets")
def list_tickets(
    room_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    team: str | None = Query(default=None),
    ticket_type: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Every ticket, oldest first, filterable by room, state, team and type.

    The response carries the view shapes the page renders: each ticket's converted
    flag, its owning team and its labels.
    """

    records = engine.tickets(
        room_id=room_id, state=state, team=team, ticket_type=ticket_type, limit=limit
    )
    return {
        "count": len(records),
        "tickets": [engine.ticket_view(record) for record in records],
        "types": list(vocab.TICKET_TYPES),
        "states": list(vocab.TICKET_STATES),
        "teams": list(vocab.TEAMS),
    }


@router.post("/tickets", status_code=201, summary="Create a ticket, or convert a conversation")
def create_ticket(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Create a ticket and fire the ticket-created trigger.

    Pass ``conversation_id`` to convert an existing WF-107 conversation; the
    ticket then carries a linked conversation and the gated actions may run. Pass
    ``created_via: tracker`` to record a tracker-created ticket: it is created and
    stored, and the trigger records the published ``tracker_created_ticket`` skip.

    The response carries the ticket, every evaluation, every notification the
    actions queued and every webhook they emitted, so one call shows the whole
    outcome rather than only the row.
    """

    return engine.create_ticket(payload, actor=actor, source=TICKET_CREATE_SOURCE)


@router.post("/tickets/search", summary="Find tickets")
def search_tickets(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Find tickets by type, state, team, room and a subject substring."""

    records = engine.search(payload)
    return {"count": len(records), "tickets": [engine.ticket_view(r) for r in records]}


@router.post("/tickets/change_type", summary="Change a ticket's type")
def change_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Change a ticket's type. "POST /tickets/change_type"."""

    ticket_id = str(payload.get("ticket_id") or "").strip()
    if not ticket_id:
        raise TicketRefused("change_type needs a ticket_id", {"ticket_id": "required"})
    updated = engine.change_type(ticket_id, payload, actor=actor, source=TICKET_TYPE_SOURCE)
    return {"ticket": engine.ticket_view(updated)}


@router.get("/tickets/{ticket_id}", summary="One ticket")
def read_ticket(ticket_id: str, engine: TicketEngine = EngineDep) -> dict[str, Any]:
    """One ticket with its converted flag, owning team and labels."""

    return engine.ticket_view(engine.require_ticket(ticket_id))


@router.post("/tickets/{ticket_id}/reply", summary="Reply to a ticket, or add an internal note")
def reply(
    ticket_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """An admin reply or internal note. "POST /tickets/{id}/reply"."""

    return engine.reply(ticket_id, payload, actor=actor, source=TICKET_REPLY_SOURCE)


@router.post("/tickets/{ticket_id}/tags", summary="Tag a ticket")
def add_tag(
    ticket_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Apply a workspace tag to a ticket. The tag must already exist."""

    updated = engine.add_tag(ticket_id, payload, actor=actor, source=TICKET_TAG_SOURCE)
    return {"ticket": engine.ticket_view(updated)}


@router.delete("/tickets/{ticket_id}/tags/{tag_name}", summary="Remove a tag from a ticket")
def remove_tag(
    ticket_id: str,
    tag_name: str,
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Remove a tag the ticket carries. "DELETE /tickets/{ticket_id}/tags/{tag_id}"."""

    updated = engine.remove_tag(ticket_id, tag_name, actor=actor, source=TICKET_UNTAG_SOURCE)
    return {"ticket": engine.ticket_view(updated)}


@router.post(
    "/tickets/{ticket_id}/link_conversation",
    summary="Link a conversation, which converts the ticket",
)
def link_conversation(
    ticket_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Link a conversation to a ticket.

    The converted-conversation predicate reads this one field, so linking a
    conversation is what turns the gated actions on for a ticket that had none.
    """

    return engine.link_conversation(ticket_id, payload, actor=actor, source=TICKET_LINK_SOURCE)


@router.delete(
    "/tickets/{ticket_id}/unlink_conversation",
    summary="Unlink the conversation",
)
def unlink_conversation(
    ticket_id: str,
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Unlink the conversation. The ticket stops being converted."""

    return engine.unlink_conversation(ticket_id, actor=actor, source=TICKET_UNLINK_SOURCE)


@router.post("/tickets/{ticket_id}/state", summary="Change a ticket's state")
def change_state(
    ticket_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """A teammate changes the state.

    The transition emits ``ticket.state.updated`` and, into ``resolved``, the
    state-based ``ticket.resolved``. It then fires the state-changed trigger, so
    the owning team is notified on a state change as well as on creation.
    """

    return engine.change_state(ticket_id, payload, actor=actor, source=TICKET_STATE_SOURCE)


@router.post(
    "/tickets/{ticket_id}/close_conversation",
    summary="Close the linked conversation and emit ticket.closed",
)
def close_conversation(
    ticket_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Close the ticket's conversation and emit ``ticket.closed``.

    "ticket.closed ... This is separate from state transitions." So this route
    closes the conversation and emits the nested payload; it does not change the
    ticket's state.
    """

    return engine.close_conversation(
        ticket_id, payload, actor=actor, source=TICKET_CLOSE_CONVERSATION_SOURCE
    )


@router.get("/tickets/{ticket_id}/activity", summary="The ticket event log")
def ticket_activity(ticket_id: str, engine: TicketEngine = EngineDep) -> dict[str, Any]:
    """The events in order: created, linked, assigned, replied, tagged, changed."""

    return engine.activity(ticket_id)


# --------------------------------------------------------------------------- #
# The reads for what the workflow emitted
# --------------------------------------------------------------------------- #


@router.get("/notifications", summary="The queued owning-team notifications")
def notifications(
    ticket_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Every notification the actions queued, newest last.

    Each row carries ``delivered_by_this_product: False``, because WF-112 owns
    Slack delivery. A ticket with no row here was never notified.
    """

    return engine.notifications(ticket_id=ticket_id, limit=limit)


@router.get("/webhooks", summary="Every ticket webhook this workflow emitted")
def webhooks(
    ticket_id: str | None = Query(default=None),
    topic: str | None = Query(default=None, description="A ticket.* topic"),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Every emitted webhook, with its payload and the ticket parsed from it.

    ``parsed_ticket`` is produced by the domain's one reader, which handles both
    payload shapes. A reader that assumed one shape would return an empty ticket
    for the other.
    """

    if topic is not None and topic not in vocab.WEBHOOK_TOPICS:
        raise TicketRefused(
            f"topic must be one of {', '.join(vocab.WEBHOOK_TOPICS)}", {"topic": "unknown"}
        )
    return engine.webhooks(ticket_id=ticket_id, topic=topic, limit=limit)


@router.get("/evaluations", summary="Every trigger decision, fired or skipped")
def evaluations(
    ticket_id: str | None = Query(default=None),
    trigger_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: TicketEngine = EngineDep,
) -> dict[str, Any]:
    """Every trigger's decision for every ticket event, with the skip reason.

    A skip is recorded here rather than raised, so a workflow that matched nobody
    has a trail rather than silence.
    """

    return engine.evaluations(ticket_id=ticket_id, trigger_id=trigger_id, limit=limit)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The workspace tags the demo triggers may select. The research is explicit that
#: a tag action cannot create a tag, so these exist before any trigger names one.
DEMO_TAGS: tuple[str, ...] = ("priority customer", "billing follow-up")
#: The demo SLA policy.
DEMO_SLA_POLICY = "Escalation response"


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed one room's tickets and the states the research names.

    Deliberately mixed. A demo of only clean escalations would teach nothing about
    the four states this workflow exists for: a converted ticket whose trigger ran
    every action, a tracker-created ticket the exclusion skips, a ticket outside
    the trigger's ticket types, an unconverted ticket whose gated actions are
    skipped, and a resolved ticket whose state change fired the second trigger.
    All of them run here and are expected.

    The last line the seed proves is the non-success state the issue requires: at
    least one ticket was created and produced no owning-team notification.

    Every character of the returned string is encodable by cp1252, because the
    seeder prints it on a Windows console whose codec is cp1252. A single
    RIGHTWARDS ARROW in one recovered feature broke the entire seeder.
    """

    store = RecordStore(db)
    clock = context.get("now")
    if clock is None:
        clock = datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    engine = TicketEngine(store, clock=lambda: clock)

    given = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:1]
    created_room = False
    if not room_ids:
        record = store.create(
            "room",
            {"name": "Northwind Traders - ticket escalation", "account": "Northwind Traders"},
        )
        room_ids.append(record["id"])
        created_room = True
    room_id = room_ids[0]

    for name in DEMO_TAGS:
        engine.create_tag({"name": name}, actor="dana", source=TAG_SOURCE)
    engine.create_sla_policy(
        {"name": DEMO_SLA_POLICY, "metric": vocab.SLA_METRIC_TIME_TO_RESOLUTION, "target_hours": 8},
        actor="dana",
        source=SLA_SOURCE,
    )

    escalation = engine.create_trigger(
        {
            "name": "Escalate a created escalation ticket",
            "kind": vocab.TRIGGER_TICKET_CREATED,
            "channel": "#escalations",
            "goal": "Own the escalation and tell the escalations team",
            "conditions": {"ticket_types": [vocab.TYPE_ESCALATION]},
            "actions": [
                {"kind": vocab.ACTION_ASSIGN_TEAM, "team": vocab.TEAM_ESCALATIONS},
                {"kind": vocab.ACTION_NOTIFY_TEAM, "team": vocab.TEAM_ESCALATIONS},
                {"kind": vocab.ACTION_APPLY_SLA, "policy": DEMO_SLA_POLICY},
                {"kind": vocab.ACTION_TAG_CONVERSATION, "tag": "priority customer"},
            ],
            "room_id": room_id,
        },
        actor="dana",
        source=TRIGGER_SOURCE,
        room_id=room_id,
    )
    engine.go_live(str(escalation["id"]), actor="dana", source=TRIGGER_GO_LIVE_SOURCE)

    state_change = engine.create_trigger(
        {
            "name": "Tell the owner when the state changes",
            "kind": vocab.TRIGGER_STATE_CHANGED,
            "channel": "#escalations",
            "goal": "Keep the owning team informed",
            "conditions": {
                "ticket_types": [vocab.TYPE_ESCALATION],
                "to_states": [vocab.STATE_RESOLVED],
            },
            "actions": [{"kind": vocab.ACTION_NOTIFY_TEAM}],
            "room_id": room_id,
        },
        actor="dana",
        source=TRIGGER_SOURCE,
        room_id=room_id,
    )
    engine.go_live(str(state_change["id"]), actor="dana", source=TRIGGER_GO_LIVE_SOURCE)

    def conversation(subject: str) -> str:
        record = store.create(
            vocab.CONVERSATION_COLLECTION,
            {
                vocab.ROOM_REF: room_id,
                "state": vocab.CONVERSATION_OPEN,
                "origin": vocab.CONVERSATION_ORIGIN_API,
                "inbox": "sales",
                "tags": [],
                "priority": False,
                "subject": subject,
            },
            room_id=room_id,
            actor="dana",
            source=TICKET_CREATE_SOURCE,
        )
        return str(record["id"])

    converted = engine.create_ticket(
        {
            "subject": "Platform availability for 400 seats",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_CONVERSATION,
            "conversation_id": conversation("Platform availability for 400 seats"),
            "contact_email": "buyer@northwind.example",
            "room_id": room_id,
        },
        actor="dana",
        source=TICKET_CREATE_SOURCE,
    )

    engine.create_ticket(
        {
            "subject": "Tracker-raised operational item",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_TRACKER,
            "room_id": room_id,
        },
        actor="dana",
        source=TICKET_CREATE_SOURCE,
    )

    engine.create_ticket(
        {
            "subject": "Renewal billing question",
            "ticket_type": vocab.TYPE_BILLING,
            "created_via": vocab.VIA_API,
            "room_id": room_id,
        },
        actor="dana",
        source=TICKET_CREATE_SOURCE,
    )

    engine.create_ticket(
        {
            "subject": "Security questionnaire from the partner",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_INBOUND,
            "team": vocab.TEAM_SALES,
            "room_id": room_id,
        },
        actor="dana",
        source=TICKET_CREATE_SOURCE,
    )

    resolved = engine.change_state(
        str(converted["ticket"]["id"]),
        {"state": vocab.STATE_RESOLVED},
        actor="dana",
        source=TICKET_STATE_SOURCE,
    )

    closed = engine.close_conversation(
        str(converted["ticket"]["id"]),
        {},
        actor="dana",
        source=TICKET_CLOSE_CONVERSATION_SOURCE,
    )

    counts = engine.summary(room_id=room_id)
    evaluation_report = engine.evaluations(limit=1000)
    webhook_report = engine.webhooks(limit=1000)
    notification_report = engine.notifications(limit=1000)
    gate_skips = [
        action
        for record in evaluation_report["evaluations"]
        for action in (record["data"].get("actions") or [])
        if str(action.get("outcome")) == vocab.ACTION_SKIPPED_NOT_CONVERTED
    ]
    return (
        f"{counts['tickets']} ticket(s) in one room; "
        f"{counts['notified_tickets']} notified and "
        f"{counts['tickets_without_a_notification']} with no owning-team notification "
        f"(one tracker-created, one outside the trigger's ticket types); "
        f"{counts['triggers']} trigger(s), {counts['live_triggers']} live "
        f"(one ticket-created, one state-changed); "
        f"{counts['converted']} converted; "
        f"{len(gate_skips)} action(s) skipped because the ticket was not converted; "
        f"{notification_report['count']} notification(s) queued and none delivered by this product; "
        f"{webhook_report['count']} webhook(s) emitted: "
        f"{counts['by_state'].get(vocab.STATE_RESOLVED, 0)} resolved, "
        f"ticket.closed nested under data.item; "
        f"the resolved ticket is {resolved['state']} and its conversation is "
        f"{closed['conversation']['state']}"
        + (f"; {created_room} room created for these states" if created_room else "")
        + f"; {len(given)} room(s) from the seeder"
    )
