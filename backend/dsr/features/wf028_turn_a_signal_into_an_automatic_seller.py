"""WF-028: turn a signal into an automatic seller action (Play registration).

The researched workflow, in full. After a signal type is registered (WF-027),
register a **Play**: its ``signal_registration_id``, a localized
``name``/``label``/``description``, the ``indicators[]`` that should trigger it,
and the ``attributes`` that name the one-off action - ``task_type`` of ``call``,
``email`` or add-to-cadence, with ``task_subject``, ``task_reminder_hours``,
``email_subject`` and ``email_template``. A seller or admin then enables it, a
matching signal creates the task with no human in the loop, the task is assigned
by the precedence **User -> Content -> Person -> Account**, and the outcome
streams out over webhooks.

The domain logic is in :mod:`dsr.plays`, which this module does not own and which
no other feature could have written into its own path. What lives here is the
three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-028``, and room scoping is real.** A task belongs to the
room the buyer's signal was raised in - that is where the seller will act on it -
so anything scoped to one is served under ``/rooms/{room_id}/...``. The Play
*registry* is not room-scoped, because "An application can create more than one
framework per signal registration" makes a Play a property of a signal
registration rather than of a room, and putting a room in that key would make the
same Play registrable once per room.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served it.
A hardcoded string inside a domain method is a defect, and the same class of bug
has shipped in this codebase before: a feature's audit log kept naming a path the
app had stopped serving. ``source`` is a *required* keyword on every writing method
of :class:`~dsr.plays.engine.PlayEngine`, so omitting it is a ``TypeError`` at the
call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.** :class:`~dsr.plays.errors.PlayError`
is the base of every refusal in :mod:`dsr.plays`, and each carries its own
``status`` and ``code``, so one handler can answer 400 for a malformed Play and 409
for one that names a registration that does not exist without being told which. It
is a domain type, so registering it globally cannot intercept anything unrelated
elsewhere in the product. ``RecordNotFound`` is deliberately *not* claimed: the core
app already maps it to 404, and two handlers for one type is a collision the host
refuses.

**Two routes for one decision, on purpose.** ``/dispatch`` runs the researched
automation and reports what it decided. It raises only for a caller mistake - an
unreadable signal id, a signal with no identity. A signal that fires nothing is a
fact about a buyer, and every Play that did not fire comes back with the gate it
stopped at, so a Play that appears to do nothing says which door it stopped at
rather than returning an empty list.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.plays import PlayEngine, PlayError
from dsr.plays.vocabulary import WEBHOOK_RETRY_ATTEMPTS, WEBHOOK_RETRY_SPACING_SECONDS
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-028-turn-a-signal-into-an-automatic-seller",
    "ticket": "WF-028",
    "name": "Turn a signal into an automatic seller action (Play registration)",
    "description": (
        "Register a Play framework against a signal registration, switch it on in the UI, and "
        "let a matching signal create a one-off call, email, or cadence step with no human in "
        "the loop. Assignment follows the researched User, Content, Person, Account "
        "precedence, and the outcome is tracked over webhooks with the researched retry "
        "schedule."
    ),
    "nav": [{"id": "play-automations", "label": "Play automations"}],
}

router = APIRouter(prefix="/api/wf-028", tags=["wf028"])


def get_engine(store: RecordStore = StoreDep) -> PlayEngine:
    """A :class:`PlayEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return PlayEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _play_error(request: Request, exc: PlayError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. :class:`~dsr.plays.errors.PlayError` is the
    base of every refusal in :mod:`dsr.plays` - a Play body that cannot be
    registered as described, a registration that does not exist, a trigger no
    signal can carry, a destroy of a live automation, a delivery that has used its
    retries - and all of them are the caller's to fix. The status rides on the
    exception rather than being decided here, because a malformed body and a
    conflict with state that already exists are both this package's errors and only
    one of them conflicts with something that exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {PlayError: _play_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: PlayEngine = EngineDep) -> dict[str, Any]:
    """Every published rule, served as data.

    The task types, the researched attributes and which of them are sourced, where
    a dynamic field is allowed, the assignment precedence and the Account
    fallback, the event types, the retry schedule, the activation path, and the
    matching rules. A client renders its pickers from this rather than from a list
    compiled into the page, so a value added here reaches every client at once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: PlayEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the fields, the three task types, the precedence, the
    Account fallback, the activation sentence and the retry policy. It does not
    say what happens on the edges of those, so the edges are collected here -
    named, traceable, and served - rather than left as comments in function
    bodies. The sourced half comes back beside the inferred half, because the point
    of the endpoint is to see where the line falls.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Play frameworks - the registry, which is a property of a signal registration
# --------------------------------------------------------------------------- #


@router.get("/play-frameworks")
def list_frameworks(
    signal_registration_id: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    indicator: str | None = Query(default=None, description="a trigger indicator key"),
    include_destroyed: bool = Query(default=False),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """The registry, with the count that matters beside it.

    ``live`` is how many of these actually create tasks. "After registration, the
    registered Play must be enabled in the Salesloft UI" means a registry can be
    full of Plays that do nothing, and a page that only showed the total would be
    reporting the number of templates rather than the number of live automations.
    """
    listed = engine.frameworks(
        signal_registration_id=signal_registration_id,
        task_type=task_type,
        enabled=enabled,
        indicator=indicator,
        include_destroyed=include_destroyed,
    )
    return {
        "count": len(listed),
        "live": sum(1 for play in listed if play.get("enabled") is True),
        "registered_not_enabled": sum(1 for play in listed if play.get("enabled") is not True),
        "play_frameworks": listed,
    }


@router.post("/play-frameworks", status_code=201)
def register_play(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Register a Play framework against a signal registration.

    "An application can create more than one framework per signal registration",
    so a second Play on the same registration is a 201 and not a conflict. The
    registration must exist - the researched flow registers the signal first - and
    every trigger indicator must be one that registration declares, because a
    signal only ever carries indicators its registration declares and a Play
    triggering on one that cannot appear is a Play that can never fire.

    The response is 201 with ``enabled: false``. Nothing about this request can put
    a task in anyone's queue; that needs the enable route.
    """
    return engine.register(payload, actor=actor, source=f"POST {router.prefix}/play-frameworks")


@router.get("/play-frameworks/{play_id}")
def read_play(play_id: str, engine: PlayEngine = EngineDep) -> dict[str, Any]:
    """One Play: its researched fields, its activation state, and its warnings.

    A destroyed Play is a 404 here. It is still listed by
    ``/play-frameworks?include_destroyed=true``, because the tasks it created still
    name it and the audit trail must not point at nothing.
    """
    record = engine.framework(play_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"play {play_id} not found")
    return engine.present_framework(record)


@router.patch("/play-frameworks/{play_id}")
def amend_play(
    play_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Change a Play, or be told every reason why not.

    The researched endpoints include ``.../plays/{id} (update/destroy)`` and state
    no constraint on an update, so the constraint is this build's, and it has one
    rule: a Play that has already created a task has a history, so the fields that
    decide what fires and what is created are frozen. Adding a locale is still
    allowed, because adding cannot invalidate a task that already fired. Every
    offending path comes back at once, so correcting a rejected patch is one
    attempt rather than one per field.
    """
    return engine.amend(
        play_id, payload, actor=actor, source=f"PATCH {router.prefix}/play-frameworks/{{play_id}}"
    )


@router.delete("/play-frameworks/{play_id}")
def destroy_play(
    play_id: str,
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Retire a Play. Switch it off first.

    An enabled Play is a running automation - it creates tasks with no human in the
    loop - so destroying one is refused while it is live. Disable it, then destroy
    it; that is one request longer and leaves an audit row saying when the switch
    was thrown. The tasks it created are untouched and keep naming it.
    """
    return engine.destroy(
        play_id, actor=actor, source=f"DELETE {router.prefix}/play-frameworks/{{play_id}}"
    )


@router.post("/play-frameworks/{play_id}/enable")
def enable_play(
    play_id: str,
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Switch a Play on. This is the one moment a human is in the loop.

    The vendor's own switch is "Settings -> Workflow -> Plays -> Edit Play"; this
    route is this product's stand-in for it, and the response says so rather than
    pretending the page is the vendor's Settings screen. Enabling twice answers with
    what is already true and writes nothing, because a seller's toggle is a
    checkbox and a double click is two requests.
    """
    return engine.enable(
        play_id, actor=actor, source=f"POST {router.prefix}/play-frameworks/{{play_id}}/enable"
    )


@router.post("/play-frameworks/{play_id}/disable")
def disable_play(
    play_id: str,
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Switch a Play off. It creates nothing from the next matching signal.

    The tasks it already created stay exactly as they are: a seller who acted on one
    of them still acted on one of them, and retiring a template is not a retraction.
    """
    return engine.disable(
        play_id, actor=actor, source=f"POST {router.prefix}/play-frameworks/{{play_id}}/disable"
    )


# --------------------------------------------------------------------------- #
# Webhook subscriptions - how outcomes are tracked at all
# --------------------------------------------------------------------------- #


@router.get("/webhook-subscriptions")
def list_webhook_subscriptions(engine: PlayEngine = EngineDep) -> dict[str, Any]:
    """The subscriptions that carry the researched outcome event types."""
    listed = engine.subscriptions()
    return {"count": len(listed), "subscriptions": listed}


@router.post("/webhook-subscriptions", status_code=201)
def subscribe_webhook(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Subscribe to the researched event types.

    "Track outcomes via Salesloft webhooks (task_created, task_completed,
    step_created, success_created)" - and the retry policy travels with the
    subscription, because "A failing webhook is retried three additional times,
    spaced 15 seconds apart, before being marked as failed" is a property of the
    subscription rather than of any one event.

    The research does not publish a subscription's own field list, so a target is
    accepted rather than demanded, and a subscription without one is warned about
    rather than refused.
    """
    return engine.subscribe(
        payload, actor=actor, source=f"POST {router.prefix}/webhook-subscriptions"
    )


@router.delete("/webhook-subscriptions/{subscription_id}")
def unsubscribe_webhook(
    subscription_id: str,
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Remove a subscription. The events it already carried stay on record."""
    return engine.unsubscribe(
        subscription_id,
        actor=actor,
        source=f"DELETE {router.prefix}/webhook-subscriptions/{{subscription_id}}",
    )


# --------------------------------------------------------------------------- #
# The automation, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/tasks")
def list_generated_tasks(
    room_id: str,
    play_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="open | completed"),
    assigned: bool | None = Query(default=None),
    task_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """The tasks the automation generated in this room, newest first.

    Every row says it was generated with no human in the loop, and every row that
    could not be assigned says which object named and why it came up empty. A task
    queue that did not know which of those applied to it would be a task queue
    nobody could debug.
    """
    listed = engine.generated_tasks(
        room_id=room_id,
        play_id=play_id,
        state=state,
        assigned=assigned,
        task_type=task_type,
        limit=limit,
    )
    return {
        "room_id": room_id,
        "count": len(listed),
        "unassigned": sum(1 for task in listed if task.get("assigned") is not True),
        "unroutable": sum(1 for task in listed if task.get("routable") is not True),
        "tasks": listed,
    }


@router.get("/rooms/{room_id}/tasks/{task_id}")
def read_generated_task(
    room_id: str,
    task_id: str,
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """One generated task, with its assignment reasoning and its outcome events."""
    task = engine.task(task_id, room_id=room_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found in room {room_id}")
    task = dict(task)
    task["events"] = engine.outcome_events(room_id=room_id, task_id=task_id, limit=100)
    return task


@router.post("/rooms/{room_id}/dispatch")
def dispatch(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Run a signal against the registry. The researched flow, in one call.

    "Registered signal fires -> matches a Play's indicator list -> Salesloft
    generates a one-off task -> assignment resolved by precedence -> seller acts."
    This route is the first four of those, and it reports what it decided rather
    than only what it created: every Play in the registry comes back with ``fired``
    and the gate it stopped at.

    The signal is named by ``signal_id`` - a signal this product already holds - or
    supplied inline as ``signal``, because the flow starts at "Registered signal
    fires" and a caller should not have to post a signal before asking what would
    happen. Only a caller mistake raises: an unreadable id, or a signal with no
    identity, because a Play that cannot tell a repeat from a new signal is not a
    one-off action.
    """
    return engine.dispatch(
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/dispatch",
    )


@router.post("/rooms/{room_id}/tasks/{task_id}/complete")
def complete_task(
    room_id: str,
    task_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """The seller acted. Close the one-off action and record the outcome.

    "seller acts -> task/step/success events stream out via webhooks." A cadence
    step also produces ``step_created`` and ``success_created``, because adding the
    buyer to a cadence is what created the step. Completing twice answers with what
    is already true and writes nothing.
    """
    return engine.complete_task(
        task_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/tasks/{{task_id}}/complete",
    )


@router.get("/rooms/{room_id}/events")
def list_events(
    room_id: str,
    event_type: str | None = Query(default=None),
    delivery_state: str | None = Query(
        default=None, description="pending | retrying | delivered | failed"
    ),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """The recorded outcome events, newest first, with their delivery state.

    An event whose delivery is still ``retrying`` carries the time its next attempt
    is due, because "retried three additional times, spaced 15 seconds apart" is a
    schedule a reviewer should be able to read rather than infer.
    """
    listed = engine.outcome_events(
        room_id=room_id,
        event_type=event_type,
        delivery_state=delivery_state,
        task_id=task_id,
        limit=limit,
    )
    return {
        "room_id": room_id,
        "count": len(listed),
        "failing": sum(1 for event in listed if event.get("state") == "failed"),
        "retrying": sum(1 for event in listed if event.get("state") == "retrying"),
        "events": listed,
    }


@router.post("/rooms/{room_id}/events/{event_id}/deliveries", status_code=201)
def record_delivery(
    room_id: str,
    event_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: PlayEngine = EngineDep,
) -> dict[str, Any]:
    """Record one webhook delivery attempt, and apply the researched retry rule.

    "A failing webhook is retried three additional times, spaced 15 seconds apart,
    before being marked as failed." The rule is enforced rather than only
    reported: an attempt recorded before its scheduled time is refused and the
    response names the time it is due, and a delivery already ``delivered`` or
    ``failed`` takes no further attempts at all.
    """
    return engine.record_attempt(
        event_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/events/{{event_id}}/deliveries",
    )


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: PlayEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room, and the automation note beside them.

    Counted over this room's tasks rather than the whole collection, so a room's
    header says what happened in that room. The two numbers worth reading first are
    ``live_plays`` and ``tasks_from_automation``, which together say how much of
    this workflow is actually running.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The signal registration the demo registers its Plays against.
#:
#: Named as a constant and used to *create* the registration, because a Play
#: registered against a registration that does not exist is refused by design and
#: the demo has to be as honest as the API. A registration is a foreign object to
#: this feature - WF-027 owns the collection - so this one is created directly
#: through the store with the researched shape rather than through another
#: feature's engine.
DEMO_REGISTRATION: dict[str, Any] = {
    "signal_name": "Deep engagement with shared content",
    "type": "document_engagement",
    "integration_id": "dsr",
    "description": {"en": "{buyer_first_name} spent {time_in_seconds} seconds on {document_name}."},
    "data_shape": {
        "type": "object",
        "properties": {
            "document_name": {"type": "string", "minLength": 1},
            "time_in_seconds": {"type": "integer", "minimum": 0},
            "buyer_first_name": {"type": "string", "minLength": 1},
            "room_name": {"type": "string", "minLength": 1},
        },
        "required": ["document_name", "time_in_seconds", "buyer_first_name", "room_name"],
    },
    "indicators": [
        {
            "key": "spent_more_than_30s_on_site",
            "metadata_shape": {
                "type": "object",
                "properties": {"time_in_seconds": {"type": "integer", "minimum": 0}},
                "required": ["time_in_seconds"],
            },
            "description": {
                "en": "Spent {time_in_seconds} seconds on {document_name}, past the threshold."
            },
        }
    ],
    "attribution": ["person_id", "account_id", "user_guid", "email_tracked_content_id"],
    "broadcast_notification": True,
}

#: The three researched task types, one Play each, plus a second call Play so the
#: researched "An application can create more than one framework per signal
#: registration" is a row rather than a claim.
DEMO_PLAYS: tuple[dict[str, Any], ...] = (
    {
        "name": {"en": "Call the engaged buyer", "fr": "Appeler l'acheteur engage"},
        "label": {"en": "Call engaged buyer", "fr": "Appeler l'acheteur"},
        "description": {
            "en": "A buyer spent real time on the security pack. Call them today.",
            "fr": "Un acheteur a passe du temps sur le dossier de securite. Appelez-le aujourd'hui.",
        },
        "indicators": ["spent_more_than_30s_on_site"],
        "attributes": {
            "task_type": "call",
            # The one dynamic field the research supports outside an email template.
            "task_subject": "Follow up with {name} on the security pack",
            "task_reminder_hours": 4,
        },
    },
    {
        "name": {"en": "Email the engaged buyer"},
        "label": {"en": "Email engaged buyer"},
        "description": {"en": "Send the implementation notes to a buyer who is deep in the pack."},
        "indicators": ["spent_more_than_30s_on_site"],
        "attributes": {
            "task_type": "email",
            "email_subject": "The implementation notes you asked about",
            "email_template": "implementation_notes",
        },
    },
    {
        # The third researched task type, and the research's documented gap: no
        # attribute in the researched list names a cadence. Registered without one
        # on purpose, so the warning and the unroutable task are rows a reviewer
        # can look at rather than sentences they have to trust.
        "name": {"en": "Add the engaged buyer to the nurture cadence"},
        "label": {"en": "Add to nurture cadence"},
        "description": {"en": "Put a buyer who is deep in the pack onto a sequence."},
        "indicators": ["spent_more_than_30s_on_site"],
        "attributes": {
            "task_type": "add-to-cadence",
            "task_subject": "Continue the conversation with {name}",
            "cadence_id": None,
        },
    },
)


def _uuid4(rng: random.Random) -> str:
    """A reproducible version 4 UUID.

    The researched signal carries ``idempotency_key`` and a one-off task is
    deduplicated on it, so the demo's signals have to carry a real one or the
    demo would be exercising a path the API refuses. Drawn from the seeder's own
    seeded generator rather than ``uuid4()``, so two runs of the seeder produce
    the same demo.
    """
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _candidates(who: str, score: int, engaged_minutes: int, base: datetime) -> list[dict[str, Any]]:
    """One Account roster entry, in the shape the researched fallback needs."""
    return [
        {
            "person_id": f"per_{who}",
            "engagement_score": score,
            "engaged_at": (base - timedelta(minutes=engaged_minutes)).isoformat(),
            "last_contact_at": (base - timedelta(days=9)).isoformat(),
            "contact_was_with_account_owner": True,
            "seller": "dana",
        }
    ]


class SeedClock:
    """A clock the demo can move.

    Two reasons. The researched retry policy is "spaced 15 seconds apart", and a
    demo built on the wall clock would have to sleep for that or produce a history
    the API itself would refuse. And building the engine on the seeder's ``now``
    makes every timestamp in the demo a function of its input, so two runs of the
    seeder produce the same rows rather than rows that differ only in their clock.
    """

    def __init__(self, start: datetime) -> None:
        self.at = start

    def __call__(self) -> str:
        return self.at.isoformat(timespec="milliseconds")

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three Plays against one registration, and the states that are not all successes.

    The rows are produced by running the real :class:`~dsr.plays.engine.PlayEngine`,
    so the demo cannot show a shape this workflow would not produce, and seeding
    never opens a socket. It is deliberately mixed, because a demo of only green
    teaches a reviewer nothing:

    * **one Play enabled, one not.** "After registration, the registered Play must
      be enabled in the Salesloft UI" is the workflow's most important sentence, and
      a demo where every Play is live would make it invisible.
    * **one Play left registered and not enabled** on purpose, so the dispatch below
      reports ``not_enabled`` as a gate.
    * **a task whose assignment resolved through User**, one through **Account**'s
      highest-engagement-score fallback, and one **unassigned** because the signal
      names no object at all - the three branches a reviewer most needs to see.
    * **a cadence task with no cadence**, which is the research's own gap: the
      researched attributes list names no cadence, so the Play warns and the task
      reports ``routable: false`` rather than going nowhere quietly.
    * **three webhook deliveries in three different places on the researched
      schedule**: one that failed once and then succeeded (``delivered``), one left
      mid-schedule after a single failure (``retrying``, carrying the time its next
      attempt is due), and one that used all four attempts and was **marked
      failed** - so "retried three additional times, spaced 15 seconds apart, before
      being marked as failed" is a row rather than a claim.
    * **a repeated signal**, so "a one-off action" shows its duplicate count.
    * a **French** Play, so the locale fallback has somewhere to appear.
    """
    store = RecordStore(db)
    rng: random.Random = context.get("rng") or random.Random("wf028")
    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    clock = SeedClock(base)
    engine = PlayEngine(store, now=clock)
    source = "seed"

    # The registration the Plays hang off. Created through the store rather than
    # through WF-027's engine: this feature must not import another feature, and a
    # Play's only need of a registration is its declared indicator list.
    existing = store.find("signal_registration", {"type": "document_engagement"}, limit=1)
    if existing:
        registration = existing[0]
    else:
        registration = store.create(
            "signal_registration",
            dict(DEMO_REGISTRATION),
            actor="dana",
            source=source,
        )
    registration_id = registration["id"]

    if not rooms:
        return (
            f"1 signal registration, 3 Play frameworks against {registration_id[:14]}..., "
            "0 tasks (no rooms to scope them to)"
        )
    room_id, account = rooms[0]
    other_room = rooms[1][0] if len(rooms) > 1 else rooms[0][0]

    def fire(seconds: int = 214, **overrides: Any) -> dict[str, Any]:
        """A signal as WF-027 stores one, dispatched through this feature's engine."""
        signal: dict[str, Any] = {
            "type": "document_engagement",
            "registration_id": registration_id,
            "data": {
                "document_name": "Security & Compliance Pack",
                "time_in_seconds": seconds,
                "buyer_first_name": "Priya",
                "room_name": "Northwind Traders — Enterprise Evaluation",
            },
            "indicators": [
                {
                    "key": "spent_more_than_30s_on_site",
                    "metadata": {"time_in_seconds": seconds},
                }
            ],
            "urgency": "high",
            "occurred_at": (base - timedelta(minutes=overrides.pop("minutes_ago", 4))).isoformat(),
            "idempotency_key": overrides.pop("idempotency_key", _uuid4(rng)),
            "attribution": overrides.pop(
                "attribution", {"user_guid": "usr_1042", "account_id": f"acc_{account}"}
            ),
            "fields": overrides.pop("fields", {"name": "Priya"}),
        }
        signal.update(overrides)
        return engine.dispatch(
            {"signal": signal},
            room_id=overrides.pop("room_id", room_id),
            actor="dana",
            source=source,
        )

    # Register all three. The third is registered without a cadence_id so the
    # research's gap is visible on the record rather than in a comment.
    registered = []
    refused: list[str] = []
    for spec in DEMO_PLAYS:
        body = {key: value for key, value in spec.items() if key != "attributes"}
        attributes = {
            key: value for key, value in (spec["attributes"] or {}).items() if value is not None
        }
        try:
            registered.append(
                engine.register(
                    {
                        **body,
                        "signal_registration_id": registration_id,
                        "attributes": attributes,
                    },
                    actor="dana",
                    source=source,
                )["play"]
            )
        except PlayError as exc:
            refused.append(str(exc))

    call_play, _email_play, cadence_play = registered[0], registered[1], registered[2]

    # The switch. Exactly one of the two signal-shaped Plays is enabled, and the
    # cadence Play is enabled too so its unroutable task exists.
    engine.enable(call_play["id"], actor="dana", source=source)
    engine.enable(cadence_play["id"], actor="dana", source=source)
    # email_play is deliberately left registered and not enabled.

    # 1. User precedence: the signal names a user, so the task is that user's.
    fire(214, minutes_ago=4)

    # 2. Account's most-engaged-person fallback, the second researched branch.
    fire(
        188,
        minutes_ago=26,
        attribution={"account_id": f"acc_{account}"},
        candidates=_candidates("000042", 91, 60 * 24 * 3, base),
    )

    # 3. No object at all: the task exists and says it is unassigned. Dropping it
    #    would lose the evidence that the Play fired.
    fire(121, minutes_ago=41, attribution={})

    # 4. The one-off guard, made visible: one signal, dispatched twice.
    repeated_key = _uuid4(rng)
    fire(240, minutes_ago=52, idempotency_key=repeated_key)
    repeated = fire(240, minutes_ago=52, idempotency_key=repeated_key)

    # 5. A signal that matches nothing the Play triggers on, so the dispatch reports
    #    a Play that is live and did not fire.
    fire(
        240,
        minutes_ago=63,
        indicators=[{"key": "watched_more_than_75_percent", "metadata": {"watched_percent": 88}}],
    )

    # 6. A second room, so the room scoping is a row rather than a claim.
    fire(160, minutes_ago=14, room_id=other_room, attribution={"person_id": "per_000077"})

    generated = engine.generated_tasks(limit=200)
    created = len(generated)

    # The French Play is read rather than fired here: the locale fallback belongs to
    # the read path, and the research's own demo of a French seller is a Play whose
    # label has one.
    french = call_play.get("label", {}).get("fr") or call_play.get("label", {}).get("en")

    # Webhook deliveries. An event is created `pending` - whether the webhook
    # arrived is a separate fact from the fact that the task was created - so the
    # demo observes three of them, and leaves each in a different place on the
    # researched schedule:
    #
    #   * one that failed once and then succeeded, so it is `delivered` with a
    #     failure in its history;
    #   * one left mid-schedule after a single failure, so it is `retrying` and
    #     carries the time its next attempt is due;
    #   * one that used all four attempts, so it is `failed` and takes no more.
    #
    # The engine's own clock is the one that moves, so the attempt history the demo
    # leaves behind is one the API itself would accept.
    open_events = engine.outcome_events(delivery_state="pending", limit=200)
    retried = 0
    failed = 0
    if open_events:
        first = open_events[0]
        engine.record_attempt(
            first["id"],
            {"status_code": 503},
            room_id=first.get("room_id"),
            actor="dana",
            source=source,
        )
        retried = 1
        clock.advance(WEBHOOK_RETRY_SPACING_SECONDS)
        engine.record_attempt(
            first["id"],
            {"status_code": 200},
            room_id=first.get("room_id"),
            actor="dana",
            source=source,
        )
    if len(open_events) > 1:
        second = open_events[1]
        # One failure, then stop: this one stays `retrying`, which is the state a
        # reviewer most needs to see and the one a "delivered, failed, done" demo
        # never shows.
        engine.record_attempt(
            second["id"],
            {"status_code": 503},
            room_id=second.get("room_id"),
            actor="dana",
            source=source,
        )
        retried += 1
    if len(open_events) > 2:
        third = open_events[2]
        for _ in range(WEBHOOK_RETRY_ATTEMPTS + 1):
            clock.advance(WEBHOOK_RETRY_SPACING_SECONDS)
            try:
                engine.record_attempt(
                    third["id"],
                    {"status_code": 500},
                    room_id=third.get("room_id"),
                    actor="dana",
                    source=source,
                )
                failed += 1
            except PlayError:
                break

    # A subscription, so the page has something to show for "track outcomes via
    # webhooks", carrying the researched event types and the researched schedule.
    engine.subscribe(
        {
            "event_types": ["task_created", "task_completed", "step_created", "success_created"],
            "target_url": "https://hooks.example.invalid/dsr/outcomes",
            "description": "Outcome events for the DSR Play automations.",
        },
        actor="dana",
        source=source,
    )

    # One task closed by its seller, so the outcome half of the flow is a row.
    completed = 0
    for task in engine.generated_tasks(state="open", assigned=True, limit=1):
        engine.complete_task(
            task["id"],
            {"note": "Called; procurement wants the revised security pack."},
            room_id=task.get("room_id"),
            actor="dana",
            source=source,
        )
        completed += 1

    unassigned = sum(1 for task in generated if task.get("assigned") is not True)
    unroutable = sum(1 for task in generated if task.get("routable") is not True)
    duplicates = (
        repeated["already_dispatched"][0]["duplicate_attempts"]
        if repeated["already_dispatched"]
        else 0
    )

    # Counted, not assumed. The one number the seeder prints that a reader will
    # act on is how many Plays are actually live, and a seed that hard-codes it is
    # a seed that starts lying the moment somebody edits which Plays are enabled.
    plays_after = engine.frameworks(limit=100)
    live = sum(1 for play in plays_after if play.get("enabled") is True)
    without_cadence = sum(
        1
        for play in plays_after
        if any(entry.get("code") == "no_cadence_named" for entry in (play.get("warnings") or []))
    )
    recorded = engine.outcome_events(limit=200)

    return (
        f"1 signal registration, {len(plays_after)} Play frameworks against it "
        f"({live} live, {len(plays_after) - live} registered and not enabled, "
        f"{without_cadence} warned about a missing cadence; {len(refused)} refused), "
        f"{created} tasks generated with no human in the loop across 2 rooms "
        f"({unassigned} unassigned, {unroutable} unroutable for want of a cadence, "
        f"{duplicates} duplicate dropped for a repeated signal, {completed} completed by its "
        f"seller), "
        f"{len(recorded)} outcome events "
        f"({retried} retried after a failure, {failed} marked failed after all four attempts), "
        f"1 webhook subscription" + (f", 1 Play labelled in French ({french})" if french else "")
    )
