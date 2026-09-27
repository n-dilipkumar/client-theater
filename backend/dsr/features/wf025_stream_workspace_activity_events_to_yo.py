"""WF-025: stream workspace activity events to your own systems in real time.

A BUILD, not a port: there is no source branch. The research document *is* the
specification - ``docs/research/digital-sales-room-workflows/wf/WF-025.md``,
whose source is section 10 of ``docs/research/raw/analytics-intent.md`` - and the
job was to land its decisions, not to improve on them.

What this module is
-------------------
The three things the plugin contract asks a feature to own, and nothing else:

* the **route table**, as an ``APIRouter`` on ``/api/wf-025``;
* the **error mapping**, as ``EXCEPTION_HANDLERS`` for the host to attach;
* the **demo rows**, as a ``seed(db, context)`` hook the seeder calls.

The researched rules live in :mod:`dsr.event_stream`, which imports nothing from
``dsr.api`` and holds no HTTP at all. That is what keeps this module readable: a
route here is a translation of a request into one domain call, and every rule
that is argued about lives beside the other rules it is argued with.

The researched user flow, mapped onto these routes
--------------------------------------------------
1. "As an account **admin**, go to **Settings -> Webhooks**" ->
   ``POST /webhooks`` with ``role=admin``. The role is **required**, and absent
   is refused as firmly as wrong: a permission rule that cannot be seen failing
   is not a rule. See ``inferences`` entry ``admin-only-creates-a-webhook``.
2. "Click **Create Webhook**, name it, and enter the HTTPS target URL (Dock
   verifies the URL with a POST)" -> the same call. The URL is validated and
   then verified with a POST *before* anything is written, so a target that
   would not answer leaves no webhook behind.
3. "Copy the generated **secret** (View Key) and verify each delivery's
   signature" -> the secret is in the create response once, and again from
   ``POST /webhooks/{id}/key``. Every other read path returns a masked hint.
4. "Click **Create subscription** and pick subscription types" ->
   ``POST /webhooks/{id}/subscriptions`` with ``types``, served by
   ``GET /vocabulary`` so the picker and the validator read the same list.
5. "Pause or unsubscribe from the **Webhook** page ... use **Send test events**
   during setup" -> ``PATCH /subscriptions/{id}``,
   ``DELETE /subscriptions/{id}``, ``PATCH /webhooks/{id}``,
   ``POST /webhooks/{id}/test-events``.
6. "Consume ``webhook-event`` JSON and push to a data warehouse, Slack, or a
   CRM" -> ``POST /events`` records one and fans it out; ``GET /events/{id}``
   shows the exact payload that was or would be sent; ``GET /deliveries`` is
   the record of what each subscriber did with it.

And the pull-based backfill the research lists alongside it:
``GET /backfill/...`` for the five researched paths, with the researched
``properties`` parameter and the researched ``429``.

``source=`` comes from the route, never from the domain
-------------------------------------------------------
:func:`_source` builds every audit ``source`` from ``router.prefix``, so the audit
log and the route table cannot drift. The build brief names the defect this
prevents - a feature whose audit log kept recording a path the app had stopped
serving - and a domain function that hard-codes its own path cannot be caught by
reading the route table at all, so ``source`` is a *required* keyword on every
writing method in :mod:`dsr.event_stream` and a test checks every recorded
source against the routes the host actually mounted.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.event_stream import EventStream
from dsr.event_stream.errors import EventStreamError
from dsr.event_stream.filters import describe as describe_filters
from dsr.event_stream.inferences import describe as describe_inferences
from dsr.event_stream.vocabulary import vocabulary
from dsr.store import RecordStore, parse_where

FEATURE = {
    "id": "wf-025-stream-workspace-activity-events-to-yo",
    "ticket": "WF-025",
    "name": "Stream workspace activity events to your own systems in real time",
    "description": (
        "Register a signed HTTPS endpoint, subscribe it to the researched workspace "
        "activity types, and watch every delivery: what was sent, what the subscriber "
        "answered, and where the researched retry ladder says to go next."
    ),
    "nav": [{"id": "event-stream", "label": "Event stream"}],
}

router = APIRouter(prefix="/api/wf-025", tags=["wf025"])


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #

#: One :class:`EventStream` per store, rebuilt if the store itself changes.
#:
#: Cached rather than rebuilt per request for one reason, and it is a rule
#: rather than an optimisation: the researched backfill seam answers ``429`` on
#: rate limit, and a rate limit is a property of the process, not of a request. A
#: limiter rebuilt on every call would hand every request a full allowance and
#: the documented limit would not exist at all.
#:
#: Keyed on the store so tests stay honest: a new ``TestClient`` builds a new
#: store, so a suite that opens several never reaches through a service bound to
#: a database that has been closed. The HTTP suite replaces the whole service
#: through ``app.dependency_overrides``, which is also how it replaces the
#: transport, so no socket is ever opened in a test.
_STREAM: EventStream | None = None


def get_stream(store: RecordStore = StoreDep) -> EventStream:
    """The event stream over the process-wide audited store.

    Composes the documented dependency seam rather than reading
    ``request.app.state``, so this module imports nothing from ``dsr.api``.
    """
    global _STREAM
    if _STREAM is None or _STREAM.store is not store:
        _STREAM = EventStream(store)
    return _STREAM


StreamDep = Depends(get_stream)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# ``EventStreamError`` is the base of every refusal in :mod:`dsr.event_stream` and
# is raised by nothing else in the product, which is what makes it safe to hand
# to the host. A handler for ``ValueError`` or ``PermissionError`` would let this
# feature intercept exceptions raised anywhere in the app. One handler for the
# whole hierarchy also means the family keeps one shape on the wire, with the
# status carried per error rather than per class.
#
# ``RecordNotFound`` is deliberately *not* claimed: the core app already maps it
# to 404, and two handlers for one type is a collision the host refuses.


def _event_stream_error(request: Request, exc: EventStreamError) -> JSONResponse:
    """Render a domain refusal with its status, code, and remediation.

    ``detail`` is the one awkward part. The shared ``apiRequest`` in
    ``frontend/src/lib/api.js`` keeps only ``body.detail`` (and the status) and
    discards the rest of the body, so a client built on it can only ever show
    ``detail``. Rather than let the code, the remedy, and the correlation id be
    silently dropped on the way to the screen, the sentence in ``detail`` carries
    them. The structured fields are still on the wire verbatim for any other
    client.

    If ``apiRequest`` ever keeps the parsed body - as ``lib/api.js`` would if it
    set ``error.body`` - this composition can be dropped and the UI can read the
    fields again. That edit is a shared-file change and so is not made here.
    """
    payload = exc.to_payload()
    parts = [f"{exc.code}: {exc.detail}"]
    if exc.remediation:
        parts.append(exc.remediation)
    parts.append(f"correlation id {exc.correlation_id}")
    payload["detail"] = " ".join(parts)
    return JSONResponse(status_code=exc.status, content=payload, headers=exc.headers)


EXCEPTION_HANDLERS = {EventStreamError: _event_stream_error}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, so the audit log and
    the route table cannot drift apart. A domain function that hard-codes its
    own path is the defect this prevents: the audit log would keep naming a
    route the app had stopped serving, which is worse than no audit row because
    it looks authoritative.
    """
    return f"{verb} {router.prefix}{suffix}"


def _role(role: str | None, request: Request) -> str | None:
    """The caller's role, from the query string or the ``X-DSR-Role`` header.

    Two spellings because the researched UI is a page and this is an API: a form
    post has no header to spare, and a scripted client would rather set a header
    than repeat a query parameter on every call. Whichever arrives, the rule in
    :meth:`EventStream.require_admin` is the same.
    """
    return role or request.headers.get("X-DSR-Role")


def _caller(actor: str | None, request: Request, default: str = "anonymous") -> str:
    """Who the backfill rate limit is charged to.

    The actor when the caller said who they are, otherwise the client address,
    otherwise a single ``anonymous`` bucket. A shared anonymous bucket is
    deliberate: a caller that does not identify itself gets the ordinary
    allowance, not an unlimited one.
    """
    if actor:
        return str(actor)
    client = request.client
    return client.host if client and client.host else default


def _filters(raw: str | None) -> dict[str, Any]:
    try:
        return parse_where(raw)
    except ValueError as exc:
        raise EventStreamError(
            str(exc),
            code="invalid_where",
            remediation='Send where={"event":"workspace.viewed"} or where=state=failed.',
        ) from exc


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def get_vocabulary() -> dict[str, Any]:
    """The subscription types, the associated-object kinds, and the delivery policy.

    Served as data so a client renders its pickers from the same source the
    validator enforces against. Also the place a client reads the pull-surface
    mapping, so a backfill consumer can see which collection each researched
    path reads instead of guessing.
    """
    return vocabulary()


@router.get("/filters", summary="The subscription filter grammar")
def get_filter_grammar() -> dict[str, Any]:
    """What a subscription filter may say.

    Published so a filter editor can grey out what this package will refuse
    rather than letting an operator discover it at subscribe time.
    """
    return describe_filters()


@router.get("/inferences", summary="Every judgement call this build made")
def get_inferences() -> dict[str, Any]:
    """The researched half of the workflow and the inferred half, side by side.

    The point of this endpoint is that a reviewer can see where the line falls,
    which means showing the sourced vocabulary next to the inferred behaviour
    rather than only the latter. It is a read with no side effect, so it needs
    no store.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


@router.get("/webhooks", summary="List webhooks")
def list_webhooks(
    include_paused: bool = Query(default=True),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Every webhook, with its counters and a masked secret hint.

    Never the secret: a signing key readable out of a list response is an
    accident waiting to happen, and :func:`dsr.event_stream.signing.mask` is
    what makes it impossible rather than merely discouraged.
    """
    webhooks = stream.list_webhooks(include_paused=include_paused)
    return {"count": len(webhooks), "webhooks": webhooks}


@router.post("/webhooks", status_code=201, summary="Create a webhook")
def create_webhook(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    role: str | None = Query(default=None, description="must be 'admin'"),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Create a webhook, verifying the target URL with a POST first.

    The researched flow in one call: name it, enter the HTTPS target URL, and
    Dock verifies it with a POST. So the order is enforced - role checked, name
    and URL validated, verification POST sent, and only then the record written.
    A target that would not accept the POST means **no webhook is created** and
    nothing is written, so the list never fills up with endpoints that have never
    worked.

    ``role=admin`` is required. The research quotes exactly one permission
    sentence and this is that operation.
    """
    return stream.create_webhook(
        payload.get("name"),
        payload.get("targetUrl") or payload.get("target_url"),
        role=_role(role, request),
        description=str(payload.get("description") or ""),
        metadata=payload.get("metadata"),
        actor=actor,
        source=_source("POST", "/webhooks"),
    )


@router.get("/webhooks/{webhook_id}", summary="One webhook, in detail")
def read_webhook(webhook_id: str, stream: EventStream = StreamDep) -> dict[str, Any]:
    """One webhook and every subscription under it."""
    return stream.read_webhook(webhook_id)


@router.patch("/webhooks/{webhook_id}", summary="Rename, describe, or pause a webhook")
def update_webhook(
    webhook_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Patch a webhook.

    ``active: false`` is the researched pause, and it stops every subscription
    under the webhook - which is why the skip rows say ``webhook_paused`` rather
    than being absent.

    The target URL and the secret are refused here rather than ignored. Changing
    a URL re-opens the verification question and changing a secret is the rotate
    route, so the overlap rules apply; a PATCH that quietly dropped half its body
    would leave a rep believing they had changed the target.
    """
    return stream.update_webhook(webhook_id, payload, actor=actor, source=_source("PATCH", f"/webhooks/{webhook_id}"))


@router.delete("/webhooks/{webhook_id}", status_code=204, summary="Retire a webhook")
def delete_webhook(
    webhook_id: str,
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> Response:
    """Retire a webhook and unsubscribe everything under it.

    204, and a soft delete, so the cancellation is audited and the record of
    what was sent where outlives the unsubscribe.
    """
    stream.retire_webhook(webhook_id, actor=actor, source=_source("DELETE", f"/webhooks/{webhook_id}"))
    return Response(status_code=204)


@router.post("/webhooks/{webhook_id}/key", summary="View Key: read the signing secret again")
def read_key(webhook_id: str, stream: EventStream = StreamDep) -> dict[str, Any]:
    """The researched "**View Key**" affordance.

    Deliberately writes nothing, so there is no audit row for it: this product
    audits mutations, and a read that audited itself would put a signing secret
    into the audit log on every page view of the Subscriptions page. The trade is
    noted here rather than hidden - see the ``secret-is-stored-in-the-record``
    entry at ``/inferences``, which says the same thing about the create path.
    """
    return stream.reveal_key(webhook_id)


@router.post("/webhooks/{webhook_id}/key/rotate", summary="Rotate the signing secret")
def rotate_key(
    webhook_id: str,
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Rotate the secret, keeping the old one for an overlap.

    Deliveries then carry both ``X-DSR-Signature`` (new) and
    ``X-DSR-Signature-Old`` (previous), so a subscriber that has not yet
    deployed the new key can still verify. The overlap ends on the first
    successful delivery under the new secret, or on the next rotation.
    """
    return stream.rotate_key(
        webhook_id, actor=actor, source=_source("POST", f"/webhooks/{webhook_id}/key/rotate")
    )


@router.post("/webhooks/{webhook_id}/test-events", summary="Send test events")
def send_test_events(
    webhook_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """The researched "**Send test events**" during setup.

    One test event per subscribed type by default, so an operator who subscribed
    to nine types sees nine shapes arrive. Each is a real delivery row, so a test
    that fails appears in the same place a real failure does.
    """
    types = payload.get("types")
    return stream.send_test_events(
        webhook_id,
        types=list(types) if isinstance(types, (list, tuple)) else None,
        actor=actor,
        source=_source("POST", f"/webhooks/{webhook_id}/test-events"),
    )


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


@router.get("/webhooks/{webhook_id}/subscriptions", summary="A webhook's subscriptions")
def list_webhook_subscriptions(
    webhook_id: str,
    include_paused: bool = Query(default=True),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """The Subscriptions page for one webhook."""
    subscriptions = stream.list_subscriptions(
        webhook_id=webhook_id, include_paused=include_paused
    )
    return {"webhook_id": webhook_id, "count": len(subscriptions), "subscriptions": subscriptions}


@router.post("/webhooks/{webhook_id}/subscriptions", status_code=201, summary="Create a subscription")
def create_subscription(
    webhook_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Subscribe a webhook to one or more of the researched types.

    ``types`` comes from ``GET /vocabulary``, and a type outside the published
    set is accepted and reported in ``unknown_types`` rather than refused - the
    research introduces its list with "include", not "are exactly". An empty or
    non-string type is still refused, which is where the real typo risk is.

    ``filter`` is compiled here, so a bad expression is refused while the
    operator is looking at it. Omit it and the subscription receives everything
    it subscribed to, which is the researched behaviour for a vendor with no
    server-side filtering.
    """
    return stream.create_subscription(
        webhook_id,
        payload.get("types") or payload.get("events"),
        room_id=payload.get("roomId") or payload.get("room_id"),
        filter_expression=payload.get("filter"),
        description=str(payload.get("description") or ""),
        actor=actor,
        source=_source("POST", f"/webhooks/{webhook_id}/subscriptions"),
    )


@router.get("/subscriptions", summary="Every subscription")
def list_subscriptions(
    webhook_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    include_paused: bool = Query(default=True),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Every subscription across every webhook.

    ``room_id`` is a scope filter, not an "only that room" filter: a
    subscription with no room receives every room's events and is included.
    """
    subscriptions = stream.list_subscriptions(
        webhook_id=webhook_id, room_id=room_id, include_paused=include_paused
    )
    return {"count": len(subscriptions), "subscriptions": subscriptions}


@router.get("/subscriptions/{subscription_id}", summary="A subscription in detail")
def read_subscription(subscription_id: str, stream: EventStream = StreamDep) -> dict[str, Any]:
    """A subscription "viewed in detail", as the research describes.

    Detail includes the compiled filter and the recent deliveries, because a
    subscription is only understandable next to what it has actually received.
    """
    return stream.read_subscription(subscription_id)


@router.patch("/subscriptions/{subscription_id}", summary="Pause, resume, retype, or refilter")
def update_subscription(
    subscription_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Patch a subscription.

    ``{"active": false}`` is the researched pause. A flag rather than a delete,
    because a rep who misconfigures a target should not have to retype the
    subscription to stop the traffic, and the counters survive the pause.
    """
    return stream.update_subscription(
        subscription_id, payload, actor=actor, source=_source("PATCH", f"/subscriptions/{subscription_id}")
    )


@router.delete("/subscriptions/{subscription_id}", status_code=204, summary="Unsubscribe")
def delete_subscription(
    subscription_id: str,
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> Response:
    """Unsubscribe. 204, and a soft delete, so it stays auditable."""
    stream.unsubscribe(
        subscription_id, actor=actor, source=_source("DELETE", f"/subscriptions/{subscription_id}")
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@router.get("/events", summary="Recorded activity events")
def list_events(
    room_id: str | None = Query(default=None),
    event: str | None = Query(default=None),
    known: bool | None = Query(default=None, description="false for a type outside the published set"),
    anonymous: bool | None = Query(default=None, description="true when the activity had no user"),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """The event log, newest first.

    ``anonymous=true`` is the researched rule made queryable: anonymous activity
    omits ``user``, so an operator can find exactly the events a deal-room
    visitor produced without identifying themselves.
    """
    records = stream.list_events(
        room_id=room_id,
        event=event,
        known=known,
        anonymous=anonymous,
        where=_filters(where),
        limit=limit,
    )
    return {"count": len(records), "events": records}


@router.post("/events", status_code=201, summary="Record an activity event and fan it out")
def record_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Record a workspace activity and deliver it to the matching subscriptions.

    Validated before anything is written, so a subscriber can rely on the
    payload's shape: an event type that is not a string, a
    ``workspace.page.viewed`` with no ``workspacePage``, a ``presentation.viewed``
    with no share link, or a ``file_upload`` response with no ``expiresAt`` are
    all refused here rather than posted.

    The response carries the stored event, the exact payload that went out, and
    one line per delivery - including the ones that were **skipped**, with the
    reason, because "it was paused" and "your filter excluded it" are the two
    answers an operator most needs and neither can be reconstructed from a row
    that was never written.
    """
    return stream.record_event(
        payload.get("event") or payload.get("type"),
        room_id=room_id or payload.get("roomId") or payload.get("room_id"),
        property_name=payload.get("propertyName") or payload.get("property_name"),
        property_previous_value=payload.get("propertyPreviousValue")
        if "propertyPreviousValue" in payload
        else payload.get("property_previous_value"),
        property_value=payload.get("propertyValue", payload.get("property_value")),
        associated_objects=payload.get("associatedObjects") or payload.get("associated_objects"),
        asset=payload.get("asset"),
        form_questions=payload.get("formQuestions") or payload.get("form_questions"),
        form_question_responses=payload.get("formQuestionResponses")
        or payload.get("form_question_responses"),
        share_link=payload.get("shareLink") or payload.get("share_link"),
        occurred_at=payload.get("occurredAt") or payload.get("occurred_at"),
        account=payload.get("account"),
        metadata=payload.get("metadata"),
        actor=actor,
        source=_source("POST", "/events"),
    )


@router.get("/events/{event_id}", summary="One event and the payload it produced")
def read_event(event_id: str, stream: EventStream = StreamDep) -> dict[str, Any]:
    """One recorded event, with the ``webhook-event`` body built from it.

    The body is rebuilt rather than stored so this is exactly what a subscriber
    would receive, which is the only version of it worth reading.
    """
    return stream.read_event(event_id)


# --------------------------------------------------------------------------- #
# Deliveries
# --------------------------------------------------------------------------- #


@router.get("/deliveries", summary="The delivery log")
def list_deliveries(
    room_id: str | None = Query(default=None),
    subscription_id: str | None = Query(default=None),
    webhook_id: str | None = Query(default=None),
    event: str | None = Query(default=None),
    state: str | None = Query(default=None, description="delivered | retrying | failed | skipped | tested"),
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Every delivery attempt, with each retry kept whole.

    Filter on ``state`` to separate the two problems an operator is chasing: a
    ``failed`` row is a subscriber that refused or could not be reached, and a
    ``skipped`` row is one this product deliberately did not send.
    """
    records = stream.list_deliveries(
        room_id=room_id,
        subscription_id=subscription_id,
        webhook_id=webhook_id,
        event=event,
        state=state,
        where=_filters(where),
        limit=limit,
    )
    summary: dict[str, int] = {}
    for record in records:
        key = str(record["data"].get("state"))
        summary[key] = summary.get(key, 0) + 1
    return {"count": len(records), "summary": summary, "deliveries": records}


@router.get("/deliveries/{delivery_id}", summary="One delivery, with its full attempt history")
def read_delivery(delivery_id: str, stream: EventStream = StreamDep) -> dict[str, Any]:
    """One delivery row.

    ``attempt_log`` holds every attempt rather than a summary of them, so the
    reason attempt 2 happened is still readable long after the request that made
    it has gone.
    """
    return stream.read_delivery(delivery_id)


@router.post("/deliveries/{delivery_id}/retry", summary="Perform the next attempt")
def retry_delivery(
    delivery_id: str,
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Spend the next rung of the researched retry ladder.

    The 26-retry ladder is implemented as a *schedule*, not a sleep: 1 minute,
    then 10 minutes, then hourly 24 times, with a 10-second timeout per attempt.
    This route performs one attempt so a queue worker - or an operator whose
    endpoint has just come back - can drive it.

    Refuses anything that is not ``retrying``. Retrying a delivered payload is
    how a warehouse gets the same row twice.
    """
    return stream.retry_delivery(delivery_id, actor=actor, source=_source("POST", f"/deliveries/{delivery_id}/retry"))


# --------------------------------------------------------------------------- #
# Pull-based backfill
# --------------------------------------------------------------------------- #


@router.get("/backfill/workspaces", summary="Pull workspaces")
def backfill_workspaces(
    request: Request,
    properties: str | None = Query(default=None, description="omit for id, object and url only"),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """``GET /v1/workspaces``, with the researched ``properties`` and ``429``.

    Omitting ``properties`` returns only ``id``, ``object`` and ``url`` - which
    is the researched behaviour, implemented as a constant rather than an ``if``
    so that asking for nothing can never mean asking for everything.
    """
    return stream.backfill(
        "workspaces",
        properties=properties,
        limit=limit,
        room_id=room_id,
        caller=_caller(actor, request),
    )


@router.get("/backfill/workspaces/{workspace_id}", summary="Pull one workspace")
def backfill_workspace(
    workspace_id: str,
    request: Request,
    properties: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """``GET /v1/workspaces/{id}``.

    A missing id answers 404 rather than an empty list, because the researched
    path is item-addressable and "this workspace does not exist" is a different
    answer from "this workspace has no properties selected".
    """
    result = stream.backfill(
        "workspaces/{workspaceId}",
        properties=properties,
        record_id=workspace_id,
        caller=_caller(actor, request),
    )
    if result["count"] == 0:
        raise HTTPException(status_code=404, detail=f"workspace {workspace_id} not found")
    return result


@router.get("/backfill/assets", summary="Pull library assets")
def backfill_assets(
    request: Request,
    properties: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """``GET /v1/assets``."""
    return stream.backfill(
        "assets",
        properties=properties,
        limit=limit,
        room_id=room_id,
        caller=_caller(actor, request),
    )


@router.get("/backfill/forms/{form_id}/responses", summary="Pull one form's responses")
def backfill_form_responses(
    form_id: str,
    request: Request,
    properties: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """``GET /v1/forms/{id}/responses``.

    The responses a ``workspace.form.submitted`` event points at, so a
    subscriber whose warehouse missed the event can pull the same data
    afterwards. That is the whole point of the research listing this path
    alongside the webhook seam.
    """
    return stream.backfill(
        "forms/{formId}/responses",
        properties=properties,
        limit=limit,
        form_id=form_id,
        caller=_caller(actor, request),
    )


@router.get("/backfill/workspace-plan-tasks", summary="Pull workspace plan tasks")
def backfill_plan_tasks(
    request: Request,
    properties: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    actor: str | None = Query(default=None),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """``GET /v1/workspace-plan-tasks``.

    The collection behind the ``workspacePlanTask`` associated object, so the
    ``course.completed`` and ``course.reviewed`` events have something to be
    joined against.
    """
    return stream.backfill(
        "workspace-plan-tasks",
        properties=properties,
        limit=limit,
        room_id=room_id,
        caller=_caller(actor, request),
    )


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the whole account's stream")
def summary(stream: EventStream = StreamDep) -> dict[str, Any]:
    """Everything above the fold, unscoped.

    Webhooks are account level, so the unscoped summary is the natural one; the
    room-scoped route below answers the same question for one deal.
    """
    return stream.summary()


@router.get("/rooms/{room_id}/summary", summary="Counts for one room's stream")
def room_summary(room_id: str, stream: EventStream = StreamDep) -> dict[str, Any]:
    """Everything above the fold for one room.

    Scoped to exactly the rows the room-scoped lists would return, so the tiles
    and the tables cannot disagree.
    """
    return stream.summary(room_id=room_id)


@router.get("/rooms/{room_id}/events", summary="One room's activity events")
def room_events(
    room_id: str,
    event: str | None = Query(default=None),
    where: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Events for one room, plus the unscoped ones that reach every room.

    A subscription with no room receives every room's activity, so an operator
    looking at one deal still sees what the account-wide integration receives.
    Excluding those would make the room view disagree with the delivery log.
    """
    records = stream.list_events(room_id=room_id, event=event, where=_filters(where), limit=limit)
    return {"room_id": room_id, "count": len(records), "events": records}


@router.get("/rooms/{room_id}/deliveries", summary="One room's deliveries")
def room_deliveries(
    room_id: str,
    state: str | None = Query(default=None),
    subscription_id: str | None = Query(default=None),
    where: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    stream: EventStream = StreamDep,
) -> dict[str, Any]:
    """Deliveries for one room, with the state counts for exactly those rows."""
    records = stream.list_deliveries(
        room_id=room_id,
        state=state,
        subscription_id=subscription_id,
        where=_filters(where),
        limit=limit,
    )
    summary: dict[str, int] = {}
    for record in records:
        key = str(record["data"].get("state"))
        summary[key] = summary.get(key, 0) + 1
    return {"room_id": room_id, "count": len(records), "summary": summary, "deliveries": records}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The brief is explicit that demo data containing only success teaches a
# reviewer nothing, and that the features already on main seed a declined room,
# a failed delivery, a retried one, and a pending approval. This one seeds the
# states *this* research says matter:
#
# * a healthy, signed, verified webhook with two subscriptions;
# * a paused subscription - the researched "paused" state, and the one that
#   produces a ``skipped`` delivery row rather than nothing at all;
# * a webhook whose deliveries fail permanently, so the log has something that
#   needs a person;
# * a delivery sitting mid-ladder with ``retrying`` and a ``next_attempt_at``,
#   because the ladder is the researched policy and a demo that never shows a
#   retry has not shown the feature;
# * an **anonymous** event, which is the researched rule that omits ``user``;
# * a ``workspace.form.submitted`` whose ``file_upload`` response carries a
#   presigned URL that has already expired - the researched one-hour expiry,
#   made visible;
# * a ``presentation.viewed`` share-link event and an ``asset.downloaded`` with
#   ``trackingEnabled``, the two asset rules the research calls out by name;
# * ``form_response`` and ``plan_task`` rows, so the two backfill routes that
#   have no other source in the demo dataset return something.

DEMO_HEALTHY_TARGET = "https://hooks.example/northwind-warehouse"
DEMO_SIGNAL_TARGET = "https://hooks.example/slack-notifier"
DEMO_RETIRED_TARGET = "https://hooks.example/retired-hook"

DEMO_PLAN_TASKS = (
    ("Security review", "completed", "2026-08-14"),
    ("SOC 2 evidence pack", "completed", "2026-09-02"),
    ("Procurement checklist", "in_progress", None),
)

DEMO_FORM_RESPONSES = (
    (
        "form_security",
        "Northwind Traders",
        (
            {"id": "q_size", "type": "number", "label": "How many seats?"},
            {"id": "q_upload", "type": "file_upload", "label": "Attach your security policy"},
        ),
        (
            {"questionId": "q_size", "value": 40},
            {
                "questionId": "q_upload",
                "value": {
                    "url": "https://objects.example/signed/northwind/security-policy.pdf?expires=1788000000",
                    "key": "northwind/security-policy.pdf",
                    "expiresAt": "2026-08-28T18:40:00.000+00:00",
                },
            },
        ),
    ),
    (
        "form_order_form",
        "Fabrikam Logistics",
        ({"id": "q_accept", "type": "boolean", "label": "Accept the order form?"},),
        ({"questionId": "q_accept", "value": True},),
    ),
)


class DemoTransport:
    """A scripted transport, so seeding the demo never opens a socket.

    A real :class:`~dsr.event_stream.delivery.UrllibTransport` would try to
    POST to ``hooks.example`` from ``backend/seed.py``. This one answers from a
    fixed script, which also makes the demo's rows deterministic rather than
    dependent on what a hostname happens to answer today.

    Two scripts, and both apply to **real traffic only**:

    * the retired target answers ``404``, which is the permanent failure no retry
      count can fix - the row the log has to send a person back to;
    * the rate-limited path answers ``429`` on its first real attempt, which is
      the researched rate limit and produces a real ``retrying`` row with the
      first rung of the ladder on it.

    "Real traffic only" is what lets the same URL be *verified* at creation and
    broken afterwards, which is the state a retired hook and a rate-limited
    notifier are actually in by the time somebody opens the demo. A webhook whose
    target never accepted a POST would not exist at all, so a transport that
    404'd the verification too would prevent this feature from seeding the very
    state the research says the log exists to track.
    """

    RATE_LIMITED_TARGET = DEMO_SIGNAL_TARGET

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.real_calls: dict[str, int] = {}

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float):
        from dsr.event_stream.delivery import DeliveryResult

        self.calls[url] = self.calls.get(url, 0) + 1
        if self._is_test(body):
            return DeliveryResult(ok=True, status=202, body="verification accepted", duration_ms=6.0)

        self.real_calls[url] = self.real_calls.get(url, 0) + 1
        if url == DEMO_RETIRED_TARGET:
            return DeliveryResult(
                ok=False,
                status=404,
                body="no such hook",
                error="HTTP 404",
                retryable=False,
                duration_ms=9.0,
            )
        if url == self.RATE_LIMITED_TARGET and self.real_calls[url] == 1:
            # [sourced] "429 on rate limit" is the one response code the
            # research names on the delivery-adjacent seam. No Retry-After, so
            # the recorded next attempt is the ladder's genuine first rung - one
            # minute - rather than a value written in by hand.
            return DeliveryResult(
                ok=False,
                status=429,
                body="slow down",
                error="HTTP 429",
                retryable=True,
                duration_ms=11.0,
            )
        return DeliveryResult(ok=True, status=202, body="accepted", duration_ms=14.0)

    @staticmethod
    def _is_test(body: bytes) -> bool:
        try:
            return bool(json.loads(body.decode("utf-8")).get("test"))
        except (UnicodeDecodeError, ValueError):  # pragma: no cover - defensive
            return False


def seed(db, context: dict[str, Any]) -> str:
    """Seed the webhooks, subscriptions, events, and deliveries the research makes matter.

    Everything is produced by running the real :class:`EventStream` over
    :class:`DemoTransport`, so the demo cannot show a shape the workflow would
    not produce - same envelope, same states, same audit rows.
    """
    from dsr.event_stream import EventStream
    from dsr.event_stream.targets import issue_presigned_url
    from dsr.store import RecordStore

    store = RecordStore(db)
    now = context.get("now")
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    stream = EventStream(store, transport=DemoTransport(), now=now)
    source = "seed"

    # The two backfill routes with no other source in the core dataset.
    tasks = []
    for index, (title, status, completed_on) in enumerate(DEMO_PLAN_TASKS):
        room_id = rooms[index % len(rooms)][0] if rooms else None
        tasks.append(
            db.create(
                "plan_task",
                {
                    "title": title,
                    "status": status,
                    "completed_on": completed_on,
                    "course": "Security onboarding",
                },
                room_id=room_id,
                actor="dana",
                source=source,
            )
        )

    responses = []
    # The second demo deal where there is one, so a reviewer sees the same form
    # answered in two rooms rather than everything piled onto room zero.
    second_room = rooms[min(1, len(rooms) - 1)][0] if rooms else None
    for form_id, account, questions, answers in DEMO_FORM_RESPONSES:
        room_id = second_room
        upload = next(
            (a["value"] for a in answers if isinstance(a.get("value"), dict) and "url" in a["value"]),
            None,
        )
        responses.append(
            db.create(
                "form_response",
                {
                    "formId": form_id,
                    "account": account,
                    "formQuestions": [dict(q) for q in questions],
                    "formQuestionResponses": [dict(a) for a in answers],
                    "submittedAt": stream.moment(),
                    # The researched expiry, one hour out from issue. Carried on
                    # the row so a backfill consumer sees the same deadline the
                    # event did.
                    "uploadExpiresAt": (upload or {}).get("expiresAt"),
                },
                room_id=room_id,
                actor="dana",
                source=source,
            )
        )

    if not rooms:
        # Nothing to attach events to. The registries and the backfill rows are
        # still worth having, and the seeder prints what was skipped.
        return (
            f"0 webhooks, 0 subscriptions, {len(tasks)} plan tasks, {len(responses)} form responses "
            "(no rooms to scope the stream to)"
        )

    # 1. The healthy integration: signed, verified, two subscriptions.
    healthy = stream.create_webhook(
        "Northwind to warehouse",
        DEMO_HEALTHY_TARGET,
        role="admin",
        description="Everything the account does, into the warehouse.",
        actor="dana",
        source=source,
    )
    healthy_id = healthy["webhook"]["id"]
    stream.create_subscription(
        healthy_id,
        [
            "workspace.viewed",
            "workspace.page.viewed",
            "workspace.file.downloaded",
            "asset.downloaded",
            "presentation.viewed",
        ],
        description="Engagement for the whole account.",
        actor="dana",
        source=source,
    )
    stream.create_subscription(
        healthy_id,
        ["workspace.form.submitted"],
        room_id=rooms[0][0],
        description="Only Northwind, only the security form.",
        filter_expression=f"$.associatedObjects.account[?(@.id == '{rooms[0][1]}')]",
        actor="dana",
        source=source,
    )

    # 2. The Slack notifier: rate-limited on its first attempt, so the ladder has
    #    a first rung sitting on a real row. And a paused subscription, so the
    #    researched "paused" state has something to show.
    signal = stream.create_webhook(
        "Slack notifier",
        DEMO_SIGNAL_TARGET,
        role="admin",
        description="Ping Slack when a deck is downloaded.",
        actor="dana",
        source=source,
    )
    signal_id = signal["webhook"]["id"]
    loud = stream.create_subscription(
        signal_id,
        ["workspace.file.downloaded"],
        description="Deck downloads. Paused during the security review.",
        actor="dana",
        source=source,
    )
    stream.create_subscription(
        signal_id,
        ["workspace.viewed", "course.completed"],
        description="Every view and every course completion, unscoped.",
        actor="dana",
        source=source,
    )
    stream.update_subscription(
        loud["id"], {"active": False}, actor="dana", source=source
    )

    # 3. The retired hook: permanent 404, so the log has a row that needs a person.
    retired = stream.create_webhook(
        "Retired CRM hook",
        DEMO_RETIRED_TARGET,
        role="admin",
        description="The hook was retired. This row will not fix itself.",
        actor="dana",
        source=source,
    )
    retired_id = retired["webhook"]["id"]
    stream.create_subscription(
        retired_id,
        ["workspace.created", "workspace.order_form.fully_signed"],
        room_id=rooms[2 % len(rooms)][0] if len(rooms) > 2 else rooms[0][0],
        description="Room lifecycle, scoped to one deal.",
        actor="dana",
        source=source,
    )

    def objects(workspace: str, account: str, user: str | None = None, **extra: Any) -> dict[str, Any]:
        built: dict[str, Any] = {"workspace": {"id": workspace}, "account": {"id": account}}
        if user:
            built["user"] = {"id": user, "email": f"{user}@northwind.example"}
        built.update(extra)
        return built

    room0, account0 = rooms[0]
    room1, account1 = rooms[min(1, len(rooms) - 1)]

    # A signed-in view, delivered.
    stream.record_event(
        "workspace.viewed",
        room_id=room0,
        account=account0,
        associated_objects=objects(room0, account0, "a.buyer"),
        actor="dana",
        source=source,
    )
    # A page view, delivered, carrying the researched workspacePage.
    stream.record_event(
        "workspace.page.viewed",
        room_id=room0,
        account=account0,
        associated_objects=objects(
            room0, account0, "b.buyer", workspacePage={"id": "page_security", "title": "Security review"}
        ),
        actor="dana",
        source=source,
    )
    # An anonymous view: no user, which is the researched rule. The paused
    # subscription also skips here, so one event exercises both.
    stream.record_event(
        "workspace.viewed",
        room_id=room0,
        account=account0,
        associated_objects=objects(room0, account0),
        metadata={"kind": "deal-room link click"},
        actor="dana",
        source=source,
    )
    # A file download: rate-limited on the first attempt, so this produces the
    # retrying row with the first rung of the ladder on it.
    stream.record_event(
        "workspace.file.downloaded",
        room_id=room0,
        account=account0,
        associated_objects=objects(
            room0, account0, "a.buyer", file={"id": "file_deck", "name": "Enterprise Overview Deck"}
        ),
        actor="dana",
        source=source,
    )
    # An asset download with the researched trackingEnabled flag.
    stream.record_event(
        "asset.downloaded",
        room_id=room1,
        account=account1,
        associated_objects=objects(room1, account1, "procurement"),
        asset={
            "name": "Security & Compliance Pack",
            "type": "pdf",
            "shareUrl": "https://share.example/s/northwind",
            "isInternal": False,
            "tags": ["security", "compliance"],
            "downloadEnabled": True,
            "trackingEnabled": True,
        },
        actor="dana",
        source=source,
    )
    # A form submission whose upload was signed two days ago: the researched
    # one-hour presigned expiry, already spent.
    stale = issue_presigned_url("northwind/security-policy.pdf", now=now)
    stale["expiresAt"] = "2026-08-28T18:40:00.000+00:00"
    stale["url"] = "https://objects.example/signed/northwind/security-policy.pdf?expires=1788000000"
    stream.record_event(
        "workspace.form.submitted",
        room_id=room0,
        account=account0,
        associated_objects=objects(
            room0,
            account0,
            "a.buyer",
            workspaceForm={"id": "form_security", "name": "Security review"},
        ),
        form_questions=[
            {"id": "q_size", "type": "number", "label": "How many seats?"},
            {"id": "q_upload", "type": "file_upload", "label": "Attach your security policy"},
        ],
        form_question_responses=[
            {"questionId": "q_size", "value": 40},
            {"questionId": "q_upload", "value": stale},
        ],
        actor="dana",
        source=source,
    )
    # A share-link presentation view, and a course completion.
    stream.record_event(
        "presentation.viewed",
        room_id=room1,
        account=account1,
        associated_objects=objects(room1, account1, "lead"),
        share_link="https://share.example/s/northwind-deck",
        actor="dana",
        source=source,
    )
    if tasks:
        stream.record_event(
            "course.completed",
            room_id=room1,
            account=account1,
            associated_objects=objects(
                room1, account1, "procurement", workspacePlanTask={"id": tasks[0]["id"], "title": tasks[0]["data"]["title"]}
            ),
            actor="dana",
            source=source,
        )
    # A room lifecycle event, so the retired hook's subscription has something to
    # fail on.
    lifecycle_room, lifecycle_account = (
        (rooms[2][0], rooms[2][1]) if len(rooms) > 2 else (room0, account0)
    )
    stream.record_event(
        "workspace.order_form.fully_signed",
        room_id=lifecycle_room,
        account=lifecycle_account,
        associated_objects=objects(
            lifecycle_room,
            lifecycle_account,
            "ops",
            file={"id": "file_order_form", "name": "Order Form"},
        ),
        property_name="orderForm.status",
        property_previous_value="pending",
        property_value="fully_signed",
        actor="dana",
        source=source,
    )

    deliveries = store.list("stream_delivery", limit=500)
    states: dict[str, int] = {}
    for record in deliveries:
        key = str(record["data"].get("state"))
        states[key] = states.get(key, 0) + 1

    return (
        f"3 webhooks, 5 subscriptions (1 paused), {len(tasks)} plan tasks, "
        f"{len(responses)} form responses, {len(deliveries)} deliveries "
        f"({states.get('delivered', 0)} delivered, {states.get('retrying', 0)} retrying, "
        f"{states.get('failed', 0)} failed, {states.get('skipped', 0)} skipped)"
    )
