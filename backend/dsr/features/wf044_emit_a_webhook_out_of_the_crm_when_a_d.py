"""WF-044: emit a webhook out of the CRM when a deal stage changes.

The researched workflow, in full. A rep builds the automation **in the CRM** -
*Automation -> Workflows -> edit workflow -> + -> Data ops -> Send a webhook*
(step 1) - adds a start condition such as *"deal stage becomes 'Contract Sent'"*
or *"a contact property changes"* (step 2), picks method **POST**, enters the
room's HTTPS webhook URL, and configures authentication (step 3), chooses the body
as **Include all [object] properties** or **Customize request body** (step 4), then
clicks **Save**, **Publish**, and the built-in **Test** control (step 5). The
room's endpoint verifies the signature, resolves the record, and updates the
buyer's room state (step 6).

What the contract meant for this build
--------------------------------------
**The prefix is ``/api/wf-044``, and the endpoint is room-scoped.** The research's
extensibility line is *"The room exposes one inbound endpoint per tenant with a
versioned payload contract"*, so everything that belongs to a tenant lives under
``/rooms/{room_id}/...``, and the contract version is in the path -
``/rooms/{room_id}/crm/v1/webhook`` - because a versioned contract whose version is
only in a body is not versioned.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."``, so the audit row names the route that actually served
it. A URL string hardcoded inside a domain method is a defect, and the same class
of bug has shipped in this codebase before: a feature's audit log kept naming a
path the app had stopped serving. ``source`` is a *required* keyword on every
writing method of :class:`~dsr.crm_outbound_webhooks.engine.WebhookEngine`, so
omitting it is a ``TypeError`` at the call site rather than an untraceable row.

**One handler for the whole error hierarchy.** ``WebhookError`` is the base of
every refusal in :mod:`dsr.crm_outbound_webhooks`, and each carries its own
``status`` and ``code``, so one handler answers 400 for a malformed body, 401 for
a request that failed authentication, 403 for a missing permission and 409 for one
that conflicts with live state - without the handler knowing which is which. It is
a domain type, so registering it globally cannot intercept anything unrelated.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
404, and two handlers for one type is a collision the host refuses.

**The webhook route takes the raw request, not a parsed body.** A signature covers
the exact bytes that were sent, so this route reads ``await request.body()`` and
hands the string on rather than letting FastAPI re-serialise a parsed object. A
room that verified its own re-serialisation would refuse a correctly signed
request whose keys arrived in a different order, and that failure would only ever
appear against a real CRM.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm_outbound_webhooks import (
    CONTRACT_VERSION,
    OUTCOME_DUPLICATE,
    WebhookEngine,
    WebhookError,
    sign,
    uri_for,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-044-emit-a-webhook-out-of-the-crm-when-a-d",
    "ticket": "WF-044",
    "name": "Emit a webhook out of the CRM when a deal stage changes",
    "description": (
        "Publish one inbound endpoint per room, aim any number of CRM-side automations at "
        "it, and take a signed POST or GET from the CRM: verify the signature, resolve "
        "the record, update the deal panel, and notify the rep. Workflow webhook calls do "
        "not count towards the API rate limit, and the room needs no per-workflow secret "
        "because it can verify the request signature."
    ),
    "nav": [{"id": "crm-outbound-webhook", "label": "CRM webhook"}],
}

router = APIRouter(prefix="/api/wf-044", tags=["wf044"])


def get_engine(store: RecordStore = StoreDep) -> WebhookEngine:
    """A :class:`WebhookEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` the feature host exists to make unnecessary. Building it here
    also leaves the engine a plain object, which is what a test constructs.
    """
    return WebhookEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _webhook_error(request: Request, exc: WebhookError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``WebhookError`` is the base of every
    refusal in :mod:`dsr.crm_outbound_webhooks` - a URL that is not HTTPS, a
    method the researched action cannot send, an authentication type outside the
    three published, a missing App ID, a second endpoint for a room, a missing
    permission, an endpoint nobody published, a request that failed
    authentication, a payload that is not the contract - and all of them are the
    caller's to fix.

    The status rides on the exception rather than being decided here, because a
    malformed body and a delivery that conflicts with live state are both this
    package's errors and only one of them conflicts with state that already
    exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {WebhookError: _webhook_error}


def _request_uri(request: Request) -> str:
    """The request URI exactly as it arrived, query string included.

    A signature covers the URI, and for a GET the query string *is* the payload -
    so the bytes are taken from the raw scope rather than re-encoded by the
    framework, which would let a correctly signed request fail on a
    space-versus-plus difference in one parameter.
    """
    raw_query = request.scope.get("query_string") or b""
    suffix = raw_query.decode("utf-8", errors="replace")
    return f"{request.url.path}?{suffix}" if suffix else request.url.path


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The methods, the HTTPS rule, the three authentication types and what each one
    requires, the two body modes, the objects, the two permissions, the
    1,000-per-app cap, the outcomes, the reasons, and the contract version. A
    client renders its endpoint editor from this rather than from a list compiled
    into the page, so a mode added server-side reaches every client at once - and
    the editor can never disagree with the validator about what is legal.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the editor on the CRM side in unusual detail and says
    almost nothing about how the *receiving* end behaves. It does not publish the
    signature scheme, does not say whether a Super Admin may publish, does not say
    what a failed authentication records, and does not say what a delivery for a
    deal this room has never seen does. Those are collected here - named,
    traceable, bounded, and served - rather than left as comments in function
    bodies. The sourced half comes back beside the inferred half, because the
    point of the endpoint is to see where the line falls.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Steps 1 to 4, 5 and 6: the room's Webhook settings page
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/endpoint")
def read_endpoint(room_id: str, engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """The room's Webhook settings: the endpoint URL and the secret.

    The research names this page - *"room **Webhook settings** page showing the
    endpoint URL and secret"* - so the secret is in the response rather than
    masked. It is one secret for the whole endpoint, which is the point: *"Because
    the room can verify the request signature, it does not need a per-workflow
    secret."*

    A room with no endpoint gets 404 with the researched flow's first step named,
    rather than an empty object a page would render as a configured endpoint.
    """
    endpoint = engine.endpoint(room_id)
    if endpoint is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"room {room_id} has no inbound webhook endpoint yet. In the CRM: "
                "Automation -> Workflows -> edit workflow -> + -> Data ops -> Send a "
                "webhook, and point it at this room's URL."
            ),
        )
    automations = engine.automations(room_id, include_retired=True)
    return {
        "room_id": room_id,
        "endpoint": dict(endpoint["data"], id=endpoint["id"], revision=endpoint["revision"]),
        "path": uri_for(room_id),
        "contract_version": CONTRACT_VERSION,
        "automations": [
            dict(row["data"], id=row["id"]) for row in automations if not row.get("deleted_at")
        ],
        "retired_automations": [
            dict(row["data"], id=row["id"]) for row in automations if row.get("deleted_at")
        ],
    }


@router.post("/rooms/{room_id}/endpoint", status_code=201)
def register_endpoint(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    permissions: str | None = Query(
        default=None, description="edit_workflows or super_admin, comma separated"
    ),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 1 to 4, as the room records them: one endpoint, HTTPS, one auth type.

    The record is what the rep is about to type into the CRM's **Send a webhook**
    panel - the URL, the method, the authentication and the body - plus the two
    room-side field names: which property carries the external record id, and which
    carries the stage. Naming those here rather than hard-coding a vendor's field
    names is what lets a team whose CRM calls them something else configure them
    once.

    Draft until it is published, because *"Workflows must be **published** to go
    live"*, and a second endpoint for the room is refused: one per tenant is what
    lets any number of automations target it.
    """
    return engine.register(
        room_id,
        payload,
        permissions=permissions,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/endpoint",
    )


@router.patch("/rooms/{room_id}/endpoint")
def amend_endpoint(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    permissions: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Correct the configuration - the rep's edit-and-republish, room-side.

    The whole merged endpoint is re-validated rather than field by field, so an
    amendment cannot smuggle in a rule another key would have broken. A published
    endpoint is amendable; that judgement call is ``published-endpoints-are-amendable``
    in the inference register, and unpublishing is the separate, deliberate act.
    """
    return engine.amend(
        room_id,
        payload,
        permissions=permissions,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{{room_id}}/endpoint",
    )


@router.post("/rooms/{room_id}/endpoint/publish")
def publish_endpoint(
    room_id: str,
    actor: str | None = Query(default=None),
    permissions: str | None = Query(default=None, description="publish_workflows, comma separated"),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Step 5's **Publish**: the gate every delivery is turned away at until it is.

    Needs the permission the source names for this step and no other - *"To publish
    workflows, users must have Publish permissions for workflows"* - so the save
    permission that registered this endpoint is not enough on its own.
    """
    return engine.publish(
        room_id,
        permissions=permissions,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/endpoint/publish",
    )


@router.post("/rooms/{room_id}/endpoint/unpublish")
def unpublish_endpoint(
    room_id: str,
    actor: str | None = Query(default=None),
    permissions: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """The off switch. Deliveries stop being taken and start being recorded and refused.

    Not retirement: the configuration, the automations and every delivery stay, so
    re-publishing resumes exactly where it left off. This is a separate route from
    :func:`amend_endpoint` precisely so that stopping the traffic is a deliberate
    act rather than a side effect of fixing a URL.
    """
    return engine.unpublish(
        room_id,
        permissions=permissions,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/endpoint/unpublish",
    )


@router.get("/rooms/{room_id}/endpoint/sample")
def endpoint_sample(room_id: str, engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """The payload the CRM's built-in **Test** control will send, and its signature.

    The control lives in the CRM - there is no room-side button that can press it
    - so this is a preview, and the response says ``is_preview``. It is here
    because a rep comparing their CRM test send against the room's delivery log
    needs both to be the same bytes, and the signed canonical string is what makes
    that checkable instead of a guess.
    """
    return engine.sample(room_id)


# --------------------------------------------------------------------------- #
# Step 2, as the room records it: the automations aimed at the endpoint
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/automations")
def list_automations(
    room_id: str,
    include_retired: bool = Query(default=False),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """The CRM-side workflows pointed at this room's endpoint.

    Unbounded by design - *"any number of CRM-side automations can target it"* -
    and bounded by the vendor's own 1,000 per app, reported beside the count. Each
    row carries its start condition and the static values its **Customize request
    body** adds, which are the two things the room can report about an automation
    it does not otherwise control.
    """
    rows = engine.automations(room_id, include_retired=include_retired)
    endpoint = engine.endpoint(room_id)
    app_id = str(((endpoint or {}).get("data", {}).get("auth") or {}).get("app_id") or room_id)
    used = engine.subscriptions_used(app_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "retired": sum(1 for row in rows if row.get("deleted_at")),
        "app_id": app_id,
        "subscriptions_used": used,
        "subscription_limit": 1000,
        "automations": [
            dict(row["data"], id=row["id"], room_id=row.get("room_id")) for row in rows
        ],
    }


@router.post("/rooms/{room_id}/automations", status_code=201)
def create_automation(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    permissions: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Add one more CRM-side automation: a start condition, and its body overrides.

    The 1,000-per-app cap is enforced here and refused rather than clamped, and it
    is counted across every room bound to the same App ID - *"You can create up to
    1,000 webhook subscriptions per app"* is a limit per app, so a per-room count
    would not be the limit the research quotes.
    """
    return engine.add_automation(
        room_id,
        payload,
        permissions=permissions,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/automations",
    )


@router.delete("/rooms/{room_id}/automations/{automation_id}")
def retire_automation(
    room_id: str,
    automation_id: str,
    actor: str | None = Query(default=None),
    permissions: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Retire one automation. A soft delete, so the deliveries naming it resolve.

    The delivery log says which automation fired, and destroying the row would
    leave that history pointing at nothing - which is the exact defect the
    audit-source rule exists to prevent, one layer up.
    """
    return engine.retire_automation(
        room_id,
        automation_id,
        permissions=permissions,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{{room_id}}/automations/{{automation_id}}",
    )


# --------------------------------------------------------------------------- #
# The inbound endpoint, room-scoped and versioned
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/crm/v1/webhook", status_code=201)
async def deliver(
    room_id: str,
    request: Request,
    response: Response,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Step 6: the signed POST the CRM sends, taken and applied.

    The researched data flow end to end: *"outbound HTTP request built from the
    object + selected properties (plus static fields) -> signed POST to the room
    endpoint -> room authenticates (signature / bearer) -> maps payload onto the
    room model -> updates deal panel and notifies the rep."*

    201 when the delivery was applied and 200 when it was a repeat of one already
    taken. A retry after a timeout is normal CRM behaviour - *"When a webhook is
    slow or times out, the workflow action may take longer than expected to
    execute"* - and answering it 201 would tell the CRM a new stage change happened
    when nothing did.

    The raw body is read and handed on as text, because a signature covers the
    exact bytes that were sent and a re-serialised object is not those bytes.
    """
    raw = (await request.body()).decode("utf-8", errors="replace")
    report = engine.receive(
        room_id,
        method="POST",
        uri=_request_uri(request),
        headers=dict(request.headers),
        query=dict(request.query_params),
        body=raw,
        actor=actor or "crm",
        source=f"POST {router.prefix}/rooms/{{room_id}}/crm/v{{version}}/webhook".replace(
            "{version}", str(CONTRACT_VERSION)
        ),
    )
    if report.get("outcome") == OUTCOME_DUPLICATE:
        response.status_code = 200
    return report


@router.get("/rooms/{room_id}/crm/v1/webhook", status_code=201)
def deliver_get(
    room_id: str,
    request: Request,
    response: Response,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """The same contract, sent as a GET.

    *"You can send both POST and GET requests using workflows."* Both are served,
    because the method dropdown offers both and refusing one would break a
    configuration the researched tool allows. A GET has no body, so its properties
    arrive in the query string - which is also what the signature covers, since the
    signed URI includes it.

    It writes, like the POST above. That is unusual for a GET and it is what the
    vendor's own action does; the request is authenticated by signature or bearer,
    so there is no ambient authority a browser could borrow to trigger it.
    """
    uri = _request_uri(request)
    report = engine.receive(
        room_id,
        method="GET",
        uri=uri,
        headers=dict(request.headers),
        query=dict(request.query_params),
        body=None,
        actor=actor or "crm",
        source=f"GET {router.prefix}/rooms/{{room_id}}/crm/v{{version}}/webhook".replace(
            "{version}", str(CONTRACT_VERSION)
        ),
    )
    if report.get("outcome") == OUTCOME_DUPLICATE:
        response.status_code = 200
    return report


# --------------------------------------------------------------------------- #
# What the endpoint produced
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/deals")
def list_deals(room_id: str, engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """The deal panel: what the room currently believes each deal's stage to be.

    Every property a payload carried is here verbatim, alongside the keys the last
    delivery changed and the keys it stopped sending - so a value the room is
    holding but no longer being told about is visible rather than quietly believed.
    """
    deals = engine.deals(room_id)
    return {
        "room_id": room_id,
        "count": len(deals),
        "stale_properties": sorted({key for deal in deals for key in deal["stale_keys"]}),
        "deals": deals,
    }


@router.get("/rooms/{room_id}/deals/{deal_id}")
def read_deal(room_id: str, deal_id: str, engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """One deal, by this room's record id or by the CRM's own external id.

    Both spellings answer, because the settings page holds an external id and the
    delivery log holds a record id, and a reader should not have to know which one
    a link was built from.
    """
    deal = engine.deal(room_id, deal_id)
    if deal is None:
        raise HTTPException(
            status_code=404, detail=f"no deal {deal_id} in room {room_id}'s deal panel"
        )
    return deal


@router.get("/rooms/{room_id}/deliveries")
def list_deliveries(
    room_id: str,
    outcome: str | None = Query(default=None, description="accepted | duplicate | refused"),
    effect: str | None = Query(
        default=None, description="stage_changed | stage_unchanged | properties_only | none"
    ),
    external_id: str | None = Query(default=None),
    include_refused: bool = Query(
        default=True, description="refused deliveries are the rows a rep needs"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """The delivery log, newest first, refused deliveries included.

    A refused delivery is the row a rep reads when their automation reached a room
    and nothing happened, and each one carries the reason from the served reason
    vocabulary plus the endpoint status at the time - so "it never got published"
    and "your signature did not match" are distinguishable without reading a
    support ticket.
    """
    rows = engine.deliveries(
        room_id,
        outcome=outcome,
        effect=effect,
        external_id=external_id,
        include_refused=include_refused,
        limit=limit,
    )
    by_outcome: dict[str, int] = {}
    for row in rows:
        key = str(row.get("outcome") or "unknown")
        by_outcome[key] = by_outcome.get(key, 0) + 1
    return {"room_id": room_id, "count": len(rows), "by_outcome": by_outcome, "deliveries": rows}


@router.get("/rooms/{room_id}/deliveries/{delivery_id}")
def read_delivery(
    room_id: str, delivery_id: str, engine: WebhookEngine = EngineDep
) -> dict[str, Any]:
    """One delivery, with the payload as it arrived and every decision made on it."""
    row = engine.delivery(room_id, delivery_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"delivery {delivery_id} not found in room {room_id}"
        )
    return row


@router.get("/rooms/{room_id}/notices")
def list_notices(
    room_id: str,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """What the endpoint told the rep, newest first.

    One notice per delivery that changed the deal panel, and none for a delivery
    that did not - so this list is a list of things that actually happened rather
    than a list of webhook calls.
    """
    rows = engine.notices(room_id, unread_only=unread_only, limit=limit)
    return {
        "room_id": room_id,
        "count": len(rows),
        "unread": sum(1 for row in rows if not row.get("read")),
        "notices": rows,
    }


@router.post("/rooms/{room_id}/notices/acknowledge")
def acknowledge_notices(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WebhookEngine = EngineDep,
) -> dict[str, Any]:
    """Mark notices read, by id or all of them.

    *"Notifies the rep"* is half a feature if a notification cannot be dealt with.
    It is a write, so it is audited under this route rather than folded into a read.
    """
    return engine.acknowledge(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/notices/acknowledge",
    )


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: WebhookEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room's header, and the researched constraints beside them.

    Counted over this room's endpoint, deliveries, deals and notices rather than
    the whole collections, so a room's header says what happened in that room. The
    two notes are here because both are things a reader looks for and does not find:
    this endpoint applies no inbound quota, and every delivery is one transaction
    because a slow endpoint makes the *CRM's* workflow action slow.
    """
    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The feature's own prefix, duplicated here so the demo data's audit sources name
#: the same routes the router serves. A change to the prefix has to be made
#: deliberately in both places, which is the point of writing it twice.
PREFIX = "/api/wf-044"

#: The three researched authentication types, so the demo can show all three
#: working rather than one of them described.
DEMO_SIGNATURE_SECRET = "northwind-hubspot-client-secret"
DEMO_BEARER_SECRET = "contoso-room-bearer-token"
DEMO_API_KEY = "fabrikam-deal-key"
DEMO_APP_ID = "4821"

#: The stage the research names, spelled the way a rep would type it, plus the two
#: a demo needs to show a stage actually moving.
DEMO_NEGOTIATION = "Negotiation in Progress"
DEMO_CONTRACT_SENT = "Contract Sent"
DEMO_CLOSED_WON = "Closed Won"

#: The static value the researched body editor adds by hand - "To add a static
#: field, enter the Key and Value" - used here as the delivery id that makes the
#: demo's retried delivery unambiguous.
DEMO_STATIC_DELIVERY_KEY = "delivery_id"


def _signature_headers(secret: str, method: str, uri: str, raw_body: str) -> dict[str, str]:
    """Sign a request the way the CRM-side action would.

    The timestamp is the wall clock rather than the seeded one: a delivery whose
    signature covers a timestamp from seeding time would be outside the replay
    window by the time anybody looked at it, and the demo would show a signature
    failure that nobody caused.
    """
    timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    return {
        "X-HubSpot-Signature-v3": sign(secret, method, uri, raw_body, timestamp),
        "X-HubSpot-Request-Timestamp": timestamp,
    }


def _send(
    engine: WebhookEngine,
    room_id: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    *,
    source: str,
    method: str = "POST",
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one request through the real engine, the way the CRM would."""
    uri = uri_for(room_id)
    if query:
        uri = f"{uri}?" + "&".join(f"{key}={value}" for key, value in sorted(query.items()))
    raw = "" if body is None else json.dumps(body, sort_keys=True)
    return engine.receive(
        room_id,
        method=method,
        uri=uri,
        headers=headers,
        query=query or {},
        body=raw,
        actor="crm",
        source=source,
    )


def _post(
    engine: WebhookEngine, room_id: str, secret: str, body: dict[str, Any], *, source: str
) -> dict[str, Any]:
    """One signed POST, as an endpoint authenticating by request signature."""
    uri = uri_for(room_id)
    return _send(
        engine,
        room_id,
        _signature_headers(secret, "POST", uri, json.dumps(body, sort_keys=True)),
        body,
        source=source,
    )


def _room(store: RecordStore, name: str, account: str) -> str:
    return store.create("room", {"name": name, "account": account}, actor="dana")["id"]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Four rooms, and the states that are not all successes.

    The rows are produced by running the real :class:`WebhookEngine`, so the demo
    cannot show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only green teaches a
    reviewer nothing:

    * **one room** - a published, signature-authenticated endpoint using **Include
      all [object] properties**, with two automations, the research's own *"deal
      stage becomes 'Contract Sent'"* among them. Its deliveries include a real
      stage change, a **retry of that same delivery** (counted, never applied
      twice), a **repeat of the same stage under a different delivery id**
      (accepted, changed nothing, notified nobody), and a payload from an
      **automation this room never registered** (applied and reported, because the
      endpoint is not an allow-list);
    * **the same room** - a second deal, and then a payload with **no id at all**,
      which is refused as ``deal_unresolved`` because a room with two deals has no
      ambiguity-free reading of it, and an **acknowledged** notice beside the unread
      ones;
    * **a second room** - a **GET** endpoint authenticating by **bearer**, with a
      **Customized request body** naming a key one delivery does not send, so
      ``body_key_missing`` is a row rather than a claim;
    * **a third room** - an **API key in a query parameter**, which the research
      lists as an option in its own words;
    * **a fourth room** - an endpoint that was **saved and never published**, and a
      correctly signed delivery to it, so ``endpoint_not_published`` is a row
      because *"Workflows must be **published** to go live"*;
    * one **retired** automation, so a delivery naming it is reported as retired
      rather than unknown, and the log and the automation list agree.

    The seeder hands over ``[(room_id, account), ...]``. It is topped up with rooms
    this seed creates, because the states above are per-room - a publish gate needs
    an unpublished room, a GET needs its own method - and a demo that quietly
    dropped them because the core dataset is small would be a demo that lies about
    what this workflow does. The return string says how many rooms came from the
    seeder.
    """
    store = RecordStore(db)
    engine = WebhookEngine(store)
    given: list[str] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    create_source = f"POST {PREFIX}/rooms/{{room_id}}/endpoint"
    amend_source = f"PATCH {PREFIX}/rooms/{{room_id}}/endpoint"
    automation_source = f"POST {PREFIX}/rooms/{{room_id}}/automations"
    publish_source = f"POST {PREFIX}/rooms/{{room_id}}/endpoint/publish"
    retire_source = f"DELETE {PREFIX}/rooms/{{room_id}}/automations/{{automation_id}}"
    post_source = f"POST {PREFIX}/rooms/{{room_id}}/crm/v1/webhook"
    get_source = f"GET {PREFIX}/rooms/{{room_id}}/crm/v1/webhook"
    acknowledge_source = f"POST {PREFIX}/rooms/{{room_id}}/notices/acknowledge"

    def at(minutes_ago: int) -> str:
        return (base - timedelta(minutes=minutes_ago)).isoformat()

    # One room per state, topped up from the seeder's rooms and then from rooms
    # this seed makes, so every state below is present whatever the core dataset
    # looks like.
    names = [
        ("Northwind Traders - deal stage -> room", "Northwind Traders"),
        ("Contoso Health - contact property -> room", "Contoso Health"),
        ("Fabrikam Logistics - company property -> room", "Fabrikam Logistics"),
        ("Draft endpoint room - saved, never published", "Draft account"),
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:3]
    created = 0
    for index in range(4):
        if index < len(room_ids):
            continue
        name, account = names[index]
        room_ids.append(_room(store, name, account))
        created += 1
    primary, secondary, tertiary, draft = room_ids

    # -- room one: the research's own example, in full ---------------------- #
    engine.register(
        primary,
        {
            "name": "Deal stage -> room",
            "url": "https://rooms.northwind.example/api/wf-044/rooms/primary/crm/v1/webhook",
            "method": "POST",
            "object": "deals",
            "auth": {
                "mode": "signature",
                "app_id": DEMO_APP_ID,
                "secret": DEMO_SIGNATURE_SECRET,
            },
            "body": {"mode": "include_all"},
            "id_key": "deal_id",
            "stage_key": "stage",
            "notes": (
                "Data ops -> Send a webhook. Authentication type: Include request "
                "signature in header; HubSpot App ID 4821. Body: Include all [object] "
                "properties."
            ),
        },
        permissions="edit_workflows",
        actor="dana",
        source=create_source,
    )
    contract_sent = engine.add_automation(
        primary,
        {
            "name": "Contract sent",
            "description": 'The researched start condition: deal stage becomes "Contract Sent".',
            "trigger": {"property": "dealstage", "equals": DEMO_CONTRACT_SENT},
        },
        permissions="edit_workflows",
        actor="dana",
        source=automation_source,
    )
    engine.add_automation(
        primary,
        {
            "name": "Amount changed",
            "description": 'The researched second condition: "or a contact property changes".',
            "trigger": {"property": "amount", "changed": True},
        },
        permissions="edit_workflows",
        actor="dana",
        source=automation_source,
    )
    engine.publish(primary, permissions="publish_workflows", actor="dana", source=publish_source)

    # The stage change the rep built the automation for.
    stage_change = {
        "v": 1,
        "object": "deals",
        "automation": "Contract sent",
        "occurred_at": at(42),
        "properties": {
            "deal_id": "1001",
            "stage": DEMO_CONTRACT_SENT,
            "amount": "42000",
            "owner": "dana@revenue.example",
        },
    }
    landed = _post(engine, primary, DEMO_SIGNATURE_SECRET, stage_change, source=post_source)
    # The CRM retries after a timeout - "When a webhook is slow or times out, the
    # workflow action may take longer than expected to execute" - so the same
    # delivery arrives twice. Counted on the row that was kept, never stored twice.
    retried = _post(engine, primary, DEMO_SIGNATURE_SECRET, stage_change, source=post_source)
    # A delivery from an automation this room never registered. Applied and
    # reported: the endpoint takes any number of automations, so an allow-list
    # would mean every new CRM-side workflow needed this room's say-so.
    unknown = _post(
        engine,
        primary,
        DEMO_SIGNATURE_SECRET,
        {
            "v": 1,
            "object": "deals",
            "automation": "Renewal date moved",
            "properties": {
                "deal_id": "1001",
                "stage": DEMO_CONTRACT_SENT,
                "renewal_date": "2027-02-01",
            },
        },
        source=post_source,
    )
    # The same stage again, under a different delivery id: a real delivery that
    # changed nothing, so it is recorded and notifies nobody.
    unchanged = _post(
        engine,
        primary,
        DEMO_SIGNATURE_SECRET,
        {
            "v": 1,
            "object": "deals",
            "delivery_id": "seed-same-stage",
            "properties": {
                "deal_id": "1001",
                "stage": DEMO_CONTRACT_SENT,
                "amount": "42000",
            },
        },
        source=post_source,
    )
    # A second deal in the same room, so the id-less payload below has a choice it
    # cannot make.
    _post(
        engine,
        primary,
        DEMO_SIGNATURE_SECRET,
        {
            "v": 1,
            "object": "deals",
            "properties": {
                "deal_id": "1002",
                "stage": DEMO_NEGOTIATION,
                "amount": "17500",
            },
        },
        source=post_source,
    )
    unresolved = "deal_unresolved"
    try:
        _post(
            engine,
            primary,
            DEMO_SIGNATURE_SECRET,
            {"v": 1, "object": "deals", "properties": {"stage": DEMO_CLOSED_WON}},
            source=post_source,
        )
        unresolved = "not refused"
    except WebhookError as exc:
        unresolved = exc.code
    # An automation retired after the fact, so a delivery naming it is reported as
    # retired rather than unknown - the log and the automation list agree.
    engine.retire_automation(
        primary,
        contract_sent["id"],
        permissions="edit_workflows",
        actor="dana",
        source=retire_source,
    )
    retired_named = _post(
        engine,
        primary,
        DEMO_SIGNATURE_SECRET,
        {
            "v": 1,
            "object": "deals",
            "automation": "Contract sent",
            "properties": {
                "deal_id": "1002",
                "stage": DEMO_CLOSED_WON,
                "amount": "17500",
            },
        },
        source=post_source,
    )
    # One notice acknowledged, so the list shows read beside unread.
    notices = engine.notices(primary)
    if notices:
        engine.acknowledge(
            primary,
            {"notice_ids": [notices[-1]["id"]]},
            actor="dana",
            source=acknowledge_source,
        )

    # -- room two: GET, bearer, and a customized body ----------------------- #
    engine.register(
        secondary,
        {
            "name": "Contact property -> room",
            "url": "https://rooms.contoso.example/api/wf-044/rooms/secondary/crm/v1/webhook",
            "method": "GET",
            "object": "contacts",
            "auth": {"mode": "bearer", "secret": DEMO_BEARER_SECRET},
            "body": {
                "mode": "customize",
                "keys": [
                    {"key": "contact_id", "property": "contact_id"},
                    {"key": "stage", "property": "lifecyclestage"},
                    {"key": "automation", "value": "Contact property changed"},
                    {"key": DEMO_STATIC_DELIVERY_KEY, "property": "hs_correlation_id"},
                ],
            },
            "id_key": "contact_id",
            "stage_key": "stage",
            "notes": (
                "Method GET, Authorization: Bearer [YOUR_TOKEN], and a customized body "
                "carrying a static value - the researched body editor's two halves."
            ),
        },
        permissions="edit_workflows",
        actor="dana",
        source=create_source,
    )
    engine.add_automation(
        secondary,
        {
            "name": "Contact property changed",
            "description": 'The researched second start condition: "a contact property changes".',
            "trigger": {"property": "hs_lead_status", "changed": True},
        },
        permissions="edit_workflows",
        actor="dana",
        source=automation_source,
    )
    engine.publish(secondary, permissions="publish_workflows", actor="dana", source=publish_source)
    # A bearer POST that omits one key the customized body names, so
    # body_key_missing is a row rather than a claim about the reader's body.
    missing_key = _send(
        engine,
        secondary,
        {"Authorization": f"Bearer {DEMO_BEARER_SECRET}"},
        {
            "v": 1,
            "object": "contacts",
            "automation": "Contact property changed",
            "properties": {"contact_id": "c-77", "stage": "customer"},
        },
        source=post_source,
    )
    # The same endpoint as a GET: the properties arrive in the query string and
    # the signature covers it, because "You can send both POST and GET requests".
    get_report = _send(
        engine,
        secondary,
        {"Authorization": f"Bearer {DEMO_BEARER_SECRET}"},
        None,
        source=get_source,
        method="GET",
        query={
            "contact_id": "c-77",
            "stage": "customer",
            "automation": "Contact property changed",
        },
    )

    # -- room three: an API key in a query parameter ------------------------ #
    engine.register(
        tertiary,
        {
            "name": "Company property -> room",
            "url": "https://rooms.fabrikam.example/api/wf-044/rooms/tertiary/crm/v1/webhook",
            "method": "POST",
            "object": "companies",
            "auth": {
                "mode": "api_key",
                "name": "deal_key",
                "location": "query",
                "secret": DEMO_API_KEY,
            },
            "body": {"mode": "include_all"},
            "notes": (
                "API key in a query parameter, which the research lists beside the "
                "request-header location."
            ),
        },
        permissions="edit_workflows",
        actor="dana",
        source=create_source,
    )
    engine.publish(tertiary, permissions="publish_workflows", actor="dana", source=publish_source)
    api_key_report = _send(
        engine,
        tertiary,
        {},
        {
            "v": 1,
            "object": "companies",
            "properties": {"company_id": "co-9", "stage": "expansion", "seats": "480"},
        },
        source=post_source,
        query={"deal_key": DEMO_API_KEY},
    )

    # -- room four: saved, never published ----------------------------------- #
    engine.register(
        draft,
        {
            "name": "Saved but never published",
            "url": "https://rooms.northwind.example/api/wf-044/rooms/draft/crm/v1/webhook",
            "method": "POST",
            "object": "deals",
            "auth": {
                "mode": "signature",
                "app_id": DEMO_APP_ID,
                "secret": DEMO_SIGNATURE_SECRET,
            },
            "body": {"mode": "include_all"},
        },
        permissions="edit_workflows",
        actor="dana",
        source=create_source,
    )
    # One amendment that did not change the traffic, so the audit log shows the
    # rep's edit rather than only their saves.
    engine.amend(
        draft,
        {"notes": "Publish once the deal stage vocabulary is agreed with the sales team."},
        permissions="edit_workflows",
        actor="dana",
        source=amend_source,
    )
    draft_refusal = "endpoint_not_published"
    try:
        _post(
            engine,
            draft,
            DEMO_SIGNATURE_SECRET,
            {
                "v": 1,
                "object": "deals",
                "properties": {"deal_id": "1003", "stage": DEMO_CONTRACT_SENT},
            },
            source=post_source,
        )
        draft_refusal = "not refused"
    except WebhookError as exc:
        draft_refusal = exc.code

    summary = engine.summary(primary)
    secondary_summary = engine.summary(secondary)
    return (
        f"{summary['automations']} live automation(s) (1 retired) on a published, "
        f"signature-authenticated endpoint using Include all properties, "
        f"{summary['deliveries']} deliver(ies) on it "
        f"({summary['by_outcome'].get('accepted', 0)} accepted, "
        f"{summary['by_outcome'].get('refused', 0)} refused as {unresolved}), "
        f"{landed['effect']} on the stage change the rep built it for, "
        f"{retried['outcome']} on the retry (count {retried['duplicate_attempts']}, "
        f"never applied twice), "
        f"1 {unchanged['effect']} delivery that notified nobody, "
        f"1 from an automation this room never registered "
        f"({'reported' if 'automation_unknown' in (unknown['notes'] or []) else 'not reported'}), "
        f"1 naming a retired automation "
        f"({'retired' if retired_named['automation_retired'] else 'not retired'}), "
        f"{summary['deals']} deal(s) in the panel, {summary['unread']} unread notice(s), "
        f"a {secondary_summary['endpoint']['method']}/bearer endpoint on a second room "
        f"({get_report['outcome']}, {len(missing_key.get('missing_keys') or [])} body key missing), "
        f"an api-key endpoint on a third ({api_key_report['outcome']}), "
        f"a correctly signed delivery to an unpublished endpoint refused as {draft_refusal}, "
        f"{summary['subscriptions_used']}/1000 webhook subscriptions used"
        + (f", {created} room(s) created for the per-room states" if created else "")
        + f", {len(given)} room(s) from the seeder"
    )
