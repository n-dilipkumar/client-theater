"""WF-026: write DSR events into the seller activity feed.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-026.md``, which is
the specification. The researched decisions are the product: the app-scoped event
name, the ``{{prospect}}`` placeholder in a template that is configured in the
portal rather than sent, the ``externalUrl`` deep link, the optional ``body``, the
``prospect`` relationship, the ``mailing*`` webhook resources, ``payloadVersion:
2``, the ``Outreach-Webhook-Signature`` header, the 5 second timeout, and the
sourced fact that shapes the whole inbound half - Outreach does not retry webhook
deliveries, so nothing this build cannot understand may be thrown away.

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the demo
data. The domain lives in :mod:`dsr.outreach_feed`.

Why the prefix is ``/api/wf-026``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide with
a feature-shaped one by construction. Every room-scoped path is room-scoped, and
the host's loader would report a ``(method, path)`` clash as a failed feature
rather than shadowing it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain method that writes, so it cannot silently
regress. A test asserts that every source recorded in the audit log matches a
route the host actually mounted. The delivery log keeps the exact request headers
as evidence of what went over the wire, with the ``Authorization`` value redacted -
that header *is* the S2S token, and a delivery row is readable by anyone who can
read a delivery.

Error mapping
-------------
Four handlers, one per distinct HTTP answer, and all four types are this feature's
own. ``RecordNotFound`` and ``AuditError`` are deliberately not claimed: the core
app already maps them correctly, and two handlers for one type is a collision the
host refuses. The split between ``400`` and ``428`` is deliberate - "not
configured yet" and "the request failed" are different things for a client, and
``apiRequest`` in the frontend carries the status so a page can tell them apart.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.outreach_feed import (
    DELIVERY_STATUSES,
    FeedError,
    FeedNotConfiguredError,
    FeedPublisher,
    PostResult,
    SignatureError,
    UnknownRoom,
    describe_inferences,
    describe_vocabulary,
    webhooks,
)
from dsr.outreach_feed.vocabulary import SIGNATURE_HEADER
from dsr.outreach_feed.webhooks import compute_signature
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-026-write-dsr-events-into-the-seller-activ",
    "ticket": "WF-026",
    "name": "Write DSR events into the seller activity feed",
    "description": (
        "Map qualifying DSR engagement events onto configured Outreach custom "
        "events, POST them to the prospect activity feed with a deep link back into "
        "the room, and record intent coming back out through Outreach webhooks."
    ),
    "nav": [{"id": "seller-activity-feed", "label": "Seller activity feed"}],
}

router = APIRouter(prefix="/api/wf-026", tags=["wf026"])


def get_publisher(store: RecordStore = StoreDep) -> FeedPublisher:
    """A :class:`FeedPublisher` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and its transport, and ``app.state`` is where the engine would
    otherwise have to be built in the shared app's lifespan. Building it here also
    leaves the transport an overridable dependency, so the suite can drive delivery
    without a socket.
    """
    return FeedPublisher(store)


PublisherDep = Depends(get_publisher)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _feed_error(request: Request, exc: FeedError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    ``FeedError`` is the base of every refusal in :mod:`dsr.outreach_feed`: an
    event name that is not ``<app identifier>:<event id>``, a template carrying a
    placeholder Outreach will not replace, a custom event claiming no buyer action,
    a link that cannot produce a valid deep link. All of them are the caller's to
    fix.
    """
    return JSONResponse(status_code=400, content={"error": "feed_error", "detail": str(exc)})


def _signature_error(request: Request, exc: SignatureError) -> JSONResponse:
    """The delivery did not prove it came from Outreach. 401.

    Its own type, and its own status, because a caller who fixes the body still
    fails and a caller who fixes the secret should not have to touch the body.
    Starlette picks the most specific registered handler by MRO, so this wins over
    ``_feed_error`` for the same exception.
    """
    return JSONResponse(status_code=401, content={"error": "bad_signature", "detail": str(exc)})


def _not_configured(request: Request, exc: FeedNotConfiguredError) -> JSONResponse:
    """Well formed, but this installation is not set up to answer it yet. 428.

    Distinct from 400 so a client can say "finish the setup" rather than "you got
    the request wrong" - the frontend's ``apiRequest`` carries the status for
    exactly this.
    """
    return JSONResponse(status_code=428, content={"error": "not_configured", "detail": str(exc)})


def _unknown_room(request: Request, exc: UnknownRoom) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": f"room {exc} not found", "id": str(exc)},
    )


EXCEPTION_HANDLERS = {
    FeedError: _feed_error,
    SignatureError: _signature_error,
    FeedNotConfiguredError: _not_configured,
    UnknownRoom: _unknown_room,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The sourced vocabulary: the write, the event-name grammar, the webhook.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a reviewer can read the researched facts
    without opening a Python file. Includes the exact JSON:API body the research
    quotes, and the adjacent surfaces this build deliberately does not implement.
    """
    payload = describe_vocabulary()
    payload["subscription"] = webhooks.describe_subscription()
    payload["delivery_statuses"] = list(DELIVERY_STATUSES)
    return payload


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about the wire contract and silent about almost
    everything around it. The parts that are therefore judgement calls - the retry
    ladder, the signature encoding, the deep-link shape, the event-name character
    set, how a room is matched to a prospect, what happens to an event that matches
    nothing - are collected in :mod:`dsr.outreach_feed.inferences` and served here,
    next to the sourced facts they are measured against.

    A read with no side effect, so it needs no store.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Apps: step 1, recorded
# --------------------------------------------------------------------------- #


@router.get("/apps")
def list_apps(publisher: FeedPublisher = PublisherDep) -> dict[str, Any]:
    """Registered Outreach apps.

    The S2S token and the webhook secret are never in a read: a read answers
    ``has_token`` and a masked hint, because the token *is* the Authorization
    header and the delivery log's recorded headers are readable.
    """
    apps = publisher.list_apps()
    return {"count": len(apps), "apps": apps}


@router.post("/apps", status_code=201)
def register_app(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Register the app the custom events are configured under.

    The researched step 1 happens in the Outreach developer portal; this records
    the identity and the credentials the write needs, so a configured event name
    can be checked against a real app and a write can be authorized.
    """
    return publisher.register_app(payload, actor=actor, source=f"POST {router.prefix}/apps")


@router.get("/apps/{app_id}")
def read_app(app_id: str, publisher: FeedPublisher = PublisherDep) -> dict[str, Any]:
    for app in publisher.list_apps():
        if app["id"] == app_id:
            return app
    raise FeedError(f"app {app_id} not found")


@router.patch("/apps/{app_id}")
def update_app(
    app_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Patch an app: rotate the token, flip the toggle, set the room base URL."""
    return publisher.update_app(
        app_id, payload, actor=actor, source=f"PATCH {router.prefix}/apps/{app_id}"
    )


@router.delete("/apps/{app_id}", status_code=204)
def delete_app(
    app_id: str,
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> Response:
    """Soft-delete an app. The delivery log outlives it, and stays readable."""
    publisher.delete_app(app_id, actor=actor, source=f"DELETE {router.prefix}/apps/{app_id}")
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Custom events: step 2
# --------------------------------------------------------------------------- #


@router.get("/event-types")
def list_event_types(publisher: FeedPublisher = PublisherDep) -> dict[str, Any]:
    """Configured custom events: the app-scoped name, the template, the body.

    Each carries a ``card_preview`` that says which half of the card this build
    controls. The template is configured in the Outreach portal and never sent.
    """
    types = publisher.list_event_types()
    return {"count": len(types), "event_types": types}


@router.post("/event-types", status_code=201)
def create_event_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Declare one custom event.

    ``name`` must be ``<app identifier>:<event id>`` and the identifier has to be
    an app registered here, because Outreach matches the name against the app whose
    custom event was configured in the portal. ``template`` is required and may
    contain ``{{prospect}}`` and nothing else. ``actions`` is required: an event
    claiming no buyer action would claim every event in the room, which is not what
    "qualifying DSR event" means.
    """
    return publisher.create_event_type(
        payload, actor=actor, source=f"POST {router.prefix}/event-types"
    )


@router.get("/event-types/{event_type_id}")
def read_event_type(event_type_id: str, publisher: FeedPublisher = PublisherDep) -> dict[str, Any]:
    found = publisher.get_event_type(event_type_id)
    if found is None:
        raise FeedError(f"custom event {event_type_id} not found")
    return found


@router.patch("/event-types/{event_type_id}")
def update_event_type(
    event_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Patch a custom event, or flip it on and off with ``{"enabled": false}``.

    The toggle lives on the event type rather than beside it, because Outreach
    renders the card from the portal-side configuration for *this* event: "off" is
    a property of the event, and a separate flag could drift out of step with it.
    """
    return publisher.update_event_type(
        event_type_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/event-types/{event_type_id}",
    )


@router.delete("/event-types/{event_type_id}", status_code=204)
def delete_event_type(
    event_type_id: str,
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> Response:
    """Soft-delete a custom event. The delivery log outlives it."""
    publisher.delete_event_type(
        event_type_id, actor=actor, source=f"DELETE {router.prefix}/event-types/{event_type_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Prospect links: who the feed belongs to
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/prospects")
def list_prospects(room_id: str, publisher: FeedPublisher = PublisherDep) -> dict[str, Any]:
    """The Outreach prospects this room's activity feed belongs to.

    A room may hold several: the research's data source is the prospect object and
    the account/opportunity behind it, and a deal commonly has more than one buyer
    contact. One DSR event then produces one card per linked prospect.
    """
    links = publisher.list_prospects(room_id)
    return {"room_id": room_id, "count": len(links), "prospects": links}


@router.post("/rooms/{room_id}/prospects", status_code=201)
def link_prospect(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Link a room to a prospect.

    Every event write carries a ``prospect`` relationship, so a room with no link
    can send nothing - which is a state the publish ledger reports, not one it
    swallows. ``external_url`` on the link overrides the app's room base URL for
    this prospect, which is how a deployment that routes rooms by slug uses it.
    """
    return publisher.link_prospect(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/prospects"
    )


@router.delete("/rooms/{room_id}/prospects/{link_id}", status_code=204)
def unlink_prospect(
    room_id: str,
    link_id: str,
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> Response:
    """Soft-delete one link. Deliveries already made keep pointing at the prospect."""
    publisher.unlink_prospect(
        room_id,
        link_id,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{room_id}/prospects/{link_id}",
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The automation: step 3
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/feed")
def room_feed(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """What a rep's activity feed would show for this room, and how it got there.

    The chronological, clickable trail the research describes, assembled from the
    delivery rows, alongside the blockers that stop anything being sent and the
    buyer actions no configured custom event claims.
    """
    return publisher.room_feed(room_id, limit=limit)


@router.post("/rooms/{room_id}/preview")
def preview(
    room_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """What a publish run would do, and why - with nothing written.

    The same ledger as ``/publish``, so the fall-through can be seen before it
    happens: which events would be sent, which would be blocked and on what ground,
    and which buyer actions nobody has configured a custom event for.
    """
    return publisher.preview(room_id, limit=limit)


@router.post("/rooms/{room_id}/publish")
def publish(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Write this room's qualifying DSR events into the seller's activity feed.

    [sourced] "On each qualifying DSR event, POST https://api.outreach.io/api/v2/events
    with an S2S token, the configured name, an externalUrl deep link back into the
    DSR, an optional body, and a prospect relationship."

    Safe to run repeatedly: a delivery key of (room, DSR event, name, prospect)
    makes a second run a no-op for anything already delivered, and a blocked event
    is re-evaluated rather than remembered as blocked - so linking the prospect and
    running again sends exactly what was waiting. Pass ``event_type_ids`` to publish
    one configured event rather than all of them.
    """
    only = payload.get("event_type_ids")
    return publisher.publish(
        room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/publish",
        limit=int(payload.get("limit") or 200),
        only=[str(item) for item in only] if isinstance(only, (list, tuple)) and only else None,
    )


# --------------------------------------------------------------------------- #
# The outbound log
# --------------------------------------------------------------------------- #


@router.get("/deliveries")
def list_deliveries(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="delivered | failed | skipped"),
    event_name: str | None = Query(default=None),
    needs_manual_update: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Every event write this installation attempted, newest first.

    Filters are JSON paths in each row's own payload, resolved through the dynamic
    index, so a new status or a new event name needs no change here. The summary is
    computed over exactly the rows returned, so a filtered view does not report
    totals for the whole log.
    """
    rows = publisher.deliveries(
        room_id=room_id,
        status=status,
        event_name=event_name,
        needs_manual_update=needs_manual_update,
        limit=limit,
    )
    summary = {status: 0 for status in DELIVERY_STATUSES}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    summary["needs_manual_update"] = sum(1 for row in rows if row["needs_manual_update"])
    return {"count": len(rows), "summary": summary, "deliveries": rows}


@router.get("/deliveries/{delivery_id}")
def read_delivery(delivery_id: str, publisher: FeedPublisher = PublisherDep) -> dict[str, Any]:
    for row in publisher.deliveries(limit=1000):
        if row["id"] == delivery_id:
            return row
    raise FeedError(f"delivery {delivery_id} not found")


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(
    delivery_id: str,
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Send one delivery again, by hand, and say what happened.

    The researched platform does not retry anything - "Outreach does not retry
    webhook deliveries upon receiving any of the Status Codes" - so a row that is
    ``needs_manual_update`` waits for exactly this. It is also the way a ``skipped``
    row is cleared: run it once the blocker is fixed and the same code path that
    publishes does the work. Earlier attempts are appended to the attempt log, not
    overwritten: the reason a second attempt happened is the first attempt.
    """
    return publisher.retry_delivery(
        delivery_id, actor=actor, source=f"POST {router.prefix}/deliveries/{delivery_id}/retry"
    )


# --------------------------------------------------------------------------- #
# The inbound half
# --------------------------------------------------------------------------- #


@router.post("/webhooks/outreach")
async def receive_outreach_webhook(
    request: Request,
    actor: str | None = Query(default=None),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """The matching subscription's receiving end: intent coming back out of Outreach.

    The raw body is read rather than a parsed model, because the
    ``Outreach-Webhook-Signature`` is an HMAC over the exact bytes received and a
    re-serialised body would not verify.

    [sourced] "Outreach does not retry webhook deliveries upon receiving any of the
    Status Codes including 500 Internal Server Error and 429 Too Many Requests", so
    anything readable is stored and acknowledged, including a resource outside the
    documented ``mailing*`` family - it is recorded as ``ignored``, not refused. The
    two refusals are the two no amount of persistence fixes: a signature that does
    not verify (401) and a ``payloadVersion`` this build does not speak (400).
    """
    raw = await request.body()
    result = publisher.receive_webhook(
        raw, request.headers, actor=actor, source=f"POST {router.prefix}/webhooks/outreach"
    )
    return {"accepted": True, **result}


@router.get("/signals")
def list_signals(
    room_id: str | None = Query(default=None),
    type: str | None = Query(
        default=None, description="opened | replied | bounced | delivered | …"
    ),
    status: str | None = Query(default=None, description="recorded | ignored | duplicate"),
    limit: int = Query(default=100, ge=1, le=1000),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """Intent that came back out of Outreach, newest first.

    Each row keeps the ``beforeUpdate`` block verbatim, the whole payload, and
    whether the prospect it names was linked to a room here.
    """
    rows = publisher.signals(room_id=room_id, signal_type=type, status=status, limit=limit)
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
        if row["intent"]:
            summary["intent"] = summary.get("intent", 0) + 1
    return {"count": len(rows), "summary": summary, "signals": rows}


@router.get("/rooms/{room_id}/signals")
def room_signals(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    publisher: FeedPublisher = PublisherDep,
) -> dict[str, Any]:
    """The intent signals recorded against one room."""
    publisher.require_room(room_id)
    rows = publisher.signals(room_id=room_id, limit=limit)
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {"room_id": room_id, "count": len(rows), "summary": summary, "signals": rows}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The base URL the demo builds its deep links from. A real deployment puts its own
#: on the app record; the demo needs one that resolves to nothing, because the
#: demo never opens a socket.
DEMO_ROOM_BASE_URL = "https://rooms.example/r"

#: The two apps the demo registers. The second is switched off and carries no
#: events, so the app list shows a second integration in a state a person has to
#: deal with, rather than only healthy ones.
DEMO_APPS: tuple[Mapping[str, Any], ...] = (
    {
        "app_identifier": "dsr",
        "token": "demo-s2s-token-a1b2c3d4",
        "webhook_secret": "demo-webhook-secret",
        "room_base_url": DEMO_ROOM_BASE_URL,
        "enabled": True,
        "environment": "production",
    },
    {
        "app_identifier": "dsr-sandbox",
        "token": "demo-s2s-token-sandbox",
        "room_base_url": DEMO_ROOM_BASE_URL,
        "enabled": False,
        "environment": "sandbox",
    },
)

#: The configured custom events. Chosen to exercise the researched rules rather
#: than to look tidy:
#:
#: * ``dsr:room-viewed`` claims ``viewed`` and carries a body, so a card with
#:   accompanying text is in the demo.
#: * ``dsr:document-downloaded`` claims ``downloaded``, narrows to two documents and
#:   has **no** body, so the demo shows the optional-body rule: Outreach renders the
#:   template alone. It carries a French localisation because the research says
#:   localised descriptions are configured as templates.
#: * ``dsr:room-accepted`` claims ``shared`` and is **disabled**, so a buyer action
#:   the seller has defined but not switched on falls into ``unmapped_actions``
#:   rather than silently disappearing.
DEMO_EVENT_TYPES: tuple[Mapping[str, Any], ...] = (
    {
        "app_identifier": "dsr",
        "name": "dsr:room-viewed",
        "template": "{{prospect}} opened the digital sales room",
        "body": "Opened the room",
        "actions": ["viewed"],
        "enabled": True,
        "configured_in_portal": True,
    },
    {
        "app_identifier": "dsr",
        "name": "dsr:document-downloaded",
        "template": "{{prospect}} downloaded a document from the room",
        "localizations": {"fr": "{{prospect}} a telecharge un document de la salle"},
        "actions": ["downloaded"],
        "documents": ["Security & Compliance Pack", "Pricing One-Pager"],
        "enabled": True,
        "configured_in_portal": True,
    },
    {
        "app_identifier": "dsr",
        "name": "dsr:room-accepted",
        "template": "{{prospect}} shared the room with a colleague",
        "actions": ["shared"],
        "enabled": False,
        "configured_in_portal": False,
    },
)

#: ``(room index, prospect id, label, extra)``. The suffixes are read by
#: :class:`DemoTransport` and are what produce the interesting states.
DEMO_LINKS: tuple[tuple[int, str, str, Mapping[str, Any]], ...] = (
    (0, "pros_northwind_ok", "Procurement lead, Northwind", {}),
    (0, "pros_northwind_retired", "Security reviewer (prospect retired)", {}),
    (2, "pros_fabrikam_limited", "Ops lead, Fabrikam", {}),
)

#: The three signals the demo records. Each is a real body run through the real
#: signature check and the real normaliser, so the demo cannot show a shape the
#: workflow would not produce.
DEMO_SIGNALS: tuple[Mapping[str, Any], ...] = (
    {
        "note": "opened - linked, so the card lands on the room's feed",
        "payload": {
            "type": "mailing.opened",
            "sequence": 41,
            "createdAt": "2026-09-24T08:14:00.000Z",
            "payloadVersion": 2,
            "data": {
                "id": "mail_4f21",
                "type": "mailing",
                "relationships": {
                    "prospect": {"data": {"type": "prospect", "id": "pros_northwind_ok"}}
                },
                "beforeUpdate": {"status": "delivered", "deliveredAt": "2026-09-24T08:02:11.000Z"},
                "attributes": {"subject": "Northwind pricing, as discussed"},
            },
        },
    },
    {
        "note": "replied - the intent a rep acts on, and it carries a beforeUpdate block",
        "payload": {
            "type": "mailing.replied",
            "sequence": 44,
            "createdAt": "2026-09-24T19:02:00.000Z",
            "payloadVersion": 2,
            "data": {
                "id": "mail_4f21",
                "type": "mailing",
                "relationships": {
                    "prospect": {"data": {"type": "prospect", "id": "pros_northwind_ok"}}
                },
                "beforeUpdate": {"status": "opened", "openedAt": "2026-09-24T08:14:00.000Z"},
                "attributes": {"subject": "Re: Northwind pricing, as discussed"},
            },
        },
    },
    {
        "note": "bounced for a prospect nobody linked here - recorded, marked unlinked",
        "payload": {
            "type": "mailing.bounced",
            "sequence": 12,
            "createdAt": "2026-09-19T11:40:00.000Z",
            "payloadVersion": 2,
            "data": {
                "id": "mail_3a08",
                "type": "mailing",
                "relationships": {
                    "prospect": {"data": {"type": "prospect", "id": "pros_unknown_9c1"}}
                },
                "attributes": {"reason": "550 mailbox unavailable"},
            },
        },
    },
    {
        "note": (
            "a resource outside the documented mailing family - recorded as ignored and "
            "still acknowledged, because the sourced no-retry guarantee makes a refusal "
            "permanent data loss"
        ),
        "payload": {
            "type": "sequence.created",
            "sequence": 7,
            "createdAt": "2026-09-25T06:00:00.000Z",
            "payloadVersion": 2,
            "data": {
                "id": "seq_88",
                "type": "sequence",
                "relationships": {
                    "prospect": {"data": {"type": "prospect", "id": "pros_northwind_ok"}}
                },
                "attributes": {"name": "Q4 nurture"},
            },
        },
    },
)

#: Where the scripted transport draws the line. A prospect whose id ends in
#: ``_ok`` is accepted, one ending in ``_limited`` is rate-limited on its first
#: attempt and accepted on the retry, and one ending in ``_retired`` is refused
#: with a 404 that no retry count can fix.
LIMITED_SUFFIX = "_limited"
RETIRED_SUFFIX = "_retired"


class DemoTransport:
    """A scripted transport, so seeding the demo never opens a socket.

    A real :class:`~dsr.outreach_feed.delivery.UrllibTransport` would try to POST to
    ``api.outreach.io`` from ``backend/seed.py``. This one answers from a fixed
    script keyed on the prospect id in the body, which also makes the demo's rows
    deterministic rather than dependent on what a hostname answers today.

    The rate limit is counted per prospect, so the retry it provokes is a real
    second attempt through the real retry policy rather than a hand-written
    ``attempts: 2``.
    """

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> PostResult:
        prospect = ""
        try:
            parsed = json.loads(body)
            prospect = str(
                (((parsed or {}).get("data") or {}).get("relationships") or {})
                .get("prospect", {})
                .get("data", {})
                .get("id", "")
            )
        except Exception:  # noqa: BLE001 - a demo transport, never the real path
            prospect = ""

        self.calls[prospect] = self.calls.get(prospect, 0) + 1
        if prospect.endswith(RETIRED_SUFFIX):
            return PostResult(
                ok=False,
                status=404,
                body='{"errors":[{"detail":"prospect not found"}]}',
                error="HTTP 404",
                retryable=False,
                duration_ms=11.0,
            )
        if prospect.endswith(LIMITED_SUFFIX) and self.calls[prospect] == 1:
            return PostResult(
                ok=False,
                status=429,
                body="rate limited",
                error="HTTP 429",
                retryable=True,
                retry_after=0,
                duration_ms=8.0,
            )
        return PostResult(ok=True, status=201, body='{"data":{"type":"event"}}', duration_ms=13.0)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed two apps, three custom events, three prospect links, and real traffic.

    The deliveries are produced by running the real
    :class:`~dsr.outreach_feed.engine.FeedPublisher` over :class:`DemoTransport`, so
    the demo's log is what this workflow actually produces rather than rows written
    by hand, and seeding never opens a socket.

    It is deliberately mixed, because a demo showing only green teaches a reviewer
    nothing:

    * a **delivered** event to a healthy prospect;
    * a **retried then failed** event - the rate limiter answers 429 on the first
      attempt, the retry policy tries again, and the second attempt is a 404 no
      retry count can fix, so the row is ``failed`` and ``needs_manual_update`` with
      a two-entry attempt log;
    * a **skipped** event on a room with no prospect link, carrying the reason, and
      a readiness view that says so rather than an empty feed;
    * **unmapped** buyer actions nobody has configured a custom event for, and one
      custom event defined but switched off;
    * four inbound **signals**: a linked open, a linked reply, an unlinked bounce,
      and a resource outside the documented family recorded as ``ignored``.

    Returns a short description of what was added, which the seeder prints.
    """
    store = RecordStore(db)
    publisher = FeedPublisher(
        store,
        transport=DemoTransport(),
        backoff=0,
        sleep=lambda _seconds: None,
    )
    source = "seed"
    actor = "dana"

    apps = {
        spec["app_identifier"]: publisher.register_app(spec, actor=actor, source=source)
        for spec in DEMO_APPS
    }

    for spec in DEMO_EVENT_TYPES:
        payload = {key: value for key, value in spec.items() if key != "app_identifier"}
        payload["app_id"] = apps[spec["app_identifier"]]["id"]
        publisher.create_event_type(payload, actor=actor, source=source)

    rooms: list[tuple[str, str]] = [
        (str(room_id), str(account))
        for room_id, account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        # No demo rooms to attach to. The apps and the configured custom events are
        # still worth having, and the seeder prints what was skipped. Filtered by
        # existence rather than trusted, because a seeder that aborts a whole
        # feature over one stale room id leaves a page nobody can review.
        return f"2 apps, {len(DEMO_EVENT_TYPES)} custom events, 0 links (no rooms to scope them to)"

    for index, prospect_id, label, extra in DEMO_LINKS:
        if index >= len(rooms):
            continue
        publisher.link_prospect(
            rooms[index][0],
            {"prospect_id": prospect_id, "label": label, **extra},
            actor=actor,
            source=source,
        )

    # The event stream the demo publishes from. The core dataset already holds
    # random activity; these are the rows that make the states above reachable, and
    # they are placed deliberately rather than at random.
    stream: list[tuple[str, dict[str, Any]]] = [
        (
            rooms[0][0],
            {
                "person": "procurement@northwind.example",
                "action": "viewed",
                "target": "Enterprise Overview Deck",
            },
        ),
        (
            rooms[0][0],
            {
                "person": "a.buyer@northwind.example",
                "action": "downloaded",
                "target": "Security & Compliance Pack",
            },
        ),
        (
            rooms[0][0],
            {
                "person": "a.buyer@northwind.example",
                "action": "commented",
                "target": "Security & Compliance Pack",
            },
        ),
    ]
    if len(rooms) > 2:
        stream.append(
            (
                rooms[2][0],
                {
                    "person": "ops@fabrikam.example",
                    "action": "downloaded",
                    "target": "Pricing One-Pager",
                },
            )
        )
    if len(rooms) > 3:
        # The last room deliberately has no prospect link, so the demo carries the
        # state the research's flow makes unavoidable: a qualifying event on a room
        # that cannot carry a prospect relationship. It is recorded as skipped with
        # the reason, not dropped, and the readiness view says so.
        stream.append(
            (
                rooms[3][0],
                {
                    "person": "lead@adventure.example",
                    "action": "viewed",
                    "target": "Enterprise Overview Deck",
                },
            )
        )
    for room_id, payload in stream:
        store.create("activity", payload, room_id=room_id, actor="system", source=source)

    ledgers = [publisher.publish(room_id, actor=actor, source=source) for room_id, _ in rooms]

    delivered = sum(ledger["counts"]["sent"] for ledger in ledgers)
    failed = sum(ledger["counts"]["failed"] for ledger in ledgers)
    skipped = sum(ledger["counts"]["skipped"] for ledger in ledgers)
    unmapped = sum(ledger["counts"]["unmapped_events"] for ledger in ledgers)

    # The four inbound deliveries, through the real signature check and normaliser.
    secret = DEMO_APPS[0]["webhook_secret"]

    def deliver(entry: Mapping[str, Any]) -> None:
        body = json.dumps(entry["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8")
        publisher.receive_webhook(
            body, {SIGNATURE_HEADER: compute_signature(secret, body)}, actor=actor, source=source
        )

    for entry in DEMO_SIGNALS:
        deliver(entry)

    # And the first one again, so the demo carries a duplicate the sender would not
    # have produced. The [sourced] no-retry guarantee means a repeat is a sender
    # bug, and a log that hid it would hide the next one too.
    deliver(DEMO_SIGNALS[0])
    signals = len(DEMO_SIGNALS) + 1

    return (
        f"2 apps, {len(DEMO_EVENT_TYPES)} custom events, {len(DEMO_LINKS)} prospect links, "
        f"{delivered} delivered, {failed} failed and needing a human, {skipped} skipped with a reason, "
        f"{unmapped} buyer actions with no custom event, {signals} webhook deliveries"
    )
