"""WF-108: run SLA response timers with office-hour awareness.

An SLA policy carries first reply, next reply, time to close and (for tickets) time to
resolution targets. Each target runs as a live timer that counts office hours and pauses
while the conversation is snoozed or waiting on the customer. The timer's state is
written back to the inbox row as a badge and a nearest target, and the aggregate
hit/miss/fixed rates feed the SLAs report.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-108`` and the routes are room-scoped.** The research's join key
is a conversation's message timestamps and the room is what a seller is looking at when
they ask which of their conversations is about to miss.

**The timer is a route, not a thread.** "The SLA timer is a continuous background timer,
not a discrete job." This product runs no worker, a thread needs a clock no test can move,
it writes audit rows naming no route, and under pytest-xdist it races. ``POST
/rooms/{id}/timers/evaluate`` is the sweep, and the timer state is also a pure projection
readable through ``GET /rooms/{id}/timers/{timer_id}``. Put to Jev as audit
``jev-20261013T171356-7564-36737``, which selected it at confidence 1.00.

**The conversation vocabulary is WF-107's.** This feature reads the conversation records
and message parts WF-107 owns, and the office-hours calendar WF-107 owns, rather than
declaring a second copy of either. The pair of WF-107 write routes it uses are named as
constants so a seeded row's audit still names a route the app serves.

**Only one active SLA per conversation.** ``apply`` soft-deletes the existing timer in the
same transaction as the new insert, so a conversation is never observed with two.

**One handler for the whole error hierarchy.** :class:`~dsr.sla_timers.rules.SlaRefusal`
is the base of every refusal the rules raise and each carries its own published ``code``
and ``status``. The three not-found types are claimed separately and they are this
feature's own types, because a feature may only map error types it raises itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.conversation_chase.engine import ConversationChaseEngine
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.sla_timers import inferences as inference_register, rules, vocabulary as vocab
from dsr.sla_timers.engine import SlaEngine
from dsr.sla_timers.rules import ConversationNotFound, PolicyNotFound, SlaRefusal, TimerNotFound
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-108-sla-timers",
    "ticket": "WF-108",
    "name": "Run SLA response timers with office-hour awareness",
    "description": (
        "An SLA policy with first reply, next reply, time to close and (for tickets) time "
        "to resolution targets, run as a live timer that counts office hours and pauses "
        "while the conversation is snoozed or waiting on the customer. The timer's state "
        "is written back to the inbox row as a badge and a nearest target, only one SLA is "
        "active per conversation, an already-breached snoozed timer reopens its "
        "conversation, and the aggregate hit/miss/fixed rates feed the SLAs report."
    ),
}

router = APIRouter(prefix="/api/wf-108", tags=["wf108"])

PREFIX = router.prefix


def get_engine(store: RecordStore = StoreDep) -> SlaEngine:
    """An :class:`SlaEngine` over the process-wide audited store.

    Built per request rather than held on ``app.state``, because putting it there is the
    edit to the shared ``dsr/api.py`` this feature host exists to make unnecessary. The
    engine holds a store handle and a clock and nothing else, so a test can build one
    directly and move the clock by hand.
    """

    return SlaEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _sla_refusal(request: Request, exc: SlaRefusal) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": exc.detail,
            "status": exc.status,
            "errors": exc.errors,
        },
    )


def _policy_not_found(request: Request, exc: PolicyNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "policy_not_found", "detail": str(exc)})


def _timer_not_found(request: Request, exc: TimerNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "timer_not_found", "detail": str(exc)})


def _conversation_not_found(request: Request, exc: ConversationNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "conversation_not_found", "detail": str(exc)}
    )


EXCEPTION_HANDLERS = {
    SlaRefusal: _sla_refusal,
    PolicyNotFound: _policy_not_found,
    TimerNotFound: _timer_not_found,
    ConversationNotFound: _conversation_not_found,
}


# --------------------------------------------------------------------------- #
# Audit sources
# --------------------------------------------------------------------------- #
#
# Every one built from ``router.prefix``, so the audit row names a path the host actually
# mounted. test_wf108_http.py asserts each of these against the live registry.

POLICY_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/policies"
POLICY_PATCH_SOURCE = f"PATCH {PREFIX}/rooms/{{room_id}}/policies/{{policy_id}}"
POLICY_DELETE_SOURCE = f"DELETE {PREFIX}/rooms/{{room_id}}/policies/{{policy_id}}"
APPLY_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla"
REMOVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla/remove"
EVALUATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/timers/evaluate"
OFFICE_HOURS_SOURCE = f"PUT {PREFIX}/rooms/{{room_id}}/office-hours"

#: WF-107's conversation and message routes. This feature reads WF-107's records and its
#: seed writes them through WF-107's own engine, so the audit row names WF-107's route
#: rather than a path this feature does not serve.
CONVERSATION_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations"
MESSAGE_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations/{conversation_id}/messages"
CLOSE_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations/{conversation_id}/close"
SNOOZE_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations/{conversation_id}/snooze"

SEED_SOURCES = (
    POLICY_SOURCE,
    APPLY_SOURCE,
    EVALUATE_SOURCE,
    OFFICE_HOURS_SOURCE,
    CONVERSATION_SEED_SOURCE,
    MESSAGE_SEED_SOURCE,
    CLOSE_SEED_SOURCE,
    SNOOZE_SEED_SOURCE,
)


# --------------------------------------------------------------------------- #
# The published vocabulary and the judgement calls
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every researched term and every refusal code")
def vocabulary() -> dict[str, Any]:
    """The terms, the labels and the refusal codes, as data.

    The page renders its pickers from this and the validator raises from the same tuples,
    so a term added in one place reaches every client at once.
    """

    return vocab.published_vocabulary()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inferences_route() -> dict[str, Any]:
    """The decisions the specification left open, and the alternative each rejected."""

    return inference_register.inferences_report()


@router.get("/decisions/{decision_id}", summary="One recorded decision")
def read_decision(decision_id: str) -> dict[str, Any]:
    entry = inference_register.decision_by_id(decision_id)
    if entry is None:
        raise SlaRefusal(
            "decision_not_found",
            "decision_id",
            f"No recorded decision {decision_id!r}; this workflow recorded "
            f"{inference_register.inferences_report()['count']}.",
        )
    return entry


# --------------------------------------------------------------------------- #
# Policies
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/policies", summary="A room's SLA policies")
def list_policies(
    room_id: str,
    include_archived: bool = Query(default=False),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    policies = engine.policies(room_id, include_archived=include_archived)
    return {
        "room_id": room_id,
        "policies": policies,
        "count": len(policies),
        "metrics": list(vocab.METRIC_KINDS),
        "metric_labels": dict(vocab.METRIC_LABELS),
        "pause_reasons": list(vocab.PAUSE_REASONS),
    }


@router.post("/rooms/{room_id}/policies", status_code=201, summary="Create an SLA policy")
def create_policy(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """Name the policy and set its targets.

    A policy with no explicit target takes the derived defaults; a policy that sends an
    empty targets object is refused, because an SLA policy with no target is not a policy.
    """

    return engine.create_policy(room_id, payload, source=POLICY_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/policies/{policy_id}", summary="One SLA policy")
def read_policy(room_id: str, policy_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    return engine.policy_view(policy_id)


@router.patch("/rooms/{room_id}/policies/{policy_id}", summary="Edit an SLA policy")
def patch_policy(
    room_id: str,
    policy_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """Edit targets, pause rules or fields. Every field is revalidated on the way in."""

    return engine.update_policy(policy_id, payload, source=POLICY_PATCH_SOURCE, actor="seller")


@router.delete("/rooms/{room_id}/policies/{policy_id}", summary="Archive an SLA policy")
def delete_policy(room_id: str, policy_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    """Archive a policy. Its timers keep the policy snapshot they were created with."""

    return engine.delete_policy(policy_id, source=POLICY_DELETE_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/policies/{policy_id}/used-by", summary="Who uses this SLA")
def used_by(room_id: str, policy_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    """The workflows and conversations that depend on a policy, for the Used by column."""

    return engine.used_by(policy_id)


# --------------------------------------------------------------------------- #
# Applying an SLA
# --------------------------------------------------------------------------- #


@router.get(
    "/rooms/{room_id}/conversations/{conversation_id}/sla",
    summary="A conversation's active SLA",
)
def read_conversation_sla(
    room_id: str, conversation_id: str, engine: SlaEngine = EngineDep
) -> dict[str, Any]:
    """The timer, or ``null`` when the conversation has none. Never a 404 for absence."""

    return {
        "room_id": room_id,
        "conversation_id": conversation_id,
        "timer": engine.timer_for(conversation_id),
    }


@router.post(
    "/rooms/{room_id}/conversations/{conversation_id}/sla",
    status_code=201,
    summary="Apply an SLA to a conversation",
)
def apply_sla(
    room_id: str,
    conversation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """Apply a policy, removing any existing active SLA in the same transaction.

    "Intercom supports only one active SLA per conversation, so when a Workflow applies a
    new SLA later, the existing SLA is automatically removed."
    """

    return engine.apply(conversation_id, payload, source=APPLY_SOURCE, actor="seller")


@router.post(
    "/rooms/{room_id}/conversations/{conversation_id}/sla/remove",
    summary="Remove a conversation's active SLA",
)
def remove_sla(room_id: str, conversation_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    """Remove the active timer. A conversation with none is a conflict, not a success."""

    return engine.remove(conversation_id, source=REMOVE_SOURCE, actor="seller")


@router.get(
    "/rooms/{room_id}/conversations/{conversation_id}/activity",
    summary="A conversation's SLA event log",
)
def conversation_activity(
    room_id: str, conversation_id: str, engine: SlaEngine = EngineDep
) -> dict[str, Any]:
    rows = engine.activity(room_id, conversation_id=conversation_id)
    return {
        "room_id": room_id,
        "conversation_id": conversation_id,
        "activity": rows,
        "count": len(rows),
    }


# --------------------------------------------------------------------------- #
# The sweep and the reads
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/timers/evaluate",
    summary="Recompute every active timer and log the transitions",
)
def evaluate(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """One pass over the room's active timers.

    Nothing is due until somebody calls this, which is the cost of the explicit-route
    decision, and it is reported rather than hidden: the response counts the timers by
    state and names the transitions it wrote. A breach on a snoozed conversation reopens
    it, per the auto-unsnooze rule.
    """

    return engine.evaluate(room_id, payload, source=EVALUATE_SOURCE, actor="scheduler")


@router.get("/rooms/{room_id}/timers/{timer_id}", summary="One SLA timer")
def read_timer(room_id: str, timer_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    """One timer, its four metrics, its badge and its nearest target."""

    return engine.timer_view(timer_id)


@router.get("/rooms/{room_id}/inbox", summary="The inbox, sorted by next SLA target")
def inbox(
    room_id: str,
    sort: str | None = Query(default=None, description="Use 'next_target' for the SLA sort"),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """Every conversation with its badge and nearest target."""

    return engine.inbox(room_id, sort=sort)


@router.get("/rooms/{room_id}/report", summary="The hit, miss and fixed rates")
def report(
    room_id: str,
    policy_id: str | None = Query(default=None),
    team: str | None = Query(default=None),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """The aggregate rates per SLA, optionally filtered by policy or team."""

    return engine.report(room_id, policy_id=policy_id, team=team)


@router.get("/rooms/{room_id}/office-hours", summary="The room's office hours")
def read_office_hours(room_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    """The weekly schedule an SLA counts against. WF-107's stored schedule, or the default."""

    return engine.office_hours(room_id)


@router.put("/rooms/{room_id}/office-hours", summary="Set the room's office hours")
def save_office_hours(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEngine = EngineDep,
) -> dict[str, Any]:
    """Write the weekly schedule. Validated before the write."""

    return engine.save_office_hours(room_id, payload, source=OFFICE_HOURS_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/summary", summary="Counts for the page header")
def room_summary(room_id: str, engine: SlaEngine = EngineDep) -> dict[str, Any]:
    """Policies, active timers, timers by state, and the office-hours calendar."""

    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

MINUTE = 60


def _previous_weekday_930(moment: datetime) -> datetime:
    """09:30 on the most recent weekday before ``moment``.

    Fixed relative to the clock the seeder hands in, never to today's date. Anchoring a
    demo breach to a weekday morning guarantees a full open window has elapsed whatever
    day the seed runs, which a "now minus two hours" anchor does not.
    """

    day = (moment - timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    for _ in range(7):
        if day.weekday() < 5:
            break
        day = (day - timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    return day


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Five conversations carrying the states the page has to show.

    The rows are produced by running the real engines, so the demo cannot show a shape the
    workflow would not produce. Every instant derives from ``context["now"]`` and is
    anchored to a weekday morning, so nothing here carries a fixed date and the
    office-hours cases hold whenever the seeder runs.

    It is deliberately mixed, because a demo of only green teaches a reviewer nothing:
    one conversation meets every target, one is breached on its first reply, one is paused
    while waiting on the customer, one is reopened by the auto-unsnooze rule, and one is a
    ticket carrying time to resolution instead of time to close.

    Every character in the returned string is encodable by cp1252. The seeder prints it on
    a Windows console, and a single RIGHTWARDS ARROW in one recovered feature broke the
    whole seed.
    """

    store = RecordStore(db)
    clock = context.get("now")
    if clock is None:
        clock = datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    given = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:1]
    created = 0
    if not room_ids:
        name = "Northwind Traders - SLA timers"
        record = store.create("room", {"name": name, "account": name.split(" - ")[0]}, actor="dana")
        room_ids.append(record["id"])
        created += 1
    room_id = room_ids[0]

    base = _previous_weekday_930(clock)
    earlier = _previous_weekday_930(base)

    def at(moment: datetime) -> str:
        return rules.stamp(moment)

    def engine_at(moment: datetime) -> SlaEngine:
        return SlaEngine(store, now=lambda: moment)

    def chase_at(moment: datetime) -> ConversationChaseEngine:
        return ConversationChaseEngine(store, now=lambda: moment)

    # -- the calendar and two policies ---------------------------------------- #
    builder = engine_at(base)
    builder.save_office_hours(
        room_id,
        {"schedule": rules.default_schedule(), "timezone": "UTC"},
        source=OFFICE_HOURS_SOURCE,
        actor="dana",
    )
    enterprise = builder.create_policy(
        room_id,
        {
            "name": "Enterprise first response",
            "description": "A fast first reply for enterprise leads.",
            "targets": {"frt": 15 * MINUTE, "nrt": 30 * MINUTE, "ttc": 8 * 60 * MINUTE},
            "pause_reasons": list(vocab.DEFAULT_PAUSE_REASONS),
            "object_kind": vocab.KIND_CONVERSATION,
            "teams": ["enterprise"],
            "workflow_name": "Escalate enterprise leads",
            "live": True,
        },
        source=POLICY_SOURCE,
        actor="dana",
    )
    standard = builder.create_policy(
        room_id,
        {
            "name": "Standard service",
            "description": "The default service targets for a sales conversation.",
            "targets": {"frt": 60 * MINUTE, "nrt": 4 * 60 * MINUTE, "ttc": 24 * 60 * MINUTE},
            "pause_reasons": [vocab.PAUSE_WAITING_ON_CUSTOMER],
            "object_kind": vocab.KIND_CONVERSATION,
            "teams": ["sales"],
            "workflow_name": "Standard service cover",
            "live": True,
        },
        source=POLICY_SOURCE,
        actor="dana",
    )
    tickets = builder.create_policy(
        room_id,
        {
            "name": "Ticket resolution",
            "description": "Resolution targets for ticket-shaped work.",
            "targets": {"frt": 30 * MINUTE, "nrt": 2 * 60 * MINUTE, "ttr": 2 * 24 * 60 * MINUTE},
            "pause_reasons": list(vocab.DEFAULT_PAUSE_REASONS),
            "object_kind": vocab.KIND_TICKET,
            "teams": ["support"],
            "workflow_name": "Ticket triage",
            "live": True,
        },
        source=POLICY_SOURCE,
        actor="dana",
    )

    # -- the conversations ---------------------------------------------------- #
    def open_conversation(subject: str, inbox: str = "sales") -> str:
        chase = chase_at(base)
        view = chase.open_conversation(
            room_id,
            {"origin": "inbox", "inbox": inbox, "subject": subject},
            source=CONVERSATION_SEED_SOURCE,
            actor="dana",
        )
        return view["id"]

    def says(conversation_id: str, author: str, moment: datetime, body: str) -> None:
        chase_at(moment).add_message(
            conversation_id,
            {"author_kind": author, "at": at(moment), "body": body},
            source=MESSAGE_SEED_SOURCE,
            actor="buyer" if author == "customer" else "dana",
        )

    def apply(conversation_id: str, policy: dict[str, Any]) -> None:
        engine_at(base).apply(
            conversation_id,
            {"policy_id": policy["id"], "workflow_name": policy["workflow_name"]},
            source=APPLY_SOURCE,
            actor="dana",
        )

    # 1. Meets every target: a first reply five minutes after the message, and a close.
    met = open_conversation("Platform availability", inbox="enterprise")
    apply(met, enterprise)
    says(met, "customer", base, "Is the platform available for 400 seats?")
    says(met, "teammate", base + timedelta(minutes=5), "Yes, it scales to 400 seats.")
    chase_at(base + timedelta(minutes=60)).close(met, {}, source=CLOSE_SEED_SOURCE, actor="dana")

    # 2. Breached on the first reply: the buyer asked and nobody answered.
    breached = open_conversation("Security review", inbox="enterprise")
    apply(breached, enterprise)
    says(breached, "customer", base, "Can you complete our security review?")

    # 3. Paused while waiting on the customer: the last word is the teammate's.
    paused = open_conversation("Pilot pricing")
    apply(paused, standard)
    says(paused, "customer", base, "What does a pilot cost?")
    says(paused, "teammate", base + timedelta(minutes=3), "Sending the pilot rates now.")
    says(paused, "customer", base + timedelta(minutes=10), "Thanks, and the discount?")
    says(paused, "teammate", base + timedelta(minutes=13), "The annual discount is 15 percent.")

    # 4. Already breached when it was snoozed, so the auto-unsnooze rule reopens it.
    reopened = open_conversation("Waiting on procurement")
    apply(reopened, enterprise)
    says(reopened, "customer", earlier, "Procurement needs three weeks.")
    chase_at(base).snooze(
        reopened,
        {"reason": "waiting on procurement"},
        source=SNOOZE_SEED_SOURCE,
        actor="dana",
    )

    # 5. A ticket policy, so time to resolution is a metric and time to close is not.
    ticket = open_conversation("Renewal date", inbox="support")
    apply(ticket, tickets)
    says(ticket, "customer", base, "When does our renewal fall?")

    # -- the sweep ------------------------------------------------------------ #
    sweep = engine_at(clock).evaluate(
        room_id, {"now": at(clock)}, source=EVALUATE_SOURCE, actor="scheduler"
    )
    counts = sweep["counts"]
    policies = engine_at(clock).policies(room_id)
    report = engine_at(clock).report(room_id)
    ticket_timer = engine_at(clock).timer_for(ticket)
    ttr_state = None
    ttc_state = None
    if ticket_timer:
        for entry in ticket_timer["metrics"]:
            if entry["metric"] == vocab.METRIC_TTR:
                ttr_state = entry["state"]
            if entry["metric"] == vocab.METRIC_TTC:
                ttc_state = entry["state"]

    return (
        f"{counts.get(vocab.TIMER_MET, 0)} conversation(s) met every target, "
        f"{counts.get(vocab.TIMER_BREACHED, 0)} breached, "
        f"{counts.get(vocab.TIMER_PAUSED, 0)} paused while waiting on the customer; "
        f"{len(sweep['reopened'])} snoozed conversation(s) reopened by the auto-unsnooze rule; "
        f"{len(policies)} SLA policies on a Mon-Fri 09:00-18:00 office-hours calendar; "
        f"the ticket carries time to resolution ({ttr_state}) and not time to close "
        f"({ttc_state or 'not applicable'}); report rates are "
        f"hit {report['rates'].get(vocab.OUTCOME_HIT, 0):.2f}, "
        f"miss {report['rates'].get(vocab.OUTCOME_MISS, 0):.2f}, "
        f"fixed {report['rates'].get(vocab.OUTCOME_FIXED, 0):.2f}"
        + (f"; {created} room created for these states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
    )
