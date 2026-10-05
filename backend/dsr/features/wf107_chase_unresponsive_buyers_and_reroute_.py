"""WF-107: chase unresponsive buyers and reroute unattended conversations.

The researched workflow, in full. A seller builds a trigger on **"If customer has been
unresponsive"** with an inactivity timer and the trigger's Channels, Audience,
Scheduling and Goal (steps 1 and 2), adds a message block (step 3), a Wait block whose
duration and interruption events are configurable (step 4), then a closing message, a
Close action and a Tag (step 5). A second trigger on **"If teammate has been
unresponsive"** shows the expected reply time from office hours, marks the conversation
as priority, tags it *delayed response* and assigns it to another inbox (step 6). Both
are drafts until they are set live (step 7).

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-107`` and every route is room-scoped.** The research's join
key is a conversation's last-message timestamp and the room is what a seller is looking
at when they ask which of their conversations went quiet.

**``source`` comes from the route.** Every write passes a string built from
``router.prefix``, so the audit row names the route that served it. A hardcoded URL
inside a domain method is a defect, which is why every writing method on
:class:`~dsr.conversation_chase.engine.ConversationChaseEngine` takes ``source`` as a
required keyword. ``test_wf107_http.py`` asserts that every source recorded names a path
the host actually mounted.

**The timers are routes, not threads.** The research calls both triggers "purely
time-based, automatic" and the Wait a "timer". This product runs no worker, a thread
needs a clock no test can move, it would write audit rows naming no route, and under
pytest-xdist it would race across workers. ``POST /rooms/{id}/evaluate`` is therefore
the sweep, and its response reports a ``due`` count so the gap that leaves is visible on
the page rather than silent. Put to Jev as audit
``jev-20261005T125557-19056-57169``, which selected it at confidence 1.00.

**One handler for the whole error hierarchy.**
:class:`~dsr.conversation_chase.rules.ChaseRefusal` is the base of every refusal the
rules raise and each carries its own published ``code`` and ``status``, so one handler
answers 422 for a duration outside the researched bounds and 409 for a conversation
that is already closed. The three not-found types are claimed separately and they are
this feature's own types, because a feature may only map error types it raises itself.

**The domain module is a new package.** ``backend/dsr/conversation_chase/`` holds the
four modules and touches nothing existing. ``dsr/reassign/`` is the nearest name but it
owns *meeting* reassignment and its vocabulary is host- and meeting-shaped; ``dsr/signals/``
owns intent signals. Put to Jev as audit ``jev-20261005T125556-19056-56859`` at
confidence 0.99.

**Nothing here claims a message left this product.** The research's extensibility names
``POST /messages`` replay, Data Connectors and ``X-Hub-Signature`` webhooks, all of which
need a credential this product does not hold. Every message is written as a conversation
part and every response carries ``sent_by_this_product: False``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.conversation_chase import inferences as inference_register, rules, vocabulary as vocab
from dsr.conversation_chase.engine import ConversationChaseEngine
from dsr.conversation_chase.rules import (
    ChaseRefusal,
    ConversationNotFound,
    RunNotFound,
    TriggerNotFound,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-107-chase-unresponsive-buyers-and-reroute-",
    "ticket": "WF-107",
    "name": "Chase unresponsive buyers and reroute unattended conversations",
    "description": (
        "Two time-based triggers. 'If customer has been unresponsive' chases a buyer "
        "who has gone quiet: a message, a wait that a customer or teammate message "
        "cancels, then a closing message, a Close and a Tag. 'If teammate has been "
        "unresponsive' shows the expected reply time from office hours, marks the "
        "conversation as priority, tags it 'delayed response' and assigns it to another "
        "inbox. Both timers are bounded to longer than 30 seconds and shorter than 14 "
        "days, the teammate timer is measured from the customer's first message, each "
        "customer message re-arms a trigger exactly once, a workflow containing a Wait "
        "or Snooze takes precedence over the global auto-close setting, and a "
        "conversation created through the REST API never triggers."
    ),
}

router = APIRouter(prefix="/api/wf-107", tags=["wf107"])

#: The inboxes this account knows about. A reroute to an inbox not in this list is
#: refused with ``inbox_unknown`` rather than accepted, because "**Assign
#: conversation** to reroute the conversation to the desired Inbox" names an existing
#: destination and inventing one is how a conversation ends up somewhere nobody watches.
#: Seeded data and the seeder both use these names.
KNOWN_INBOXES: tuple[str, ...] = ("sales", "enterprise", "escalations", "support")


def get_engine(store: RecordStore = StoreDep) -> ConversationChaseEngine:
    """A :class:`ConversationChaseEngine` over the process-wide audited store.

    Built per request rather than held on ``app.state``, because putting it there is the
    edit to the shared ``dsr/api.py`` this feature host exists to make unnecessary. The
    engine holds a store handle and a clock and nothing else, so a test can build one
    directly and move the clock by hand.
    """

    return ConversationChaseEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _chase_refusal(request: Request, exc: ChaseRefusal) -> JSONResponse:
    """A domain refusal, answered with the status and published code it carries.

    One handler for the hierarchy, because a duration outside the researched bounds and a
    conversation that is already closed are both this package's errors and only one of
    them is about a value the caller sent.
    """

    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": exc.detail,
            "status": exc.status,
            "errors": exc.errors,
        },
    )


def _trigger_not_found(request: Request, exc: TriggerNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "trigger_not_found", "detail": str(exc)})


def _conversation_not_found(request: Request, exc: ConversationNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "conversation_not_found", "detail": str(exc)}
    )


def _run_not_found(request: Request, exc: RunNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "run_not_found", "detail": str(exc)})


#: Subclasses first, so FastAPI resolves the more specific type where one exists. Each
#: type is raised only by this feature, which is what makes mapping them safe.
EXCEPTION_HANDLERS = {
    ChaseRefusal: _chase_refusal,
    TriggerNotFound: _trigger_not_found,
    ConversationNotFound: _conversation_not_found,
    RunNotFound: _run_not_found,
}


# --------------------------------------------------------------------------- #
# Audit sources
# --------------------------------------------------------------------------- #
#
# Every one built from ``router.prefix``, so the audit row names a path the host
# actually mounted. ``test_wf107_http.py`` asserts each of these against the registry.

PREFIX = router.prefix

VOCABULARY_SOURCE = f"GET {PREFIX}/vocabulary"
SUMMARY_SOURCE = f"GET {PREFIX}/rooms/{{room_id}}/summary"
EVALUATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/evaluate"
TRIGGER_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/triggers"
TRIGGER_PATCH_SOURCE = f"PATCH {PREFIX}/rooms/{{room_id}}/triggers/{{trigger_id}}"
GO_LIVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/triggers/{{trigger_id}}/go-live"
TRIGGER_DELETE_SOURCE = f"DELETE {PREFIX}/rooms/{{room_id}}/triggers/{{trigger_id}}"
CONVERSATION_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations"
MESSAGE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/messages"
REROUTE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/reroute"
CLOSE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/close"
SNOOZE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/snooze"
ADVANCE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/runs/{{run_id}}/advance"
RESOLVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/runs/{{run_id}}/resolve"
OFFICE_HOURS_SOURCE = f"PUT {PREFIX}/rooms/{{room_id}}/office-hours"

#: The seeder writes through the same sources, so a seeded row's audit names a route
#: the app serves rather than a string that exists only in demo data.
SEED_SOURCES = (
    CONVERSATION_SOURCE,
    MESSAGE_SOURCE,
    REROUTE_SOURCE,
    CLOSE_SOURCE,
    SNOOZE_SOURCE,
    TRIGGER_SOURCE,
    GO_LIVE_SOURCE,
    EVALUATE_SOURCE,
    ADVANCE_SOURCE,
    RESOLVE_SOURCE,
    OFFICE_HOURS_SOURCE,
)


# --------------------------------------------------------------------------- #
# The published vocabulary and the judgement calls
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every researched term and every refusal code")
def vocabulary() -> dict[str, Any]:
    """The terms, the labels and the refusal codes, as data.

    The page renders its pickers from this and the validator raises from the same
    tuples, so a term added in one place reaches every client at once.
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
        raise ChaseRefusal(
            "trigger_not_found",
            "decision_id",
            f"No recorded decision {decision_id!r}; this workflow recorded "
            f"{inference_register.inferences_report()['count']}.",
        )
    return entry


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/triggers", summary="A room's triggers")
def list_triggers(room_id: str, engine: ConversationChaseEngine = EngineDep) -> dict[str, Any]:
    triggers = engine.triggers(room_id)
    return {
        "room_id": room_id,
        "triggers": triggers,
        "count": len(triggers),
        "live": sum(1 for entry in triggers if entry["live"]),
        "inboxes": list(KNOWN_INBOXES),
    }


@router.post("/rooms/{room_id}/triggers", status_code=201, summary="Configure a trigger")
def create_trigger(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Build a trigger from the researched blocks.

    The duration is bound-checked on the way in: "The duration must be longer than 30
    seconds and shorter than 14 days", both bounds exclusive. A trigger is a draft until
    it is set live, because step 7 is "Save both and set live".
    """

    return engine.create_trigger(room_id, payload, source=TRIGGER_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/triggers/{trigger_id}", summary="One trigger")
def read_trigger(
    room_id: str, trigger_id: str, engine: ConversationChaseEngine = EngineDep
) -> dict[str, Any]:
    return engine.trigger_view(trigger_id)


@router.patch("/rooms/{room_id}/triggers/{trigger_id}", summary="Edit a trigger, or set it live")
def patch_trigger(
    room_id: str,
    trigger_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Edit the duration, steps, channels or fields.

    Every field is revalidated on the way in, so a value that was legal at creation and
    illegal after an edit cannot be stored.
    """

    return engine.update_trigger(trigger_id, payload, source=TRIGGER_PATCH_SOURCE, actor="seller")


@router.post("/rooms/{room_id}/triggers/{trigger_id}/go-live", summary="Set a trigger live")
def go_live(
    room_id: str, trigger_id: str, engine: ConversationChaseEngine = EngineDep
) -> dict[str, Any]:
    """Step 7: "Save both and set live". A draft fires nothing."""

    return engine.go_live(trigger_id, source=GO_LIVE_SOURCE, actor="seller")


@router.delete("/rooms/{room_id}/triggers/{trigger_id}", summary="Remove a trigger")
def delete_trigger(
    room_id: str, trigger_id: str, engine: ConversationChaseEngine = EngineDep
) -> dict[str, Any]:
    """Soft-delete a trigger.

    The runs it produced stay, because "the workflow can only trigger once per customer
    message" is checked against the tokens on those runs.
    """

    return engine.delete_trigger(trigger_id, source=TRIGGER_DELETE_SOURCE, actor="seller")


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/evaluate",
    summary="Evaluate the inactivity triggers and fire the ones that are due",
)
def evaluate(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    kind: str | None = Query(default=None, description="Restrict to one trigger kind"),
    conversation_id: str | None = Query(default=None, description="Restrict to one conversation"),
    trigger_id: str | None = Query(default=None, description="Restrict to one trigger"),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """One pass over the room's live triggers and its conversations.

    Nothing is due until somebody calls this. That is the cost of the explicit-route
    decision and it is reported rather than hidden: the response carries ``due``, the
    count the *next* call would act on, so a page can show that a sweep is due.

    Skips are returned, not raised. "Nothing is due yet" is the common case and is not a
    fault in the caller's request. Each skip names a published code, and the two that are
    specification rules rather than states of this room are flagged
    ``is_specification_rule``.

    The three filters narrow the sweep rather than changing it. ``trigger_id`` matters
    when a room holds two live triggers of the same kind: the once-per-message limit is
    per trigger, so both fire against one conversation and only a caller naming the
    trigger can tell the two runs apart.
    """

    body = dict(payload or {})
    if kind is not None:
        body["kind"] = kind
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    if trigger_id is not None:
        body["trigger_id"] = trigger_id
    return engine.evaluate(room_id, body, source=EVALUATE_SOURCE, actor="scheduler")


@router.get("/rooms/{room_id}/summary", summary="What the sweep would do right now")
def room_summary(room_id: str, engine: ConversationChaseEngine = EngineDep) -> dict[str, Any]:
    """Per-state counts, the live trigger count, and how many conversations are due."""

    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Conversations and parts
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/conversations", summary="A room's conversations")
def list_conversations(
    room_id: str,
    state: str | None = Query(default=None),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Every conversation, optionally filtered by state.

    A closed conversation is listed. The researched exemption scopes *triggers*, not the
    record, and hiding a closed conversation would make the Close action look like a
    deletion.
    """

    rows = engine.conversations(room_id)
    if state:
        wanted = rules.require_state(state)
        rows = [row for row in rows if row.get("state") == wanted]
    return {
        "room_id": room_id,
        "conversations": rows,
        "count": len(rows),
        "origins": list(vocab.ORIGINS),
        "inboxes": list(KNOWN_INBOXES),
    }


@router.post("/rooms/{room_id}/conversations", status_code=201, summary="Open a conversation")
def open_conversation(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Open a conversation and record its origin.

    The origin is explicit on every call, because "This workflow won't trigger for
    conversations created via our REST API" is only a rule if the origin is recorded.
    ``create_conversation_without_contact_reply`` defaults to ``False`` here, stated in
    the vocabulary rather than inherited from the caller.
    """

    return engine.open_conversation(room_id, payload, source=CONVERSATION_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/conversations/{conversation_id}", summary="One conversation")
def read_conversation(
    room_id: str, conversation_id: str, engine: ConversationChaseEngine = EngineDep
) -> dict[str, Any]:
    """The conversation, its parts, and both anchors the triggers measure from."""

    return engine.conversation_view(conversation_id)


@router.post(
    "/rooms/{room_id}/conversations/{conversation_id}/messages",
    status_code=201,
    summary="Write a customer, teammate or workflow message",
)
def add_message(
    room_id: str,
    conversation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Write one message part.

    A customer message re-arms the trigger, which is the whole of "can only trigger once
    per customer message": each customer message mints a new token and the next sweep may
    fire once against it. A teammate or workflow message mints nothing.
    """

    return engine.add_message(conversation_id, payload, source=MESSAGE_SOURCE, actor="seller")


@router.post(
    "/rooms/{room_id}/conversations/{conversation_id}/reroute",
    summary="Assign the conversation to another inbox",
)
def reroute(
    room_id: str,
    conversation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Step 6: "**Assign conversation** to reroute the conversation to the desired Inbox".

    The previous inbox is kept on the conversation, so "assignment moves the conversation
    between teams" is checkable afterwards. A reroute to the inbox it is already in is
    refused: a step naming the conversation's own inbox is a misconfiguration, and
    reporting it as success would hide that.
    """

    return engine.reroute(
        conversation_id,
        payload,
        source=REROUTE_SOURCE,
        actor="seller",
        known_inboxes=KNOWN_INBOXES,
    )


@router.post(
    "/rooms/{room_id}/conversations/{conversation_id}/close",
    summary="Close the conversation",
)
def close(
    room_id: str,
    conversation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Step 5's **Close** action. Idempotent: a closed conversation stays closed."""

    return engine.close(
        conversation_id, payload, source=CLOSE_SOURCE, actor="seller", reason="manual_close"
    )


@router.post(
    "/rooms/{room_id}/conversations/{conversation_id}/snooze",
    summary="Snooze the conversation",
)
def snooze(
    room_id: str,
    conversation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """The **Snooze** action, which pauses both triggers.

    A snoozed conversation is skipped with its own published code rather than treated as
    closed or as due. A customer message unsnoozes it, because the buyer is talking.
    """

    return engine.snooze(conversation_id, payload, source=SNOOZE_SOURCE, actor="seller")


@router.get(
    "/rooms/{room_id}/conversations/{conversation_id}/activity",
    summary="The conversation event log",
)
def conversation_activity(
    room_id: str,
    conversation_id: str,
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """The events in order: the firing, the messages, the close, the tag, the reroute."""

    rows = engine.activity(room_id, conversation_id)
    return {
        "room_id": room_id,
        "conversation_id": conversation_id,
        "activity": rows,
        "count": len(rows),
    }


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/runs", summary="A room's runs")
def list_runs(room_id: str, engine: ConversationChaseEngine = EngineDep) -> dict[str, Any]:
    """Every firing, with its cursor and wait state.

    A run is the unit that holds "the Wait/Snooze timer runs and can be interrupted": an
    interrupted run is a run in the ``interrupted`` state, not a row that vanished.
    """

    runs = engine.runs(room_id)
    by_state: dict[str, int] = {state: 0 for state in vocab.RUN_STATES}
    for run in runs:
        if run["state"] in by_state:
            by_state[run["state"]] += 1
    return {
        "room_id": room_id,
        "runs": runs,
        "count": len(runs),
        "by_state": by_state,
        "state_labels": dict(vocab.RUN_STATE_LABELS),
    }


@router.get("/rooms/{room_id}/runs/{run_id}", summary="One run")
def read_run(
    room_id: str, run_id: str, engine: ConversationChaseEngine = EngineDep
) -> dict[str, Any]:
    """One run with its step list, cursor, next step and history."""

    return engine.run_view(run_id)


@router.post("/rooms/{room_id}/runs/{run_id}/advance", summary="Write the next step")
def advance(
    room_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Walk one step, or start the wait.

    The step list is walked in order, which is why the research's "a closing message
    block, then a Close conversation action, then Tag conversation" is preserved rather
    than reordered. A Wait or Snooze step starts the timer and moves the run to
    ``waiting`` rather than being stepped over.
    """

    return engine.advance(
        run_id, payload, source=ADVANCE_SOURCE, actor="scheduler", known_inboxes=KNOWN_INBOXES
    )


@router.post("/rooms/{room_id}/runs/{run_id}/resolve", summary="Resolve a wait")
def resolve(
    room_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Finish a wait, or end the run because it was interrupted.

    Interruption is checked first, because it is the researched behaviour: "configure
    the duration and which interruption events cancel the wait". A cancelled wait is not a
    wait that ran out, and an interrupted run is finished -- there is no resume.
    """

    return engine.resolve(
        run_id, payload, source=RESOLVE_SOURCE, actor="scheduler", known_inboxes=KNOWN_INBOXES
    )


# --------------------------------------------------------------------------- #
# Office hours
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/office-hours", summary="The room's office hours")
def read_office_hours(room_id: str, engine: ConversationChaseEngine = EngineDep) -> dict[str, Any]:
    """The weekly schedule, with the derived default filled in.

    ``derived`` is ``True`` when nothing has been stored, so a reader can tell the
    built-in default from one somebody set. The derivation is returned beside it.
    """

    return engine.office_hours(room_id)


@router.put("/rooms/{room_id}/office-hours", summary="Set the room's office hours")
def save_office_hours(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConversationChaseEngine = EngineDep,
) -> dict[str, Any]:
    """Write the weekly schedule.

    Validated before the write, so a schedule whose close is before its open is refused
    and no unusable row is stored. Seven named days, each either closed or a pair of
    open/close minutes.
    """

    return engine.save_office_hours(room_id, payload, source=OFFICE_HOURS_SOURCE, actor="seller")


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

MINUTE = 60


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Six conversations and the states that are not all successes.

    The rows are produced by running the real :class:`ConversationChaseEngine`, so the
    demo cannot show a shape this workflow would not produce. Every instant derives from
    ``context["now"]``, so nothing here carries a fixed date and the working-day office
    hours cases hold whenever the seeder runs.

    It is deliberately mixed, because a demo of only green teaches a reviewer nothing:

    * **one chased and closed**, so the whole message / wait / closing message / Close /
      Tag sequence is a row rather than a claim;
    * **one interrupted mid-wait** by the buyer answering, so the Wait block's configurable
      interruption is visible and the run is genuinely terminal;
    * **one still in its wait**, because a run nobody resolved is the state a sweep leaves
      behind, and a page showing only finished runs would hide it;
    * **one created through the REST API**, so the researched exemption is a row carrying
      its published skip code;
    * **one snoozed**, so the paused state and its own skip code are visible;
    * **one with three customer messages in a row**, so the teammate trigger's
      first-message anchor is visible against the customer trigger's last-message anchor;
    * **one trigger with a Wait in its step list and one without**, so the precedence over
      the global auto-close setting is readable from the trigger list;
    * **one rerouted** to the escalations inbox with priority and *delayed response* on it,
      so the teammate-idle workflow's steps are all rows.

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
    base = int(clock.timestamp())

    def moment_at(minutes: float) -> datetime:
        return datetime.fromtimestamp(base + int(minutes * MINUTE * 60), tz=timezone.utc)

    def at(minutes: float) -> str:
        return moment_at(minutes).isoformat(timespec="milliseconds")

    def engine_at(minutes: float) -> ConversationChaseEngine:
        frozen = moment_at(minutes)
        return ConversationChaseEngine(store, now=lambda: frozen)

    given = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:1]
    created = 0
    if not room_ids:
        name = "Northwind Traders - chase and reroute"
        record = store.create("room", {"name": name, "account": name.split(" - ")[0]}, actor="dana")
        room_ids.append(record["id"])
        created += 1
    room_id = room_ids[0]

    builder = engine_at(0)

    # -- the two triggers, and the office hours they read ---------------------- #
    office = builder.save_office_hours(
        room_id,
        {"schedule": rules.default_office_hours(), "timezone": "UTC"},
        source=OFFICE_HOURS_SOURCE,
        actor="dana",
    )

    chase = builder.create_trigger(
        room_id,
        {
            "kind": vocab.CUSTOMER_IDLE,
            "duration_seconds": vocab.DEFAULT_TRIGGER_SECONDS,
            "channels": [vocab.CHANNEL_MESSENGER, vocab.CHANNEL_EMAIL],
            "audience": "Buyers who asked a question and have not answered",
            "scheduling": {"timezone": "UTC", "office_hours": True},
            "goal": "Chase a buyer who has gone quiet, then close the conversation",
            "steps": [
                {
                    "kind": vocab.STEP_MESSAGE,
                    "body": "Just checking if you are still there? Let us know if you need any help.",
                },
                {
                    "kind": vocab.STEP_WAIT,
                    "duration_seconds": vocab.DEFAULT_WAIT_SECONDS,
                    "interruption_events": [
                        vocab.INTERRUPTION_CUSTOMER_MESSAGE,
                        vocab.INTERRUPTION_TEAMMATE_MESSAGE,
                    ],
                },
                {"kind": vocab.STEP_CLOSE_MESSAGE, "body": "Closing this for now."},
                {"kind": vocab.STEP_CLOSE},
                {"kind": vocab.STEP_TAG, "tag": "no reply"},
            ],
        },
        source=TRIGGER_SOURCE,
        actor="dana",
    )
    builder.go_live(chase["id"], source=GO_LIVE_SOURCE, actor="dana")

    reroute_trigger = builder.create_trigger(
        room_id,
        {
            "kind": vocab.TEAMMATE_IDLE,
            "duration_seconds": 15 * MINUTE,
            "channels": [vocab.CHANNEL_MESSENGER],
            "audience": "Conversations a rep has not answered",
            "scheduling": {"timezone": "UTC", "office_hours": True},
            "goal": "Hand an unattended conversation to another inbox",
            "steps": [
                {"kind": vocab.STEP_SHOW_EXPECTED_REPLY_TIME, "duration_seconds": 15 * MINUTE},
                {"kind": vocab.STEP_MARK_PRIORITY},
                {"kind": vocab.STEP_TAG, "tag": vocab.DELAYED_RESPONSE_TAG},
                {"kind": vocab.STEP_ASSIGN, "inbox": "escalations"},
            ],
        },
        source=TRIGGER_SOURCE,
        actor="dana",
    )
    builder.go_live(reroute_trigger["id"], source=GO_LIVE_SOURCE, actor="dana")

    # -- a third trigger with no Wait, so the precedence rule is visible -------- #
    plain = builder.create_trigger(
        room_id,
        {
            "kind": vocab.CUSTOMER_IDLE,
            "duration_seconds": 2 * 60 * MINUTE,
            "goal": "A workflow with no Wait or Snooze, so the global setting owns the close",
            "steps": [
                {"kind": vocab.STEP_MESSAGE, "body": "Still interested?"},
                {"kind": vocab.STEP_TAG, "tag": "no reply"},
            ],
        },
        source=TRIGGER_SOURCE,
        actor="dana",
    )
    builder.go_live(plain["id"], source=GO_LIVE_SOURCE, actor="dana")

    def new_conversation(origin: str, inbox: str | None = "sales", **extra: Any) -> dict[str, Any]:
        return builder.open_conversation(
            room_id,
            {
                "origin": origin,
                "inbox": inbox,
                "customer_email": "buyer@northwind.example",
                **extra,
            },
            source=CONVERSATION_SOURCE,
            actor="dana",
        )

    def buyer_says(conversation_id: str, minutes: float, body: str) -> None:
        builder.add_message(
            conversation_id,
            {"author_kind": vocab.AUTHOR_CUSTOMER, "at": at(minutes=minutes), "body": body},
            source=MESSAGE_SOURCE,
            actor="buyer@northwind.example",
        )

    # Six conversations, each one carrying a state the page needs to show. The instants
    # are relative to ``now`` so the same six states appear whenever the seed runs.
    chased = new_conversation(vocab.ORIGIN_INBOX, subject="Platform availability")
    buyer_says(chased["id"], -40, "Is the platform available for 400 seats?")

    answered = new_conversation(vocab.ORIGIN_INBOX, subject="Pricing for the pilot")
    buyer_says(answered["id"], -38, "What does a pilot cost?")

    api_conversation = new_conversation(vocab.ORIGIN_API, subject="Partner referral")
    buyer_says(api_conversation["id"], -36, "Referred by your partner.")

    snoozed = new_conversation(vocab.ORIGIN_INBOX, subject="Waiting on procurement")
    buyer_says(snoozed["id"], -34, "Procurement needs three weeks.")
    builder.snooze(
        snoozed["id"],
        {"reason": "waiting on procurement"},
        source=SNOOZE_SOURCE,
        actor="dana",
    )

    # Three messages in a row. The teammate trigger must stay on the first of the three.
    burst = new_conversation(vocab.ORIGIN_INBOX, subject="Security review")
    for index, minute in enumerate((-30, -25, -20)):
        buyer_says(burst["id"], minute, f"Security question {index + 1}")

    # A conversation whose window has not elapsed, so the page shows a countdown.
    fresh = new_conversation(vocab.ORIGIN_INBOX, subject="Renewal date")
    buyer_says(fresh["id"], -2, "When does our renewal fall?")

    # The sweep, fifteen minutes on.
    sweep_time = -15

    def sweep_one(conversation_id: str, kind: str, trigger_id: str | None = None) -> dict[str, Any]:
        """Sweep one conversation, scoped to this seed's own trigger.

        Both filters matter. Scoping by conversation keeps a room that already carries
        earlier demo rows untouched. Scoping by trigger keeps a second run in the same
        room from pairing each of this run's triggers with the previous run's: the
        once-per-message limit is per trigger, so two live triggers of the same kind both
        fire against one conversation, and the second ``assign`` would find the
        conversation already in the target inbox.
        """

        payload: dict[str, Any] = {"kind": kind, "conversation_id": conversation_id}
        if trigger_id is not None:
            payload["trigger_id"] = trigger_id
        return engine_at(sweep_time).evaluate(
            room_id, payload, source=EVALUATE_SOURCE, actor="scheduler"
        )

    def advance_all(run_ids: list[str], minutes: float) -> None:
        """Walk every runnable run to its next hold, at one frozen instant."""

        runner = engine_at(minutes)
        for run_id in run_ids:
            view = runner.run_view(run_id)
            while view["state"] == vocab.RUN_RUNNING:
                view = runner.advance(
                    run_id,
                    {},
                    source=ADVANCE_SOURCE,
                    actor="scheduler",
                    known_inboxes=KNOWN_INBOXES,
                )

    # ``chased`` is quiet, so the customer-idle trigger fires. Its Wait then runs out, so
    # the closing message, Close and Tag all run.
    chased_sweep = sweep_one(chased["id"], vocab.CUSTOMER_IDLE, chase["id"])
    chased_run = chased_sweep["fired"][0]["id"] if chased_sweep["fired"] else None
    if chased_run:
        advance_all([chased_run], sweep_time)
        engine_at(sweep_time + 25).resolve(chased_run, {}, source=RESOLVE_SOURCE, actor="scheduler")
        advance_all([chased_run], sweep_time + 25)

    # ``answered`` is quiet too, so it also fires. Its Wait is then cancelled by the buyer
    # answering inside the window, so the run ends there and the closing message, Close
    # and Tag never run on it. That is what an interruption is for.
    answered_sweep = sweep_one(answered["id"], vocab.CUSTOMER_IDLE, chase["id"])
    answered_run = answered_sweep["fired"][0]["id"] if answered_sweep["fired"] else None
    if answered_run:
        advance_all([answered_run], sweep_time)
        builder.add_message(
            answered["id"],
            {
                "author_kind": vocab.AUTHOR_CUSTOMER,
                "at": at(sweep_time + 3),
                "body": "Sorry for the delay, what does a pilot cost?",
            },
            source=MESSAGE_SOURCE,
            actor="buyer@northwind.example",
        )
        engine_at(sweep_time + 4).resolve(
            answered_run, {}, source=RESOLVE_SOURCE, actor="scheduler"
        )

    # ``burst`` is three customer messages in a row, so the teammate trigger is measured
    # from the first of them and is already fifteen minutes past its window. Its step list
    # has no Wait, so it reroutes rather than chases: expected reply time, priority, the
    # 'delayed response' tag, then the assign.
    burst_sweep = sweep_one(burst["id"], vocab.TEAMMATE_IDLE, reroute_trigger["id"])
    burst_runs = [row["id"] for row in burst_sweep["fired"]]
    advance_all(burst_runs, sweep_time)

    # ``fresh``, ``snoozed`` and the API-created one are swept too, so the three skip
    # reasons are on the record: not yet elapsed, snoozed, and API-created.
    fresh_sweep = sweep_one(fresh["id"], vocab.CUSTOMER_IDLE, chase["id"])
    snoozed_sweep = sweep_one(snoozed["id"], vocab.CUSTOMER_IDLE, chase["id"])
    api_sweep = sweep_one(api_conversation["id"], vocab.CUSTOMER_IDLE, chase["id"])

    sweeps = [chased_sweep, answered_sweep, burst_sweep, fresh_sweep, snoozed_sweep, api_sweep]

    summary = builder.summary(room_id)
    interrupted_count = summary["runs_by_state"].get(vocab.RUN_INTERRUPTED, 0)
    waiting_count = summary["runs_by_state"].get(vocab.RUN_WAITING, 0)
    finished_count = summary["runs_by_state"].get(vocab.RUN_FINISHED, 0)
    closed = summary["by_state"].get(vocab.STATE_CLOSED, 0)
    rows = builder.conversations(room_id)
    tags = sorted({tag for row in rows for tag in row.get("tags") or []})
    rerouted = [row for row in rows if row.get("inbox") == "escalations"]
    skipped_reasons = sorted({row["reason"] for entry in sweeps for row in entry["skipped"]})
    anchors = sorted(
        {
            row.get("anchor_kind")
            for entry in sweeps
            for row in entry["fired"]
            if row.get("anchor_kind")
        }
    )

    return (
        f"{summary['conversations']} conversation(s) in one room: {closed} closed by the chase "
        f"workflow, {summary['by_state'].get(vocab.STATE_OPEN, 0)} still open, "
        f"{summary['by_state'].get(vocab.STATE_SNOOZED, 0)} snoozed, "
        f"{summary['api_created']} created through the REST API and therefore exempt; "
        f"{summary['live_triggers']} live trigger(s) of {summary['triggers']} -- a customer-idle one "
        f"measured from the last message of any kind, a teammate-idle one measured from the "
        f"customer's first message, and one with no Wait or Snooze so the global auto-close "
        f"setting owns the close; "
        f"{summary['runs']} run(s) anchored on {', '.join(anchors) or 'none'}: "
        f"{finished_count} finished, {interrupted_count} interrupted by the buyer answering, "
        f"{waiting_count} still in its wait; "
        f"{len(rerouted)} rerouted to the escalations inbox, "
        f"{summary['priority']} marked priority, tagged {', '.join(tags) or 'nothing'}; "
        f"the sweeps skipped on {', '.join(skipped_reasons) or 'nothing'}; "
        f"{len(office['schedule'])} office-hours day(s) on the derived Mon-Fri schedule"
        + (f"; {created} room created for these states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
    )
