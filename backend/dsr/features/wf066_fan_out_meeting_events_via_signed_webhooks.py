"""WF-066: fan out meeting lifecycle events via signed webhooks.

The researched workflow, in full. An admin opens *Command Center > Integrations >
Webhooks* and clicks **Create Custom Webhook**, picks a type (**For New Meeting**,
**For Meeting Update**, or **For Canceled Meeting**), pastes the subscriber URL and
clicks Create, then sets the row's status to **Enabled**. The tenant obtains its
HMAC signing secret by emailing support, because no vendor screen shows one. From
then on, a meeting created, updated or cancelled is serialised once, signed with
HMAC-SHA256 over ``{timestamp}.{raw_body}``, and POSTed to every enabled
subscription naming that event type.

What the contract meant for this build
--------------------------------------
**The prefix is ``/api/wf-066``, and everything the tenant owns is room-scoped.**
The research's fan-out is unbounded in both directions - *"You are not limited by
the number of webhooks you have"*, and *"multiple webhook types [may] have the
same webhook URL, and multiple webhook URLs for the same type"* - so a URL is not
a tenant's identity and a meeting is not either. Everything below
``/rooms/{room_id}/`` belongs to one room, which is what makes the many-to-many
expressible at all.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."``, so the audit row names the route that actually served
it. A URL string hardcoded inside a domain method is a defect, and the same class
of bug has shipped in this codebase before: a feature's audit log kept naming a
path the app had stopped serving. ``source`` is a *required* keyword on every
writing method of :class:`~dsr.meeting_webhook_fanout.MeetingWebhookFanout`, so
omitting it is a ``TypeError`` at the call site rather than an untraceable row.

**One handler for the whole error hierarchy.**
:class:`~dsr.meeting_webhook_fanout.MeetingWebhookError` is the base of every refusal
in :mod:`dsr.meeting_webhook_fanout`, and each carries its own ``status`` and
``code``, so one handler answers 400 for a subscriber URL this deployment refuses,
403-style 404 for a room that does not exist and 409 for a second subscription for
one URL and event type - without the handler knowing which is which.

**One flat envelope, and the bytes are built once.** The research quotes two
envelope shapes and warns against mixing them silently. This build fires the
three Chili Piper types, whose documented fields are all top level, so it sends
one flat envelope and never a ``payload`` wrapper. The body is serialised once and
those exact bytes are both what gets signed and what gets sent, because the
research records a signature mismatch as *"Ensure you're verifying against the raw
request body, not a re-serialised/parsed JSON object."* Jev chose the flat
envelope at confidence 0.96, audit ``jev-20261004T070110-25984-70596``.

**Replay protection is not this route's job.** The research says it belongs to the
consumer with ``MAX_AGE_SECONDS = 300``, so the room ships the timestamp header
and publishes the window and refuses nothing on age. A sender that enforced the
window on its own outbound POSTs would reject a correctly signed delivery whose
clock runs fast, and that is a clock bug wearing the costume of a security
control.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.meeting_webhook_fanout import MeetingWebhookError, MeetingWebhookFanout
from dsr.meeting_webhook_fanout.transport import FakeTransport
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-066-fan-out-meeting-events-via-signed-webhooks",
    "ticket": "WF-066",
    "name": "Fan out meeting lifecycle events via signed webhooks",
    "description": (
        "Register any number of subscriber URLs against the three researched meeting "
        "event types, then serialise each meeting lifecycle event once, sign it with "
        "HMAC-SHA256 over a timestamp and the raw body, and POST it to every enabled "
        "subscriber. The fan-out is unbounded in both directions, so one URL can serve "
        "several event types and one event type can have several URLs. Replay protection "
        "is the consumer's: the room ships the timestamp header and the window, and "
        "refuses nothing on age."
    ),
    "nav": [{"id": "meeting-webhook-fanout", "label": "Meeting webhooks"}],
}

router = APIRouter(prefix="/api/wf-066", tags=["wf066"])

#: The feature's own prefix, duplicated here so the demo data's audit sources name
#: the same routes the router serves. A change to the prefix has to be made
#: deliberately in both places, which is the point of writing it twice.
PREFIX = "/api/wf-066"


def get_fanout(store: RecordStore = StoreDep) -> MeetingWebhookFanout:
    """A :class:`MeetingWebhookFanout` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the fanout holds nothing but
    the store handle and a transport, and an ``app.state`` entry is exactly the
    edit to the shared ``dsr/api.py`` the feature host exists to make
    unnecessary. Building it here also leaves the fanout a plain object, which is
    what a test constructs.
    """
    return MeetingWebhookFanout(store)


FanoutDep = Depends(get_fanout)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _meeting_webhook_error(request: Request, exc: MeetingWebhookError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. A malformed subscriber URL and a
    subscription that conflicts with one that already exists are both this
    package's errors and only one of them conflicts with state that already
    exists, so the status rides on the exception rather than being decided here.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {MeetingWebhookError: _meeting_webhook_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(fanout: MeetingWebhookFanout = FanoutDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The three event types and the payload value each fires, the status control, the
    two header definitions, the signing rule, every documented payload field, the
    two deployment modes, the collection names, the outcomes and the skip reasons.

    A client renders its subscriber editor from this rather than from a list
    compiled into the page, so a type added server-side reaches every client at
    once - and the editor can never disagree with the validator about what is legal.
    """
    return fanout.vocabulary()


@router.get("/inferences")
def inferences(fanout: MeetingWebhookFanout = FanoutDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research is unusually specific about the sender's contract: the signing
    rule step by step, both header definitions, the three subscription names, the
    payload field list, and an explicit statement that replay protection belongs to
    the consumer. What it does not say is which deployment this product is, what
    makes two subscriptions the same subscription, or whether the sender retries.
    Those are collected here - named, traceable, bounded, and served - rather than
    left as comments in function bodies.
    """
    return fanout.inferences()


# --------------------------------------------------------------------------- #
# Steps 1 to 3: the Webhooks table and the signing secret
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/subscriptions")
def list_subscriptions(
    room_id: str,
    include_retired: bool = Query(default=False),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """The Webhooks table: one row per subscriber URL and event type.

    The research names a *Command Center > Integrations > Webhooks* table with a
    status toggle, a per-row delete and an ordering, so the response carries the
    counts by event type, the enabled count, and the rows in creation order.

    The subscription limit is ``None`` and says why, because a reader looking for a
    cap will not find one and deserves the reason: *"You are not limited by the
    number of webhooks you have."*
    """
    return fanout.subscriptions(room_id, include_retired=include_retired)


@router.post("/rooms/{room_id}/subscriptions", status_code=201)
def create_subscription(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """Steps 1 and 2: **Create Custom Webhook**, then set the row to **Enabled**.

    The researched flow is *"pastes the subscriber URL and clicks Create, then sets
    the row's status to **Enabled**"*, so a new row lands disabled and a body that
    asks for ``enabled`` has already made the second decision.

    The URL is validated against the room's deployment mode, which decides between
    the research's two quoted rules. A second subscription for the same URL and the
    same event type is refused: both halves of that pair are needed to identify one,
    and two rows for one pair would deliver the same event twice to the same
    address.
    """
    return fanout.subscribe(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/subscriptions",
    )


@router.patch("/rooms/{room_id}/subscriptions/{subscription_id}")
def amend_subscription(
    room_id: str,
    subscription_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """The row's status toggle, or a corrected URL or event type.

    Step 2's control is a ``PATCH`` and not a separate enable route, because a
    reader looking at the table wants one row and one edit rather than a row and a
    button. The whole merged row is re-validated rather than field by field, so an
    amendment cannot smuggle in a rule the original create would have refused.
    """
    return fanout.amend(
        room_id,
        subscription_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{{room_id}}/subscriptions/{{subscription_id}}",
    )


@router.get("/rooms/{room_id}/subscriptions/{subscription_id}")
def read_subscription(
    room_id: str,
    subscription_id: str,
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """One row, by record id."""
    return fanout.subscription(room_id, subscription_id)


@router.delete("/rooms/{room_id}/subscriptions/{subscription_id}")
def retire_subscription(
    room_id: str,
    subscription_id: str,
    actor: str | None = Query(default=None),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """The per-row delete the research names.

    A soft delete, so the delivery log's references to this subscription still
    resolve. Destroying the row would leave that history pointing at nothing, which
    is the exact defect the audit-source rule exists to prevent one layer up.
    """
    return fanout.retire(
        room_id,
        subscription_id,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{{room_id}}/subscriptions/{{subscription_id}}",
    )


@router.get("/rooms/{room_id}/secret")
def read_secret(room_id: str, fanout: MeetingWebhookFanout = FanoutDep) -> dict[str, Any]:
    """The tenant's HMAC signing secret, and where it came from.

    The research is explicit that no vendor screen shows this: *"Optionally emails
    support to obtain the tenant's HMAC signing secret (not shown in the UI)."* So
    this route is the only place a team can read the value their subscriber needs,
    and it is served rather than masked.
    """
    return fanout.secret(room_id)


@router.post("/rooms/{room_id}/signing-key")
def set_secret(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """Set or rotate the room's signing secret.

    The research gives no screen that sets one, so this route is how a tenant
    supplies the secret support issued them. It is a write, and an audited one:
    rotating a secret invalidates every subscriber holding the old one, and a
    reader deciding whether a delivery failure is a clock problem or a rotation
    needs the audit row.
    """
    return fanout.set_secret(
        room_id,
        str(payload.get("secret") or ""),
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/signing-key",
    )


# --------------------------------------------------------------------------- #
# The data flow: serialise, sign, POST
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/events", status_code=201)
def emit_event(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """The whole researched data flow: serialise once, sign, POST to every subscriber.

    *"meeting created/updated/canceled -> Chili Piper serialises a meeting payload ->
    HMAC-SHA256 signature + timestamp headers -> POST to subscriber"*, and on the
    far side *"the subscriber updates warehouse / triggers automations"*.

    ``event_type`` is one of the three the admin picked when creating the
    subscription, and it is what decides the payload's ``type`` value: create is
    ``Created``, update is ``Updated``, and cancel is ``Deleted``.

    The event row is written before the first delivery is attempted, so a fan-out
    that fails halfway still leaves the reader with the signed event and the
    deliveries that did land. Writing it last would lose the event entirely if the
    third subscriber timed out, leaving a partial delivery list and no way to
    redeliver.
    """
    meeting = payload.get("meeting")
    if not isinstance(meeting, dict) or not meeting:
        raise HTTPException(
            status_code=422,
            detail="the request needs a 'meeting' object; the room serialises what it holds",
        )
    report = fanout.emit(
        room_id,
        meeting,
        str(payload.get("event_type") or ""),
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/events",
    )
    # The fields the room was given are echoed back, because a reader asking why a
    # subscriber saw a field they did not expect needs to see which fields arrived
    # and which the room synthesised from them.
    report["request"] = {
        "event_type": payload.get("event_type"),
        "meeting_fields": sorted(meeting),
    }
    return report


@router.get("/rooms/{room_id}/events")
def list_events(
    room_id: str,
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """The event log, newest first.

    The body and the payload are left off each row: fifty events would otherwise
    carry fifty copies of a payload no list view renders. Both are served by
    :func:`read_event` and by :func:`sample`.
    """
    return fanout.events(room_id, event_type=event_type, limit=limit)


@router.get("/rooms/{room_id}/events/{event_id}")
def read_event(
    room_id: str, event_id: str, fanout: MeetingWebhookFanout = FanoutDep
) -> dict[str, Any]:
    """One event, with the exact bytes that were signed and the signature over them.

    ``raw_body`` is what a subscriber compares against, and ``signing_input`` is
    the ``{timestamp}.{raw_body}`` string the digest covers. Serving both is what
    makes a signature mismatch debuggable on the room's side rather than only on the
    subscriber's.
    """
    return fanout.event(room_id, event_id)


@router.post("/rooms/{room_id}/events/{event_id}/redeliver")
def redeliver_event(
    room_id: str,
    event_id: str,
    actor: str | None = Query(default=None),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """Re-attempt one event, and record the attempt beside the first.

    The research names no retry ladder, so this is a route a person calls rather
    than a scheduler this build invented. The *same bytes* are re-signed and
    re-sent: the raw body is read back off the event row rather than rebuilt from
    the payload, because a rebuilt body is one whose field order this build now
    chooses rather than one the subscriber already saw.
    """
    return fanout.redeliver(
        room_id,
        event_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/events/{{event_id}}/redeliver",
    )


# --------------------------------------------------------------------------- #
# What the endpoint produced
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/deliveries")
def list_deliveries(
    room_id: str,
    event_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None, description="delivered | failed | skipped"),
    include_skipped: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=1000),
    fanout: MeetingWebhookFanout = FanoutDep,
) -> dict[str, Any]:
    """One row per attempt, with the outcome that decided it.

    A failed delivery is the row a person reads when their subscriber reached the
    room and nothing came back, so the status, the error, the response excerpt and
    the retryable flag are all here. ``retryable`` is advice and nothing schedules
    on it, because this build has no retry ladder.
    """
    return fanout.deliveries(
        room_id,
        event_type=event_type,
        outcome=outcome,
        include_skipped=include_skipped,
        limit=limit,
    )


@router.get("/rooms/{room_id}/deliveries/{delivery_id}")
def read_delivery(
    room_id: str, delivery_id: str, fanout: MeetingWebhookFanout = FanoutDep
) -> dict[str, Any]:
    """One attempt, with the response that came back."""
    return fanout.delivery(room_id, delivery_id)


@router.get("/rooms/{room_id}/sample")
def sample(room_id: str, fanout: MeetingWebhookFanout = FanoutDep) -> dict[str, Any]:
    """The exact bytes, the signing input, the signature, and how to check it.

    The research's step 4 is the *subscriber's* side: recompute the HMAC, compare in
    constant time, and reject a stale timestamp. A subscriber cannot check any of
    that without the room's secret and one real body, so this serves both beside the
    string that was signed, along with the window the research gives the consumer.

    The body is a shape the research documents rather than a shape this room happens
    to hold, so a team can verify their implementation before they have a real
    meeting to point it at.
    """
    return fanout.sample(room_id)


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, fanout: MeetingWebhookFanout = FanoutDep) -> dict[str, Any]:
    """Counts for the room's page header, and the researched constraints beside them.

    Counted over this room's own rows rather than the whole collections, so a room's
    header says what happened in that room. The three notes are here because all
    three are things a reader looks for and does not find: the fan-out is
    unbounded, the replay window is the consumer's, and there is no retry ladder.
    """
    return fanout.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The researched subscription names, used as the demo rows' descriptions.
#: The URLs point at ``example`` hosts on purpose: the seeder supplies a
#: :class:`FakeTransport`, so nothing here opens a socket and nothing tries to
#: reach a host that does not exist.
DEMO_URL_NEW = "https://hooks.northwind.example/meetings/created"
DEMO_URL_UPDATED = "https://hooks.northwind.example/meetings/updated"
DEMO_URL_CANCELED = "https://hooks.northwind.example/meetings/deleted"
#: One URL carrying several event types, which the research allows: *"multiple
#: webhook types [may] have the same webhook URL"*.
DEMO_URL_SHARED = "https://hooks.northwind.example/meetings/all"
DEMO_SECRET = "northwind-tenant-hmac-signing-secret"


def _meeting(index: int, base: datetime, *, title: str | None = None) -> dict[str, Any]:
    """One meeting in the shape the research documents.

    Every documented Chili Piper field is present, so a reviewer comparing the
    seeded payload against the contract sees the full contract rather than the
    subset this room happened to hold.
    """
    start = base + timedelta(days=index, hours=2)
    return {
        "meetingIdChili": f"m-{index:04d}",
        "title": title or f"Enterprise evaluation {index}",
        "description": "Walk through the evaluation with the buying committee.",
        "location": "Microsoft Teams",
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=45)).isoformat(),
        "primaryGuestTimeZone": "Europe/London",
        "primaryGuestName": "Priya Raman",
        "primaryGuestEmail": "priya.raman@northwind.example",
        "primaryGuestIdChili": f"g-{index:04d}",
        "primaryGuestDataFields": {"seats": 480, "region": "EMEA"},
        "hostIdChili": "h-0001",
        "hostName": "Dana Okafor",
        "assigneeIdChili": "h-0001",
        "assigneeName": "Dana Okafor",
        "bookerIdChili": f"g-{index:04d}",
        "bookerName": "Priya Raman",
        "additionalGuests": [{"name": "Tom Alvarez", "email": "tom.alvarez@northwind.example"}],
        "workspaceId": "ws-0001",
        "workspaceName": "Northwind Traders",
        "productFeatureType": "ConciergeRouter",
        "productFeatureName": "Concierge router",
        "productFeatureId": "pf-0001",
        "distributionName": "Enterprise inbound",
        "distributionId": "d-0001",
        "meetingTypeName": "Demo",
        "meetingTypeId": "mt-0001",
    }


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Four rooms, and the states that are not all successes.

    The rows are produced by running the real :class:`MeetingWebhookFanout`, so the
    demo cannot show a shape this workflow would not produce, and seeding never
    opens a socket: every attempt goes through a :class:`FakeTransport`.

    It is deliberately mixed, because a demo of only green teaches a reviewer
    nothing:

    * **one room** - an enabled subscriber per event type, plus a second room whose
      subscriptions were **created but never enabled**, because step 2 is a
      deliberate second act. An event fired at that room reaches nobody and is
      recorded as ``skipped: no_enabled_subscriptions``;
    * the **same room** - a **failed** delivery (the fake answers 500), so the
      failure path is a row rather than a claim, and a **redelivery** of the event
      that answered 202 both times, so the log shows two attempts against one event;
    * a third room - **one URL carrying all three event types**, which the research
      allows, so the many-to-many is visible rather than described;
    * a fourth room - a subscription **retired** with the per-row delete, so the
      table shows a retired row and the delivery log still resolves it;
    * a second subscription for one event type at a different URL, so the other
      direction of the fan-out (one type, several URLs) is a row.

    The seeder hands over ``[(room_id, account), ...]``. It is topped up with rooms
    this seed creates, because the states above are per-room - an all-disabled room
    needs its own room, and a retired row needs another - and a demo that quietly
    dropped them because the core dataset is small would be a demo that lies about
    what this workflow does. The return string says how many rooms came from the
    seeder.
    """
    store = RecordStore(db)
    fanout = MeetingWebhookFanout(store, FakeTransport(status=202, body="accepted"))
    failing = MeetingWebhookFanout(store, FakeTransport(fail=True, error="URLError: timed out"))
    given: list[str] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    if not given:
        return "no demo rooms, so no meeting webhook rows"

    create_source = f"POST {router.prefix}/rooms/{{room_id}}/subscriptions"
    enable_source = f"PATCH {router.prefix}/rooms/{{room_id}}/subscriptions/{{subscription_id}}"
    event_source = f"POST {router.prefix}/rooms/{{room_id}}/events"
    retire_source = f"DELETE {router.prefix}/rooms/{{room_id}}/subscriptions/{{subscription_id}}"
    key_source = f"POST {router.prefix}/rooms/{{room_id}}/signing-key"
    redeliver_source = f"POST {router.prefix}/rooms/{{room_id}}/events/{{event_id}}/redeliver"

    names = [
        ("Northwind Traders - meeting webhooks live", "Northwind Traders"),
        ("Contoso Health - subscriptions created, never enabled", "Contoso Health"),
        ("Fabrikam Logistics - one URL, all three types", "Fabrikam Logistics"),
        ("Tailspin Toys - a retired subscription", "Tailspin Toys"),
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:1]
    created = 0
    for index in range(4):
        if index < len(room_ids):
            continue
        name, account = names[index]
        room_ids.append(
            store.create("room", {"name": name, "account": account}, actor="dana")["id"]
        )
        created += 1
    live, disabled, shared, retired_room = room_ids

    # -- room one: the researched flow, in full ----------------------------- #
    fanout.set_secret(live, DEMO_SECRET, actor="dana", source=key_source)
    created_subs = [
        fanout.subscribe(
            live,
            {"url": DEMO_URL_NEW, "event_type": "new_meeting"},
            actor="dana",
            source=create_source,
        ),
        fanout.subscribe(
            live,
            {"url": DEMO_URL_UPDATED, "event_type": "meeting_update"},
            actor="dana",
            source=create_source,
        ),
        fanout.subscribe(
            live,
            {"url": DEMO_URL_CANCELED, "event_type": "canceled_meeting"},
            actor="dana",
            source=create_source,
        ),
    ]
    # A second URL for one event type, so the other direction of the fan-out is a
    # row: "multiple webhook URLs for the same type".
    second_new = fanout.subscribe(
        live,
        {"url": f"{DEMO_URL_NEW}/warehouse", "event_type": "new_meeting"},
        actor="dana",
        source=create_source,
    )
    enabled = [
        fanout.amend(
            live,
            row["id"],
            {"status": "enabled"},
            actor="dana",
            source=enable_source,
        )
        for row in [*created_subs, second_new]
    ]
    assert all(row["status"] == "enabled" for row in enabled), (
        "the researched step 2 is a deliberate second act, so every row the seed "
        "enables has to have changed state"
    )

    ok = fanout.emit(
        live,
        _meeting(1, base),
        "new_meeting",
        actor="dana",
        source=event_source,
        now=base,
    )
    # An event that fails to reach its subscribers: the fake transport answers
    # with a connection error, so the failure path is a row and not a claim.
    failed = failing.emit(
        live,
        _meeting(2, base),
        "meeting_update",
        actor="dana",
        source=event_source,
        now=base + timedelta(minutes=5),
    )
    # A cancellation, which fires type Deleted, and a redelivery of the event that
    # already landed, so the log shows two attempts against one event.
    cancelled = fanout.emit(
        live,
        _meeting(3, base),
        "canceled_meeting",
        actor="dana",
        source=event_source,
        now=base + timedelta(minutes=10),
    )
    redelivered = fanout.redeliver(
        live,
        ok["event"]["id"],
        actor="dana",
        source=redeliver_source,
    )

    # -- room two: created, never enabled ------------------------------------ #
    draft = fanout.subscribe(
        disabled,
        {"url": "http://warehouse.internal/meetings/created", "event_type": "new_meeting"},
        actor="dana",
        source=create_source,
    )
    second_draft = fanout.subscribe(
        disabled,
        {"url": "http://warehouse.internal/meetings/all", "event_type": "meeting_update"},
        actor="dana",
        source=create_source,
    )
    # A self-hosted room accepts this private, plain-http address, because the
    # research says self-hosted does. A room on the SaaS rules would refuse it, and
    # the room's mode is on every row, so a reviewer can see which rule applied.
    assert draft["deployment_mode"] == "self_hosted", "the seeded URL is a self-hosted one"
    assert second_draft["status"] == "disabled", "step 2 is a deliberate second act"
    never_enabled = fanout.emit(
        disabled,
        _meeting(4, base),
        "new_meeting",
        actor="dana",
        source=event_source,
        now=base + timedelta(minutes=15),
    )

    # -- room three: one URL carrying all three types ------------------------ #
    for event_type in ("new_meeting", "meeting_update", "canceled_meeting"):
        fanout.subscribe(
            shared,
            {"url": DEMO_URL_SHARED, "event_type": event_type},
            actor="dana",
            source=create_source,
        )
    for row in fanout.subscriptions(shared)["subscriptions"]:
        fanout.amend(shared, row["id"], {"status": "enabled"}, actor="dana", source=enable_source)
    one_url = fanout.emit(
        shared,
        _meeting(5, base),
        "new_meeting",
        actor="dana",
        source=event_source,
        now=base + timedelta(minutes=20),
    )

    # -- room four: a retired subscription ----------------------------------- #
    gone = fanout.subscribe(
        retired_room,
        {"url": "https://hooks.tailspin.example/meetings/created", "event_type": "new_meeting"},
        actor="dana",
        source=create_source,
    )
    fanout.amend(
        retired_room, gone["id"], {"status": "enabled"}, actor="dana", source=enable_source
    )
    fanout.retire(retired_room, gone["id"], actor="dana", source=retire_source)
    after_retire = fanout.emit(
        retired_room,
        _meeting(6, base),
        "new_meeting",
        actor="dana",
        source=event_source,
        now=base + timedelta(minutes=25),
    )

    summary = fanout.summary(live)
    disabled_summary = fanout.summary(disabled)
    retired_summary = fanout.summary(retired_room)
    skipped_after_retire = after_retire["reason"] or "not skipped"
    unreachable = failed["deliveries"][0]["error"] if failed["deliveries"] else "nothing recorded"

    return (
        f"{summary['subscriptions']} subscription(s) on a live room "
        f"({summary['enabled']} enabled, one URL serving all three types and a second "
        f"URL for one of them), one URL carrying all three types at a "
        f"third room ({one_url['delivered']} delivered), "
        f"{summary['events']} event(s) and {summary['deliveries']} deliver(ies) on the "
        f"live room ({summary['by_outcome'].get('delivered', 0)} delivered, "
        f"{summary['by_outcome'].get('failed', 0)} failed to reach their subscriber, "
        f"{unreachable}), "
        f"a cancellation firing type Deleted ({cancelled['event']['payload_type']}), "
        f"a redelivery of the same event ({redelivered['delivered']} delivered again), "
        f"{disabled_summary['subscriptions']} subscription(s) created but never enabled at "
        f"a second room, on a private http address a self-hosted room accepts "
        f"(its event {never_enabled['reason']}), "
        f"a retired subscription at a fourth ({retired_summary['retired']} retired, its "
        f"event {skipped_after_retire})"
        + (f", {created} room(s) created for the per-room states" if created else "")
        + f", {len(given)} room(s) from the seeder"
    )
