"""WF-109: escalate on SLA breach with a pre-warning head start.

The room watches the live SLA countdown on each conversation and fires a workflow a chosen
lead time before the target is due, or at the moment of breach, so the inbox escalates in
priority. Two triggers sit under the **During a conversation** category: **Before SLA
breach** and **When SLA breaches**.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-109`` and the routes are room-scoped.** The research's join key
is a conversation's SLA timer and the room is what a seller is looking at when they ask
which of their conversations is about to miss.

**The sweep is a route, not a thread.** "Intercom checks SLAs about once a minute." This
product runs no worker, a thread needs a clock no test can move, it writes audit rows
naming no route, and under pytest-xdist it races. ``POST
/rooms/{id}/escalations/evaluate`` is the sweep, and the decision is also a pure function
a test can call with a frozen clock.

**The SLA vocabulary is WF-108's and the conversation vocabulary is WF-107's.** This
feature reads the timer records WF-108 owns and the conversation and message-part records
WF-107 owns rather than declaring a second copy of either. The WF-107 and WF-108 write
routes it uses in the seed are named as constants so a seeded row's audit still names a
route the app serves.

**One handler for the whole error hierarchy.** :class:`~dsr.sla_escalation.rules.EscalationRefusal`
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
from dsr.sla_escalation import inferences as inference_register, rules, vocabulary as vocab
from dsr.sla_escalation.engine import SlaEscalationEngine
from dsr.sla_escalation.rules import (
    ConversationNotFound,
    EscalationRefusal,
    RunNotFound,
    WorkflowNotFound,
)
from dsr.sla_timers import vocabulary as sla_vocab
from dsr.sla_timers.engine import SlaEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-109-sla-escalation",
    "ticket": "WF-109",
    "name": "Escalate on SLA breach with a pre-warning head start",
    "description": (
        "A workflow with a Before SLA breach or a When SLA breaches trigger watches the "
        "live SLA countdown on a conversation. The before-breach trigger fires a chosen "
        "lead time before the target is due, the on-breach trigger fires the moment the "
        "target is missed, three conditions cancel a run, and each firing applies assign, "
        "notify, tag, note and Data Connector alert actions so the inbox escalates in "
        "priority."
    ),
}

router = APIRouter(prefix="/api/wf-109", tags=["wf109"])

PREFIX = router.prefix


def get_engine(store: RecordStore = StoreDep) -> SlaEscalationEngine:
    """A :class:`SlaEscalationEngine` over the process-wide audited store.

    Built per request rather than held on ``app.state``, because putting it there is the
    edit to the shared ``dsr/api.py`` this feature host exists to make unnecessary. The
    engine holds a store handle and a clock and nothing else, so a test can build one
    directly and move the clock by hand.
    """

    return SlaEscalationEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _escalation_refusal(request: Request, exc: EscalationRefusal) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": exc.detail,
            "status": exc.status,
            "errors": exc.errors,
        },
    )


def _workflow_not_found(request: Request, exc: WorkflowNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "workflow_not_found", "detail": str(exc)}
    )


def _conversation_not_found(request: Request, exc: ConversationNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "conversation_not_found", "detail": str(exc)}
    )


def _run_not_found(request: Request, exc: RunNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "run_not_found", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    EscalationRefusal: _escalation_refusal,
    WorkflowNotFound: _workflow_not_found,
    ConversationNotFound: _conversation_not_found,
    RunNotFound: _run_not_found,
}


# --------------------------------------------------------------------------- #
# Audit sources
# --------------------------------------------------------------------------- #
#
# Every one built from ``router.prefix``, so the audit row names a path the host actually
# mounted. test_wf109_http.py asserts each of these against the live registry.

WORKFLOW_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/workflows"
WORKFLOW_PATCH_SOURCE = f"PATCH {PREFIX}/rooms/{{room_id}}/workflows/{{workflow_id}}"
WORKFLOW_DELETE_SOURCE = f"DELETE {PREFIX}/rooms/{{room_id}}/workflows/{{workflow_id}}"
GO_LIVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/workflows/{{workflow_id}}/go-live"
EVALUATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/escalations/evaluate"

#: WF-107's conversation, message and snooze routes, and WF-108's policy, apply and
#: office-hours routes. This feature reads their records and its seed writes them through
#: their own engines, so the audit row names the route that actually serves the write.
CONVERSATION_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations"
MESSAGE_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations/{conversation_id}/messages"
SNOOZE_SEED_SOURCE = "POST /api/wf-107/rooms/{room_id}/conversations/{conversation_id}/snooze"
POLICY_SEED_SOURCE = "POST /api/wf-108/rooms/{room_id}/policies"
APPLY_SEED_SOURCE = "POST /api/wf-108/rooms/{room_id}/conversations/{conversation_id}/sla"
OFFICE_HOURS_SEED_SOURCE = "PUT /api/wf-108/rooms/{room_id}/office-hours"

SEED_SOURCES = (
    WORKFLOW_SOURCE,
    EVALUATE_SOURCE,
    CONVERSATION_SEED_SOURCE,
    MESSAGE_SEED_SOURCE,
    SNOOZE_SEED_SOURCE,
    POLICY_SEED_SOURCE,
    APPLY_SEED_SOURCE,
    OFFICE_HOURS_SEED_SOURCE,
)


# --------------------------------------------------------------------------- #
# The published vocabulary and the judgement calls
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every researched term and every refusal code")
def vocabulary() -> dict[str, Any]:
    """The triggers, the targets, the actions, the levels and the codes, as data.

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
        raise EscalationRefusal(
            "decision_not_found",
            "decision_id",
            f"No recorded decision {decision_id!r}; this workflow recorded "
            f"{inference_register.inferences_report()['count']}.",
        )
    return entry


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/workflows", summary="A room's escalation workflows")
def list_workflows(
    room_id: str,
    live_only: bool = Query(default=False),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    workflows = engine.workflows(room_id, live_only=live_only)
    return {
        "room_id": room_id,
        "workflows": workflows,
        "count": len(workflows),
        "trigger_category": vocab.TRIGGER_CATEGORY,
        "trigger_category_label": vocab.TRIGGER_CATEGORY_LABEL,
        "trigger_kinds": list(vocab.TRIGGER_KINDS),
        "trigger_labels": dict(vocab.TRIGGER_LABELS),
        "metrics": list(vocab.METRIC_KINDS),
    }


@router.post("/rooms/{room_id}/workflows", status_code=201, summary="Create an escalation workflow")
def create_workflow(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    """Name the workflow, pick a trigger and a target, set a lead time, add actions.

    A before-breach workflow that names its SLA policy is refused here if the lead time is
    not shorter than that policy's target for the watched metric.
    """

    return engine.create_workflow(room_id, payload, source=WORKFLOW_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/workflows/{workflow_id}", summary="One escalation workflow")
def read_workflow(
    room_id: str, workflow_id: str, engine: SlaEscalationEngine = EngineDep
) -> dict[str, Any]:
    return engine.workflow_view(workflow_id)


@router.patch("/rooms/{room_id}/workflows/{workflow_id}", summary="Edit an escalation workflow")
def patch_workflow(
    room_id: str,
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    """Edit the trigger, the target, the lead time or the actions. Every field is revalidated."""

    return engine.update_workflow(
        workflow_id, payload, source=WORKFLOW_PATCH_SOURCE, actor="seller"
    )


@router.delete("/rooms/{room_id}/workflows/{workflow_id}", summary="Archive an escalation workflow")
def delete_workflow(
    room_id: str, workflow_id: str, engine: SlaEscalationEngine = EngineDep
) -> dict[str, Any]:
    """Archive a workflow. Its runs keep the history they recorded."""

    return engine.delete_workflow(workflow_id, source=WORKFLOW_DELETE_SOURCE, actor="seller")


@router.post(
    "/rooms/{room_id}/workflows/{workflow_id}/go-live",
    summary="Set an escalation workflow live",
)
def go_live(
    room_id: str, workflow_id: str, engine: SlaEscalationEngine = EngineDep
) -> dict[str, Any]:
    """The flow's step 6. A workflow that is already live is a conflict, not a success."""

    return engine.go_live(workflow_id, source=GO_LIVE_SOURCE, actor="seller")


# --------------------------------------------------------------------------- #
# The sweep and the reads
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/escalations/evaluate",
    summary="Run the SLA check and fire the workflows that are due",
)
def evaluate(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    """One pass over the room's live workflows and active SLA timers.

    Nothing is due until somebody calls this, which is the cost of the explicit-route
    decision, and it is reported rather than hidden: the response names every decision, the
    reason a run was suppressed, and the runs it wrote.
    """

    return engine.evaluate(room_id, payload, source=EVALUATE_SOURCE, actor="scheduler")


@router.get("/rooms/{room_id}/runs", summary="A room's escalation runs")
def list_runs(
    room_id: str,
    workflow_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    runs = engine.runs(room_id, workflow_id=workflow_id, conversation_id=conversation_id)
    return {"room_id": room_id, "runs": runs, "count": len(runs)}


@router.get("/rooms/{room_id}/runs/{run_id}", summary="One escalation run")
def read_run(room_id: str, run_id: str, engine: SlaEscalationEngine = EngineDep) -> dict[str, Any]:
    return engine.run_view(run_id)


@router.get("/rooms/{room_id}/alerts", summary="The Data Connector execution results")
def list_alerts(
    room_id: str,
    run_id: str | None = Query(default=None),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    """Every alert this workflow attempted, with its ``success``, ``http_status`` and
    ``error_type``, so a failed alert is a row the page can show.
    """

    alerts = engine.executions(room_id, run_id=run_id)
    return {
        "room_id": room_id,
        "alerts": alerts,
        "count": len(alerts),
        "failures": sum(1 for alert in alerts if alert.get("success") is False),
        "error_types": list(vocab.ERROR_TYPES),
        "error_type_labels": dict(vocab.ERROR_TYPE_LABELS),
    }


@router.get("/rooms/{room_id}/inbox", summary="The inbox, escalated conversations first")
def inbox(
    room_id: str,
    sort: str | None = Query(default=None, description="Use 'next_target' for the SLA sort"),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    """Every conversation with its escalation level and its next SLA target."""

    return engine.escalated_inbox(room_id, sort=sort)


@router.get("/rooms/{room_id}/activity", summary="The escalation event log")
def activity(
    room_id: str,
    conversation_id: str | None = Query(default=None),
    engine: SlaEscalationEngine = EngineDep,
) -> dict[str, Any]:
    rows = engine.activity(room_id, conversation_id=conversation_id)
    return {"room_id": room_id, "activity": rows, "count": len(rows)}


@router.get("/rooms/{room_id}/summary", summary="Counts for the page header")
def room_summary(room_id: str, engine: SlaEscalationEngine = EngineDep) -> dict[str, Any]:
    """Workflows, runs, alert executions and escalated conversations by level."""

    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

MINUTE = 60
HOUR = 60 * MINUTE


def _previous_weekday_930(moment: datetime) -> datetime:
    """09:30 on the most recent weekday before ``moment``.

    Fixed relative to the clock the seeder hands in, never to today's date. Anchoring the
    demo states to weekday mornings guarantees a full office-hours window has elapsed
    whatever day the seed runs, which a "now minus two hours" anchor does not.
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
    workflows would not produce. Every instant derives from ``context["now"]`` and is
    anchored to a weekday morning, so nothing here carries a fixed date and the
    office-hours cases hold whenever the seeder runs.

    It is deliberately mixed, because a demo of only green teaches a reviewer nothing: one
    conversation takes a pre-warning, one is breached with a failed alert, one is cancelled
    by a teammate reply, one is cancelled while snoozed, and one meets its target. Two
    sweeps run so the pre-warning and the breach are both visible.

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
        name = "Northwind Traders - SLA escalation"
        record = store.create("room", {"name": name, "account": name.split(" - ")[0]}, actor="dana")
        room_ids.append(record["id"])
        created += 1
    room_id = room_ids[0]

    base = _previous_weekday_930(clock)
    earlier = _previous_weekday_930(base)

    def at(moment: datetime) -> str:
        return rules.stamp(moment)

    def escalation_at(moment: datetime) -> SlaEscalationEngine:
        return SlaEscalationEngine(store, now=lambda: moment)

    def sla_at(moment: datetime) -> SlaEngine:
        return SlaEngine(store, now=lambda: moment)

    def chase_at(moment: datetime) -> ConversationChaseEngine:
        return ConversationChaseEngine(store, now=lambda: moment)

    # -- the calendar, the policies and the workflows ------------------------- #
    builder = sla_at(base)
    # The SLA schedule is WF-108's, so this feature writes it through WF-108's own route.
    builder.save_office_hours(
        room_id, {"timezone": "UTC"}, source=OFFICE_HOURS_SEED_SOURCE, actor="dana"
    )
    enterprise = builder.create_policy(
        room_id,
        {
            "name": "Enterprise first response",
            "description": "A fast first reply for enterprise leads.",
            "targets": {"frt": 15 * MINUTE, "nrt": 30 * MINUTE, "ttc": 8 * HOUR},
            "pause_reasons": list(sla_vocab.DEFAULT_PAUSE_REASONS),
            "object_kind": sla_vocab.KIND_CONVERSATION,
            "teams": ["enterprise"],
            "workflow_name": "Escalate enterprise leads",
            "live": True,
        },
        source=POLICY_SEED_SOURCE,
        actor="dana",
    )
    close_watch = builder.create_policy(
        room_id,
        {
            "name": "Close watch",
            "description": "Times a close against a clock that only a snooze pauses.",
            "targets": {"frt": 15 * MINUTE, "nrt": 30 * MINUTE, "ttc": 8 * HOUR},
            "pause_reasons": [sla_vocab.PAUSE_SNOOZED],
            "object_kind": sla_vocab.KIND_CONVERSATION,
            "teams": ["enterprise"],
            "workflow_name": "Close watch",
            "live": True,
        },
        source=POLICY_SEED_SOURCE,
        actor="dana",
    )

    escalations = escalation_at(base)
    pre_warn = escalations.create_workflow(
        room_id,
        {
            "name": "Pre-warn before first response",
            "description": "Warn ten minutes before the first reply is due.",
            "trigger": vocab.TRIGGER_BEFORE_BREACH,
            "metric": sla_vocab.METRIC_FRT,
            "lead_seconds": 10 * MINUTE,
            "actions": [
                {"kind": vocab.ACTION_ASSIGN, "assignee": "dana", "team": "enterprise"},
                {"kind": vocab.ACTION_TAG, "tag": "sla pre-warning"},
                {"kind": vocab.ACTION_NOTE, "body": "First response is due within ten minutes."},
                {"kind": vocab.ACTION_NOTIFY, "via": vocab.NOTIFY_SLACK, "channel": "#enterprise"},
                {
                    "kind": vocab.ACTION_ALERT,
                    "connector_id": "conn_slack_enterprise",
                    "url": "https://hooks.slack.com/services/enterprise",
                    "method": "POST",
                },
            ],
            "live": True,
        },
        source=WORKFLOW_SOURCE,
        actor="dana",
    )
    breach_watch = escalations.create_workflow(
        room_id,
        {
            "name": "Escalate a first-response breach",
            "description": "Escalate the moment the first reply is missed.",
            "trigger": vocab.TRIGGER_ON_BREACH,
            "metric": sla_vocab.METRIC_FRT,
            "actions": [
                {"kind": vocab.ACTION_ASSIGN, "team": "enterprise"},
                {"kind": vocab.ACTION_TAG, "tag": "sla breach"},
                {
                    "kind": vocab.ACTION_ALERT,
                    "connector_id": "conn_slack_enterprise",
                    "method": "POST",
                },
            ],
            "live": True,
        },
        source=WORKFLOW_SOURCE,
        actor="dana",
    )
    close_warning = escalations.create_workflow(
        room_id,
        {
            "name": "Pre-warn before a close breach",
            "description": "Warn thirty minutes before the close target is due.",
            "trigger": vocab.TRIGGER_BEFORE_BREACH,
            "metric": sla_vocab.METRIC_TTC,
            "lead_seconds": 30 * MINUTE,
            "policy_id": close_watch["id"],
            "actions": [
                {"kind": vocab.ACTION_TAG, "tag": "close warning"},
                {
                    "kind": vocab.ACTION_NOTE,
                    "body": "The close target is due within thirty minutes.",
                },
            ],
            "live": True,
        },
        source=WORKFLOW_SOURCE,
        actor="dana",
    )

    # -- the conversations ---------------------------------------------------- #
    def open_conversation(subject: str, inbox: str = "enterprise") -> str:
        view = chase_at(base).open_conversation(
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

    def apply(conversation_id: str, policy: dict[str, Any], moment: datetime) -> None:
        sla_at(moment).apply(
            conversation_id,
            {"policy_id": policy["id"], "applied_at": at(moment)},
            source=APPLY_SEED_SOURCE,
            actor="dana",
        )

    # 1. Takes a pre-warning: the first reply is due in eight minutes at the first sweep.
    #    The teammate answers in time, so the conversation stays a pre-warning afterwards.
    warned = open_conversation("Platform availability")
    apply(warned, enterprise, base)
    says(warned, "customer", base, "Is the platform available for 400 seats?")
    says(warned, "teammate", base + timedelta(minutes=12), "Yes, it scales to 400 seats.")

    # 2. Breaches before the sweep sees the lead window: the buyer asked and nobody replied.
    missed = open_conversation("Security review")
    apply(missed, enterprise, earlier)
    says(missed, "customer", earlier, "Can you complete our security review?")

    # 3. Cancelled by a teammate reply while inside the close-watch lead window.
    replied = open_conversation("Pilot pricing")
    apply(replied, close_watch, base)
    says(replied, "customer", base, "What does a pilot cost?")
    says(replied, "teammate", base + timedelta(minutes=2), "Sending the pilot rates now.")

    # 4. Cancelled while snoozed: breached, then the seller snoozed the conversation.
    snoozed = open_conversation("Waiting on procurement")
    apply(snoozed, enterprise, earlier)
    says(snoozed, "customer", earlier, "Procurement needs three weeks.")
    chase_at(base).snooze(
        snoozed, {"reason": "waiting on procurement"}, source=SNOOZE_SEED_SOURCE, actor="dana"
    )

    # 5. Meets its first reply: the teammate answered in three minutes, well inside the target.
    answered = open_conversation("Renewal date")
    apply(answered, enterprise, base)
    says(answered, "customer", base, "When does our renewal fall?")
    says(answered, "teammate", base + timedelta(minutes=3), "Your renewal falls in March.")

    # -- two sweeps ----------------------------------------------------------- #
    first = escalation_at(base + timedelta(minutes=7)).evaluate(
        room_id, {"now": at(base + timedelta(minutes=7))}, source=EVALUATE_SOURCE, actor="scheduler"
    )
    second_at = base + timedelta(hours=7, minutes=30)
    second = escalation_at(second_at).evaluate(
        room_id,
        {"now": at(second_at)},
        source=EVALUATE_SOURCE,
        actor="scheduler",
    )
    runs = escalations.runs(room_id)
    alerts = escalations.executions(room_id)
    failed = [alert for alert in alerts if alert.get("success") is False]
    cancelled = [
        decision
        for sweep in (first, second)
        for decision in sweep["decisions"]
        if decision.get("cancel")
    ]
    warned_runs = [run for run in runs if run["level"] == vocab.LEVEL_PRE_WARNING]
    breach_runs = [run for run in runs if run["level"] == vocab.LEVEL_BREACH]

    return (
        f"{len(warned_runs)} conversation(s) escalated with a pre-warning and "
        f"{len(breach_runs)} on breach, across "
        f"{len([pre_warn, breach_watch, close_warning])} live workflows on a Mon-Fri "
        f"09:00-18:00 office-hours calendar; "
        f"{len(cancelled)} run(s) cancelled, naming "
        f"{', '.join(sorted({decision['cancel'] for decision in cancelled})) or 'none'}; "
        f"{len(alerts)} Data Connector alert(s) attempted with {len(failed)} failure(s) "
        f"({failed[0]['error_type'] if failed else 'none'}); the inbox is escalated by level "
        f"then by the nearest SLA target"
        + (f"; {created} room created for these states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
    )
