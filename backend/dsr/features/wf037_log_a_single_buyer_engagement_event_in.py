"""WF-037: log a single buyer engagement event into the CRM.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-037.md``, which is the
specification. The researched decisions are the product: the two-stage write (the room
records its own row and *enqueues*, the queue worker fires it), the worker's resolution of
the buyer's CRM record from the field mapping and sync key, three genuinely different
create surfaces, and three genuinely different answers to "how do I know it worked, and
what is the new row's id".

This module is the three things the contract requires of a feature and nothing else: the
HTTP surface, the mapping from domain errors to responses, and the demo data. The domain
lives in :mod:`dsr.crm_engagement`.

Why the prefix is ``/api/wf-037``
--------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide with a
feature-shaped one by construction. Every room-scoped path is room-scoped, and the host's
loader would report a ``(method, path)`` clash as a failed feature rather than shadowing
it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain method that writes, so it cannot silently regress. The
two rows one write settles - the queue row and the engagement row the researched step 5
mirrors it onto - carry the same source, because they are one outcome. A test asserts that
every source recorded in the audit log matches a route the host actually mounted.

The token is never in a row
---------------------------
A connector's token *is* the ``Authorization`` header, and the recorded request headers are
the evidence of what was sent, so the token is redacted where the request is recorded and
withheld from every read. A test asserts it appears in no stored row.

Error mapping
-------------
Six handlers, one per distinct HTTP answer, and all six types are this feature's own.
``RecordNotFound`` and ``AuditError`` are deliberately not claimed: the core app already
maps them correctly, and two handlers for one type is a collision the host refuses. The
split between ``400`` and ``422`` is deliberate - "the request cannot be acted on" and "the
request was understood and what it says cannot be used" are different messages - and the
``428`` is deliberate because a client has to be able to say "finish the setup" rather than
"you got the request wrong".
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm_engagement import EngagementSync, EngagementSyncError, SyncBook
from dsr.crm_engagement.delivery import CreateResult
from dsr.crm_engagement.errors import (
    InvalidConnector,
    InvalidEventType,
    InvalidFieldMap,
    SyncNotConfigured,
    UnknownRoom,
)
from dsr.crm_engagement.inferences import describe as describe_inferences
from dsr.crm_engagement.mapping import describe_transforms
from dsr.crm_engagement.vocabulary import describe as describe_vocabulary
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-037-log-a-single-buyer-engagement-event-in",
    "ticket": "WF-037",
    "name": "Log a single buyer engagement event into the CRM",
    "description": (
        "Record a buyer's engagement in the room, enqueue the CRM write, and let the queue "
        "worker resolve the buyer, map the fields, and create the CRM row - storing the id "
        "it returns, and reporting everything that will not send in a Sync log."
    ),
    "nav": [{"id": "crm-engagement-log", "label": "CRM engagement log"}],
}

router = APIRouter(prefix="/api/wf-037", tags=["wf037"])


def get_sync(store: RecordStore = StoreDep) -> EngagementSync:
    """An :class:`EngagementSync` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but the
    store handle and its transport, and ``app.state`` is where it would otherwise have to
    be built in the shared app's lifespan. Building it here also leaves the transport an
    overridable dependency, so the suite can drive a create without a socket.
    """
    return EngagementSync(store)


SyncDep = Depends(get_sync)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _sync_error(request: Request, exc: EngagementSyncError) -> JSONResponse:
    """A body this layer will not act on. 400.

    The base of the hierarchy, and the only place a request that parsed and named
    something impossible lands.
    """
    return JSONResponse(status_code=400, content={"error": "sync_error", "detail": str(exc)})


def _invalid_connector(request: Request, exc: InvalidConnector) -> JSONResponse:
    """A connector that could not be written down. 422.

    Its own type and status, so a client can say "that connector is wrong" without parsing
    the message. Starlette picks the most specific registered handler by MRO, so this wins
    over ``_sync_error`` for the same exception.
    """
    return JSONResponse(status_code=422, content={"error": "invalid_connector", "detail": str(exc)})


def _invalid_event_type(request: Request, exc: InvalidEventType) -> JSONResponse:
    """An event-catalogue row that cannot be saved as asked. 422."""
    return JSONResponse(
        status_code=422, content={"error": "invalid_event_type", "detail": str(exc)}
    )


def _invalid_field_map(request: Request, exc: InvalidFieldMap) -> JSONResponse:
    """A field map that could not produce a complete, non-colliding payload. 422."""
    return JSONResponse(status_code=422, content={"error": "invalid_field_map", "detail": str(exc)})


def _not_configured(request: Request, exc: SyncNotConfigured) -> JSONResponse:
    """Well formed, but this room is not set up to answer it yet. 428.

    Distinct from 400 so a client can say "finish the setup" rather than "you got the
    request wrong" - the frontend's ``apiRequest`` carries the status for exactly this.
    """
    return JSONResponse(status_code=428, content={"error": "not_configured", "detail": str(exc)})


def _unknown_room(request: Request, exc: UnknownRoom) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": f"not found: {exc}", "id": str(exc)},
    )


EXCEPTION_HANDLERS = {
    EngagementSyncError: _sync_error,
    InvalidConnector: _invalid_connector,
    InvalidEventType: _invalid_event_type,
    InvalidFieldMap: _invalid_field_map,
    SyncNotConfigured: _not_configured,
    UnknownRoom: _unknown_room,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The researched contract: the three create surfaces and what each one answers.

    Served as data so a client renders its pickers from the same source the writer
    enforces against, and so a reviewer can read the researched facts without opening a
    Python file. Includes each vendor's success codes with the quotation behind them, where
    each vendor returns the new row's id, the ``Prefer`` tokens, and the queue states.
    """
    return describe_vocabulary()


@router.get("/transforms")
def transforms() -> dict[str, Any]:
    """The named, versioned transform registry the field maps choose from.

    A read with no side effect, so it needs no store. A client's picker is rendered from
    this rather than from a list compiled into the page, so a deployment that registers
    another transform reaches the editor without a code change on either side.
    """
    return {"transforms": describe_transforms()}


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about three create endpoints and silent about almost
    everything around them. The parts that are therefore judgement calls - which statuses
    count as success, what a create with no id means, the retry ladder, whether an
    unmapped source is sent as null, who runs the queue worker - are collected in
    :mod:`dsr.crm_engagement.inferences` and served here, next to the sourced facts they
    are measured against.

    A read with no side effect, so it needs no store.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Connectors: which CRM, and where
# --------------------------------------------------------------------------- #


@router.get("/connectors")
def list_connectors(sync: EngagementSync = SyncDep) -> dict[str, Any]:
    """Every registered CRM connector.

    The token is never in a read: a connector read answers ``has_token`` and a masked
    preference explanation instead, because the token *is* the ``Authorization`` header of
    the requests the Sync log records.
    """
    rows = sync.connectors()
    return {"count": len(rows), "connectors": rows}


@router.post("/connectors", status_code=201)
def register_connector(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None, description="scope this connector to one room"),
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Register a CRM connector: which vendor, which API root, which object to create on.

    ``vendor`` picks the researched create surface and the field it must name - HubSpot
    wants an object type, Dataverse an entity *set* name (the thing 'Copy set name'
    copies), Salesforce an sObject name - and a payload that sets the wrong one for its
    vendor is refused rather than stored and ignored.
    """
    return sync.register_connector(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connectors"
    )


@router.get("/connectors/{connector_id}")
def read_connector(connector_id: str, sync: EngagementSync = SyncDep) -> dict[str, Any]:
    return sync.connector(connector_id)


@router.patch("/connectors/{connector_id}")
def update_connector(
    connector_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Patch a connector: rotate the token, flip it off, change the object or preferences."""
    return sync.patch_connector(
        connector_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connectors/{connector_id}",
    )


@router.delete("/connectors/{connector_id}", status_code=204)
def delete_connector(
    connector_id: str,
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> Response:
    """Soft-delete a connector. The Sync log outlives it, and stays readable."""
    sync.delete_connector(
        connector_id, actor=actor, source=f"DELETE {router.prefix}/connectors/{connector_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The event catalogue: a new event type is a row, not a code path
# --------------------------------------------------------------------------- #


@router.get("/event-types")
def list_event_types(sync: EngagementSync = SyncDep) -> dict[str, Any]:
    """The room's event catalogue.

    [sourced] "New event types are rows in the room's event catalogue mapped by the field
    map, so adding 'download', 'pricing-view', 'cta-click' needs a mapping row, not a code
    path." This is the half of that sentence that names the events.
    """
    rows = sync.event_types()
    return {"count": len(rows), "event_types": rows}


@router.post("/event-types", status_code=201)
def create_event_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Add an event type to the catalogue.

    No allow-list: the engine never checks an event's type against this collection, so a
    type nobody has added is still recorded and still enqueued. It will be ``blocked`` on
    ``event_type_unmapped`` rather than dropped, which is the state the researched
    extensibility promise depends on.
    """
    return sync.add_event_type(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/event-types"
    )


@router.get("/event-types/{event_type_id}")
def read_event_type(event_type_id: str, sync: EngagementSync = SyncDep) -> dict[str, Any]:
    return sync.event_type(event_type_id)


@router.patch("/event-types/{event_type_id}")
def update_event_type(
    event_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Patch a catalogue row, or switch a type off with ``{"enabled": false}``.

    A type that is switched off stops being *offered*, not being *recorded*: an event of
    that type still writes its own row and still enqueues. Turning a type off says "do not
    propose this one", and the queue's own ``event_type_unmapped`` is what says "this one
    cannot be sent", and conflating them would make a switch-off look like a data loss.
    """
    return sync.patch_event_type(
        event_type_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/event-types/{event_type_id}",
    )


@router.delete("/event-types/{event_type_id}", status_code=204)
def delete_event_type(
    event_type_id: str,
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> Response:
    """Soft-delete a catalogue row. Events already recorded keep their queue rows."""
    sync.delete_event_type(
        event_type_id, actor=actor, source=f"DELETE {router.prefix}/event-types/{event_type_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Field maps: the researched step 3 and the mapping half of step 4
# --------------------------------------------------------------------------- #


@router.get("/field-maps")
def list_field_maps(
    event_type: str | None = Query(default=None),
    connector_id: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Every field map: which event type, which CRM, and what each room field becomes."""
    rows = sync.field_maps(event_type=event_type, connector_id=connector_id)
    return {"count": len(rows), "field_maps": rows}


@router.post("/field-maps", status_code=201)
def create_field_map(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Declare how one event type becomes CRM properties on one connector.

    Each field names a ``source`` (a canonical room field or a dotted path into the
    event's own JSON), a ``target`` property, a ``direction`` and a ``transform``. The
    ``sync_key`` is required: it is the property carrying the room's own row id, and
    without it the CRM cannot reject a duplicate or the row be updated later.
    """
    return sync.add_field_map(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/field-maps"
    )


@router.get("/field-maps/{field_map_id}")
def read_field_map(field_map_id: str, sync: EngagementSync = SyncDep) -> dict[str, Any]:
    return sync.field_map(field_map_id)


@router.patch("/field-maps/{field_map_id}")
def update_field_map(
    field_map_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Patch a field map, or switch it off with ``{"enabled": false}``.

    A switched-off map stops being chosen, so a room's rows fall back to another map for
    the same type, or become ``event_type_unmapped``. It is re-evaluated on the next
    drain, not remembered.
    """
    return sync.patch_field_map(
        field_map_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/field-maps/{field_map_id}",
    )


@router.delete("/field-maps/{field_map_id}", status_code=204)
def delete_field_map(
    field_map_id: str,
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> Response:
    """Soft-delete a field map. The rows it already sent keep their mapped payload."""
    sync.delete_field_map(
        field_map_id, actor=actor, source=f"DELETE {router.prefix}/field-maps/{field_map_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Steps 1 and 2: the buyer acts, the room records and enqueues
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/engagements", status_code=201)
def record_engagement(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    fire_queue: bool = Query(
        default=True,
        description=(
            "Fire the queue worker as the last step of this request. False records the row "
            "and enqueues only, leaving the write to POST /queue/drain."
        ),
    ),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """A buyer opened an asset or answered a CTA. Record it, enqueue it, and fire the queue.

    [sourced] The room "records a row in its own `engagement` table and enqueues a CRM
    write", and the write is "asynchronous: the room's queue worker fires it without
    further user input, with retry on failure". So the local row is written *before*
    anything is sent, and ``fire_queue`` defaults to true: one user action produces the
    CRM write, with no second one.

    The response carries the stored event, its queue row, and - when the queue fired - what
    the worker did, so a client does not need a second request to find out.
    """
    return sync.record_event(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/engagements",
        fire_queue=fire_queue,
    )


@router.get("/rooms/{room_id}/engagements")
def list_engagements(
    room_id: str,
    type: str | None = Query(default=None),
    sync_state: str | None = Query(default=None, description="pending | synced | failed | blocked"),
    limit: int = Query(default=100, ge=1, le=1000),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """The room's Analytics / Engagement feed, with each event's sync state beside it.

    [sourced] One of the two surfaces the research names: "Room **Analytics / Engagement
    feed**". The room's own record and the CRM's copy are shown together, so the question a
    rep is really asking - "did we log that download" - has one answer, and an event that
    never arrived says so instead of being absent.
    """
    return sync.feed(room_id, type=type, sync_state=sync_state, limit=limit)


@router.get("/rooms/{room_id}/engagements/{engagement_id}")
def read_engagement(
    room_id: str, engagement_id: str, sync: EngagementSync = SyncDep
) -> dict[str, Any]:
    """One event, its queue row, and every write the worker made for it.

    The drill-in for a row that is not synced: which event it was, which CRM it was headed
    for, the exact request that went out, and every attempt with the vendor's own
    explanation of each one.
    """
    sync.book.require_room(room_id)
    return sync.event_detail(engagement_id)


@router.get("/rooms/{room_id}/readiness")
def readiness(room_id: str, sync: EngagementSync = SyncDep) -> dict[str, Any]:
    """Whether this room could sync at all, and what is missing if it could not.

    Answers "why is nothing going across?" without a drain, in the same words the queue
    rows carry, so the two cannot disagree.
    """
    return sync.configuration(room_id)


# --------------------------------------------------------------------------- #
# The queue, and the worker
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/queue")
def list_queue(
    room_id: str,
    state: str | None = Query(default=None, description="pending | synced | failed | blocked"),
    limit: int = Query(default=200, ge=1, le=1000),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """The CRM write queue, oldest first - the order the worker takes it in.

    A blocked row is shown with its named reason rather than hidden. A queue that hid what
    it could not send is how engagement events go missing with nobody able to say which.
    """
    sync.book.require_room(room_id)
    rows = sync.book.queue_rows(room_id, state=state, limit=limit)
    summary = {key: 0 for key in ("pending", "synced", "failed", "blocked")}
    for row in rows:
        key = str((row.get("data") or {}).get("state") or "pending")
        summary[key] = summary.get(key, 0) + 1
    return {"room_id": room_id, "count": len(rows), "summary": summary, "queue": rows}


@router.post("/rooms/{room_id}/queue/preview")
def preview_queue(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """What a drain would do, and why - with nothing written.

    The same planner the worker uses, so the fall-through can be seen before it happens:
    which rows would be sent, which would be blocked and on what named ground, and the
    exact request each create would carry. Because the planner writes nothing, a preview
    cannot leave a mark even if it is wrong.
    """
    return sync.preview(room_id, limit=limit)


@router.post("/rooms/{room_id}/queue/drain")
def drain_queue(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Fire the queue. The researched "the room's queue worker fires it".

    Oldest first, and idempotent: a ``synced`` row is never re-sent, so a drain can run on
    a timer without creating a second CRM row. ``queue_ids`` narrows it to specific rows.

    428 when no connector applies to this room, because "finish the setup" and "you got
    the request wrong" are different instructions.
    """
    sync.require_configured(room_id)
    only = payload.get("queue_ids")
    return sync.drain(
        room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/queue/drain",
        limit=limit,
        only=[str(item) for item in only] if isinstance(only, (list, tuple)) and only else None,
    )


@router.post("/rooms/{room_id}/queue/{queue_id}/retry")
def retry_queue_row(
    room_id: str,
    queue_id: str,
    actor: str | None = Query(default=None),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """Fire one failed or blocked row again, by hand, and say what happened.

    Re-planned from scratch rather than resumed, so fixing the configuration and retrying
    sends exactly what was waiting. Attempt history is appended, not overwritten - the
    reason a second attempt happened is the first one.
    """
    sync.book.require_room(room_id)
    return sync.retry(
        queue_id, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/queue/{queue_id}/retry"
    )


# --------------------------------------------------------------------------- #
# The Sync log / Errors admin panel
# --------------------------------------------------------------------------- #


@router.get("/sync-log")
def sync_log(
    room_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None, description="synced | failed"),
    vendor: str | None = Query(default=None),
    engagement_id: str | None = Query(default=None),
    needs_manual_update: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    sync: EngagementSync = SyncDep,
) -> dict[str, Any]:
    """The Sync log / Errors admin panel: every create the worker made, newest first.

    [sourced] The other surface the research names: a failure "surfaces the failure in the
    admin **Sync log** panel". Every filter is a JSON path in the row's own payload,
    resolved through the dynamic index, so a new outcome or a new vendor needs no change
    here. The summary is computed over exactly the rows returned, so a filtered view does
    not report totals for the whole log.
    """
    return sync.sync_log(
        room_id,
        outcome=outcome,
        vendor=vendor,
        engagement_id=engagement_id,
        needs_manual_update=needs_manual_update,
        limit=limit,
    )


@router.get("/sync-log/{log_id}")
def read_sync_log_entry(log_id: str, sync: EngagementSync = SyncDep) -> dict[str, Any]:
    """One Sync log row, with the request that was sent and every attempt that was made.

    The recorded ``Authorization`` header is redacted where it was written, so this is safe
    to show and safe to export.
    """
    return sync.sync_log_entry(log_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


#: Where the scripted transport draws each line. Every demo room gets its own connector
#: with its own base URL, so the transport can key on the URL and the demo's rows are
#: deterministic rather than dependent on what a hostname answers today.
HUBSPOT_BASE = "https://crm.example/hubspot"
DATAVERSE_BASE = "https://contoso.crm.dynamics.com"
DATAVERSE_COLLISION = "https://crm.example/dataverse-collision"
SALESFORCE_BASE = "https://crm.example/salesforce"
DISABLED_BASE = "https://crm.example/retired-sandbox"

#: The event catalogue the demo ships. The names are the research's own examples, so the
#: demo demonstrates the extensibility claim with the words the claim is made in.
DEMO_EVENT_TYPES: tuple[Mapping[str, Any], ...] = (
    {
        "event_type": "document_viewed",
        "label": "Asset opened",
        "description": "A buyer opened a room asset: a document, a pricing page or a deck.",
        "canonical_fields": ["type", "occurred_at", "asset", "dwell_seconds", "buyer_email"],
        "tracks_dwell": True,
    },
    {
        "event_type": "pricing_viewed",
        "label": "Pricing page viewed",
        "description": "A buyer opened the pricing page rather than a document.",
        "tracks_dwell": True,
    },
    {
        "event_type": "cta_click",
        "label": "CTA answered",
        "description": "A buyer answered a call to action on the room.",
    },
    {
        "event_type": "document_downloaded",
        "label": "Document downloaded",
        "description": "A buyer downloaded a document.",
    },
)

#: The field map for the HubSpot room. The picklist is the researched
#: "picklist label → internal option value" transform, and it is here so the demo can show
#: what an unsupported label does: a finding, and the property left off the wire, with the
#: other four properties still sent.
HUBSPOT_MAP: Mapping[str, Any] = {
    "event_type": "document_viewed",
    "label": "Asset opens onto the contact",
    "fields": [
        {"source": "type", "target": "dsr_engagementtype"},
        {"source": "occurred_at", "target": "dsr_occurredat", "transform": "date.iso8601"},
        {"source": "asset", "target": "dsr_lastasset"},
        {"source": "dwell_seconds", "target": "dsr_dwellseconds", "transform": "number"},
        {
            "source": "buyer_stage",
            "target": "lifecyclestage",
            "transform": "picklist.map",
            "options": {"Discovery": "lead", "Evaluation": "opportunity"},
        },
        {"source": "buyer_email", "target": "email", "transform": "email.normalize"},
    ],
    "sync_key": {"source": "id", "target_property": "dsr_engagement_id"},
}

#: The field map for the Dataverse room, with both researched ``Prefer`` tokens on.
#: ``dsr_buyeremail`` is a plain column, and the demo sends one event whose buyer field is
#: not an address: the mapping flags it softly, ships it anyway, and the CRM refuses it -
#: which is what ``odata.include-annotations`` turns into an explanation.
DATAVERSE_MAP: Mapping[str, Any] = {
    "event_type": "pricing_viewed",
    "label": "Pricing views onto the engagement table",
    "fields": [
        {"source": "type", "target": "dsr_engagementtype"},
        {"source": "occurred_at", "target": "dsr_occurredat", "transform": "date.iso8601"},
        {"source": "asset", "target": "dsr_asset"},
        {"source": "dwell_seconds", "target": "dsr_dwellseconds", "transform": "number"},
        {"source": "buyer_email", "target": "dsr_buyeremail", "transform": "email.normalize"},
    ],
    "sync_key": {"source": "id", "target_property": "dsr_engagementid"},
}

#: The Fabrikam room writes CTA answers to a second Dataverse organisation whose unique
#: sync key is already taken - the researched "marks it unique so the CRM itself rejects
#: collisions", arriving as a 409 a create cannot fix.
COLLISION_MAP: Mapping[str, Any] = {
    "event_type": "cta_click",
    "label": "CTA answers onto the Fabrikam engagement table",
    "fields": [
        {"source": "type", "target": "dsr_engagementtype"},
        {"source": "occurred_at", "target": "dsr_occurredat", "transform": "date.iso8601"},
        {"source": "asset", "target": "dsr_asset"},
        {"source": "buyer_email", "target": "dsr_buyeremail", "transform": "email.normalize"},
    ],
    "sync_key": {"source": "id", "target_property": "dsr_engagementid"},
}

#: Two maps on the Salesforce room, so one connector can show two different answers.
#: ``DSR_Action__c`` is what :class:`DemoTransport` reads to tell them apart, which is what
#: a real CRM does with a picklist: the same endpoint, a different answer per value.
SALESFORCE_MAPS: tuple[Mapping[str, Any], ...] = (
    {
        "event_type": "cta_click",
        "label": "CTA answers onto the custom object",
        "fields": [
            {"source": "type", "target": "DSR_Action__c"},
            {"source": "type", "target": "Name"},
            {"source": "occurred_at", "target": "DSR_Occurred_At__c", "transform": "date.iso8601"},
            {"source": "asset", "target": "DSR_Asset__c"},
            {
                "source": "buyer_email",
                "target": "DSR_Buyer_Email__c",
                "transform": "email.normalize",
            },
        ],
        "sync_key": {"source": "id", "target_property": "DSR_Engagement_Id__c"},
    },
    {
        "event_type": "document_downloaded",
        "label": "Downloads onto the custom object",
        "fields": [
            {"source": "type", "target": "DSR_Action__c"},
            {"source": "occurred_at", "target": "DSR_Occurred_At__c", "transform": "date.iso8601"},
            {"source": "asset", "target": "DSR_Asset__c"},
            {
                "source": "buyer_email",
                "target": "DSR_Buyer_Email__c",
                "transform": "email.normalize",
            },
        ],
        "sync_key": {"source": "id", "target_property": "DSR_Engagement_Id__c"},
    },
)

#: ``(room index, connector, field maps)``. One enabled connector per room, so every event
#: has exactly one CRM to go to and the interesting state is in the *response* rather than
#: in an ambiguity. The disabled sandbox at the end is unscoped, which is the installation
#: default every room falls back to.
DEMO_CONNECTORS: tuple[tuple[int, Mapping[str, Any], tuple[Mapping[str, Any], ...]], ...] = (
    (
        0,
        {
            "vendor": "hubspot",
            "label": "Northwind HubSpot",
            "base_url": HUBSPOT_BASE,
            "object": "contacts",
        },
        (HUBSPOT_MAP,),
    ),
    (
        1,
        {
            "vendor": "dataverse",
            "label": "Contoso engagement table",
            "base_url": DATAVERSE_BASE,
            "entity_set": "dsr_engagements",
            # [sourced] Both preferences the research names for Dataverse: one turns the
            # 204 into a 201 with a body, the other buys enriched error detail.
            "preferences": ["return=representation", "odata.include-annotations"],
        },
        (DATAVERSE_MAP,),
    ),
    (
        2,
        {
            "vendor": "dataverse",
            "label": "Fabrikam engagement table",
            "base_url": DATAVERSE_COLLISION,
            "entity_set": "dsr_engagements",
        },
        (COLLISION_MAP,),
    ),
    (
        3,
        {
            "vendor": "salesforce",
            "label": "Adventure Room_Engagement__c",
            "base_url": SALESFORCE_BASE,
            "object": "Room_Engagement__c",
            # [sourced] The research names `respond-async` as the opt-in that lets a
            # connector return created data. Set here so the connector read explains what
            # it does - and, honestly, what it does not: this build sends the header and has
            # no completion poll.
            "preferences": ["respond-async"],
        },
        SALESFORCE_MAPS,
    ),
    (
        -1,
        {
            "vendor": "salesforce",
            "label": "Retired sandbox, switched off",
            "base_url": DISABLED_BASE,
            "object": "Room_Engagement__c",
            "enabled": False,
        },
        (),
    ),
)


class DemoTransport:
    """A scripted transport, so seeding the demo never opens a socket.

    A real :class:`~dsr.crm_engagement.delivery.UrllibTransport` would try to POST to
    ``crm.example`` from ``backend/seed.py``. This one answers from a fixed script keyed on
    the URL - and, on the Salesforce room, on the ``DSR_Action__c`` value in the body, which
    is how a real CRM tells two picklist values apart on one endpoint.

    Every branch is one of the researched states:

    ===========================  =====================================================
    ``hubspot``                  201 with ``{"id": ...}`` - the sourced HubSpot id, in a body
    ``dataverse``                204 **with** ``OData-EntityId`` and an empty body - the
                                 quoted Dataverse response, where the id is in a header and
                                 a body-only parser finds nothing
    ``dataverse-collision``      409 - the unique sync key rejecting a duplicate
    ``salesforce`` + a CTA       429 then 403 - a retried write that needs a human
    ``salesforce`` + a download  201 with an **empty** body - accepted, with no id anywhere
    anything else                404 - the case no retry count can fix
    ===========================  =====================================================

    The rate limit is counted per URL, so the retry it provokes is a real second attempt
    through the real retry policy rather than a hand-written ``attempts: 2``.
    """

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> CreateResult:
        del headers, timeout
        self.calls[url] = self.calls.get(url, 0) + 1
        attempt = self.calls[url]
        action = _body_field(body, "DSR_Action__c")

        if url.startswith(HUBSPOT_BASE):
            return CreateResult(
                ok=True,
                status=201,
                body=json.dumps({"id": "501", "properties": {"email": "buyer@example"}}),
                duration_ms=12.0,
            )
        if url.startswith(DATAVERSE_BASE):
            # [sourced] "`HTTP/1.1 204 No Content` … `OData-EntityId:
            # [Organization URI]/api/data/v9.2/accounts(00aa00aa-…)`" - the id is in the
            # header and the body is empty, which is the case a body-only parser misses.
            if _body_field(body, "dsr_buyeremail") and "@" not in _body_field(
                body, "dsr_buyeremail"
            ):
                # A value the mapping flagged softly and shipped anyway, refused by the
                # column. With `odata.include-annotations` on, the body says which.
                return CreateResult(
                    ok=False,
                    status=400,
                    body=json.dumps(
                        {
                            "error": {
                                "code": "0x80040220",
                                "message": "The value is not a valid email address.",
                                "annotations": [
                                    {
                                        "message": "dsr_buyeremail: 'procurement (northwind)' is not a valid email address.",
                                        "target": "dsr_buyeremail",
                                        "code": "0x80040220",
                                    }
                                ],
                            }
                        }
                    ),
                    error="HTTP 400",
                    duration_ms=9.0,
                )
            return CreateResult(
                ok=True,
                status=204,
                headers={
                    "OData-EntityId": (
                        f"{DATAVERSE_BASE}/api/data/v9.2/dsr_engagements"
                        f"(00aa00aa-1111-2222-3333-{attempt:012d})"
                    )
                },
                body="",
                duration_ms=15.0,
            )
        if url.startswith(DATAVERSE_COLLISION):
            return CreateResult(
                ok=False,
                status=409,
                body=json.dumps(
                    {
                        "error": {
                            "code": "0x80040220",
                            "message": "A record with the same alternate key already exists.",
                        }
                    }
                ),
                error="HTTP 409",
                duration_ms=7.0,
            )
        if url.startswith(SALESFORCE_BASE):
            if action == "document_downloaded":
                # 201 Created, and nothing in the body. The research does not say where
                # Salesforce returns the id, so this build cannot complete its own step 5
                # and says so rather than marking the event synced.
                return CreateResult(ok=True, status=201, body="", duration_ms=11.0)
            if attempt == 1:
                return CreateResult(
                    ok=False, status=429, body="", error="HTTP 429", duration_ms=6.0
                )
            return CreateResult(
                ok=False,
                status=403,
                body=json.dumps(
                    [
                        {
                            "message": "Insufficient access on the Room_Engagement__c object.",
                            "errorCode": "INSUFFICIENT_ACCESS",
                        }
                    ]
                ),
                error="HTTP 403",
                duration_ms=8.0,
            )
        return CreateResult(
            ok=False, status=404, body="", error=f"HTTP 404 for {url}", duration_ms=3.0
        )


def _body_field(body: bytes, key: str) -> str:
    """One string field out of a create body, or the empty string.

    Only used by the demo transport, to tell two values of the same picklist apart. A real
    CRM does this too, which is why the demo is not reaching into the engine to decide
    anything.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get(key) or "")


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the catalogue, the connectors, the field maps, and real traffic through them.

    Every event is recorded through the real :class:`EngagementSync` over
    :class:`DemoTransport`, so the demo's rows are what this workflow actually produces
    rather than rows written by hand, and seeding never opens a socket.

    It is deliberately mixed, because a demo showing only green teaches a reviewer nothing.
    The states here are the ones the research says a Sync log has to tell apart:

    * a **synced** HubSpot create, id read from the response body;
    * a **synced** HubSpot create where the picklist label is not in the table: the property
      is left off the wire, a finding is recorded, and the other four properties still go -
      the researched "flags unsupported option values before any data is written";
    * a **synced** Dataverse create on a **204**, id read from the ``OData-EntityId``
      header - the researched case a body-only parser cannot handle;
    * a **failed** Dataverse create whose 400 carries OData annotations, which is what the
      connector's ``odata.include-annotations`` preference bought;
    * a **failed** create refused with 409, the unique sync key doing its job;
    * a **retried then failed** write: 429, then a 403 no retry count can fix, so the row
      needs a human and carries a two-entry attempt log;
    * a **failed** create the vendor *accepted* with no id in it, reported as
      ``crm_id_absent`` rather than marked synced - the gap the research admits about
      Salesforce;
    * a **blocked** row for an event type nobody has written a mapping row for, which is
      the state the researched extensibility promise depends on.

    Four further block reasons - ``no_connector``, ``connector_disabled``,
    ``ambiguous_connector``, ``sync_key_unresolved``, and ``buyer_unresolved`` - need
    configuration the core dataset cannot give four rooms at once, so they are covered by
    the suite rather than by the demo. The ``/rooms/{room_id}/readiness`` route reports all
    of them.

    Returns a short description of what was added, which the seeder prints.
    """
    store = RecordStore(db)
    engine = EngagementSync(
        store,
        transport=DemoTransport(),
        # Retries cost nothing here: the script answers on the first attempt for every
        # healthy URL, and a backoff in a seeder would just make `backend/seed.py` slow.
        backoff=0,
        sleep=lambda _seconds: None,
    )
    source = "seed"
    actor = "dana"

    rooms: list[str] = [
        str(room_id)
        for room_id, _account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        return "0 event types, 0 connectors, 0 field maps, 0 events (no rooms to scope them to)"

    for spec in DEMO_EVENT_TYPES:
        engine.book.save_event_type(spec, actor=actor, source=source)

    for index, connector, field_maps in DEMO_CONNECTORS:
        # A negative index is the installation default, unscoped, and it is switched off -
        # so a room with no connector of its own reports `connector_disabled` rather than
        # nothing at all.
        room = rooms[index] if 0 <= index < len(rooms) else None
        row = engine.book.save_connector(
            {**connector, "token": "demo-token-not-real"}, room_id=room, actor=actor, source=source
        )
        for field_map in field_maps:
            engine.book.save_field_map(
                {**field_map, "connector_id": row["id"]}, room_id=room, actor=actor, source=source
            )

    def emit(index: int, payload: Mapping[str, Any]) -> None:
        if 0 <= index < len(rooms):
            engine.record_event(rooms[index], dict(payload), actor=actor, source=source)

    emit(
        0,
        {
            "type": "document_viewed",
            "occurred_at": "2026-09-24T08:14:00+00:00",
            "asset": "Enterprise Overview Deck",
            "dwell_seconds": 248,
            "buyer_email": "Procurement@Northwind.example",
            "buyer_stage": "Evaluation",
        },
    )
    emit(
        0,
        {
            # "Renewal" is not in the picklist table, so the property is omitted and flagged.
            # The create still happens and still succeeds: the research says the room
            # *flags* an unsupported option value, not that it refuses the event.
            "type": "document_viewed",
            "occurred_at": "2026-09-24T08:31:40+00:00",
            "asset": "Security & Compliance Pack",
            "dwell_seconds": 512,
            "buyer_email": "a.buyer@northwind.example",
            "buyer_stage": "Renewal",
        },
    )
    emit(
        0,
        {
            # A CTA answer on a room whose only field map covers asset opens. The researched
            # extensibility rule is a mapping row away, and until it exists this row is
            # recorded, enqueued, and blocked with the reason - not dropped.
            "type": "cta_click",
            "occurred_at": "2026-09-24T09:02:00+00:00",
            "asset": "Book a demo",
            "buyer_email": "a.buyer@northwind.example",
        },
    )
    emit(
        1,
        {
            "type": "pricing_viewed",
            "occurred_at": "2026-09-24T09:02:11+00:00",
            "asset": "Pricing One-Pager",
            "dwell_seconds": 95,
            "buyer_email": "ops@contoso.example",
        },
    )
    emit(
        1,
        {
            # Not an address. The mapping flags it softly, ships it anyway, and the CRM
            # refuses it - which is the case `odata.include-annotations` exists for.
            "type": "pricing_viewed",
            "occurred_at": "2026-09-24T09:40:00+00:00",
            "asset": "Security & Compliance Pack",
            "dwell_seconds": 40,
            "buyer_email": "procurement (contoso)",
        },
    )
    emit(
        2,
        {
            "type": "cta_click",
            "occurred_at": "2026-09-24T10:15:00+00:00",
            "asset": "Book a demo",
            "buyer_email": "ops@fabrikam.example",
        },
    )
    emit(
        3,
        {
            "type": "cta_click",
            "occurred_at": "2026-09-25T07:30:00+00:00",
            "asset": "Request pricing",
            "buyer_email": "lead@adventure.example",
        },
    )
    emit(
        3,
        {
            "type": "document_downloaded",
            "occurred_at": "2026-09-25T08:05:00+00:00",
            "asset": "Security & Compliance Pack",
            "buyer_email": "lead@adventure.example",
        },
    )

    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for room in rooms:
        for row in engine.book.queue_rows(room, limit=1000):
            data = row.get("data") or {}
            state = str(data.get("state") or "pending")
            counts[state] = counts.get(state, 0) + 1
            key = str(data.get("block_reason") or data.get("failure_reason") or "")
            if key:
                reasons[key] = reasons.get(key, 0) + 1

    return (
        f"{len(DEMO_EVENT_TYPES)} event types, {len(DEMO_CONNECTORS)} connectors, "
        f"{sum(len(maps) for _index, _connector, maps in DEMO_CONNECTORS)} field maps, "
        f"{sum(counts.values())} events "
        f"({counts.get('synced', 0)} synced, {counts.get('failed', 0)} failed, "
        f"{counts.get('blocked', 0)} blocked; "
        + ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
        + ")"
    )


__all__ = ["DemoTransport", "EXCEPTION_HANDLERS", "FEATURE", "SyncBook", "router", "seed"]
