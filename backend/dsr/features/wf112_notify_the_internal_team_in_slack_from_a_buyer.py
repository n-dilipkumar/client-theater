"""WF-112: notify the internal team in Slack from a buyer signal.

A buyer signal fires and the internal team is told without anyone opening the room.
The feature connects a Slack workspace with the ``bot`` scope and a channel chosen at
connect time, configures a Slack block whose message carries ``{{customer.*}}``
Liquid tags and Slack mention and link syntax, and records what WOULD be posted to
which channel - or refuses it with one of the named codes.

The domain logic is in :mod:`dsr.signal_slack`. What lives here is the three things a
workflow has to take out of shared files: the HTTP surface, the mapping from domain
errors to responses, and the demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-112``, and every collection is room-scoped.** A Slack
connection, a block and a notification decision all belong to a room, because a
notification nobody can place is not actionable.

**``source=`` names the method and the route.** Every write below passes the HTTP
method and ``router.prefix``, so the audit row names the route that actually served
it. A hardcoded string inside a domain method is a defect this codebase has shipped
before: a feature's audit log kept naming a path the app had stopped serving. The
method is part of the name because two features may share a prefix while their
concrete paths differ.

**The refusals are values, not errors.** A rate-limited post and a bot that is not in
a private channel are things the page must show, so they come back as a ``refused``
dispatch with a code and a reason rather than as a 4xx. The three refusals the issue
names by hand - the bot scope, the required channel, the private-channel invite - are
each reachable and each carries its own code. The first two are connect-time errors,
because a connection without them is not a connection; the third is a decision-time
refusal, because a seller has to be told to invite the bot.

**One handler for the whole error hierarchy.** ``SignalSlackError`` is the base of
every refusal in :mod:`dsr.signal_slack`, and each carries its own ``status`` and
``code``. ``RecordNotFound`` is deliberately *not* claimed: the core app already maps
it to 404, and two handlers for one type is a collision the host refuses.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.signal_slack import SlackNotifier
from dsr.signal_slack.errors import SignalSlackError
from dsr.signal_slack.vocabulary import SIGNAL_COLLECTION
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-112-slack-signal-notify",
    "ticket": "WF-112",
    "name": "Notify the internal team in Slack from a buyer signal",
    "description": (
        "Connect a Slack workspace with the bot scope and a required channel, author a Slack "
        "block with Liquid tags and Slack mention and link syntax, and record what WOULD be "
        "posted to which channel when a buyer signal fires. Nothing is sent: this product has "
        "no Slack transport, so the record is the output and a refused rule is a recorded "
        "state with a named code."
    ),
    "nav": [{"id": "slack-signals", "label": "Slack signals"}],
}

router = APIRouter(prefix="/api/wf-112", tags=["wf112"])


def get_notifier(store: RecordStore = StoreDep) -> SlackNotifier:
    """A :class:`SlackNotifier` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but the
    store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it here
    also leaves the engine a plain object, which is what a test constructs.
    """
    return SlackNotifier(store)


NotifierDep = Depends(get_notifier)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _slack_error(request: Request, exc: SignalSlackError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. The status rides on the exception rather
    than being decided here, because "this credential has no bot scope" and "this
    room is already connected" are both this package's errors and only one of them
    conflicts with state that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {SignalSlackError: _slack_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(notifier: SlackNotifier = NotifierDep) -> dict[str, Any]:
    """Every name, number, tag, limit and refusal this workflow uses, served as data.

    A client renders its pickers from this rather than from a list compiled into the
    page, so a tag or a refusal code changed in the domain module reaches every client
    at once.
    """
    return notifier.vocabulary()


@router.get("/inferences")
def inferences(notifier: SlackNotifier = NotifierDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, beside the sourced statements.

    The research fixes seven things and leaves the rest to a reader. The fifteen
    edges this build decided are here, each with what would change it and the risk of
    having got it wrong, so a reviewer can see where the sourced half stops.
    """
    return notifier.inferences()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """The room at a glance: the connection, the blocks, and the decisions so far.

    Every number is a count over this feature's own collections. Nothing is read from
    another feature, so a room whose dependencies have not seeded reads as zeros
    rather than failing.
    """
    return notifier.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# The Slack connection
# --------------------------------------------------------------------------- #


@router.get("/connection")
def read_connection(
    room_id: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """The room's connected workspace, or a null connection."""
    connection = notifier.connection(room_id=room_id)
    return {"room_id": room_id, "connected": connection is not None, "connection": connection}


@router.post("/connection", status_code=201)
def connect(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Authorise the Slack app and choose a channel.

    Refuses 422 ``missing_bot_scope`` for a credential without the ``bot`` scope and
    422 ``channel_required`` for one with no channel, because the research requires
    both at connect time. A second connected workspace in one room is 409.
    """
    return notifier.connect(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connection"
    )


@router.delete("/connection/{connection_id}")
def revoke_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Revoke the connection. A soft revoke, so the dispatches that named it still do."""
    return notifier.revoke(
        connection_id, actor=actor, source=f"DELETE {router.prefix}/connection/{{connection_id}}"
    )


# --------------------------------------------------------------------------- #
# Slack blocks in automations
# --------------------------------------------------------------------------- #


@router.get("/automations")
def list_automations(
    room_id: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Every Slack block in the room, newest first."""
    listed = notifier.automations(room_id=room_id)
    return {
        "count": len(listed),
        "live": sum(1 for row in listed if row.get("state") == "live"),
        "automations": listed,
    }


@router.post("/automations", status_code=201)
def create_automation(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Add a Slack block.

    The message is rendered once at save time against an empty profile, so a
    malformed mention or link is refused here rather than at the first dispatch, and
    the block's default channel is normalised to the one spelling.
    """
    return notifier.create_automation(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/automations"
    )


@router.get("/automations/{automation_id}")
def read_automation(automation_id: str, notifier: SlackNotifier = NotifierDep) -> dict[str, Any]:
    """One Slack block."""
    return notifier.read_automation(automation_id)


@router.put("/automations/{automation_id}")
def update_automation(
    automation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Patch a Slack block: its message, its channel, its trigger or its state."""
    return notifier.update_automation(
        automation_id,
        payload,
        actor=actor,
        source=f"PUT {router.prefix}/automations/{{automation_id}}",
    )


@router.delete("/automations/{automation_id}")
def delete_automation(
    automation_id: str,
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Remove a Slack block. A soft delete, so its dispatches still name it."""
    return notifier.delete_automation(
        automation_id,
        actor=actor,
        source=f"DELETE {router.prefix}/automations/{{automation_id}}",
    )


@router.post("/automations/{automation_id}/test", status_code=201)
def send_test(
    automation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """The Send-test modal: pick a test channel or profile, run it, record the decision.

    A test send is a first-class path. Its kind is ``test``, so it is judged against
    the app-API limit rather than the slower broadcast one, and the block need not be
    live for a test to run.
    """
    body = {
        "kind": "test",
        "automation_id": automation_id,
        "channel": payload.get("channel"),
        "customer": payload.get("customer") or {},
    }
    return notifier.record_dispatch(
        body,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/automations/{{automation_id}}/test",
    )


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


@router.post("/notify/preview")
def preview_notify(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Say what the rules decide, and write nothing.

    The report and the commit are two routes on purpose: a caller checking several
    signals before deciding which one to notify on gets every decision without
    creating a dispatch per check.
    """
    return notifier.decide(payload, room_id=room_id)


@router.post("/signals/{signal_id}/notify", status_code=201)
def notify_from_signal(
    signal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """Record the notification decision a buyer signal fires.

    Reads the signal from WF-027's ``intent_signal`` collection, resolves the
    connection and the block, renders the message, and writes the decision. A 201
    with ``state='recorded'`` means the message would be posted; a 201 with
    ``state='refused'`` means a named rule stopped it and ``refusal`` says which.
    """
    body = {
        "kind": "live",
        "signal_id": signal_id,
        "automation_id": payload.get("automation_id"),
        "channel": payload.get("channel"),
        "customer": payload.get("customer") or {},
    }
    return notifier.record_dispatch(
        body,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/signals/{{signal_id}}/notify",
    )


# --------------------------------------------------------------------------- #
# Recorded decisions
# --------------------------------------------------------------------------- #


@router.get("/dispatches")
def list_dispatches(
    room_id: str | None = Query(default=None),
    automation_id: str = Query(default=""),
    kind: str = Query(default=""),
    state: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    notifier: SlackNotifier = NotifierDep,
) -> dict[str, Any]:
    """The recorded notification decisions, newest first."""
    listed = notifier.dispatches(
        room_id=room_id, automation_id=automation_id, kind=kind, state=state, limit=limit
    )
    return {
        "count": len(listed),
        "by_state": _tally(row.get("state") for row in listed),
        "by_refusal": _tally(row.get("refusal") for row in listed if row.get("refusal")),
        "dispatches": listed,
    }


@router.get("/dispatches/{dispatch_id}")
def read_dispatch(dispatch_id: str, notifier: SlackNotifier = NotifierDep) -> dict[str, Any]:
    """One decision: the message that would be posted, the channel, or the refusal."""
    return notifier.read_dispatch(dispatch_id)


def _tally(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _ago(now: datetime, **back: float) -> str:
    """An ISO 8601 moment ``back`` from ``now``.

    Every string this seed returns is ASCII, because ``seed.py`` prints the return
    value on a Windows console and one rightwards arrow in one recovered feature
    broke the whole seeder.
    """
    return (now - timedelta(**back)).isoformat()


def _ensure_signal(db: AuditedDatabase, room_id: str, data: dict[str, Any]) -> None:
    """Write a WF-027 buyer signal only when one is not already there.

    The seeder runs every feature in alphabetical module order, so WF-027 may or may
    not have run by the time this one does. Writing the signals here makes this
    feature's demo self-contained, and the existence check stops a second seed run
    from producing two signals under one idempotency key.

    This writes to WF-027's collection by its published name. That is the same
    data-boundary reuse the domain package relies on; it does not import WF-027.
    """
    existing = db.find(SIGNAL_COLLECTION, {"idempotency_key": data["idempotency_key"]}, limit=1)
    if existing:
        return
    db.create(SIGNAL_COLLECTION, data, room_id=room_id, actor="seed", source="seed")


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Demo rows for the Slack-notification page.

    Three rooms, three outcomes. The first room is connected with a public channel
    and records a deliverable decision, a test send, and then a rate-limited one
    because the broadcast limit is one per ten seconds. The second room connects a
    private channel with the bot not yet invited, so its decision is refused with the
    invite message. The third room has no connection, so its decision is refused with
    no_connection. A demo where every decision is a success teaches nothing about the
    refusal states the issue asks for, and the seeder has to print states that are not
    all successes.

    The prerequisites are provisioned here rather than borrowed, so the demo does not
    depend on the alphabetical order of the seeder.
    """
    room_ids = context.get("room_ids") or []
    if not room_ids:
        return ""
    now = context["now"]
    rooms = [entry[0] for entry in room_ids]

    notifier = SlackNotifier(RecordStore(db))
    counts = {"recorded": 0, "refused": 0}
    refusals: dict[str, int] = {}

    def dispatch(payload: dict[str, Any], room_id: str) -> dict[str, Any]:
        result = notifier.record_dispatch(
            payload, room_id=room_id, actor="seed", source="seed", now=now
        )
        counts[str(result.get("state"))] = counts.get(str(result.get("state")), 0) + 1
        if result.get("refusal"):
            refusals[str(result["refusal"])] = refusals.get(str(result["refusal"]), 0) + 1
        return result

    primary = rooms[0]
    signals = {
        "wf112-demo-high": {
            "type": "pricing_page_view",
            "urgency": "high",
            "signal_name": "Buyer returned to the pricing page",
            "rendered": {"text": "Returned to pricing three times in one session."},
            "idempotency_key": "wf112-demo-high",
        },
        "wf112-demo-medium": {
            "type": "security_page_view",
            "urgency": "medium",
            "signal_name": "Buyer read the security pack",
            "rendered": {"text": "Downloaded the security and compliance pack."},
            "idempotency_key": "wf112-demo-medium",
        },
    }
    for data in signals.values():
        _ensure_signal(db, primary, data)

    existing = notifier.connection(room_id=primary)
    if existing is None:
        notifier.connect(
            {
                "workspace": "acme",
                "channel": "sales-signals",
                "channel_kind": "public",
                "scopes": ["bot", "chat:write"],
                "bot_user": "U0ACMEBOT",
            },
            room_id=primary,
            actor="seed",
            source="seed",
        )

    if not notifier.automations(room_id=primary):
        notifier.create_automation(
            {
                "name": "High-intent buyer signal",
                "trigger": "signal",
                "state": "live",
                "channel": "sales-signals",
                "message": (
                    "New buyer signal for {{customer.username}} via {{customer.channel}}. "
                    "See <https://example.co/digital-sales-room|the room> and ping <@U0ACMEBOT>."
                ),
                "note": "One message per signal, to the sales channel.",
            },
            room_id=primary,
            actor="seed",
            source="seed",
        )
        notifier.create_automation(
            {
                "name": "Quieter digest",
                "trigger": "webhook",
                "state": "draft",
                "channel": "sales-signals",
                "message": "Weekly digest for {{customer.username}}.",
                "note": "Draft until the webhook entry point lands.",
            },
            room_id=primary,
            actor="seed",
            source="seed",
        )

    automations = notifier.automations(room_id=primary)
    live_id = next((row["id"] for row in automations if row["state"] == "live"), "")
    first = db.find(SIGNAL_COLLECTION, {"idempotency_key": "wf112-demo-high"}, limit=1)
    second = db.find(SIGNAL_COLLECTION, {"idempotency_key": "wf112-demo-medium"}, limit=1)
    if first and live_id and not notifier.dispatches(room_id=primary):
        dispatch(
            {
                "kind": "live",
                "signal_id": first[0]["id"],
                "automation_id": live_id,
                "customer": {
                    "customer": {"username": "Dana Okafor", "channel": "the pricing page"}
                },
            },
            primary,
        )
        dispatch(
            {
                "kind": "test",
                "automation_id": live_id,
                "channel": "sales-signals",
                "customer": {"customer": {"username": "Dana Okafor"}},
            },
            primary,
        )
        if second:
            # Inside the broadcast window: one request every ten seconds.
            dispatch(
                {
                    "kind": "live",
                    "signal_id": second[0]["id"],
                    "automation_id": live_id,
                    "customer": {
                        "customer": {"username": "Dana Okafor", "channel": "the security pack"}
                    },
                },
                primary,
            )

    if len(rooms) > 1:
        private_room = rooms[1]
        if notifier.connection(room_id=private_room) is None:
            notifier.connect(
                {
                    "workspace": "acme",
                    "channel": "deal-desk",
                    "channel_kind": "private",
                    "scopes": ["bot", "groups:read"],
                    "bot_user": "U0ACMEBOT",
                },
                room_id=private_room,
                actor="seed",
                source="seed",
            )
        private_blocks = notifier.automations(room_id=private_room)
        if not private_blocks:
            private_blocks = [
                notifier.create_automation(
                    {
                        "name": "Private deal desk",
                        "trigger": "signal",
                        "state": "live",
                        "channel": "deal-desk",
                        "message": "Signal: {{customer.username}} - invite the bot first.",
                    },
                    room_id=private_room,
                    actor="seed",
                    source="seed",
                )
            ]
        if first and not notifier.dispatches(room_id=private_room):
            dispatch(
                {
                    "kind": "live",
                    "signal_id": first[0]["id"],
                    "automation_id": private_blocks[0]["id"],
                    "customer": {"customer": {"username": "Ines Vogel"}},
                },
                private_room,
            )

    if len(rooms) > 2:
        unconnected = rooms[2]
        if not notifier.dispatches(room_id=unconnected):
            dispatch(
                {
                    "kind": "live",
                    "signal_id": first[0]["id"] if first else "",
                    "automation_id": "",
                    "customer": {"customer": {"username": "Sam Reed"}},
                },
                unconnected,
            )

    parts = ", ".join(f"{count} {state}" for state, count in sorted(counts.items()))
    refusal_parts = ", ".join(f"{count} {code}" for code, count in sorted(refusals.items()))
    total = counts["recorded"] + counts["refused"]
    return (
        f"1 workspace connected, 2 automations, {total} notification decisions "
        f"({parts}); refusals: {refusal_parts or 'none'}; nothing sent"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
