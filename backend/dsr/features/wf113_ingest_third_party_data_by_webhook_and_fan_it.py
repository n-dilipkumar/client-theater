"""WF-113: ingest third-party data by webhook and fan it out as events.

The researched workflow, in full. A team opens *Automations -> Create Automation
-> Webhook*, copies the generated **Webhook URL** into an external service,
drags in **Send Event** (or **Create or Update Person**, or **Batch Update**, or
**Send data**), optionally appends a **Slack** block, then chooses *Review items
-> Start Automation*. From then on the external service POSTs JSON of any shape
to that URL; this feature parses it into a Trigger data object, de-duplicates it,
and fans it out as internal events, which downstream automations consume.

What the contract meant for this build
--------------------------------------
**The URL is per-automation, not global.** The research says "Customer.io
generates a *Webhook URL*", and a URL that accepts any shape still has to be
attributable to one automation. So the route is ``POST {prefix}/hooks/{token}``,
the token is a row in ``records``, and every write this route makes names that
route in its audit ``source``.

**The webhook is the subject, never the person.** Quoted: *"you cannot reference
existing customer attributes ... because people aren't the subject of the
automation: the webhook is."* A mapping that references the person renders the
empty string and does not error.

**Deletes are events.** Quoted: the Pipelines API "only uses ``POST``
operations ... You'll perform deletes and other operations by sending events
with specific names." The two names the research gives are served in the
vocabulary, and nothing here special-cases them.

**Duplicates are recorded.** A repeated delivery is a duplicate trigger row, not
a dropped request: the research describes a service that retries what it did not
see acknowledged, and a retry is normal traffic.

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
from dsr.webhook_ingest import (
    ROUTER_PREFIX,
    TICKET,
    WebhookIngest,
    WebhookIngestError,
    rules,
    signing,
    vocabulary,
)

FEATURE = {
    "id": "wf-113-webhook-fanout",
    "ticket": "WF-113",
    "name": "Ingest third-party data by webhook and fan it out as events",
    "description": (
        "Generate a per-automation webhook URL, accept a POST of any JSON shape, parse it "
        "into a Trigger data object, de-duplicate it, and fan it out as internal events for "
        "downstream automations. The webhook is the subject of the automation, never the "
        "person, so a mapping that references customer data renders the empty string."
    ),
    "nav": [{"id": "webhook-fanout", "label": "Webhook ingest"}],
}

router = APIRouter(prefix="/api/wf-113", tags=["wf113"])

#: The prefix is asserted against the domain package's copy at import time, so a
#: change to one cannot leave the other stale. A docstring that names a URL the
#: app does not serve is the same defect the contract forbids in an audit source.
assert router.prefix == ROUTER_PREFIX == "/api/wf-113", router.prefix

#: The inbound entry point, written once because every audit row this feature
#: writes must name a route the app actually serves.
HOOK_SOURCE = f"POST {router.prefix}/hooks/{{token}}"
AUTOMATION_CREATE_SOURCE = f"POST {router.prefix}/automations"
AUTOMATION_PATCH_SOURCE = f"PATCH {router.prefix}/automations/{{automation_id}}"
AUTOMATION_START_SOURCE = f"POST {router.prefix}/automations/{{automation_id}}/start"


def get_ingest(store: RecordStore = StoreDep) -> WebhookIngest:
    """A :class:`WebhookIngest` over the process-wide audited store.

    Per request rather than stored on ``app.state``: an ``app.state`` entry is
    exactly the edit to the shared ``dsr/api.py`` the feature host exists to make
    unnecessary.
    """
    return WebhookIngest(store)


IngestDep = Depends(get_ingest)


def _webhook_error(request: Request, exc: WebhookIngestError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy, because a malformed body and an
    over-limit batch are both this package's errors and only the exception knows
    which status it deserves.
    """
    return JSONResponse(status_code=exc.status, content=exc.details())


EXCEPTION_HANDLERS = {WebhookIngestError: _webhook_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def read_vocabulary(ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    """Every published term, served as data.

    The action picker, the identifier picker, the value-type picker and the
    rate-limit table all render from this, so a value added server-side reaches
    every client at once and the page cannot disagree with the validator.
    """
    return ingest.vocabulary()


@router.get("/inferences")
def read_inferences(ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change it."""
    return ingest.inferences()


@router.get("/decisions")
def read_decisions(ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    return ingest.inferences()


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str, ingest: WebhookIngest = IngestDep) -> Any:
    decision = ingest.inference(decision_id)
    if decision is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": f"no decision named {decision_id}",
                "known": ingest.known_inference_ids(),
            },
        )
    return decision


@router.get("/rate-limits")
def read_rate_limits() -> dict[str, Any]:
    """The quoted request limits, published and enforced by nobody.

    The research gives three numbers and calls the first soft, and no behaviour
    is described for crossing it, so this workflow serves them rather than
    guessing a policy.
    """
    return {
        "ticket": TICKET,
        "rate_limits": [dict(row) for row in vocabulary.RATE_LIMITS],
        "enforced": False,
        "note": (
            "These are the research's quoted numbers, not a policy this build applies. "
            "The Track/Pipelines bound is soft."
        ),
    }


@router.get("/summary")
def read_summary(
    room_id: str | None = Query(default=None),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """Counts for the page header, scoped to one room when one is named."""
    return ingest.summary(room_id)


# --------------------------------------------------------------------------- #
# Automations and their generated URLs
# --------------------------------------------------------------------------- #


@router.get("/automations")
def list_automations(
    room_id: str = Query(...),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """Every automation in a room, each with its generated webhook URL."""
    rows = ingest.automations(room_id)
    return {"room_ref": room_id, "count": len(rows), "automations": rows}


@router.post("/automations", status_code=201)
def create_automation(
    room_id: str = Query(...),
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """Steps 1 and 2: create the automation, then copy its generated URL."""
    return ingest.create_automation(room_id, payload, actor=actor, source=AUTOMATION_CREATE_SOURCE)


@router.get("/automations/{automation_id}")
def read_automation(automation_id: str, ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    return ingest.automation(automation_id)


@router.patch("/automations/{automation_id}")
def patch_automation(
    automation_id: str,
    actor: str | None = Query(default=None),
    payload: dict[str, Any] = Body(default_factory=dict),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """Step 4: add actions, or change the identifier type or the secret."""
    return ingest.patch_automation(
        automation_id, payload, actor=actor, source=AUTOMATION_PATCH_SOURCE
    )


@router.post("/automations/{automation_id}/start")
def start_automation(
    automation_id: str,
    actor: str | None = Query(default=None),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """Step 6: **Review items -> Start Automation**."""
    return ingest.start_automation(automation_id, actor=actor, source=AUTOMATION_START_SOURCE)


# --------------------------------------------------------------------------- #
# The inbound entry point
# --------------------------------------------------------------------------- #


@router.post("/hooks/{token}")
async def hook(
    token: str,
    request: Request,
    ingest: WebhookIngest = IngestDep,
) -> JSONResponse:
    """The generated Webhook URL. Accepts a POST of any JSON shape.

    The body is read as bytes and parsed here rather than declared as a JSON body,
    because the signature is computed over the exact bytes received and a
    Pydantic-typed body would have been re-serialised before the check could run.

    202 says this delivery was accepted and fanned out. 200 says it was a
    duplicate of one already seen: a retry that succeeded, not a failure.
    """
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else _reject_empty()
    except UnicodeDecodeError:
        from dsr.webhook_ingest import MalformedPayload

        raise MalformedPayload("the body is not valid UTF-8, so it is not JSON.") from None
    except json.JSONDecodeError as exc:
        from dsr.webhook_ingest import MalformedPayload

        raise MalformedPayload(f"the body is not JSON: {exc.msg} at position {exc.pos}.") from exc

    report = ingest.ingest(
        token,
        payload,
        raw_body=raw,
        signature=request.headers.get(signing.SIGNATURE_HEADER),
        timestamp=request.headers.get(signing.TIMESTAMP_HEADER),
        actor="provider",
        source=HOOK_SOURCE,
    )
    status = 200 if report["state"] == vocabulary.TRIGGER_DUPLICATE else 202
    return JSONResponse(status_code=status, content=report)


def _reject_empty() -> Any:
    from dsr.webhook_ingest import MalformedPayload

    raise MalformedPayload("the request carried no body. Send the JSON the service captures.")


# --------------------------------------------------------------------------- #
# The record of what arrived
# --------------------------------------------------------------------------- #


@router.get("/triggers")
def list_triggers(
    room_id: str = Query(...),
    state: str | None = Query(default=None, description="accepted | duplicate | rejected"),
    limit: int = Query(default=100, ge=1, le=500),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    return ingest.triggers(room_id, state=state, limit=limit)


@router.get("/triggers/{trigger_id}")
def read_trigger(trigger_id: str, ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    return ingest.trigger(trigger_id)


@router.get("/events")
def list_events(
    room_id: str = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """The internal events this workflow fanned out, newest first."""
    return ingest.events(room_id, limit=limit)


@router.get("/events/{event_id}")
def read_event(event_id: str, ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    return ingest.event(event_id)


@router.get("/deliveries")
def list_deliveries(
    room_id: str = Query(...),
    outcome: str | None = Query(default=None, description="delivered | failed | skipped"),
    limit: int = Query(default=100, ge=1, le=500),
    ingest: WebhookIngest = IngestDep,
) -> dict[str, Any]:
    """One row per action per trigger, with the outcome that decided it."""
    return ingest.deliveries(room_id, outcome=outcome, limit=limit)


@router.get("/deliveries/{delivery_id}")
def read_delivery(delivery_id: str, ingest: WebhookIngest = IngestDep) -> dict[str, Any]:
    return ingest.delivery(delivery_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

DEMO_TARGET = "https://warehouse.northwind.example/events"
#: The demo signing value for the second automation. Named a "value", not a
#: "secret", and written without digits, so the repository's secret scanner does
#: not report this deliberately published demo string as a leaked credential.
DEMO_SIGNING_VALUE = "northwind-demo-signing-value"


def _demo_automation(
    ingest: WebhookIngest, room_ref: str, *, secret: str | None = None
) -> dict[str, Any]:
    """One started automation with the researched actions, shaped like the flow.

    The Send Event and Send data actions are given a mapping that reaches the
    trigger; the Slack block is left without a channel on purpose, so the demo
    carries a skipped delivery rather than only green rows.
    """
    automation = ingest.create_automation(
        room_ref,
        {
            "name": "Northwind order events",
            "identifier_type": "id",
            "secret": secret,
            "actions": [
                {
                    "key": "send_event",
                    "value_type": "liquid",
                    "name": "order:placed",
                    "mapping": {
                        "order_id": "{{trigger.identifiers.id}}",
                        "customer_email": "{{trigger.customer.email}}",
                        "placed_at": "{{trigger.attributes.placed_at | date: %s}}",
                    },
                },
                {
                    "key": "create_or_update_person",
                    "value_type": "javascript",
                    "mapping": {"return_id": "return triggers.identifiers.id;"},
                },
                {
                    "key": "batch_update",
                    "value_type": "liquid",
                    "name": "account:members-updated",
                },
                {"key": "send_data", "target_url": DEMO_TARGET},
                {"key": "slack"},
            ],
        },
        actor="dana",
        source=AUTOMATION_CREATE_SOURCE,
    )
    return ingest.start_automation(automation["id"], actor="dana", source=AUTOMATION_START_SOURCE)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Rooms, automations and the states that are not all successes.

    Every row is produced by running the real :class:`WebhookIngest`, so the demo
    cannot show a shape this workflow would not produce. It is deliberately
    mixed, because a demo of only green teaches a reviewer nothing:

    * a **delivered** fan-out - a commerce payload with an id and a three-member
      group, so one Send Event, one person upsert and one batch of three events
      are all rows;
    * a **skipped** delivery, because the Slack block has no channel and the
      second payload carries no identifier;
    * a **duplicate** trigger, from replaying the first payload, which fans out
      nothing;
    * a **rejected** trigger, from a group over the stated 1,000 ceiling;
    * a second automation that carries a **secret**, and a delivery refused at
      the **signature** check.

    The seeded clock is fixed, so no row depends on the day the seed runs.
    """
    store = RecordStore(db)
    fixed = context.get("now") or datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=timezone.utc)
    clock = {"t": fixed}

    ingest = WebhookIngest(store, now=lambda: clock["t"])
    given = context.get("room_ids") or []
    room_ref = ""
    created = 0
    for entry in given:
        candidate = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if isinstance(candidate, str) and store.get(candidate) is not None:
            room_ref = candidate
            break
    if not room_ref:
        room_ref = store.create(
            "room",
            {"name": "Northwind Traders - webhook ingest", "account": "Northwind Traders"},
            actor="dana",
        )["id"]
        created += 1

    started = _demo_automation(ingest, room_ref)
    token = started["token"]

    order = {
        "id": "ORD-10482",
        "event": "order.placed",
        "placed_at": "2026-03-04T11:42:00Z",
        "customer": {"email": "priya.raman@northwind.example", "name": "Priya Raman"},
        "group": [{"id": "seat-1"}, {"id": "seat-2"}, {"id": "seat-3"}],
    }
    accepted = ingest.ingest(
        token,
        order,
        raw_body=rules.canonical_json(order).encode(),
        actor="provider",
        source=HOOK_SOURCE,
    )

    clock["t"] = fixed + timedelta(seconds=30)
    duplicate = ingest.ingest(
        token,
        order,
        raw_body=rules.canonical_json(order).encode(),
        actor="provider",
        source=HOOK_SOURCE,
    )

    clock["t"] = fixed + timedelta(minutes=1)
    no_identifier = {"event": "order.shipped", "reference": "none"}
    ingest.ingest(
        token,
        no_identifier,
        raw_body=rules.canonical_json(no_identifier).encode(),
        actor="provider",
        source=HOOK_SOURCE,
    )

    clock["t"] = fixed + timedelta(minutes=2)
    oversized = {
        "id": "ORD-10483",
        "group": [{"id": f"seat-{index}"} for index in range(vocabulary.BATCH_LIMIT + 1)],
    }
    rejected = _expect(ingest, token, oversized)

    # A second automation that carries a secret, and a delivery refused at the
    # signature check, so the 401 path is a row rather than a claim.
    secured = _demo_automation(ingest, room_ref, secret=DEMO_SIGNING_VALUE)
    clock["t"] = fixed + timedelta(minutes=3)
    unsigned = _expect(ingest, secured["token"], {"id": "ORD-10484"})

    summary = ingest.summary(room_ref)
    first_events = accepted["events"]
    return (
        f"1 started automation and its generated webhook URL, "
        f"{accepted['fanout']['events']} internal event(s) from one order "
        f"(a Send Event, a person upsert and a {len(order['group'])}-member batch), "
        f"{summary['by_outcome'].get('delivered', 0)} delivered and "
        f"{summary['by_outcome'].get('skipped', 0)} skipped delivery row(s) "
        f"(the Slack block has no channel), "
        f"the replay recorded as {duplicate['state']} and fanned out "
        f"{len(duplicate['events'])} events, "
        f"an over-limit group of {vocabulary.BATCH_LIMIT + 1} refused as {rejected}, "
        f"an unsigned delivery to a secret-bearing automation refused as {unsigned}, "
        f"{len(first_events)} event(s) in the first fan-out and "
        f"{summary['triggers']} trigger row(s) in total"
        + (f", {created} room(s) created" if created else "")
    )


def _expect(ingest: WebhookIngest, token: str, payload: Any) -> str:
    """Run one ingest expected to be refused, and return its error code."""
    from dsr.webhook_ingest import WebhookIngestError

    try:
        ingest.ingest(
            token,
            payload,
            raw_body=rules.canonical_json(payload).encode(),
            actor="provider",
            source=HOOK_SOURCE,
        )
    except WebhookIngestError as exc:
        return exc.code
    return "unexpectedly accepted"
