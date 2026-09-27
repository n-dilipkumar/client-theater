"""WF-016: sync room events to the CRM via webhooks and automations.

Ported from
``feature/WF-016-sync-room-events-to-the-crm-via-webhooks``.

The domain logic is reused unchanged from :mod:`dsr.crm`. This module is only
the three things the branch had to take out of shared files: the HTTP surface,
the mapping from domain errors to responses, and the demo data.

What the port changed, and why
------------------------------

**The routes became a router.** The branch registered sixteen handlers with
``@app.<verb>`` on the one shared FastAPI app in ``dsr/api.py``. That single edit
is what made the twelve workflow branches mutually unmergeable. Here they are
``@router.<verb>`` on an ``APIRouter`` the plugin host mounts by discovery.

**The prefix is ``/api/wf-016``.** The branch served ``/api/crm/*``, a prefix any
other integration feature would also have reached for, and four of those paths
are generic enough that a second workflow claiming one would have been a
collision. The contract asks for a ticket-derived prefix, the branch was never
merged, and no client depends on the old paths.

**``source=`` comes from the route.** The branch let five hardcoded strings into
the audit log - ``"subscribe"``, ``"unsubscribe"``, ``"delivery"``,
``"automation.create"``, ``"crm.event.<name>"`` and more - none of which names a
route. An audit row that cannot be traced back to the request that caused it is
not an audit trail. Every write route below passes the route that actually served
it, built from ``router.prefix`` so it cannot drift when the prefix changes, and
``source`` is now a *required* keyword on every domain method that writes, so
this cannot silently regress. The writes a fan-out makes on its way through -
the Activity Log row, the delivery counters - carry the event's ``source`` with
a note naming the channel, so a reader still starts from the route.

**``CRMSync`` is built per request from ``StoreDep``.** The branch created one
in the lifespan and hung it on ``app.state.crm``, which is another edit to
``api.py``. :class:`~dsr.crm.sync.CRMSync` holds nothing beyond the store handle
and its transport, so constructing it per request is equivalent, keeps this
module importable and testable on its own, and leaves the transport a seam a
test can override.

**The error mapping is exported.** FastAPI only accepts exception handlers on
the app object, so the branch's ``@app.exception_handler(CrmError)`` became
``EXCEPTION_HANDLERS`` for the host to attach. One handler covers the whole
hierarchy on purpose: every refusal in :mod:`dsr.crm` is the caller's fault and
answers ``400``, and ``CrmError`` is a domain type, so a global registration for
it cannot intercept an unrelated error anywhere in the product.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
404, and two handlers for one type is a collision the host refuses.

**Delivery retries are a record, not memory.** The branch kept each retry
attempt in a ``DeliveryReport`` that lived only for the length of the call, and
stored just the list of status codes. Two attempts into a rate limit, the reason
for the retry and the endpoint's own response were gone with the process. The
report now serialises every attempt in full and the Activity Log stores it, so
the retry history survives a restart. See :mod:`dsr.crm.delivery`.

**Two user-facing strings stopped naming routes.** The lint warning told a rep to
register a field "under /api/crm/fields", and an unknown preset pointed at
"/api/crm/vocabulary". Both are paths this app does not serve, and a message
that sends someone to a 404 is worse than no message. They now name the registry
and the endpoint in prose.

**Demo data is a ``seed(db, context)`` export.** The branch rewrote
``backend/seed.py`` to add CRM fields, automations, subscriptions and a filled
Activity Log. The contract forbids that, so the same rows are produced here - and
produced by running the real :class:`~dsr.crm.sync.CRMSync` over a scripted
transport, so the demo cannot show a shape the workflow would not produce, and
seeding never opens a socket.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm import CRMSync, CrmError
from dsr.crm.automations import from_preset
from dsr.crm.delivery import DeliveryResult
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-016-crm-sync",
    "ticket": "WF-016",
    "name": "Sync room events to the CRM via webhooks and automations",
    "description": (
        "Push room events to a CRM over webhooks, or describe the write as a When / Do this "
        "automation. Both paths report into an Activity Log that distinguishes success from "
        "errored and says which rows need a human."
    ),
    "nav": [{"id": "crm-sync", "label": "CRM sync"}],
}

router = APIRouter(prefix="/api/wf-016", tags=["wf016"])


def get_crm(store: RecordStore = StoreDep) -> CRMSync:
    """A :class:`CRMSync` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and ``app.state`` is where the branch had to put it, which
    meant editing the shared app. Building it here also leaves the transport as an
    overridable dependency, so the suite can drive delivery without a socket.
    """
    return CRMSync(store)


CrmDep = Depends(get_crm)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _crm_error(request: Request, exc: CrmError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    One handler for the whole hierarchy: ``CrmError`` is the base of every
    refusal in :mod:`dsr.crm` (an event outside the published enum, a target URL
    the server must not fetch, an automation in an unsupported environment) and
    all of them are the caller's to fix.
    """
    return JSONResponse(status_code=400, content={"error": "crm_error", "detail": str(exc)})


EXCEPTION_HANDLERS = {CrmError: _crm_error}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(crm: CRMSync = CrmDep) -> dict[str, Any]:
    """The published event, page-status, and field-type vocabularies.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so an event added to one place reaches every
    client at once.
    """
    return crm.vocabulary()


@router.get("/presets")
def presets(crm: CRMSync = CrmDep) -> dict[str, Any]:
    """Recommended Automations a rep can start from with one click."""
    listed = crm.presets()
    return {"presets": listed, "count": len(listed)}


# --------------------------------------------------------------------------- #
# CRM field registry
# --------------------------------------------------------------------------- #


@router.get("/fields")
def list_fields(crm: CRMSync = CrmDep) -> dict[str, Any]:
    """Declared CRM fields, used to type-check automation field maps.

    Flattened to ``data`` plus the record id: this is a list view, and the full
    envelope is one call away through the generic records API.
    """
    records = crm.fields()
    return {"fields": [record["data"] | {"id": record["id"]} for record in records], "count": len(records)}


@router.post("/fields", status_code=201)
def register_field(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Declare a CRM field. Optional, but it is what makes the lint work.

    A field that was never registered still syncs; it simply cannot be
    type-checked, and says so at ``info`` rather than failing.
    """
    return crm.register_field(payload, actor=actor, source=f"POST {router.prefix}/fields")


# --------------------------------------------------------------------------- #
# Webhook subscriptions
# --------------------------------------------------------------------------- #


@router.get("/subscriptions")
def list_subscriptions(
    room_id: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """List webhook subscriptions. The record id is the subscription id.

    The summary never carries the shared secret - only whether there is one - so
    a rep can see at a glance which of their integrations is signed.
    """
    subscriptions = crm.list_subscriptions(room_id=room_id)
    return {"count": len(subscriptions), "subscriptions": subscriptions}


@router.post("/subscriptions", status_code=201)
def create_subscription(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Subscribe a target URL to one event.

    Accepts ``target_url`` or the researched ``targetUrl`` spelling, so a client
    written against the source API's body works unchanged. The target must be an
    absolute http(s) URL: this one is fetched by the server, so an unvalidated
    value would hand a request-forgery primitive to anyone who can call the API.
    """
    target_url = payload.get("target_url") or payload.get("targetUrl")
    if not target_url:
        raise HTTPException(status_code=400, detail="target_url is required")
    return crm.subscribe(
        payload.get("event"),
        target_url,
        room_id=room_id,
        secret=payload.get("secret"),
        description=str(payload.get("description") or ""),
        actor=actor,
        source=f"POST {router.prefix}/subscriptions",
    )


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def delete_subscription(
    subscription_id: str,
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> Response:
    """Cancel a subscription. 204, matching the researched contract.

    A soft delete, so the cancellation is audited and the record of what was sent
    where outlives the unsubscribe.
    """
    crm.unsubscribe(subscription_id, actor=actor, source=f"DELETE {router.prefix}/subscriptions/{subscription_id}")
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Automations
# --------------------------------------------------------------------------- #


@router.get("/automations")
def list_automations(crm: CRMSync = CrmDep) -> dict[str, Any]:
    """The automations library, each with its compatibility warnings."""
    automations = crm.list_automations()
    return {"count": len(automations), "automations": automations}


@router.post("/automations", status_code=201)
def create_automation(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Create an automation.

    Either a full When / Do this spec, or ``{"preset_id": "..."}`` to start from
    a Recommended Automation and edit it from there. On the preset path, any
    other key in the body wins, so ``{"preset_id": ..., "name": "..."}`` renames
    the preset rather than replacing it.
    """
    preset_id = payload.get("preset_id")
    if preset_id:
        spec: dict[str, Any] = from_preset(preset_id, name=payload.get("name"))
        spec.update({key: value for key, value in payload.items() if key != "preset_id"})
    else:
        spec = payload
    return crm.create_automation(spec, actor=actor, source=f"POST {router.prefix}/automations")


@router.get("/automations/{automation_id}")
def read_automation(automation_id: str, crm: CRMSync = CrmDep) -> dict[str, Any]:
    automation = crm.get_automation(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail=f"automation {automation_id} not found")
    return automation


@router.patch("/automations/{automation_id}")
def update_automation(
    automation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Patch an automation, or flip its on/off toggle with ``{"enabled": false}``.

    The toggle lives on the rule record rather than beside it, because the
    research ties the two together: an automation is only assigned to its
    templates while it is on, so "off" is a property of the rule and a separate
    flag could drift out of step with it.
    """
    if crm.get_automation(automation_id) is None:
        raise HTTPException(status_code=404, detail=f"automation {automation_id} not found")
    return crm.update_automation(
        automation_id, payload, actor=actor, source=f"PATCH {router.prefix}/automations/{automation_id}"
    )


@router.delete("/automations/{automation_id}")
def delete_automation(
    automation_id: str,
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Soft-delete an automation. The rule and its run history stay auditable."""
    if crm.get_automation(automation_id) is None:
        raise HTTPException(status_code=404, detail=f"automation {automation_id} not found")
    return crm.delete_automation(
        automation_id, actor=actor, source=f"DELETE {router.prefix}/automations/{automation_id}"
    )


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@router.get("/events")
def list_events(
    room_id: str | None = Query(default=None),
    event: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Recorded room events, newest first."""
    records = crm.list_events(room_id=room_id, event=event, limit=limit)
    return {"count": len(records), "events": records}


@router.post("/events", status_code=201)
def record_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """Record a room event and fan it out to subscribers and automations.

    The event is stored before anything is sent, so there is a durable record of
    what happened even when every delivery downstream fails. The response carries
    the stored event plus one Activity Log row per target, so a caller can see
    what became of it without a second request.

    ``status`` defaults to the value the event implies and may be overridden with
    any of the five published page statuses; anything else is refused before a
    single row is written.
    """
    if not payload.get("event"):
        raise HTTPException(status_code=400, detail="event is required")
    return crm.record_event(
        payload["event"],
        room_id=room_id,
        status=payload.get("status"),
        metadata=payload.get("metadata"),
        actor=actor,
        source=f"POST {router.prefix}/events",
    )


# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #


@router.get("/activity")
def activity(
    room_id: str | None = Query(default=None),
    channel: str | None = Query(default=None, description="webhook | automation"),
    status: str | None = Query(default=None, description="success | error"),
    event: str | None = Query(default=None),
    subscription_id: str | None = Query(default=None),
    automation_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    crm: CRMSync = CrmDep,
) -> dict[str, Any]:
    """The Activity Log: success versus errored, and what needs manual updating.

    Every filter is a JSON path in the row's own payload, resolved through the
    dynamic index, so a new channel or status needs no change here. The summary
    is computed over exactly the rows returned, so a filtered view does not
    report totals for the whole log.
    """
    entries = crm.activity(
        room_id=room_id,
        channel=channel,
        status=status,
        event=event,
        subscription_id=subscription_id,
        automation_id=automation_id,
        limit=limit,
    )
    summary = {"success": 0, "error": 0, "needs_manual_update": 0}
    for entry in entries:
        state = str(entry["data"].get("status"))
        summary[state] = summary.get(state, 0) + 1
        if entry["data"].get("needs_manual_update"):
            summary["needs_manual_update"] += 1
    return {"count": len(entries), "summary": summary, "entries": entries}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

# The CRM fields a team would declare so the editor can type-check its mappings.
# `Stage__c` is registered but invisible to the integration user, which is the
# research's permissions trap: a custom field that does not appear is almost
# always a permission in the CRM, not a mapping mistake. One of them in the demo
# means the lint has something real to say.
DEMO_FIELDS: tuple[Mapping[str, Any], ...] = (
    {"name": "Room_Live_URL__c", "type": "url", "label": "Room live link"},
    {"name": "Room_Collab_URL__c", "type": "url", "label": "Collaborator link"},
    {"name": "Room_View_Count__c", "type": "number", "label": "Room view count"},
    {"name": "Payment_Ref__c", "type": "text", "label": "Payment reference"},
    {
        "name": "Stage__c",
        "type": "text",
        "label": "Deal stage",
        "visible_to_integration": False,
    },
)

#: Target URLs for the demo subscriptions, and what
#: :class:`DemoTransport` answers for each.
#:
#: Any path containing ``/hooks/`` succeeds, except the one ending
#: :data:`RATE_LIMITED_SUFFIX`, which is rate-limited on its first attempt and
#: accepts the retry. Anything else answers 404, and a 404 is the permanent
#: failure no retry count can fix - so the demo contains one row the Activity Log
#: has to send a human back to, which is the case the research says the log
#: exists to track.
DEMO_HEALTHY_TARGET = "https://crm.example/hooks/dsr"
DEMO_RETRYING_TARGET = "https://crm.example/hooks/limited"
DEMO_RETIRED_TARGET = "https://crm.example/retired-hook"
RATE_LIMITED_SUFFIX = "/hooks/limited"


class DemoTransport:
    """A scripted transport, so seeding the demo never opens a socket.

    A real :class:`~dsr.crm.delivery.UrllibTransport` would try to POST to
    ``crm.example`` from ``backend/seed.py``. This one answers from a fixed
    script, which also makes the demo's rows deterministic rather than dependent
    on what a hostname happens to answer today.

    The rate-limited target is counted per URL, so the retry it provokes is a
    real second attempt through the real retry policy rather than a hand-written
    "attempts: 2".
    """

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> DeliveryResult:
        self.calls[url] = self.calls.get(url, 0) + 1
        if url.endswith(RATE_LIMITED_SUFFIX):
            if self.calls[url] == 1:
                # [sourced] The research documents 429 rate limiting on the
                # webhook seam, and `Retry-After: 0` keeps the seeder instant.
                return DeliveryResult(
                    ok=False,
                    status=429,
                    body="slow down",
                    error="HTTP 429",
                    retryable=True,
                    retry_after=0,
                    duration_ms=11.0,
                )
            return DeliveryResult(ok=True, status=200, body="accepted", duration_ms=9.0)
        if "/hooks/" in url:
            return DeliveryResult(ok=True, status=200, body="accepted", duration_ms=14.0)
        return DeliveryResult(
            ok=False,
            status=404,
            body="no such hook",
            error="HTTP 404",
            retryable=False,
            duration_ms=9.0,
        )


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the CRM field registry, two automations, two subscriptions, and run
    two events through them.

    The events are recorded through the real
    :class:`~dsr.crm.sync.CRMSync` over :class:`DemoTransport`, so the Activity
    Log in the demo is what this workflow actually produces rather than rows
    written by hand. It is deliberately mixed: one successful delivery, one
    permanent failure that needs manual updating, one automation run that cannot
    resolve what it was asked for. A demo showing only green would not exercise
    the two states the research says a rep reads the log for.

    Returns a short description of what was added, which the seeder prints.
    """
    store = RecordStore(db)
    crm = CRMSync(
        store,
        transport=DemoTransport(),
        # Retries cost nothing here: the script answers on the first attempt, and
        # a backoff in a seeder would just make `backend/seed.py` slow.
        backoff=0,
        sleep=lambda _seconds: None,
    )
    source = "seed"

    for spec in DEMO_FIELDS:
        crm.register_field(spec, actor="dana", source=source)

    for preset_id in ("sync-page-urls", "sync-view-count"):
        crm.create_automation(from_preset(preset_id), actor="dana", source=source)

    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        # No demo rooms to attach to. The registry and the library are still
        # worth having, and the seeder prints what was skipped.
        return (
            f"{len(DEMO_FIELDS)} CRM fields, 2 automations, 0 subscriptions "
            "(no rooms to scope them to)"
        )

    # Unscoped and signed: the general-purpose seam, receiving every room's
    # events, and the one that is simply healthy.
    crm.subscribe(
        "pageViewed",
        DEMO_HEALTHY_TARGET,
        secret="demo-signing-secret",
        description="Every view, into the CRM.",
        actor="dana",
        source=source,
    )
    # Unscoped, unsigned, and rate-limited on its first attempt. This is the row
    # that shows the retry record: two attempts, the first of them a 429, both of
    # them in the Activity Log rather than in the memory of one request.
    crm.subscribe(
        "pageViewed",
        DEMO_RETRYING_TARGET,
        description="Rate-limited on the first attempt. The retry succeeds.",
        actor="dana",
        source=source,
    )
    # Scoped to one room, unsigned, pointing at a retired hook. The research's
    # scope rule is what makes this row's room matter, and the 404 is what makes
    # it "needs manual updating".
    crm.subscribe(
        "pageAccepted",
        DEMO_RETIRED_TARGET,
        room_id=rooms[1][0] if len(rooms) > 1 else rooms[0][0],
        description="Scoped to one room. The hook was retired; this row will not fix itself.",
        actor="dana",
        source=source,
    )

    second = rooms[1][0] if len(rooms) > 1 else rooms[0][0]
    activity = crm.record_event("pageViewed", room_id=rooms[0][0], actor="dana", source=source)[
        "activity"
    ]
    activity += crm.record_event("pageAccepted", room_id=second, actor="dana", source=source)[
        "activity"
    ]

    return (
        f"{len(DEMO_FIELDS)} CRM fields, 2 automations, 3 subscriptions, "
        f"2 events, {len(activity)} activity rows "
        "(1 delivery retried, 1 delivery failed, 1 run unresolved)"
    )
