"""WF-038: batch-upsert engagement rows keyed on the external ID.

Built from
``docs/research/digital-sales-room-workflows/wf/WF-038.md``
(source: ``docs/research/raw/crm-integration.md`` section 5). The domain is
:mod:`dsr.crm_upsert`; this module is the three things a feature must own: the
routes, the mapping from domain errors to responses, and its own demo rows.

The researched flow this HTTP surface serves
--------------------------------------------
1. The room's queue accumulates up to 200 pending engagement rows (or the nightly
   backlog).
2. Admin-triggered **Sync now** (or the scheduled job) opens **Sync -> Run
   upsert**.
3. The connector chunks rows into batches (200 for Salesforce collections, 100
   for HubSpot) and sends one upsert per chunk, keyed on the sync key chosen in
   W2.
4. Each row either **updates** the existing CRM record (key found) or **creates**
   a new one (key not found).
5. Per-row outcomes are written back to the room; failures appear in the sync log
   with the row's error text.

The routes, in the order a rep uses them
----------------------------------------
``GET  /capabilities``       the bulk-capability registry, so a client renders its
                             vendor and batch-size pickers from the server
``GET  /vocabulary``         the outcome, rejection, driver, and key-type words
``GET  /inferences``         every judgement call this build made, with its basis
``GET  /connections``        the per-connection settings the research names
``POST /connections``        create one, validated and refused up front
``GET  /connections/{id}``   one connection, with its lint
``PATCH /connections/{id}``  change its key, batch size, or allOrNone policy
``DELETE /connections/{id}`` retire it; its runs stay auditable
``POST /capabilities``       register a vendor-specific bulk capability at runtime
``GET  /rooms/{room_id}/queue``      the pending rows, with the counts a rep reads
``GET  /rooms/{room_id}/preview``    the exact requests a run would send
``POST /rooms/{room_id}/upsert``     **Sync now**
``POST /rooms/{room_id}/schedule``   the scheduled job's entry point
``GET  /rooms/{room_id}/runs``       the sync log
``GET  /runs/{run_id}``              one run, with every request and every row
``GET  /config`` / ``PATCH /config`` the rules the research left open
``POST /rooms/{room_id}/engagement`` the queue's ingest seam

Why ``/preview`` exists
-----------------------
The researched content of this workflow is the *request*: the chunking, the
``attributes.type`` per item, the external-id key with no record id, the
``allOrNone`` parameter, the ``idProperty`` selector, the ``Targets`` collection
with ``@odata.type`` and ``@odata.id``. ``GET /preview`` returns the exact
request a run would send, from the same builder the run uses, and sends nothing.
That is how a reviewer checks the shapes against the vendors' own documentation,
and it is what makes those rules assertable without a credential and a network.
The sync log stores the same object after a real run.

What this module deliberately does not build
--------------------------------------------
**A real HTTP client.** The research names four endpoints precisely and this
feature implements every one of them as a request object, but cites no
credentials, no authentication scheme, and no base URL. An unexercisable network
path is a liability: no test could run it, and the demo seeder must never open a
socket. A team that wants a live CRM implements
:meth:`dsr.crm_upsert.transport.Transport.send` in its own module and nothing
here changes. Recorded as an inference, not a silent omission.

**Retries.** The research says a row that failed appears in the sync log with its
error text, and names no retry, no dead-letter queue, and no give-up rule. A
refused or failed row keeps ``sync_status: pending``, so the next run picks it up -
the queue is the mechanism. Delivery retries with backoff are WF-016's subject.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr import crm_upsert as domain
from dsr.crm_upsert.connections import (
    COLLECTION_CONNECTION,
    COLLECTION_ENGAGEMENT,
    Connection,
    load_config,
    save_config,
)
from dsr.crm_upsert.errors import (
    BatchTooLarge,
    MixedObjectTypes,
    NoUpsertPath,
    UnknownConnection,
    UnknownRun,
    UnsupportedKey,
    UpsertError,
)
from dsr.crm_upsert.runs import DRIVERS, WRITTEN_COLLECTIONS
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-038-batch-upsert-engagement-rows-keyed-on-",
    "ticket": "WF-038",
    "name": "Batch-upsert engagement rows keyed on the external ID",
    "description": (
        "Chunk a room's pending engagement rows into vendor-sized upsert batches keyed "
        "on the CRM's external or alternate ID, and report a created, updated, failed, "
        "or unconfirmed outcome for every row. Salesforce collections, HubSpot batch "
        "upsert, and Dataverse UpsertMultiple, with an automatic per-row PATCH fallback "
        "for a table that cannot take a batch."
    ),
    "nav": [{"id": "batch-upsert", "label": "Batch upsert"}],
}

router = APIRouter(prefix="/api/wf-038", tags=["wf038"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _upsert_error(request: Request, exc: UpsertError) -> JSONResponse:
    """A well-formed request asking this connector to do something it will not do. 422.

    One handler for the whole hierarchy: a record-id key, an over-cap batch size, a
    chunk carrying two object types, and a table with no upsert path are all the
    caller's to fix and are the same shape of problem. Each is this feature's own
    type, so registering it globally cannot intercept an exception raised anywhere
    else in the product.
    """
    return JSONResponse(status_code=422, content={"error": "upsert_refused", "detail": str(exc)})


def _unknown_connection(request: Request, exc: UnknownConnection) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_connection", "detail": str(exc), "id": exc.connection_id},
    )


def _unknown_run(request: Request, exc: UnknownRun) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "unknown_run", "detail": str(exc), "id": exc.run_id}
    )


EXCEPTION_HANDLERS = {
    UpsertError: _upsert_error,
    UnknownConnection: _unknown_connection,
    UnknownRun: _unknown_run,
}

#: The concrete subclasses, so a reader can see which refusals share the 422.
#: ``BatchTooLarge``, ``UnsupportedKey``, ``NoUpsertPath`` and ``MixedObjectTypes``
#: are all ``UpsertError``, so the one handler above covers them and registering
#: them again would be a handler collision the host refuses.
UPSERT_REFUSALS = (BatchTooLarge, MixedObjectTypes, NoUpsertPath, UnsupportedKey)


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def get_transport() -> domain.Transport:
    """The outbound seam for this request.

    A :class:`~dsr.crm_upsert.transport.SimulatedTransport`, which answers each
    vendor's **documented** response shape derived from the request. It is a
    simulation and every run it serves records ``transport: "simulated"`` in the
    sync log, so a reviewer can never read one of its confirmations as a fact
    about a real CRM.

    It is the default because the research cites no credentials, no
    authentication scheme and no base URL, and a transport that answered a flat
    ``204`` would make every Salesforce row fail for a reason that has nothing to
    do with the room.

    This is the one dependency a deployment overrides: a team with credentials
    provides a real :class:`~dsr.crm_upsert.transport.Transport`, and every route
    below uses it with no other change.
    """
    return domain.SimulatedTransport()


TransportDep = Depends(get_transport)


def _config(store: RecordStore) -> dict[str, Any]:
    return load_config(store)


def _connection(store: RecordStore, connection_id: str) -> Connection:
    """One connection, or :class:`UnknownConnection` (404)."""
    return domain.load_connection(store, connection_id, load_config(store))


def _require_room_connection(store: RecordStore, connection_id: str, room_id: str) -> Connection:
    """A connection, checked against the room in the path.

    A room-scoped route must not act on another room's connection, even though the
    ids are both real. Returning 404 rather than 403 keeps the two cases
    indistinguishable from outside, which is the right answer when the only thing
    a caller learns from a 403 is that the id exists.
    """
    connection = _connection(store, connection_id)
    if connection.room_id and connection.room_id != room_id:
        raise HTTPException(
            status_code=404,
            detail=f"connection {connection_id} is not scoped to room {room_id}",
        )
    return connection


def run_payload(result: domain.RunResult) -> dict[str, Any]:
    """A run's own fields lifted to the top level, with its outcomes attached.

    The store record nests everything under ``data``. For a run response that
    nesting is one level of ceremony with no benefit - the client wants
    ``totals``, ``chunks`` and ``progress``, not ``data.totals`` - and the
    researched UI is a progress bar plus a per-row list, both of which read
    straight off the top level. The envelope keys are kept so the response is
    still a superset of a normal record.
    """
    record = dict(result.record)
    data = dict(record.get("data") or {})
    data["id"] = record.get("id")
    data["run_id"] = result.run_id
    data["room_id"] = record.get("room_id")
    data["collection"] = record.get("collection")
    data["progress"] = result.progress
    data["outcomes"] = [entry.to_dict() for entry in result.outcomes]
    return data


# --------------------------------------------------------------------------- #
# Vocabulary, capabilities, inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The words this feature answers with")
def vocabulary() -> dict[str, Any]:
    """Every published word: outcomes, rejection reasons, drivers, key types.

    Served as data rather than compiled into the client, so an outcome added in
    one place reaches every client at once and a client never has to guess a
    label for a state the server can produce.
    """
    return {
        "outcomes": list(domain.OUTCOMES),
        "rejection_reasons": dict(domain.REJECTION_REASONS),
        "drivers": list(DRIVERS),
        "key_types": list(domain.KEY_TYPES),
        "queue_statuses": list(domain.QUEUE_STATUSES),
        "collections": {
            "engagement": domain.COLLECTION_ENGAGEMENT,
            "connection": COLLECTION_CONNECTION,
            "run": domain.COLLECTION_RUN,
            "capability": domain.COLLECTION_CAPABILITY,
        },
        "written_by_a_run": sorted(WRITTEN_COLLECTIONS),
    }


@router.get("/capabilities", summary="Bulk capabilities: max batch size, key types, upsert paths")
def capabilities(store: RecordStore = StoreDep) -> dict[str, Any]:
    """The researched extensibility seam, as data.

    "A third party can register a vendor-specific 'bulk capability' (max batch
    size, supported key types) and the scheduler adapts." So a client renders its
    vendor picker, its batch-size ceiling and its key-type picker from here, and a
    stored capability appears here next to the built-in it replaces.
    """
    listed = domain.catalogue(store)
    return {"count": len(listed), "capabilities": listed, "vendors": domain.supported_vendors()}


@router.post("/capabilities", status_code=201, summary="Register a vendor-specific bulk capability")
def register_capability(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add a capability, or correct a built-in one, without a code change.

    Stored rather than process-global, so it survives a restart and so
    ``GET /capabilities`` shows the operator's own numbers rather than hiding
    them. Overriding a built-in is allowed deliberately: an operator who knows a
    vendor's cap is wrong needs to be able to say so, and every run records which
    source it used as ``capability_source`` so the override is visible in the log
    rather than discovered later as a mysterious 400.

    The vendor is read from the body *before* the capability is built, because
    :meth:`Capability.from_dict` defaults a missing one to ``"unknown"`` - which is
    right for loading a stored record and wrong here, where it would file an
    unnameable capability under a vendor nobody asked for.
    """
    vendor = str(payload.get("vendor") or "").strip()
    if not vendor:
        raise UpsertError("a capability needs a vendor name")
    capability = domain.Capability.from_dict({**payload, "vendor": vendor})
    data = capability.to_dict()
    record_id = f"{domain.COLLECTION_CAPABILITY}_{capability.vendor}"
    existing = store.get(record_id)
    if existing is None:
        store.create(
            domain.COLLECTION_CAPABILITY,
            data,
            record_id=record_id,
            actor=actor,
            source=f"POST {router.prefix}/capabilities",
        )
    else:
        store.update(record_id, data, actor=actor, source=f"POST {router.prefix}/capabilities")
    return {"registered": True, "capability": capability.to_dict()}


@router.get("/inferences", summary="Every judgement this build made, and what would change it")
def inferences() -> dict[str, Any]:
    """What the research fixed, and the remainder with its basis.

    Served as data so a reviewer can disagree with a *named* entry instead of
    hunting for it in a docstring, and so a team can change one without a code
    change. Read with no side effect, so it needs no store.
    """
    return domain.describe_inferences()


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def _connection_view(store: RecordStore, connection: Connection) -> dict[str, Any]:
    """A connection plus the numbers a client needs to render it.

    The effective batch size and the mode are resolved here rather than left for
    the client, because both depend on the capability registry and a client that
    recomputed them would have a second copy of the researched rules.
    """
    resolved = connection.resolved_capability(store)
    try:
        size: int | None = connection.effective_batch_size(store)
    except UpsertError as exc:
        size = None
        capability_error = str(exc)
    else:
        capability_error = None
    try:
        mode: str | None = domain.resolve_mode(resolved.capability)
    except UpsertError as exc:
        mode = None
        capability_error = capability_error or str(exc)
    try:
        key_type: str = connection.effective_key_type(store)
    except UpsertError as exc:
        key_type = connection.key_type
        capability_error = capability_error or str(exc)
    return {
        **({"id": connection.id} if connection.id else {}),
        "name": connection.name,
        "vendor": connection.vendor,
        "object": connection.object,
        "entity_set": connection.entity_set,
        "key_field": connection.key_field,
        "key_type": key_type,
        "key_source": connection.key_source,
        "batch_size": connection.batch_size,
        "effective_batch_size": size,
        "capability_max_batch_size": resolved.capability.max_batch_size,
        "mode": mode,
        "capability_source": resolved.source,
        "capability_sourced": resolved.capability.sourced,
        "returns_per_item_results": resolved.capability.returns_per_item_results,
        "all_or_none": connection.all_or_none,
        "update_only": connection.update_only,
        "fields": dict(connection.fields),
        "required_properties": list(connection.required_properties),
        "room_id": connection.room_id,
        "api_version": connection.api_version,
        "notes": connection.notes,
        "error": capability_error,
    }


@router.get("/connections", summary="Per-connection upsert settings")
def list_connections(
    room_id: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every connection, with the capability it resolved against.

    "The batch size, key field, and ``allOrNone`` policy are per-connection
    settings", so this is the whole configuration surface for the workflow.
    """
    config = _config(store)
    connections = domain.list_connections(store, config, room_id=room_id)
    views = [_connection_view(store, connection) for connection in connections]
    return {"count": len(views), "connections": views}


@router.post("/connections", status_code=201, summary="Create a connection")
def create_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Declare where a room's engagement rows are upserted, and on what key.

    Validated before it is stored, so a connection in the UI is always one that
    could work. Three things are refused here rather than on the first run: a key
    field that is a record id ("Only external ids are supported. Don't use record
    ids."), a batch size the vendor does not accept, and a field map that targets
    a record id.
    """
    config = _config(store)
    spec = dict(payload)
    if room_id:
        spec["room_id"] = room_id
    record = domain.save_connection(
        store, spec, config, actor=actor, source=f"POST {router.prefix}/connections"
    )
    connection = Connection.from_record(record, config=config)
    return {
        "created": True,
        "record": record,
        "connection": _connection_view(store, connection),
        "lint": domain.lint(connection, config, store),
    }


@router.get("/connections/{connection_id}", summary="One connection, with its advisories")
def read_connection(connection_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """A connection and everything a rep should be told about it.

    The lint is served beside the connection rather than only on write, so the
    one *documented gap* - Salesforce's email-as-External-ID 404 on TLD
    collisions, whose workarounds the research explicitly did not cross-verify -
    is visible on the page that configures the key.
    """
    config = _config(store)
    connection = _connection(store, connection_id)
    return {
        "connection": _connection_view(store, connection),
        "lint": domain.lint(connection, config, store),
    }


@router.patch("/connections/{connection_id}", summary="Change a connection's key, batch size, or policy")
def update_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Patch a connection, re-running every validation.

    A patch that would make the connection unable to work is refused with the same
    422 a create would give, because a connection left in a broken state by a
    partial edit is the harder problem to diagnose later.
    """
    config = _config(store)
    existing = _connection(store, connection_id)
    spec = {**(existing.to_dict() | {"room_id": existing.room_id}), **dict(payload), "id": connection_id}
    record = domain.save_connection(
        store, spec, config, actor=actor, source=f"PATCH {router.prefix}/connections/{connection_id}"
    )
    connection = Connection.from_record(record, config=config)
    return {
        "updated": True,
        "record": record,
        "connection": _connection_view(store, connection),
        "lint": domain.lint(connection, config, store),
    }


@router.delete("/connections/{connection_id}", summary="Retire a connection")
def delete_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Soft-delete a connection.

    Its runs and its synced rows stay auditable, and the engagement rows keep the
    per-connection state it wrote, so retiring a connection does not silently
    re-open its rows to whatever runs next.
    """
    _connection(store, connection_id)
    result = domain.delete_connection(
        store, connection_id, actor=actor, source=f"DELETE {router.prefix}/connections/{connection_id}"
    )
    return {"deleted": True, **result}


# --------------------------------------------------------------------------- #
# The queue
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/queue", summary="Pending engagement rows, and the counts a rep reads")
def queue(
    room_id: str,
    connection_id: str = Query(description="The connection whose queue to read"),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The room's pending rows for one connection, oldest first.

    The counts are per connection, so a room wired to two CRMs shows two
    independent queues. ``unconfirmed`` is counted apart from ``synced`` on
    purpose: a row sent to a vendor that returns no per-item result is neither
    confirmed nor failed, and reporting it as either would be the thing a rep
    cannot check in the CRM.
    """
    config = _config(store)
    connection = _require_room_connection(store, connection_id, room_id)
    view = domain.queue_view(store, connection, config, room_id=room_id)
    return {
        **view,
        "connection": _connection_view(store, connection),
        "backlog": domain.backlog(store, connection, config, room_id=room_id),
    }


@router.get("/rooms/{room_id}/preview", summary="The exact requests a run would send")
def preview(
    room_id: str,
    connection_id: str = Query(description="The connection to plan for"),
    limit: int | None = Query(default=None, ge=1),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The outbound request per chunk, and nothing sent.

    ``sends_nothing`` is in the body as well as implied by the method: a reviewer
    should not have to infer from the verb that nothing happened.
    """
    config = _config(store)
    connection = _require_room_connection(store, connection_id, room_id)
    planned = domain.preview(store, connection, config, room_id=room_id, limit=limit)
    return {**planned, "connection": _connection_view(store, connection)}


@router.post("/rooms/{room_id}/engagement", status_code=201, summary="Queue one engagement row")
def record_engagement(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The queue's ingest seam: add a row that a later run will pick up.

    A row with no ``sync_status`` is pending by definition - that is what "The
    room's queue accumulates up to 200 pending engagement rows" describes - so
    this route does not set one. The response says whether the row is queued and
    why, so a caller is never guessing whether its write reached the queue.
    """
    config = _config(store)
    collection = str((config.get("queue") or {}).get("collection") or COLLECTION_ENGAGEMENT)
    record = store.create(
        collection,
        dict(payload),
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/engagement",
    )
    key_source = str((config.get("mapping") or {}).get("key_source") or "")
    return {
        "created": True,
        "record": record,
        "collection": collection,
        "status": domain.row_status(record),
        "key_source": key_source,
        "key_value": domain.key_value(record.get("data") or {}, key_source),
    }


# --------------------------------------------------------------------------- #
# Run upsert
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/upsert", summary="Sync now: chunk the queue and upsert it")
def run_upsert(
    room_id: str,
    connection_id: str = Query(description="The connection to run"),
    limit: int | None = Query(default=None, ge=1, description="Rows to take; omit for the whole queue"),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    transport: domain.Transport = TransportDep,
) -> dict[str, Any]:
    """The researched flow's **Sync now**, end to end.

    Chunks the room's pending rows into vendor-sized batches, sends one upsert per
    chunk keyed on the connection's external-id field, matches each per-item
    result back to its row **by position** ("The `UpsertResult` objects are
    returned in the same order"), and writes every row's outcome back onto the
    room.

    The response is the whole run, not a summary: the researched UI is a progress
    bar plus a per-row error list, and both are here. ``chunks`` carries the full
    request per batch, which is what makes a run reviewable against the vendors'
    own documentation after the fact.

    An empty queue is a successful run with zero rows, not a 404 or an error: a
    scheduler that fires on a quiet room should see that it had nothing to do.
    """
    config = _config(store)
    connection = _require_room_connection(store, connection_id, room_id)
    result = domain.run_upsert(
        store,
        connection,
        config,
        room_id=room_id,
        transport=transport,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/upsert",
        limit=limit,
        driver="manual",
    )
    return {
        **run_payload(result),
        "connection": _connection_view(store, connection),
        "errors": [entry for entry in result.outcomes if entry.errors],
    }


@router.post("/rooms/{room_id}/schedule", summary="The scheduled job's entry point")
def scheduled_run(
    room_id: str,
    connection_id: str = Query(description="The connection to consider"),
    force: bool = Query(default=False, description="Run even when the backlog says it is not due"),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    transport: domain.Transport = TransportDep,
) -> dict[str, Any]:
    """Run the upsert only when the researched triggers say it is due.

    "Nightly/backlog upsert runs on the room's scheduler; the room may also upsert
    opportunistically when the queue exceeds N rows." Both triggers are evaluated
    server-side by :func:`dsr.crm_upsert.connections.backlog`, so a scheduler
    needs no threshold arithmetic of its own and cannot disagree with the
    connector about when a room is due.

    Not due is a 200 with ``ran: false`` and the reasons, because "the scheduler
    decided there was nothing to do" is a normal answer, not a failure.
    ``?force=true`` is the manual escape hatch and records ``driver:
    opportunistic`` rather than ``manual``, so a forced run is distinguishable
    from one a rep pressed.
    """
    config = _config(store)
    connection = _require_room_connection(store, connection_id, room_id)
    state = domain.backlog(store, connection, config, room_id=room_id)
    if not (state["due"] or force):
        return {"ran": False, "reason": "not due", "backlog": state}

    driver = "opportunistic" if force and not state["due"] else "scheduled"
    result = domain.run_upsert(
        store,
        connection,
        config,
        room_id=room_id,
        transport=transport,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/schedule",
        driver=driver,
    )
    return {"ran": True, "driver": driver, "backlog": state, **run_payload(result)}


# --------------------------------------------------------------------------- #
# The sync log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/runs", summary="The sync log: every run, newest first")
def runs(
    room_id: str,
    connection_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every run for a room, with its totals.

    "failures appear in the sync log with the row's error text" - the per-row
    detail is on each run, and ``GET /runs/{run_id}`` is where a client reads it
    rather than carrying every request body in the list view.
    """
    records = domain.list_runs(
        store, room_id=room_id, connection_id=connection_id, limit=limit
    )
    entries = [
        {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "started_at": (record.get("data") or {}).get("started_at"),
            "finished_at": (record.get("data") or {}).get("finished_at"),
            "driver": (record.get("data") or {}).get("driver"),
            "mode": (record.get("data") or {}).get("mode"),
            "vendor": (record.get("data") or {}).get("vendor"),
            "connection_id": (record.get("data") or {}).get("connection_id"),
            "totals": (record.get("data") or {}).get("totals"),
            "chunks": len((record.get("data") or {}).get("chunks") or []),
            "duration_ms": (record.get("data") or {}).get("duration_ms"),
        }
        for record in records
    ]
    return {"count": len(entries), "runs": entries}


@router.get("/runs/{run_id}", summary="One run: every request, every row, every error")
def read_run(run_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The full record of one run, including the request each chunk carried.

    404 for an id that is not a run, so a client cannot mistake "no such run" for
    "a run with nothing in it".
    """
    record = domain.load_run(store, run_id)
    data = record.get("data") or {}
    return {
        **record,
        "progress": {
            "chunks_total": len(data.get("chunks") or []),
            "chunks_done": len(data.get("chunks") or []),
            "rows_total": (data.get("totals") or {}).get("rows", 0),
            "rows_sent": (data.get("totals") or {}).get("rows", 0),
            "rows_rejected": (data.get("totals") or {}).get("rejected", 0),
            "rows_confirmed": (data.get("totals") or {}).get("confirmed", 0),
            "rows_unconfirmed": (data.get("totals") or {}).get("submitted", 0),
            "rows_needing_attention": (data.get("totals") or {}).get("needs_attention", 0),
            "percent": 100.0,
            "driver": data.get("driver"),
        },
    }


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@router.get("/config", summary="The rules the research left open")
def read_config(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Defaults plus any stored overrides.

    Everything here is a judgement call the research did not fix: the queue
    target, the opportunistic threshold, the room field that carries a row's
    identity, and the room-to-CRM field map. Served as data so a team can change
    one without a code change and a reviewer can see what is in force.
    """
    return {"config": _config(store)}


@router.patch("/config", summary="Override an inferred rule")
def update_config(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Merge a partial config patch.

    A deep merge, so patching one threshold cannot drop its siblings. The
    ``source`` is built from ``router.prefix`` here, in the HTTP layer, because
    only the HTTP layer knows its own path.
    """
    record = save_config(store, payload, actor=actor, source=f"PATCH {router.prefix}/config")
    return {"updated": True, "record": record, "config": _config(store)}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The four researched connections, one per vendor, on the first demo room.
#:
#: Each carries the state the research says matters, not just a working
#: configuration: Salesforce with ``allOrNone`` on (so a single bad row rolls a
#: whole batch back), HubSpot keyed on ``email`` with a required property (so the
#: researched "partial upserts are not supported" refusal has something to refuse),
#: and Dataverse, whose 204 NoContent means nothing it sends is ever confirmed.
#: The fourth has no bulk upsert, which exercises the researched auto-fallback to
#: per-row PATCH - the extensibility note's own example.
#:
#: Each carries its **own** field map rather than sharing the config default, which
#: is the researched point that "the key field" and the mapping are per-connection
#: settings: HubSpot keys on a person's email and needs a surname, while the
#: Salesforce ledger keys on the room's own engagement id and needs neither.
DEMO_CONNECTIONS: tuple[Mapping[str, Any], ...] = (
    {
        "name": "Salesforce — engagement ledger",
        "vendor": "salesforce",
        "object": "Engagement__c",
        "key_field": "External_Engagement_Id__c",
        "key_source": "engagement_id",
        "all_or_none": True,
        "fields": {
            "event_type": "Event_Type__c",
            "occurred_at": "Occurred_At__c",
            "person": "Buyer_Email__c",
            "account": "Account_Name__c",
            "document": "Document__c",
            "seconds_on_page": "Seconds_On_Page__c",
        },
        "notes": "allOrNone on, so one duplicate external id rolls the whole batch back.",
    },
    {
        "name": "HubSpot — contact activity",
        "vendor": "hubspot",
        "object": "contacts",
        "key_field": "email",
        "key_source": "person",
        "required_properties": ["lastname"],
        "fields": {
            "person": "email",
            "lastname": "lastname",
            "event_type": "event_type",
            "account": "account",
        },
        "notes": "Keyed on email. HubSpot will not take a partial upsert on email, so a "
        "row with no lastname is refused rather than sent.",
    },
    {
        "name": "Dataverse — engagement table",
        "vendor": "dataverse",
        "object": "engagements",
        "key_field": "sample_keyattribute",
        "key_source": "engagement_id",
        "fields": {
            "event_type": "eventtype",
            "occurred_at": "occurredat",
            "person": "buyeremail",
            "account": "accountname",
        },
        "notes": "UpsertMultiple returns 204 NoContent, so every row lands as submitted "
        "and never as synced.",
    },
    {
        "name": "Legacy table — no bulk upsert",
        "vendor": "legacy_table",
        "object": "engagements",
        "key_field": "eng_key",
        "key_source": "engagement_id",
        "fields": {"event_type": "event_type", "person": "buyer_email"},
        "notes": "A table that cannot take a batch, so every run falls back to one PATCH "
        "per row.",
    },
)

#: The engagement rows the demo queues, and the state each one produces.
#:
#: Deliberately mixed, because a demo of only successes teaches a reviewer
#: nothing about the states this workflow distinguishes:
#:
#: * a row that **creates** (key not found) and a row that **updates** (key found),
#: * a row that **fails** with a duplicate external id - the researched "300 error
#:   ... and no records are created or updated", and the reason ``allOrNone``
#:   exists,
#: * a row the connector **refuses before sending** because it has no key value,
#: * a row HubSpot **refuses** because it cannot supply a complete property set,
#: * rows sent to Dataverse and **never confirmed**, because 204 NoContent.
#:
#: ``engagement_id`` is repeated on the fourth row, so the Salesforce batch really
#: does carry one external id twice and the 300 comes out of the connector's own
#: scripted response rather than being written into the data by hand.
DEMO_ROWS: tuple[Mapping[str, Any], ...] = (
    {
        "engagement_id": "eng-0001",
        "event_type": "viewed",
        "person": "a.buyer@northwind.example",
        "lastname": "Achebe",
        "account": "Northwind Traders",
        "document": "Enterprise Overview Deck",
        "seconds_on_page": 412,
        "minutes_ago": 95,
    },
    {
        "engagement_id": "eng-0002",
        "event_type": "downloaded",
        "person": "b.buyer@northwind.example",
        "lastname": "Bianchi",
        "account": "Northwind Traders",
        "document": "Security & Compliance Pack",
        "seconds_on_page": 268,
        "minutes_ago": 60,
    },
    {
        "engagement_id": "eng-0003",
        "event_type": "commented",
        "person": "procurement@contoso.example",
        "lastname": "Cruz",
        "account": "Contoso Health",
        "document": "Pricing One-Pager",
        "seconds_on_page": 190,
        "minutes_ago": 40,
    },
    {
        # The same external id as eng-0002, so the Salesforce batch carries a
        # duplicate key and answers 300 for it. With allOrNone on, that one row
        # rolls the whole batch back.
        "engagement_id": "eng-0002",
        "event_type": "opened_link",
        "person": "b.buyer@northwind.example",
        "lastname": "Bianchi",
        "account": "Northwind Traders",
        "document": "API Integration Guide",
        "seconds_on_page": 44,
        "minutes_ago": 25,
        "duplicate_of": "eng-0002",
    },
    {
        # No engagement_id at all: there is nothing to key an upsert on, so the
        # connector refuses it before any request goes out. It is still there for
        # every other connection, which key on a different field.
        "engagement_id": "",
        "event_type": "viewed",
        "person": "ops@fabrikam.example",
        "lastname": "Duarte",
        "account": "Fabrikam Logistics",
        "document": "Implementation Roadmap",
        "seconds_on_page": 77,
        "minutes_ago": 12,
        "note": "No external id for the keying connections, so they must refuse it "
        "rather than invent a key.",
    },
    {
        # Keyed on the room's `person` field for the HubSpot connection, and it
        # has no lastname, so the researched "partial upserts are not supported"
        # refusal applies to it. The other connections, which key on
        # engagement_id, have no trouble with it.
        "engagement_id": "eng-0006",
        "event_type": "completed_section",
        "person": "lead@adventure.example",
        "lastname": "",
        "account": "Adventure Works",
        "document": "Contract Draft",
        "seconds_on_page": 903,
        "minutes_ago": 8,
        "note": "Keyed on email for HubSpot with no lastname: refused, not sent.",
    },
)


def _demo_transport(connection: Mapping[str, Any]):
    """A scripted transport for one demo connection.

    Every response is derived from the **request**, never from the configured row
    list, because a result count that does not match the batch is a real error the
    connector is right to raise - and a demo that tripped it would be teaching the
    wrong lesson. Each vendor's documented response shape is reproduced rather
    than invented, so the demo log shows what the workflow would really produce.

    The Salesforce script is the interesting one: it answers 300 for a repeated
    external id, which is the researched behaviour, and because that connection
    has ``allOrNone`` on, the run's rollback is a consequence of the response
    rather than something written into the demo by hand.
    """
    vendor = str(connection.get("vendor"))

    if vendor == "salesforce":
        seen: set[str] = set()

        def send_salesforce(request):
            results = []
            for item in (request.body or {}).get("records") or []:
                key = str(item.get("External_Engagement_Id__c") or "")
                if key in seen:
                    # "If the external ID matches multiple existing records, then
                    # a 300 error is returned, and no records are created or
                    # updated."
                    results.append(
                        {
                            "id": None,
                            "success": False,
                            "created": False,
                            "errors": [
                                {
                                    "statusCode": "300",
                                    "message": (
                                        "The external ID field is not unique: more than one "
                                        "record matches."
                                    ),
                                    "fields": ["External_Engagement_Id__c"],
                                }
                            ],
                        }
                    )
                    continue
                created = len(seen) == 0
                seen.add(key)
                results.append(
                    {"id": f"a0{len(seen):08d}", "success": True, "created": created, "errors": []}
                )
            return domain.salesforce_upsert_results(results)

        return domain.ScriptedTransport(send_salesforce, note="demo")

    if vendor == "hubspot":
        def send_hubspot(request):
            # Alternates created/updated, so the run shows both halves of the
            # researched "key found" / "key not found" split.
            inputs = (request.body or {}).get("inputs") or []
            return domain.hubspot_upsert_results(
                [
                    {"id": f"hs-{index:04d}", "new": index % 2 == 1}
                    for index, _item in enumerate(inputs)
                ]
            )

        return domain.ScriptedTransport(send_hubspot, note="demo")

    if vendor == "dataverse":
        return domain.ScriptedTransport(domain.dataverse_upsert_multiple(), note="demo")

    # A table with no bulk upsert: one PATCH per row, so the transport sees N
    # requests and the run's mode is `single`.
    counter = {"n": 0}

    def send_single(request):
        counter["n"] += 1
        created = counter["n"] % 2 == 1
        return domain.salesforce_single(201 if created else 204, f"leg-{counter['n']:04d}", created)

    return domain.ScriptedTransport(send_single, note="demo")


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed four connections, a mixed queue, and one real run of each.

    The runs go through the same :func:`dsr.crm_upsert.runs.run_upsert` the HTTP
    routes use, over scripted transports, so the sync log in the demo is what this
    workflow actually produces rather than rows written by hand. Seeding never
    opens a socket.

    Every connection runs against the same queue, which is the point: each one
    has its own per-connection state, so the four runs together show one row
    synced, another updated, one rolled back, one refused, and one that leaves the
    room as submitted and never as synced.

    Returns a short description of what was added, which the seeder prints.
    """
    from datetime import timedelta

    from dsr.store import RecordStore

    store = RecordStore(db)
    config = load_config(store)
    now = context.get("now")
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])

    if not rooms:
        return "0 connections, 0 engagement rows (no demo rooms to scope them to)"

    room_id, account = rooms[0]
    actor = "dana"
    source = "seed"

    # -- the queue ---------------------------------------------------------- #
    queued: list[dict[str, Any]] = []
    for spec in DEMO_ROWS:
        data = {k: v for k, v in spec.items() if k not in ("minutes_ago", "duplicate_of", "note")}
        data["occurred_at"] = (
            (now - timedelta(minutes=int(spec.get("minutes_ago") or 0))).isoformat(timespec="seconds")
            if now
            else None
        )
        if data["occurred_at"] is None:
            data.pop("occurred_at")
        queued.append(
            store.create(COLLECTION_ENGAGEMENT, data, room_id=room_id, actor=actor, source=source)
        )

    # -- the connections --------------------------------------------------- #
    connections = []
    for spec in DEMO_CONNECTIONS:
        record = domain.save_connection(
            store, {**dict(spec), "room_id": room_id}, config, actor=actor, source=source
        )
        connections.append(Connection.from_record(record, config=config))

    # -- one real run each ------------------------------------------------- #
    summaries: list[str] = []
    counts = {"created": 0, "updated": 0, "failed": 0, "rolled_back": 0, "rejected": 0, "submitted": 0}
    for connection in connections:
        run = domain.run_upsert(
            store,
            connection,
            config,
            room_id=room_id,
            transport=_demo_transport({**connection.to_dict()}),
            actor=actor,
            source=source,
            now=now,
        )
        totals = run.record["data"]["totals"]
        for name in counts:
            counts[name] += int(totals.get(name, 0))
        summaries.append(f"{connection.vendor} {run.record['data']['mode']}")

    room_view = domain.queue_view(store, connections[0], config, room_id=room_id)
    dataverse_view = domain.queue_view(store, connections[2], config, room_id=room_id)
    return (
        f"{len(connections)} connections ({', '.join(summaries)}), {len(queued)} engagement rows, "
        f"{len(domain.list_runs(store, room_id=room_id))} runs: "
        f"{counts['created']} created, {counts['updated']} updated, {counts['failed']} failed, "
        f"{counts['rolled_back']} rolled back, {counts['rejected']} refused, "
        f"{counts['submitted']} submitted-but-unconfirmed. "
        f"After the runs the Salesforce queue is {room_view['counts']['pending']} pending and the "
        f"Dataverse queue is {dataverse_view['counts']['unconfirmed']} unconfirmed "
        f"(204 NoContent confirms nothing). Room {room_id} ({account})."
    )
