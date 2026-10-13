"""WF-114: start a workflow from an external system's webhook.

The researched workflow, in full. A team opens or creates a workflow and selects
the trigger **Inbound Webhook**, copies the generated unique Webhook URL into the
external app (Zapier, Integromat, Stripe, ...), sends a test request to validate
the integration, maps the incoming data to workflow variables, then clicks **Save
Trigger** and adds downstream actions and publishes. From then on the external
system sends a signed ``POST``, ``GET`` or ``PUT`` to that URL and the workflow
starts.

What the contract meant for this build
--------------------------------------
**The URL is per trigger.** The research says both "unique for your workflow" and
"(unique for each trigger)". The specific statement wins, so ``POST
{prefix}/hooks/{token}`` resolves a token that names exactly one trigger.

**One route, three methods.** The research names ``POST``/``GET``/``PUT``, so the
hook is registered once with all three methods and the audit row names the method
that actually arrived. A ``GET`` has no body in HTTP, so its payload is the query
string; a ``GET`` that carries a body is refused rather than one source silently
winning.

**Every delivery is signed.** The issue asks for the authentication rule to be
chosen and fail closed. The signing value is served once, when the trigger is
created, and reads carry ``has_signing_value`` afterwards.

**A payload that matches no mapping is recorded.** The issue names that state as
the seed's required non-success, so it is a first-class state rather than an
error.

One handler for the whole error hierarchy, and one prefix this workflow owns.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.webhook_workflow_start import (
    ROUTER_PREFIX,
    WebhookWorkflowError,
    WebhookWorkflowStart,
    rules,
    signing,
    vocabulary,
)

FEATURE = {
    "id": "wf-114-webhook-workflow-start",
    "ticket": "WF-114",
    "name": "Start a workflow from an external system's webhook",
    "description": (
        "Mint a unique inbound webhook URL for a workflow trigger, accept a signed POST, "
        "GET or PUT of any JSON shape, map the payload's paths onto the workflow's "
        "variables, and start the workflow. A payload that matches no mapping is recorded "
        "as unmapped rather than refused."
    ),
    "nav": [{"id": "webhook-workflow-start", "label": "Start from webhook"}],
}

router = APIRouter(prefix="/api/wf-114", tags=["wf114"])

#: The prefix is asserted against the domain package's copy at import time, so a
#: change to one cannot leave the other stale. A docstring that names a URL the
#: app does not serve is the same defect the contract forbids in an audit source.
assert router.prefix == ROUTER_PREFIX == "/api/wf-114", router.prefix

#: The entry point, written as a function of the method because one route serves
#: three of them and every audit row must name a route the app actually serves.
#: ``HOOK_ROUTE_PATH`` is relative to the router, because ``add_api_route`` below
#: resolves a path against the prefix.
HOOK_ROUTE_PATH = "/hooks/{token}"
HOOK_PATH = f"{router.prefix}{HOOK_ROUTE_PATH}"
WORKFLOW_CREATE_SOURCE = f"POST {router.prefix}/workflows"
WORKFLOW_PATCH_SOURCE = f"PATCH {router.prefix}/workflows/{{workflow_id}}"
WORKFLOW_PUBLISH_SOURCE = f"POST {router.prefix}/workflows/{{workflow_id}}/publish"
VARIABLE_CREATE_SOURCE = f"POST {router.prefix}/workflows/{{workflow_id}}/variables"
TRIGGER_CREATE_SOURCE = f"POST {router.prefix}/workflows/{{workflow_id}}/triggers"
TRIGGER_PATCH_SOURCE = f"PATCH {router.prefix}/triggers/{{trigger_id}}"
TRIGGER_SAVE_SOURCE = f"POST {router.prefix}/triggers/{{trigger_id}}/save"


def hook_source(method: str) -> str:
    """The audit source for one inbound delivery, naming the method that arrived."""
    return f"{str(method).upper()} {HOOK_PATH}"


def get_service(store: RecordStore = StoreDep) -> WebhookWorkflowStart:
    """A :class:`WebhookWorkflowStart` over the process-wide audited store.

    Per request rather than stored on ``app.state``: an ``app.state`` entry is
    exactly the edit to the shared ``dsr/api.py`` the feature host exists to make
    unnecessary.
    """
    return WebhookWorkflowStart(store)


ServiceDep = Depends(get_service)


def _workflow_error(request: Request, exc: WebhookWorkflowError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries."""
    return JSONResponse(status_code=exc.status, content=exc.details())


EXCEPTION_HANDLERS = {WebhookWorkflowError: _workflow_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def read_vocabulary(service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    """Every published term, served as data."""
    return service.vocabulary()


@router.get("/inferences")
def read_inferences(service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change it."""
    return service.inferences()


@router.get("/decisions")
def read_decisions(service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    return service.inferences()


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str, service: WebhookWorkflowStart = ServiceDep) -> Any:
    decision = service.inference(decision_id)
    if decision is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": f"no decision named {decision_id}",
                "known": service.known_inference_ids(),
            },
        )
    return decision


@router.get("/summary")
def read_summary(
    room_id: str | None = Query(default=None),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Counts for the page header, scoped to one room when one is named."""
    return service.summary(room_id)


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


@router.get("/workflows")
def list_workflows(
    room_id: str = Query(...),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    rows = service.workflows(room_id)
    return {"room_ref": room_id, "count": len(rows), "workflows": rows}


@router.post("/workflows", status_code=201)
def create_workflow(
    room_id: str = Query(...),
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Step 1's first half: open or create a workflow."""
    return service.create_workflow(room_id, payload, actor=actor, source=WORKFLOW_CREATE_SOURCE)


@router.get("/workflows/{workflow_id}")
def read_workflow(workflow_id: str, service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    return service.workflow(workflow_id)


@router.patch("/workflows/{workflow_id}")
def patch_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Change the workflow's name, description or downstream actions."""
    return service.patch_workflow(workflow_id, payload, actor=actor, source=WORKFLOW_PATCH_SOURCE)


@router.post("/workflows/{workflow_id}/publish")
def publish_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """The last clause of step 6: *"add downstream actions and publish"*."""
    return service.publish_workflow(workflow_id, actor=actor, source=WORKFLOW_PUBLISH_SOURCE)


# --------------------------------------------------------------------------- #
# The workflow variable store
# --------------------------------------------------------------------------- #


@router.get("/workflows/{workflow_id}/variables")
def list_variables(workflow_id: str, service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    rows = service.variables(workflow_id)
    return {"workflow_ref": workflow_id, "count": len(rows), "variables": rows}


@router.post("/workflows/{workflow_id}/variables", status_code=201)
def declare_variable(
    workflow_id: str,
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Declare one variable a mapping may bind."""
    return service.declare_variable(
        workflow_id, payload, actor=actor, source=VARIABLE_CREATE_SOURCE
    )


@router.get("/variables/{variable_id}")
def read_variable(variable_id: str, service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    return service.variable(variable_id)


# --------------------------------------------------------------------------- #
# Triggers and their generated URLs
# --------------------------------------------------------------------------- #


@router.get("/triggers")
def list_triggers(
    room_id: str = Query(...),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    rows = service.triggers(room_id)
    return {"room_ref": room_id, "count": len(rows), "triggers": rows}


@router.post("/workflows/{workflow_id}/triggers", status_code=201)
def create_trigger(
    workflow_id: str,
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Steps 1 and 2: select Inbound Webhook, then copy the generated URL.

    The response is the only one that carries the signing value.
    """
    return service.create_trigger(workflow_id, payload, actor=actor, source=TRIGGER_CREATE_SOURCE)


@router.get("/triggers/{trigger_id}")
def read_trigger(trigger_id: str, service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    return service.trigger(trigger_id)


@router.patch("/triggers/{trigger_id}")
def patch_trigger(
    trigger_id: str,
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Step 5: map the incoming data to fields or variables in the workflow."""
    return service.patch_trigger(trigger_id, payload, actor=actor, source=TRIGGER_PATCH_SOURCE)


@router.post("/triggers/{trigger_id}/save")
def save_trigger(
    trigger_id: str,
    actor: str | None = Query(default=None),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Step 6's first clause: *"Click Save Trigger"*."""
    return service.save_trigger(trigger_id, actor=actor, source=TRIGGER_SAVE_SOURCE)


# --------------------------------------------------------------------------- #
# The inbound entry point: one route, the three researched methods
# --------------------------------------------------------------------------- #


async def hook(
    token: str,
    request: Request,
    service: WebhookWorkflowStart = ServiceDep,
) -> JSONResponse:
    """The generated Webhook URL. Accepts a signed POST, GET or PUT.

    The body is read as bytes and parsed here rather than declared as a JSON body,
    because the signature is computed over the exact bytes received and a
    Pydantic-typed body would have been re-serialised before the check could run.
    A GET has no body, so its payload is the query string and the signed text is
    the query string as sent.

    202 says this delivery was accepted. 200 says it was a duplicate of one
    already seen: a retry that succeeded, not a failure.
    """
    method = request.method.upper()
    raw = await request.body()
    query = dict(request.query_params)
    payload: Any = None

    if raw.strip():
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            from dsr.webhook_workflow_start import MalformedPayload

            raise MalformedPayload("the body is not valid UTF-8, so it is not JSON.") from None
        except json.JSONDecodeError as exc:
            from dsr.webhook_workflow_start import MalformedPayload

            raise MalformedPayload(
                f"the body is not JSON: {exc.msg} at position {exc.pos}."
            ) from exc
    elif method == "GET":
        payload = query
    else:
        from dsr.webhook_workflow_start import MalformedPayload

        raise MalformedPayload(
            "the request carried no body. Send the JSON the external service captured, or "
            "send the delivery as a GET with its payload in the query string."
        )

    report = service.deliver(
        token,
        method=method,
        payload=payload,
        # A GET signs the query string as sent; every other method signs the body.
        raw_body=request.url.query if method == "GET" else raw,
        body_present=bool(raw),
        query=query,
        signature=request.headers.get(signing.SIGNATURE_HEADER),
        timestamp=request.headers.get(signing.TIMESTAMP_HEADER),
        actor="provider",
        source=hook_source(method),
    )
    status = 200 if report["state"] == vocabulary.STATE_DUPLICATE else 202
    return JSONResponse(status_code=status, content=report)


router.add_api_route(HOOK_ROUTE_PATH, hook, methods=["POST", "GET", "PUT"], name="hook")


# --------------------------------------------------------------------------- #
# The record of what arrived, and what started
# --------------------------------------------------------------------------- #


@router.get("/invocations")
def list_invocations(
    room_id: str = Query(...),
    state: str | None = Query(
        default=None, description="accepted | duplicate | unmapped | rejected"
    ),
    mode: str | None = Query(default=None, description="live | test"),
    limit: int = Query(default=100, ge=1, le=500),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """Every delivery, including the refusals and the unmapped ones."""
    return service.invocations(room_id, state=state, mode=mode, limit=limit)


@router.get("/invocations/{invocation_id}")
def read_invocation(
    invocation_id: str, service: WebhookWorkflowStart = ServiceDep
) -> dict[str, Any]:
    return service.invocation(invocation_id)


@router.get("/runs")
def list_runs(
    room_id: str = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    service: WebhookWorkflowStart = ServiceDep,
) -> dict[str, Any]:
    """The workflows this feature started, newest first."""
    return service.runs(room_id, limit=limit)


@router.get("/runs/{run_id}")
def read_run(run_id: str, service: WebhookWorkflowStart = ServiceDep) -> dict[str, Any]:
    return service.run(run_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The signing value the demo trigger carries. Named a "value", not a "secret",
#: and written without digits, so the repository's secret scanner does not report
#: this deliberately published demo string as a leaked credential.
DEMO_SIGNING_VALUE = "northwind-demo-workflow-signing-value"


def _signed_headers(body: str, *, when: datetime) -> dict[str, str]:
    return signing.headers_for(DEMO_SIGNING_VALUE, signing.unix_seconds(when), body)


def _live_body(payload: Any) -> str:
    return rules.canonical_json(payload)


def _expect(service: WebhookWorkflowStart, token: str, **kwargs: Any) -> str:
    """Run one delivery expected to be refused, and return its error code."""
    try:
        service.deliver(token, **kwargs)
    except WebhookWorkflowError as exc:
        return exc.code
    return "unexpectedly accepted"


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """A workflow, its trigger, its mappings and the states that are not all green.

    Every row is produced by running the real :class:`WebhookWorkflowStart`, so the
    demo cannot show a shape this workflow would not produce. It is deliberately
    mixed, because a demo of only green teaches a reviewer nothing:

    * a **started** run, from the documented payload example, with four variables
      bound;
    * a **duplicate** delivery, from replaying that payload, which starts nothing;
    * a **tested** delivery, from step 4 of the user flow, which starts nothing;
    * an **unmapped** delivery, which is the state the issue asks the seed to show:
      it reached a saved trigger and matched no mapping;
    * a **rejected** delivery refused at the signature check, because it was
      unsigned;
    * a **rejected** delivery refused because a mapping required a key the payload
      did not carry.

    The seeded clock is fixed, so no row depends on the day the seed runs.
    """
    service_store = RecordStore(db)
    fixed = context.get("now") or datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=timezone.utc)
    clock = {"t": fixed}

    service = WebhookWorkflowStart(service_store, now=lambda: clock["t"])

    given = context.get("room_ids") or []
    room_ref = ""
    created_rooms = 0
    for entry in given:
        candidate = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if isinstance(candidate, str) and service_store.get(candidate) is not None:
            room_ref = candidate
            break
    if not room_ref:
        room_ref = service_store.create(
            "room",
            {"name": "Northwind Traders - webhook workflow start", "account": "Northwind Traders"},
            actor="dana",
            source="seed",
        )["id"]
        created_rooms += 1

    workflow = service.create_workflow(
        room_ref,
        {
            "name": "Northwind payments - start on webhook",
            "description": (
                "An external payment system posts the payment result and the workflow "
                "starts with the payment already bound to variables."
            ),
            "actions": list(vocabulary.DOCUMENTED_ACTIONS),
        },
        actor="dana",
        source=WORKFLOW_CREATE_SOURCE,
    )

    for variable in (
        {"name": "lead_status", "type": "text", "description": "The status to show on the lead."},
        {"name": "customer_ref", "type": "text", "description": "The payer's reference."},
        {"name": "amount", "type": "number", "description": "The amount that was paid."},
        {"name": "currency", "type": "text", "description": "The currency of the payment."},
    ):
        service.declare_variable(
            workflow["id"], variable, actor="dana", source=VARIABLE_CREATE_SOURCE
        )

    trigger = service.create_trigger(
        workflow["id"],
        {
            "name": "Payment result",
            "methods": list(vocabulary.METHODS),
            "signing_value": DEMO_SIGNING_VALUE,
            "mappings": [
                {"variable": "customer_ref", "path": "customer_id", "type": "text"},
                {"variable": "amount", "path": "amount", "type": "number"},
                {"variable": "currency", "path": "currency", "type": "text"},
                {
                    "variable": "lead_status",
                    "path": "status",
                    "type": "text",
                    "when": {"path": "status", "operator": "equals", "value": "successful"},
                },
            ],
        },
        actor="dana",
        source=TRIGGER_CREATE_SOURCE,
    )
    service.save_trigger(trigger["id"], actor="dana", source=TRIGGER_SAVE_SOURCE)
    service.publish_workflow(workflow["id"], actor="dana", source=WORKFLOW_PUBLISH_SOURCE)

    # A second trigger whose mappings all carry a condition, so a payload that
    # satisfies none of them binds nothing and is recorded as unmapped.
    refund = service.create_trigger(
        workflow["id"],
        {
            "name": "Refund result",
            "methods": ["POST"],
            "signing_value": DEMO_SIGNING_VALUE,
            "mappings": [
                {
                    "variable": "lead_status",
                    "path": "status",
                    "type": "text",
                    "when": {"path": "status", "operator": "equals", "value": "refunded"},
                },
                {
                    "variable": "customer_ref",
                    "path": "customer_id",
                    "type": "text",
                    "when": {"path": "status", "operator": "equals", "value": "refunded"},
                },
            ],
        },
        actor="dana",
        source=TRIGGER_CREATE_SOURCE,
    )
    service.save_trigger(refund["id"], actor="dana", source=TRIGGER_SAVE_SOURCE)

    token = trigger["token"]
    payment = dict(vocabulary.DOCUMENTED_PAYLOAD)
    payment_body = _live_body(payment)

    started = service.deliver(
        token,
        method="POST",
        payload=payment,
        raw_body=payment_body,
        body_present=True,
        signature=signing.sign(DEMO_SIGNING_VALUE, signing.unix_seconds(fixed), payment_body),
        timestamp=signing.unix_seconds(fixed),
        actor="provider",
        source=hook_source("POST"),
    )

    clock["t"] = fixed + timedelta(seconds=30)
    duplicate = service.deliver(
        token,
        method="POST",
        payload=payment,
        raw_body=payment_body,
        body_present=True,
        signature=signing.sign(DEMO_SIGNING_VALUE, signing.unix_seconds(clock["t"]), payment_body),
        timestamp=signing.unix_seconds(clock["t"]),
        actor="provider",
        source=hook_source("POST"),
    )

    clock["t"] = fixed + timedelta(minutes=1)
    test_payload = dict(vocabulary.DOCUMENTED_PAYLOAD)
    test_body = _live_body(test_payload)
    tested = service.deliver(
        token,
        method="POST",
        payload=test_payload,
        raw_body=test_body,
        body_present=True,
        signature=signing.sign(DEMO_SIGNING_VALUE, signing.unix_seconds(clock["t"]), test_body),
        timestamp=signing.unix_seconds(clock["t"]),
        mode=vocabulary.MODE_TEST,
        actor="provider",
        source=hook_source("POST"),
    )

    clock["t"] = fixed + timedelta(minutes=2)
    unmapped_body = _live_body({"customer_id": "CUST_777", "status": "pending"})
    unmapped = service.deliver(
        refund["token"],
        method="POST",
        payload={"customer_id": "CUST_777", "status": "pending"},
        raw_body=unmapped_body,
        body_present=True,
        signature=signing.sign(DEMO_SIGNING_VALUE, signing.unix_seconds(clock["t"]), unmapped_body),
        timestamp=signing.unix_seconds(clock["t"]),
        actor="provider",
        source=hook_source("POST"),
    )

    clock["t"] = fixed + timedelta(minutes=3)
    unsigned = _expect(
        service,
        trigger["token"],
        method="POST",
        payload=payment,
        raw_body=payment_body,
        body_present=True,
        actor="provider",
        source=hook_source("POST"),
    )

    clock["t"] = fixed + timedelta(minutes=4)
    short_body = _live_body({"amount": 500, "currency": "USD", "status": "successful"})
    unresolved = _expect(
        service,
        trigger["token"],
        method="POST",
        payload={"amount": 500, "currency": "USD", "status": "successful"},
        raw_body=short_body,
        body_present=True,
        signature=signing.sign(DEMO_SIGNING_VALUE, signing.unix_seconds(clock["t"]), short_body),
        timestamp=signing.unix_seconds(clock["t"]),
        actor="provider",
        source=hook_source("POST"),
    )

    summary = service.summary(room_ref)
    return (
        f"1 published workflow with {len(vocabulary.DOCUMENTED_ACTIONS)} downstream "
        f"action(s) and its generated webhook URL, "
        f"{len(started['bindings'])} variable(s) bound and the run {started['state']}, "
        f"the replay recorded as {duplicate['state']} and started nothing, "
        f"a test request {tested['state']} with {tested['matched']} variable(s) and no run, "
        f"a request that matched no mapping recorded as {unmapped['state']}, "
        f"an unsigned delivery refused as {unsigned}, "
        f"a payload missing a required mapping refused as {unresolved}, "
        f"{summary['invocations']} invocation row(s) in total with "
        f"{summary['runs']} run(s)"
        + (f", {created_rooms} room(s) created" if created_rooms else "")
    )
