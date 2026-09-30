"""WF-043: stream CRM record changes into the room in near real time.

The researched workflow, in full. An operator enables Change Data Capture for
the objects a room cares about and picks a subscription channel; the room's
subscriber client opens a long-lived subscription; each event is checked for its
``changeType``, parked under its ``transactionKey``, and committed to the room's
local replica only when that key changes; the affected buyer's deal panel is
refreshed; and a field the room needs but the change did not touch - the research's
own example is the external ID - is added as an **enriched** field on the channel.

The domain logic is in :mod:`dsr.change_stream`, which this module does not own
and which no other feature could have written into its own path. What lives here
is the three things a workflow has to take out of shared files: the HTTP surface,
the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-043``, and room scoping is real.** The research is
about changes arriving for *a buyer inside a room*, so the event path, the
replica, the buffer, the Live activity panel and the deal panel are all served
under ``/rooms/{room_id}/...``. The channel registry, the org registry and the
Dataverse and HubSpot tables are not room-scoped: "A subscription channel is a
stream of change events that correspond to one or more entities", which is an
org-level object, and putting a room in its key would make the same channel name
registrable twice - which the case-sensitivity rule already permits exactly once
per org.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served
it. A hardcoded string inside a domain method is a defect, and the same class of
bug has shipped in this codebase before: a feature's audit log kept naming a path
the app had stopped serving. ``source`` is a *required* keyword on every writing
method of :class:`~dsr.change_stream.engine.ChangeStreamEngine`, so omitting it is
a ``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.** ``ChangeStreamError`` is the base
of every refusal in :mod:`dsr.change_stream`, and each carries its own ``status``
and ``code`` on the exception, so one handler can answer 400 for an unknown change
type and 409 for one that conflicts with state that already exists without being
told which. It is a domain type, so registering it globally cannot intercept
anything unrelated elsewhere in the product. ``RecordNotFound`` is deliberately
*not* claimed: the core app already maps it to 404, and two handlers for one type
is a collision the host refuses.

**Two ways to read the same event, on purpose.** ``/rooms/{room_id}/events`` is
the strict path: it refuses anything it cannot vouch for, including an update
whose sync key is not in the payload and not in the enriched fields, and the
refusal names the field to add. That is the researched contract, and the room
should be able to apply a change it cannot resolve.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.change_stream import ChangeStreamEngine, ChangeStreamError
from dsr.change_stream import hubspot as hubspot_rules
from dsr.change_stream import vocabulary
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-043-stream-crm-record-changes-into-the-roo",
    "ticket": "WF-043",
    "name": "Stream CRM record changes into the room in near real time",
    "description": (
        "Open a Change Data Capture channel, buffer each change event under its transactionKey "
        "and commit it to the room's replica only when that key changes, and enrich a custom "
        "channel with the unchanged field the room needs to resolve a record. The Dataverse "
        "delta link and the HubSpot workflow webhook sit beside it."
    ),
    "nav": [{"id": "crm-change-stream", "label": "CRM change stream"}],
}

router = APIRouter(prefix="/api/wf-043", tags=["wf043"])


def get_engine(store: RecordStore = StoreDep) -> ChangeStreamEngine:
    """A :class:`ChangeStreamEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and its in-process buffers, and an ``app.state`` entry is
    exactly the edit to the shared ``dsr/api.py`` that the feature host exists to
    make unnecessary. Building it here also leaves the engine a plain object,
    which is what a test constructs.
    """
    return ChangeStreamEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _change_stream_error(request: Request, exc: ChangeStreamError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``ChangeStreamError`` is the base of
    every refusal in :mod:`dsr.change_stream` - a change type outside the four the
    research lists, a channel name already in use on this org, an update event
    whose sync key nothing can resolve, a poll that left off the header Dataverse
    requires - and all of them are the caller's to fix. The status rides on the
    exception rather than being decided here, because an unknown change type and
    a channel that would break other subscribers' isolation are both this
    package's errors and only one of them conflicts with state that already
    exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {ChangeStreamError: _change_stream_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route(engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The four change types, the six fields a change event carries, the standard
    channel and its case rule, the transports and which of them support
    enrichment, the editions that have Change Data Capture, the four query options
    Dataverse refuses under change tracking, the ``ChangeTracking`` annotation,
    HubSpot's cap and its rate-limit exemption, and the 3 MB buffer
    recommendation. A client renders its pickers from this rather than from a list
    compiled into a page, so a value added here reaches every client at once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the six fields of a change event, the four change types,
    the buffering rule, the channel-name case rule, the enrichment isolation rule
    and the enrichment-by-change-type rule, the Pub/Sub flow control, the 3 MB
    buffer recommendation, the Dataverse header, the four refused query options,
    the annotation, and HubSpot's cap and exemption. It does not say what happens
    to the last transaction in a stream, where a buyer comes from, or what an
    unresolvable event should do. Those edges are served here rather than left for
    a reader to reconstruct from a diff, alongside the sourced half so the line
    is visible.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Orgs, and the edition gate
# --------------------------------------------------------------------------- #


@router.get("/orgs")
def list_orgs(engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """The registered CRM orgs, and which of them has Change Data Capture on."""
    listed = engine.orgs()
    return {
        "count": len(listed),
        "cdc_editions": list(vocabulary.CDC_EDITIONS),
        "orgs": [
            {
                "id": record["id"],
                "created_at": record.get("created_at"),
                **(record["data"] or {}),
            }
            for record in listed
        ],
    }


@router.post("/orgs", status_code=201)
def register_org(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Register a connected CRM org, with the edition the gate reads.

    The edition is declared rather than detected. This product holds no OAuth
    connection to a vendor org - that is a different researched workflow - so a
    declared value that is wrong enables nothing that would not have worked
    anyway, and a real org on a lower edition finds out from the vendor.
    """
    return engine.register_org(payload, actor=actor, source=f"POST {router.prefix}/orgs")


@router.get("/orgs/{org_id}")
def read_org(org_id: str, engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """One org, with the gate's own reading of its edition beside it."""
    record = engine.org(org_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"org {org_id} not found")
    return {
        "id": record["id"],
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        **(record["data"] or {}),
        "edition_supports_cdc": vocabulary.edition_supports_cdc(
            (record["data"] or {}).get("edition")
        ),
        "cdc_editions": list(vocabulary.CDC_EDITIONS),
    }


@router.patch("/orgs/{org_id}")
def enable_change_data_capture(
    org_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Enable Change Data Capture for the objects this room cares about.

    The first step of the researched flow, and the researched part is the gate:
    Change Data Capture is available in Enterprise, Performance, Unlimited and
    Developer editions, and an org on anything else is refused with 409 rather
    than flagged. A room that looks configured and streams nothing is worse than a
    room that knows it cannot.

    Turning it *off* is accepted. The research does not describe it, so refusing
    would be this build's rule rather than the vendor's - and an org that stops
    streaming has no reason to keep the flag on.
    """
    return engine.patch_org(
        org_id, payload, actor=actor, source=f"PATCH {router.prefix}/orgs/{{org_id}}"
    )


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


@router.get("/channels")
def list_channels(
    name: str | None = Query(default=None, description="exact, case-sensitive channel name"),
    org_id: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The channel registry, with each channel's enrichment note and field-map findings.

    ``name`` is matched byte for byte, because "The channel name is
    case-sensitive." A client that wants a case-insensitive search should ask for
    the list and fold the names itself: the API folding them on the caller's
    behalf would make a channel that reads as standard behave as custom.
    """
    listed = engine.channels(name=name, org_id=org_id)
    enriched = sum(1 for row in listed if row.get("enriched_fields"))
    return {
        "count": len(listed),
        "with_enrichment": enriched,
        "standard_channel": vocabulary.STANDARD_CHANNEL,
        "channel_name_is_case_sensitive": True,
        "channels": listed,
    }


@router.post("/channels", status_code=201)
def create_channel(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Create a subscription channel, standard or custom, on a CDC-enabled org.

    A channel is "a stream of change events that correspond to one or more
    entities", so it needs entities and a field map. The standard channel is a
    fixed fact rather than a choice: naming ``/data/ChangeEvents`` is what makes a
    channel standard, and ``kind`` cannot contradict the name.

    The buffer defaults to the vendor's recommendation - "We recommend you set the
    buffer size to 3 MB" - and a different size is allowed, because the same
    research calls the sizing tunable. The deviation is reported on the row.
    """
    return engine.create_channel(payload, actor=actor, source=f"POST {router.prefix}/channels")


@router.get("/channels/{channel_id}")
def read_channel(channel_id: str, engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """One channel, with its field-map findings and its enrichment note."""
    channel = engine.channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"channel {channel_id} not found")
    return channel


@router.patch("/channels/{channel_id}")
def patch_channel(
    channel_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Change a channel's entities, field map, or buffer size.

    Not its name and not its kind. The name is the identity the collision check
    and the enrichment-isolation rule both read, so renaming in place would let a
    custom channel become "the standard channel" without passing the check that
    created it. Create a new channel instead.
    """
    return engine.patch_channel(
        channel_id, payload, actor=actor, source=f"PATCH {router.prefix}/channels/{{channel_id}}"
    )


@router.delete("/channels/{channel_id}")
def delete_channel(
    channel_id: str,
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a channel, refusing one a live subscription is reading.

    A soft delete, so the cancellation is audited and the record of what was
    streamed outlives it. Refused while a subscription is open, because that
    subscription would keep committing against a channel this API no longer
    serves and its commits would name something nobody can read.
    """
    return engine.delete_channel(
        channel_id, actor=actor, source=f"DELETE {router.prefix}/channels/{{channel_id}}"
    )


@router.post("/channels/{channel_id}/enrichment")
def add_enrichment(
    channel_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Add enriched fields to a channel. User-flow step five, and the researched fix.

    "If the room needs an unchanged field (e.g. the external ID) to resolve the
    record, that field is added as an enriched field on the channel." Two
    refusals, both sourced:

    * **the standard channel**, with 409. The research recommends against it
      because "other subscribers that receive change events on the standard
      channel don't receive unchanged fields that they don't expect" - harm to
      parties this product cannot enumerate and therefore cannot weigh.
    * **a transport outside Pub/Sub, CometD and event relays**, with 400, because
      that list is closed in the research.
    """
    fields = payload.get("fields")
    if fields is None:
        fields = payload.get("field") or payload.get("crm_field")
    return engine.enrich_channel(
        channel_id,
        fields,
        actor=actor,
        source=f"POST {router.prefix}/channels/{{channel_id}}/enrichment",
    )


@router.delete("/channels/{channel_id}/enrichment/{field}")
def remove_enrichment(
    channel_id: str,
    field: str,
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Drop one enriched field from a channel.

    Allowed, because a room giving up the ability to resolve a record is a room's
    own decision about its own channel. What it may not do is leave the sync key
    unresolvable while still claiming to stream update events, and that is caught
    at commit time with the field named - which is the error a room can act on.
    """
    return engine.unenrich_channel(
        channel_id,
        field,
        actor=actor,
        source=f"DELETE {router.prefix}/channels/{{channel_id}}/enrichment/{{field}}",
    )


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


@router.get("/subscriptions")
def list_subscriptions(
    room_id: str | None = Query(default=None),
    channel_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="open | closed"),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The long-lived subscriptions, each with its wire format and its buffer.

    The transport decides the wire format the research names - Avro for Pub/Sub,
    JSON for CometD - and a transport the research does not name reports ``null``
    rather than a guess.
    """
    listed = engine.subscriptions(room_id=room_id, channel_id=channel_id, state=state)
    return {
        "count": len(listed),
        "open": sum(1 for row in listed if row.get("state") == "open"),
        "subscriptions": listed,
    }


@router.post("/rooms/{room_id}/subscriptions", status_code=201)
def open_subscription(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Open a long-lived subscription on a channel, for one room.

    "The room's subscriber client opens a long-lived subscription (Pub/Sub API, or
    a delta-link poll loop)." It starts with nothing outstanding, because the
    research publishes no default fetch size and the flow control is the client's
    to set - so the first thing a client does is send a FetchRequest.

    The transport cannot differ from the channel's. It decides whether enrichment
    is available, so a subscription that could pick a different one would have had
    the enrichment rule checked against a transport the events never arrive on.
    """
    return engine.open_subscription(
        payload, room_id=room_id, actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/subscriptions",
    )


@router.get("/subscriptions/{subscription_id}")
def read_subscription(subscription_id: str, engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """One subscription, with what is parked under it right now."""
    subscription = engine.subscription(subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail=f"subscription {subscription_id} not found")
    return subscription


@router.post("/subscriptions/{subscription_id}/fetch")
def fetch_more(
    subscription_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Record a FetchRequest: how many more events the client is asking for.

    "The client can control the flow of events received by setting the number of
    requested events in the FetchRequest parameter." The vendor's own ``numEvents``
    spelling is accepted as well, so a client written against the RPC shape works
    unchanged. Requested and delivered are counted apart, so a subscriber that
    asked for 100 and the stream delivered 12 shows as 88 outstanding rather than
    as nothing.
    """
    return engine.fetch(
        subscription_id, payload, actor=actor,
        source=f"POST {router.prefix}/subscriptions/{{subscription_id}}/fetch",
    )


@router.post("/subscriptions/{subscription_id}/close")
def close_subscription(
    subscription_id: str,
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Close a subscription, flushing whatever transaction is still parked.

    The drain the commit rule leaves open. "Only commits to the room's local
    replica when the key changes", so the last transaction in a stream has no key
    change coming to commit it, and dropping it would silently lose a change -
    the one outcome a near-real-time promise must not produce.

    The response carries what the flush committed, so a client that closes a
    subscription does not have to re-read the replica to find out.
    """
    return engine.close_subscription(
        subscription_id, actor=actor,
        source=f"POST {router.prefix}/subscriptions/{{subscription_id}}/close",
    )


# --------------------------------------------------------------------------- #
# The event path, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/events")
def list_events(
    room_id: str,
    change_type: str | None = Query(default=None, description="CREATE | UPDATE | DELETE | UNDELETE"),
    transaction_key: str | None = Query(default=None),
    state: str | None = Query(default=None, description="buffered | committed"),
    entity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The room's Live activity / Realtime panel, newest first.

    ``state=buffered`` is the events that have arrived but not reached the
    replica, and ``state=committed`` is the ones that have. Every filter is a JSON
    path in the event's own payload, resolved through the dynamic index, so a
    field a team added later is queryable without a change to this route.
    """
    listed = engine.events(
        room_id=room_id,
        change_type=change_type,
        transaction_key=transaction_key,
        state=state,
        entity=entity,
        limit=limit,
    )
    return {
        "room_id": room_id,
        "count": len(listed),
        "buffered": sum(1 for row in listed if row.get("state") == "buffered"),
        "committed": sum(1 for row in listed if row.get("state") == "committed"),
        "events": listed,
    }


@router.post("/rooms/{room_id}/events", status_code=201)
def deliver_event(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """One change event, from the room's subscriber client.

    The whole data flow in one route: deserialise the event, park it under its
    ``transactionKey``, and commit whatever transaction that arrival closed. The
    commit is applied before the response is written, so a client that reads the
    replica straight after sees the transaction its event triggered.

    Two refusals here are the research's own edge cases rather than pedantry:

    * **no FetchRequest outstanding**, 409. The ``Subscribe`` method uses
      bidirectional streaming so the client controls the flow, and an event
      delivered with nothing requested is one it was not authorised to receive.
    * **an unresolvable record**, 409 ``enrichment_required``, naming the field.
      An update event carries only what changed, so without the sync key in the
      payload *or* in the enriched fields the room cannot tell what changed -
      which is exactly the case event enrichment exists for.

    A sequence number already parked under the same key answers 200 with
    ``outcome: "duplicate"`` rather than 201. The event is not applied twice, and
    201 would tell the client a new change was recorded when nothing was.
    """
    result = engine.deliver_event(
        payload, room_id=room_id, actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/events",
    )
    if result["outcome"] == "duplicate" and response is not None:
        response.status_code = 200
    return result


@router.get("/rooms/{room_id}/buffer")
def read_buffer(
    room_id: str,
    subscription_id: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """What is parked right now, and why it has not committed.

    An empty buffer is the healthy idle state rather than a symptom: a change is
    only committed by the arrival of a change under a different key, so a stream
    that is merely waiting for the next transaction legitimately has nothing
    parked and something in flight.
    """
    return engine.buffer_view(room_id=room_id, subscription_id=subscription_id)


@router.get("/rooms/{room_id}/replica")
def read_replica(
    room_id: str,
    state: str | None = Query(default=None, description="live | deleted"),
    account: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The room's local replica, as the buyer's deal panel reads it.

    ``state=deleted`` is the tombstones. A delete commits a tombstone rather than
    removing the row, because the research guarantees undelete events exist and a
    replica with no row to match one against could only guess. A room asking which
    records the CRM has taken away is asking exactly this.
    """
    rows = engine.replica(room_id=room_id, state=state, account=account, limit=limit)
    return {
        "room_id": room_id,
        "count": len(rows),
        "live": sum(1 for row in rows if row["state"] == "live"),
        "deleted": sum(1 for row in rows if row["state"] == "deleted"),
        "replica": rows,
    }


@router.get("/rooms/{room_id}/replica/{external_id}")
def read_replica_row(
    room_id: str,
    external_id: str,
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """One replica row, by the CRM record's external id."""
    row = engine.replica_row(room_id=room_id, external_id=external_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"no replica row for {external_id} in room {room_id}"
        )
    return row


@router.get("/rooms/{room_id}/deal-panel")
def read_deal_panel(room_id: str, engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """The buyer's deal panel: the replica grouped by the account that owns it.

    "The room refreshes the affected buyer's deal panel" is the whole of what the
    research says a panel is, so this is a projection of the replica grouped by
    account rather than a second model that could disagree with the first. A
    record with no account is listed under its own external id rather than
    dropped, so a channel whose field map declares no ``account_field`` still
    produces a panel with something in it.
    """
    return engine.deal_panel(room_id=room_id)


@router.get("/rooms/{room_id}/invalidations")
def read_invalidations(
    room_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The panel refreshes each commit caused, newest first.

    One per replica write, carrying the record, the change type, and the account
    when the channel's field map resolves one. Where it does not, the row says
    ``resolved: false`` and the reason - the research says the room refreshes the
    affected buyer's panel and not where the buyer comes from, and this API would
    rather show the gap than guess a rep's buyer.
    """
    listed = engine.invalidations(room_id=room_id, limit=limit)
    return {
        "room_id": room_id,
        "count": len(listed),
        "resolved": sum(1 for row in listed if row.get("resolved")),
        "unresolved": sum(1 for row in listed if not row.get("resolved")),
        "invalidations": listed,
    }


# --------------------------------------------------------------------------- #
# Dataverse: change tracking and the delta link
# --------------------------------------------------------------------------- #


@router.get("/dataverse/tables")
def list_tables(engine: ChangeStreamEngine = EngineDep) -> dict[str, Any]:
    """The declared Dataverse tables and whether each one is tracking changes."""
    listed = engine.tables()
    return {
        "count": len(listed),
        "tracking": sum(1 for row in listed if row.get("track_changes")),
        "preference": vocabulary.CHANGE_TRACKING_PREFERENCE,
        "annotation": vocabulary.CHANGE_TRACKING_ANNOTATION,
        "change_tracking_is_irreversible": True,
        "tables": listed,
    }


@router.post("/dataverse/tables", status_code=201)
def declare_table(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a Dataverse table. Change tracking starts off.

    Off, because turning it on is a step with an irreversible consequence and a
    table that never took that step should not be assumed to have.
    """
    return engine.declare_table(payload, actor=actor, source=f"POST {router.prefix}/dataverse/tables")


@router.post("/dataverse/tables/{table_id}/track-changes")
def enable_track_changes(
    table_id: str,
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Turn the Track changes table property on. Idempotent.

    The researched path for this is the Power Apps table property - "select Data
    > Tables and the specific table. Under Advanced options, you find the Track
    changes property" - and this is the HTTP spelling of the same thing. A second
    call is a no-op with ``already_enabled: true`` rather than an error, because a
    room that cannot tell "already on" from "just turned on" will not call it
    twice.
    """
    return engine.enable_track_changes(
        table_id, actor=actor,
        source=f"POST {router.prefix}/dataverse/tables/{{table_id}}/track-changes",
    )


@router.delete("/dataverse/tables/{table_id}/track-changes")
def disable_track_changes(
    table_id: str,
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Always refused: "After you enable change tracking for a table, you can't disable it."

    The one genuinely one-way rule in this workflow. Refused whatever the table's
    current state, including "it was never on", because a caller reaching for the
    off switch needs the same answer either way - and because a room that
    believed it had turned the property off would keep polling a delta link it
    thinks it abandoned.
    """
    return engine.disable_track_changes(
        table_id, actor=actor,
        source=f"DELETE {router.prefix}/dataverse/tables/{{table_id}}/track-changes",
    )


@router.post("/dataverse/tables/{table_id}/poll")
def poll_table(
    table_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """One delta-link poll, in the vendor's own response shape.

    Three rules, all sourced, all refusals rather than adjustments:

    * the ``Prefer: odata.track-changes`` header is required, because that is
      what "requests that a delta link is returned" - a poll without it is a
      plain read and gets no delta link;
    * ``$filter``, ``$orderby``, ``$expand`` and ``$top`` are refused with the
      vendor's own message, because "you get an error message: The
      "${filter|orderby|expand|top}" query parameter isn't supported when Change
      Tracking is enabled." ``$select`` is not among them - the research's own
      example poll carries it;
    * the table has to be tracking, or there is no delta link to advance.

    The response carries the ``@odata.deltaLink``, the returned ``deltatoken``,
    and the ``ChangeTracking`` annotation a tracked entity set has.
    """
    return engine.poll_table(
        table_id, payload, actor=actor,
        source=f"POST {router.prefix}/dataverse/tables/{{table_id}}/poll",
    )


@router.get("/dataverse/tables/{table_id}/count")
def count_table_changes(
    table_id: str,
    deltatoken: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """``GET /accounts/$count?$deltatoken=…`` - changes committed since a token.

    Counted over the changes the room has actually committed. It says nothing
    about the vendor's unconsumed backlog behind the delta link, which this API
    cannot see, and the response says so rather than implying the number is
    complete.
    """
    return engine.count_table(table_id, deltatoken=deltatoken)


# --------------------------------------------------------------------------- #
# HubSpot: the workflow webhook, the cap, and the rate-limit fact
# --------------------------------------------------------------------------- #


@router.get("/hubspot/subscriptions")
def list_hubspot_subscriptions(
    room_id: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The room's HubSpot webhook targets, and how close the app is to its cap.

    "You can create up to 1,000 webhook subscriptions per app." The count is over
    the live subscriptions, so a cancelled one frees its slot - the vendor's
    sentence caps how many exist, not how many were ever made.
    """
    listed = engine.hubspot_subscriptions(room_id=room_id)
    return {**engine.hubspot_capacity(), "subscriptions": listed, "count": len(listed)}


@router.post("/hubspot/subscriptions", status_code=201)
def register_hubspot_subscription(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Register a workflow webhook target. The researched seam and nothing wider.

    "Webhooks can be triggered as an action in any workflow, so you can use any
    workflow starting conditions as the criteria." The criteria belong to the
    HubSpot workflow, not to this room, and the row says so.

    There is deliberately **no** subscription REST surface for HubSpot. The
    research records that the webhook subscriptions documentation is
    client-rendered and its body could not be read, so the endpoints and methods
    are not claimed and are not built.
    """
    return engine.register_hubspot_subscription(
        payload, room_id=room_id, actor=actor,
        source=f"POST {router.prefix}/hubspot/subscriptions",
    )


@router.delete("/hubspot/subscriptions/{subscription_id}")
def delete_hubspot_subscription(
    subscription_id: str,
    actor: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Cancel a webhook target, freeing its slot against the researched cap."""
    return engine.delete_hubspot_subscription(
        subscription_id, actor=actor,
        source=f"DELETE {router.prefix}/hubspot/subscriptions/{{subscription_id}}",
    )


@router.get("/hubspot/usage")
def hubspot_usage(
    org_id: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """The rate-limit fact, with the budget it is an exemption from.

    "Webhook calls made via workflows do not count towards the API rate limit."
    The research states the exemption and no limit, so a budget is declared here
    and the exempt calls are counted beside the charged ones - a report showing
    only the remaining budget would make the exemption invisible, and an invisible
    exemption cannot be audited.
    """
    return engine.hubspot_usage(org_id=org_id)


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #


@router.get("/usage")
def usage(
    room_id: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Delivery usage, as the research's ``PlatformEventUsageMetric``.

    Requested and delivered counted apart, the buffer's high-water mark against
    the vendor's 3 MB recommendation, and how many parked transactions reached the
    replica. The research names the metric and publishes no fields, so the shape
    is this build's and the response says so.
    """
    return engine.usage(room_id=room_id)


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: ChangeStreamEngine = EngineDep,
) -> dict[str, Any]:
    """Counts for the room: events by change type, replica rows by state, what is parked."""
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The field map the demo channels use, and the researched reason for the sync
#: key: "If the room needs an unchanged field (e.g. the external ID) to resolve
#: the record, that field is added as an enriched field on the channel."
DEMO_FIELD_MAP: dict[str, Any] = {
    "sync_key": "External_Id__c",
    "account_field": "Account_Name__c",
    "fields": {
        "External_Id__c": "external_id",
        "StageName": "stage",
        "Amount": "amount",
        "CloseDate": "close_date",
        "Probability": "probability",
        "Owner_Name__c": "owner",
    },
}

#: A second field map with no ``account_field``, so the demo contains a channel
#: whose commits refresh the deal panel without naming a buyer. That is the
#: research's silence made into a row: "The room refreshes the affected buyer's
#: deal panel" and nothing about where the buyer comes from.
DEMO_FIELD_MAP_UNATTRIBUTED: dict[str, Any] = {
    "sync_key": "Contact_External_Id__c",
    "fields": {
        "Contact_External_Id__c": "external_id",
        "Title": "title",
        "Email": "email",
    },
}

#: The change types the demo exercises, in the order the buffer parks them.
DEMO_ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("dsr-opp-1001", "Northwind Traders", "Negotiation"),
    ("dsr-opp-1002", "Contoso Health", "Discovery"),
    ("dsr-opp-1003", "Fabrikam Logistics", "Proposal"),
    ("dsr-ctc-2001", "Adventure Works", "Nurture"),
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Two orgs, four channels, twelve change events, and the states that are not green.

    The rows are produced by running the real
    :class:`~dsr.change_stream.engine.ChangeStreamEngine`, so the demo cannot show
    a shape this workflow would not produce, and seeding never opens a socket. It
    is deliberately mixed, because a demo of only success teaches a reviewer
    nothing:

    * a **standard channel** with enrichment refused, and a **custom** channel that
      has the external ID enriched, so both branches of the isolation rule are
      rows rather than claims;
    * a channel whose field map declares **no** ``account_field``, so a commit
      refreshes the panel without naming a buyer and the invalidation says
      ``resolved: false``;
    * an **unresolvable update** - the sync key in neither the payload nor the
      enriched fields - refused with 409 and the field to add named;
    * a **delete** that commits a tombstone and an **undelete** that brings it
      back, so the two change types that the research says fire together are both
      visible;
    * a **duplicate sequence number** in one transaction, refused rather than
      applied twice;
    * a **sequence gap** inside a transaction, reported and not repaired;
    * a **Professional-edition** org, so the edition gate is a row;
    * a Dataverse table with change tracking **on** and one still off, so the
      irreversible property and its remedy are both visible;
    * HubSpot webhook subscriptions and their rate-limit exemption.
    """
    store = RecordStore(db)
    engine = ChangeStreamEngine(store)
    rng: random.Random = context.get("rng") or random.Random("wf043")
    # ``backend/seed.py`` passes ``[(room_id, account), ...]``. A bare id is
    # accepted too, because a caller assembling a context by hand should not have
    # to know the tuple shape to seed a feature.
    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    source = "seed"
    actor = "dana"

    def at(minutes_ago: int) -> str:
        return (base - timedelta(minutes=minutes_ago)).isoformat()

    def stamp(micro: int) -> str:
        """A sequence number, drawn from the seeder's own generator."""
        return str(1000 + micro)

    # -- the org, and the gate ------------------------------------------- #
    org = engine.register_org(
        {
            "system": "salesforce",
            "edition": "Unlimited",
            "org_name": "Northwind Production",
        },
        actor=actor,
        source=source,
    )
    engine.patch_org(
        org["id"],
        {"cdc_enabled": True, "entities": ["Opportunity", "Contact"]},
        actor=actor,
        source=source,
    )
    # A lower edition, kept precisely so the gate has something to refuse. The
    # refusal is caught rather than raised, because a seed that aborted here
    # would leave the demo with no rooms to show anything in.
    professional = engine.register_org(
        {"system": "dataverse", "edition": "Professional", "org_name": "Contoso Sandbox"},
        actor=actor,
        source=source,
    )
    edition_refused = "not attempted"
    try:
        engine.patch_org(
            professional["id"],
            {"cdc_enabled": True, "entities": ["account"]},
            actor=actor,
            source=source,
        )
    except ChangeStreamError as exc:
        edition_refused = exc.code

    # -- the channels --------------------------------------------------- #
    standard = engine.create_channel(
        {
            "name": vocabulary.STANDARD_CHANNEL,
            "org_id": org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": DEMO_FIELD_MAP,
        },
        actor=actor,
        source=source,
    )
    opportunities = engine.create_channel(
        {
            "name": "/data/dsrOpportunities",
            "org_id": org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": DEMO_FIELD_MAP,
        },
        actor=actor,
        source=source,
    )
    # Enriched, which is the researched fix for an update that carries only what
    # changed. This is the channel that commits.
    engine.enrich_channel(
        opportunities["id"], ["External_Id__c", "Account_Name__c"], actor=actor, source=source
    )
    # A channel with a buffer the vendor would not recommend, which the research
    # calls tunable. Permitted, and reported.
    cometd = engine.create_channel(
        {
            "name": "/data/dsrDealsCometD",
            "org_id": org["id"],
            "entities": ["Opportunity"],
            "transport": "cometd",
            "field_map": DEMO_FIELD_MAP,
            "buffer_bytes": 1 * 1024 * 1024,
        },
        actor=actor,
        source=source,
    )
    engine.enrich_channel(cometd["id"], "External_Id__c", actor=actor, source=source)
    # No account_field, so this channel's commits refresh the panel without
    # naming a buyer.
    contacts = engine.create_channel(
        {
            "name": "/data/dsrContacts",
            "org_id": org["id"],
            "entities": ["Contact"],
            "transport": "relay",
            "field_map": DEMO_FIELD_MAP_UNATTRIBUTED,
        },
        actor=actor,
        source=source,
    )
    engine.enrich_channel(contacts["id"], "Contact_External_Id__c", actor=actor, source=source)

    # The two refusals, made visible.
    enrichment_refused_on_standard = "not attempted"
    try:
        engine.enrich_channel(standard["id"], "External_Id__c", actor=actor, source=source)
    except ChangeStreamError as exc:
        enrichment_refused_on_standard = exc.code

    if not rooms:
        return (
            f"2 orgs (1 on an edition without Change Data Capture, enable refused: "
            f"{edition_refused}), "
            f"{len(engine.channels())} channels "
            f"(1 standard, enrichment {enrichment_refused_on_standard} on it), "
            "0 events (no rooms to scope them to)"
        )

    room_id, _account = rooms[0]
    other_room = rooms[1][0] if len(rooms) > 1 else rooms[0][0]
    # Reported rather than assumed: ``backend/seed.py`` passes four rooms, but a
    # caller assembling a context by hand may pass one, and a summary that claims
    # "2 rooms" when there is one is the kind of small lie a reviewer stops
    # trusting the whole string for.
    room_count = len({room_id, other_room})

    # -- the four subscriptions ------------------------------------------ #
    sub = engine.open_subscription(
        {"channel_id": opportunities["id"]}, room_id=room_id, actor=actor, source=source
    )
    engine.fetch(sub["id"], {"num_requested": 24}, actor=actor, source=source)
    cometd_sub = engine.open_subscription(
        {"channel_id": cometd["id"]}, room_id=other_room, actor=actor, source=source
    )
    engine.fetch(cometd_sub["id"], {"numEvents": 6}, actor=actor, source=source)
    contact_sub = engine.open_subscription(
        {"channel_id": contacts["id"]}, room_id=room_id, actor=actor, source=source
    )
    engine.fetch(contact_sub["id"], {"num_requested": 4}, actor=actor, source=source)
    # On the standard channel, which by rule cannot be enriched. Left open at the
    # end of this seed, with a transaction parked that nothing can commit until
    # the room moves to a custom channel.
    standard_sub = engine.open_subscription(
        {"channel_id": standard["id"]}, room_id=other_room, actor=actor, source=source
    )
    engine.fetch(standard_sub["id"], {"num_requested": 4}, actor=actor, source=source)

    delivered: list[dict[str, Any]] = []

    def send(
        target_sub: dict[str, Any],
        target_room: str,
        *,
        change_type: str,
        transaction: str,
        sequence: int,
        minutes_ago: int,
        payload: dict[str, Any],
        changed: list[str] | None,
        enriched: dict[str, Any] | None = None,
        entity: str | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "subscription_id": target_sub["id"],
            "changeType": change_type,
            "transactionKey": transaction,
            "sequenceNumber": stamp(sequence),
            "commitTimestamp": at(minutes_ago),
            "payload": payload,
        }
        if changed is not None:
            event["changedFields"] = changed
        if enriched:
            event["enrichedFields"] = enriched
        if entity:
            event["entity"] = entity
        result = engine.deliver_event(event, room_id=target_room, actor=actor, source=source)
        delivered.append(result)
        return result

    # Unpacked in the same order the tuples are written: (external id, account,
    # stage). Getting that order wrong is invisible in the return string and
    # shows up as a deal panel grouped by external id, which is the sort of thing
    # a demo that only ever shows green would have shipped.
    external, account, stage = DEMO_ACCOUNTS[0]
    second_external, account_two, _stage_two = DEMO_ACCOUNTS[1]
    third_external, account_three, _stage_three = DEMO_ACCOUNTS[2]
    contact_external, _contact_account, _contact_stage = DEMO_ACCOUNTS[3]

    # 1. A create, then two updates in the same transaction. The create carries
    #    every populated field, so it needs no enrichment; the updates carry only
    #    what changed, and the sync key arrives enriched - which is the whole
    #    reason the channel has enrichment on it.
    send(
        sub, room_id,
        change_type="CREATE", transaction="txn-1001", sequence=1, minutes_ago=42,
        payload={
            "External_Id__c": external, "Account_Name__c": account, "StageName": "Qualification",
            "Amount": 18000, "CloseDate": "2026-12-15", "Probability": 25, "Owner_Name__c": "dana",
        },
        changed=None, entity="Opportunity",
    )
    send(
        sub, room_id,
        change_type="UPDATE", transaction="txn-1001", sequence=2, minutes_ago=41,
        payload={"StageName": stage},
        changed=["StageName"], enriched={"External_Id__c": external, "Account_Name__c": account},
        entity="Opportunity",
    )
    send(
        sub, room_id,
        change_type="UPDATE", transaction="txn-1001", sequence=4, minutes_ago=40,
        payload={"Amount": 48000, "CloseDate": "2026-11-30"},
        changed=["Amount", "CloseDate"],
        enriched={"External_Id__c": external, "Account_Name__c": account},
        entity="Opportunity",
    )
    # 2. A new key commits the parked transaction. This is the researched rule
    #    being visible: two updates to one record, applied as a unit, in
    #    sequence order, with a gap in the sequence numbers reported.
    send(
        sub, room_id,
        change_type="UPDATE", transaction="txn-1002", sequence=5, minutes_ago=33,
        payload={"Amount": 27500},
        changed=["Amount"],
        enriched={"External_Id__c": second_external, "Account_Name__c": account_two},
        entity="Opportunity",
    )

    # 3. A delete, then an undelete. Both fire, per the research, and the
    #    tombstone is what lets the undelete find the row again.
    send(
        sub, room_id,
        change_type="DELETE", transaction="txn-1003", sequence=6, minutes_ago=28,
        payload={},
        changed=None,
        enriched={"External_Id__c": second_external},
        entity="Opportunity",
    )
    send(
        sub, room_id,
        change_type="CREATE", transaction="txn-1004", sequence=7, minutes_ago=21,
        payload={
            "External_Id__c": third_external, "Account_Name__c": account_three,
            "StageName": "Proposal", "Amount": 9200, "CloseDate": "2027-01-31",
            "Probability": 40, "Owner_Name__c": "sam",
        },
        changed=None, entity="Opportunity",
    )
    send(
        sub, room_id,
        change_type="UNDELETE", transaction="txn-1005", sequence=8, minutes_ago=14,
        payload={
            "External_Id__c": second_external, "Account_Name__c": account_two,
            "StageName": "Discovery", "Amount": 27500,
        },
        changed=None, entity="Opportunity",
    )

    # 4. A duplicate sequence number in one transaction. Refused, not applied
    #    twice, and the research says nothing about at-least-once delivery, so
    #    this is reported rather than treated as a redelivery. Both sends happen;
    #    the second is the one that is refused.
    send(
        sub, room_id,
        change_type="UPDATE", transaction="txn-1006", sequence=9, minutes_ago=9,
        payload={"Probability": 55},
        changed=["Probability"],
        enriched={"External_Id__c": external, "Account_Name__c": account},
        entity="Opportunity",
    )
    duplicate = send(
        sub, room_id,
        change_type="UPDATE", transaction="txn-1006", sequence=9, minutes_ago=9,
        payload={"Probability": 99},
        changed=["Probability"],
        enriched={"External_Id__c": external, "Account_Name__c": account},
        entity="Opportunity",
    )

    # 5. A delete that is never undeleted, so the demo holds a tombstone as well
    #    as a restored one. "Changes include ... deletion of a record, and
    #    undeletion of a record" - the research promises both, and a room needs
    #    to be able to see which records the CRM has taken away.
    send(
        sub, room_id,
        change_type="DELETE", transaction="txn-1007", sequence=10, minutes_ago=5,
        payload={}, changed=None,
        enriched={"External_Id__c": third_external, "Account_Name__c": account_three},
        entity="Opportunity",
    )

    # 6. The researched unresolvable case, on the one channel that cannot be
    #    fixed by adding an enriched field: the standard channel. An update
    #    carrying only StageName cannot say which record it changed, enrichment
    #    is refused on the standard channel, and the refusal names the field and
    #    the custom channel to put it on.
    #
    #    It surfaces when the *next* key arrives, because the commit rule commits
    #    a transaction when a new key closes it - which is the researched rule
    #    being visible rather than a defect. The transaction stays parked.
    unresolvable = "not attempted"
    send(
        standard_sub, other_room,
        change_type="UPDATE", transaction="txn-4001", sequence=1, minutes_ago=8,
        payload={"StageName": "Closed Won"}, changed=["StageName"], entity="Opportunity",
    )
    try:
        send(
            standard_sub, other_room,
            change_type="UPDATE", transaction="txn-4002", sequence=2, minutes_ago=7,
            payload={"Amount": 51000},
            changed=["Amount"],
            enriched={"External_Id__c": second_external},
            entity="Opportunity",
        )
    except ChangeStreamError as exc:
        unresolvable = exc.code

    # And the close of that subscription is refused for the same reason, with
    # the transaction still parked - the state a room reaches when the fix is
    # not "add a field" and it needs a different channel.
    close_refused = "not attempted"
    try:
        engine.close_subscription(standard_sub["id"], actor=actor, source=source)
    except ChangeStreamError as exc:
        close_refused = exc.code

    # 7. A second room over CometD, so the room scoping and the JSON wire format
    #    are rows. JSON: this is the transport the research names for CometD.
    send(
        cometd_sub, other_room,
        change_type="CREATE", transaction="txn-2001", sequence=1, minutes_ago=51,
        payload={
            "External_Id__c": second_external, "Account_Name__c": account_two,
            "StageName": "Discovery", "Amount": 41000, "Owner_Name__c": "sam",
        },
        changed=None, entity="Opportunity",
    )

    # 8. Contacts over an event relay, with no account_field on the map: the
    #    commits refresh the panel and cannot say which buyer's.
    send(
        contact_sub, room_id,
        change_type="CREATE", transaction="txn-3001", sequence=1, minutes_ago=37,
        payload={
            "Contact_External_Id__c": contact_external,
            "Title": "VP Security",
            "Email": "lead@adventure.example",
        },
        changed=None, entity="Contact",
    )
    send(
        contact_sub, room_id,
        change_type="UPDATE", transaction="txn-3002", sequence=2, minutes_ago=30,
        payload={"Title": "CISO"},
        changed=["Title"], enriched={"Contact_External_Id__c": contact_external},
        entity="Contact",
    )

    # 9. Closing the first subscription flushes the last parked transaction -
    #    the research's commit rule has no terminal case, and this is the drain.
    closed = engine.close_subscription(sub["id"], actor=actor, source=source)
    flushed = len(closed["flushed"])

    # -- Dataverse ------------------------------------------------------- #
    accounts_table = engine.declare_table(
        {"logical_name": "account", "entity_set": "accounts"}, actor=actor, source=source
    )
    contacts_table = engine.declare_table(
        {"logical_name": "contact", "entity_set": "contacts"}, actor=actor, source=source
    )
    engine.enable_track_changes(accounts_table["id"], actor=actor, source=source)
    # Enabling twice is allowed and reports already_enabled, so the second call
    # is made on purpose: "already on" and "just turned on" have to be
    # distinguishable or a room will not call it twice.
    repeated = engine.enable_track_changes(accounts_table["id"], actor=actor, source=source)
    engine.poll_table(
        accounts_table["id"],
        {
            "prefer": vocabulary.CHANGE_TRACKING_PREFERENCE,
            "options": {"select": "accountid, name"},
            "changes_observed": 4,
        },
        actor=actor,
        source=source,
    )
    refused_query_option = "not attempted"
    try:
        engine.poll_table(
            accounts_table["id"],
            {"prefer": vocabulary.CHANGE_TRACKING_PREFERENCE, "options": {"top": 50}},
            actor=actor,
            source=source,
        )
    except ChangeStreamError as exc:
        refused_query_option = exc.code
    irreversible = "not attempted"
    try:
        engine.disable_track_changes(accounts_table["id"], actor=actor, source=source)
    except ChangeStreamError as exc:
        irreversible = exc.code

    # -- HubSpot --------------------------------------------------------- #
    hooks: list[dict[str, Any]] = []
    charges: list[dict[str, Any]] = []
    for index, (workflow, hook) in enumerate(
        (
            ("wf-8841", "stage-advanced"),
            ("wf-8842", "close-date-moved"),
            ("wf-9007", "contact-enrolled"),
        )
    ):
        hooks.append(
            engine.register_hubspot_subscription(
                {
                    "target_url": f"https://hooks.example.invalid/{hook}",
                    "workflow_id": workflow,
                    "workflow_name": hook.replace("-", " ").title(),
                },
                room_id=room_id if index < 2 else other_room,
                actor=actor,
                source=source,
            )
        )
        # One workflow-enrolled call each, so the exemption the research states is
        # a number in the demo rather than a sentence in a docstring. The count
        # of exempt calls is reported beside the budget precisely so the
        # exemption is auditable.
        charges.append(
            engine.charge_hubspot_call(hooks[-1]["id"], via_workflow=True, actor=actor, source=source)
        )

    usage_report = engine.usage()
    panel = engine.deal_panel(room_id=room_id)
    unresolved_invalidations = sum(1 for row in engine.invalidations(room_id=room_id)
                                   if not row.get("resolved"))

    return (
        f"2 orgs (1 on an edition without Change Data Capture: "
        f"{professional['data']['edition']}, enable refused: {edition_refused}), "
        f"{len(engine.channels())} channels (1 standard, enrichment {enrichment_refused_on_standard} "
        f"on it; 1 with a non-recommended {cometd['buffer_bytes'] // (1024 * 1024)} MB buffer; "
        f"1 with no account_field), "
        f"{len(delivered)} change events across {room_count} room(s) "
        f"({sum(1 for r in delivered if r['outcome'] == 'duplicate')} duplicate refused), "
        f"1 commit refused: {unresolvable}, and its close refused: {close_refused}, "
        f"{flushed} transaction flushed on close, "
        f"{panel['count']} replica rows ({panel['deleted']} tombstoned), "
        f"{unresolved_invalidations} panel refreshes with no buyer named, "
        f"2 Dataverse tables (1 tracking, 1 not; already_enabled on the repeat: "
        f"{repeated['already_enabled']}; disable refused: {irreversible}; "
        f"{refused_query_option} on a delta poll), "
        f"{len(hooks)} HubSpot workflow webhooks "
        f"({sum(c['calls_exempt_from_rate_limit'] for c in charges)} calls exempt from the "
        f"rate limit, {sum(c['calls_charged_to_budget'] for c in charges)} charged) "
        f"{usage_report['count']} subscriptions tracked"
    )


#: Exported so the seed's refusal codes have a name a test can assert on, and so
#: nothing in the demo is a string literal a reader has to hunt for.
UNRESOLVABLE_EXTERNAL_ID_FIELD = "External_Id__c"

#: The HubSpot transport, named here so a caller building a subscription against
#: the vocabulary endpoint and a reader of the seed are reading the same string.
HUBSPOT_TRANSPORT = hubspot_rules.HUBSPOT_TRANSPORT

#: The room-panel name the invalidations carry, re-exported for the same reason.
DEAL_PANEL = vocabulary.DEAL_PANEL
