"""WF-065: write the booking back into the CRM.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-065.md``, which is
the specification. The researched decisions are the product: the eight router node
names and the vendor each belongs to, the six branch labels, the two selection
rules - *most recently created Open Case*, *nearest Close Date Opportunity* - the
``Booked`` CampaignMember status, the three owner identities, the org-wide Sync
Meeting Type toggle, per-additional-guest child Events, and the two sentences most
implementations lose: the node that must precede the rest, and the Contact that
gates the extra relation.

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the demo
data. The domain lives in :mod:`dsr.booking_crm`.

Why the prefix is ``/api/wf-065``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is room-scoped
- ``/rooms/{room_id}/meeting-types``, ``/rooms/{room_id}/flows``,
``/rooms/{room_id}/runs``, ``/rooms/{room_id}/events-history`` - and the host's
loader would report a ``(method, path)`` clash as a failed feature rather than
shadowing it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain method that writes, so it cannot silently
regress. A test asserts that every source recorded in the audit log names a route
the host actually mounted. The rows the CRM creates carry the writeback route's
own source too - they are writes, and they are audited, and an audit row that
cannot be traced back to the request that caused it is not an audit trail.

Error mapping
-------------
Five handlers, one per distinct HTTP answer, and all five types are this
feature's own. ``RecordNotFound`` and ``AuditError`` are deliberately not claimed:
the core app already maps them correctly, and two handlers for one type is a
collision the host refuses. The split between ``400`` and ``428`` is deliberate -
"this flow is wrong" and "this installation has not wired that path up yet" are
different things for a client, and ``apiRequest`` in the frontend carries the
status so a page can tell them apart.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from dsr.booking_crm import (
    COLLECTIONS,
    CRM_RECORD_COLLECTION,
    BookingWriteback,
    InvalidConfig,
    InvalidNode,
    LocalCrm,
    NodeOrderError,
    NotConfigured,
    NotFound,
    WritebackError,
    describe_inferences,
    describe_vocabulary,
    node_vocabulary,
)
from dsr.booking_crm.flow import PATH_MEANING
from dsr.booking_crm.local_crm import describe_record_keys
from dsr.booking_crm.vocabulary import HISTORY_RETRY_QUOTE, ORDERING_QUOTE
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-065-write-the-booking-back-into-the-crm",
    "ticket": "WF-065",
    "name": "Write the booking back into the CRM",
    "description": (
        "Run a router flow's CRM nodes against a booked meeting: match the Lead or "
        "Contact by email and update and/or create it, relate the Event to the open "
        "Case with the most recent creation or the Opportunity with the nearest Close "
        "Date, update the selected fields, add the CampaignMember with status Booked, "
        "reassign the Owner, and surface every Event attempt in Events History with a "
        "retry. The create node must precede every other node, and the extra relation "
        "is available only once a Contact has been found."
    ),
    "nav": [{"id": "booking-writeback", "label": "Booking writeback"}],
}

router = APIRouter(prefix="/api/wf-065", tags=["wf065"])


def get_engine(store: RecordStore = StoreDep) -> BookingWriteback:
    """A :class:`BookingWriteback` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but the
    store handle, and ``app.state`` is where it would otherwise have to be built in
    the shared app's lifespan. Building it here also leaves the CRM a seam a test can
    override, so the suite can run a whole flow without a network.
    """
    return BookingWriteback(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _writeback_error(request: Request, exc: WritebackError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    ``WritebackError`` is the base of every refusal in :mod:`dsr.booking_crm`: a
    node this build does not know, a node of the other vendor's palette, a branch
    that contradicts itself, a field map with no target. All of them are the
    caller's to fix, and the message always names the node and the rule it broke.
    """
    return JSONResponse(status_code=400, content={"error": "writeback_error", "detail": str(exc)})


def _node_order(request: Request, exc: NodeOrderError) -> JSONResponse:
    """The sourced ordering rule, refused. 400 with the sentence in the body.

    Its own handler, and its own error code, because this is the one refusal in the
    whole feature a client is likely to hit by accident and the one whose fix is a
    single sentence. ``detail`` carries the quoted rule, so a client can show it
    without having it hard-coded twice.
    """
    return JSONResponse(
        status_code=400,
        content={
            "error": "node_order",
            "detail": str(exc),
            "ordering_quote": ORDERING_QUOTE,
        },
    )


def _invalid_node(request: Request, exc: InvalidNode) -> JSONResponse:
    """A node name this build does not know, or one of the other vendor's. 400.

    Split from the base because a client can act on it differently: this is a
    palette mistake, and the body says which palette the vendor does have.
    """
    return JSONResponse(status_code=400, content={"error": "invalid_node", "detail": str(exc)})


def _invalid_config(request: Request, exc: InvalidConfig) -> JSONResponse:
    """A node declared with a setting this build cannot honour. 400.

    Also the handler for a run carrying its own ``sync_to_crm``, which is the one
    per-run override the research rules out.
    """
    return JSONResponse(status_code=400, content={"error": "invalid_config", "detail": str(exc)})


def _not_configured(request: Request, exc: NotConfigured) -> JSONResponse:
    """Well formed, but this installation has not wired that path up. 428.

    Distinct from 400 so a client can say "declare a flow for this path" rather
    than "you got the request wrong" - and a path with no flow is a real state a
    tenant reaches by wiring up only some of the router's three paths.
    """
    return JSONResponse(status_code=428, content={"error": "not_configured", "detail": str(exc)})


def _not_found(request: Request, exc: NotFound) -> JSONResponse:
    """A room, flow, meeting type, run or history id that does not resolve. 404.

    The resource name travels in the body rather than only in the prose, so a
    client can branch on it instead of pattern-matching a message.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": str(exc),
            "resource": exc.resource,
            "id": exc.record_id,
            "room_id": exc.room_id,
        },
    )


EXCEPTION_HANDLERS = {
    WritebackError: _writeback_error,
    NodeOrderError: _node_order,
    InvalidNode: _invalid_node,
    InvalidConfig: _invalid_config,
    NotConfigured: _not_configured,
    NotFound: _not_found,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The sourced vocabulary: the nodes, the branches, the related objects, the
    selection rules, the two paths the run fires on, and the quoted sentences all
    of it is measured against.

    Served as data so a client renders its pickers from the same source the
    validator enforces against. Includes the research's own two stated gaps, so a
    reader knows which strings are quoted and which shapes are not.
    """
    payload = describe_vocabulary()
    payload["collections"] = list(COLLECTIONS)
    payload["path_meaning"] = dict(PATH_MEANING)
    return payload


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about the nodes and about the two selection rules,
    and silent about almost everything around them. The parts that are therefore
    judgement calls - what "Create Contact or Lead" makes, what "nearest" is
    nearest to, what the ``Delete Event`` behaviour fires on, which record type
    wins when an email matches both - are collected in
    :mod:`dsr.booking_crm.inferences` and served here, next to the sourced facts
    they are measured against.

    A read with no side effect, so it needs no store. The node table rides along,
    because a client building a node picker from a hand-written list is a second
    source of truth that will drift from the palette the validator enforces.
    """
    payload = describe_inferences()
    payload["nodes"] = node_vocabulary()
    payload["crm_records"] = describe_record_keys()
    return payload


# --------------------------------------------------------------------------- #
# Meeting types: the Sync Meeting Type to the CRM toggle
# --------------------------------------------------------------------------- #


@router.get("/connectors")
def list_connectors(engine: BookingWriteback = EngineDep) -> dict[str, Any]:
    """Registered CRM connections.

    [sourced] "Global Salesforce connection required for Event details in Events
    History", so a connector is a global row rather than a room's. A read never
    returns the token: it answers ``has_token`` and a masked hint of the last four
    characters, because the token *is* the ``Authorization`` header on every request
    and a connector row is readable by anyone who can call the API.
    """
    connectors = [engine.connector_summary(row) for row in engine.list_connectors()]
    return {"count": len(connectors), "connectors": connectors}


@router.post("/connectors", status_code=201)
def create_connector(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Register the global CRM connection: which vendor, which org, whether it is
    the global one Events History reads.

    Not required to run. A room's own CRM works without one, and Events History
    then says the Event *details* are unavailable rather than refusing to show the
    timestamp or the error - both of which the research says it must show.
    """
    created = engine.create_connector(
        payload, actor=actor, source=f"POST {router.prefix}/connectors"
    )
    return engine.connector_summary(created)


@router.get("/rooms/{room_id}/meeting-types")
def list_meeting_types(room_id: str, engine: BookingWriteback = EngineDep) -> dict[str, Any]:
    """A room's meeting types and, on each, whether it syncs to the CRM.

    [sourced] "**Sync Meeting Type to the CRM** … your Admins can define other
    behaviors to be taken when a meeting is booked", so the toggle lives here rather
    than on a flow or a link - a booking cannot turn it on for itself.
    """
    meeting_types = engine.list_meeting_types(room_id)
    return {
        "room_id": room_id,
        "count": len(meeting_types),
        "syncing": sum(1 for row in meeting_types if row["data"].get("sync_to_crm")),
        "meeting_types": meeting_types,
    }


@router.post("/rooms/{room_id}/meeting-types", status_code=201)
def create_meeting_type(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Register a meeting type and set the toggle.

    Off by default, because the research says the behaviours are admin-defined: a
    meeting type nobody opted in has not been opted in, and the run that hits it is
    written as a skip rather than disappearing.
    """
    return engine.create_meeting_type(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/meeting-types"
    )


@router.get("/rooms/{room_id}/meeting-types/{meeting_type_id}")
def read_meeting_type(
    room_id: str, meeting_type_id: str, engine: BookingWriteback = EngineDep
) -> dict[str, Any]:
    return engine.meeting_type(meeting_type_id, room_id=room_id)


@router.patch("/rooms/{room_id}/meeting-types/{meeting_type_id}")
def update_meeting_type(
    room_id: str,
    meeting_type_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Patch a meeting type: switch the toggle, rename it, repoint its event type."""
    return engine.update_meeting_type(
        room_id,
        meeting_type_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{room_id}/meeting-types/{meeting_type_id}",
    )


@router.delete("/rooms/{room_id}/meeting-types/{meeting_type_id}", status_code=204)
def delete_meeting_type(
    room_id: str,
    meeting_type_id: str,
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> Response:
    """Soft-delete a meeting type. Its runs and its Events History stay readable.

    A soft delete rather than a hard one so a run's ``meeting_type_id`` still
    resolves to something, and a reader can see what the toggle said at the time -
    which is the only version of it that matters.
    """
    engine.delete_meeting_type(
        room_id,
        meeting_type_id,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{room_id}/meeting-types/{meeting_type_id}",
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Flows: the declared nodes, and the ordering rule
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/flows")
def list_flows(
    room_id: str,
    path: str | None = Query(default=None),
    meeting_type_id: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """The flows declared for this room, newest first.

    Filterable by router path and by meeting type - both are JSON paths in each
    row's own payload, resolved through the dynamic index, so a fourth path needs
    no change here.
    """
    flows = engine.list_flows(room_id, path=path, meeting_type_id=meeting_type_id)
    return {
        "room_id": room_id,
        "count": len(flows),
        "path": path,
        "meeting_type_id": meeting_type_id,
        "path_meaning": dict(PATH_MEANING),
        "flows": flows,
    }


@router.post("/rooms/{room_id}/flows", status_code=201)
def create_flow(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Declare a flow: a router path, a meeting type, and an ordered node list.

    The node list is checked against the sourced ordering rule **here**, at
    declaration:

        [sourced] "Note this node must precede the **Create Event**, **Update Field**,
        **Add to Campaign**, and **Update Ownership** nodes"

    A flow that declares them the other way round is refused with that sentence
    rather than sorted, because the declared order is the thing an admin reads to
    understand their own router. Checking here rather than at run time means a
    stored flow is always a runnable one.
    """
    return engine.create_flow(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/flows"
    )


@router.get("/rooms/{room_id}/flows/{flow_id}")
def read_flow(room_id: str, flow_id: str, engine: BookingWriteback = EngineDep) -> dict[str, Any]:
    return engine.flow(flow_id, room_id=room_id)


@router.patch("/rooms/{room_id}/flows/{flow_id}")
def update_flow(
    room_id: str,
    flow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Patch a flow: reorder the nodes, change a branch, retarget the related object.

    Reordering goes through the same check as creating one, so a patch cannot
    smuggle in a flow that would be refused on creation. Every key is ordinary JSON
    and no key is required, so a team adding a Data Field to a field map does it by
    shipping a payload rather than by a migration.
    """
    return engine.update_flow(
        room_id,
        flow_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/rooms/{room_id}/flows/{flow_id}",
    )


@router.delete("/rooms/{room_id}/flows/{flow_id}", status_code=204)
def delete_flow(
    room_id: str,
    flow_id: str,
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> Response:
    """Soft-delete a flow. Its runs stay, and keep the nodes they ran."""
    engine.delete_flow(
        room_id,
        flow_id,
        actor=actor,
        source=f"DELETE {router.prefix}/rooms/{room_id}/flows/{flow_id}",
    )
    return Response(status_code=204)


@router.post("/rooms/{room_id}/flows/{flow_id}/validate")
def validate_flow(
    room_id: str,
    flow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Check a node list against the researched rules, and write nothing.

    The flow builder's own dry run. Takes a *candidate* node list as well as the
    stored one, so an admin can try a reorder and be told it is wrong before
    saving - and so a client can render the ordering rule without re-implementing
    it. A candidate that fails is reported here rather than refused, because the
    whole point of a check is to say what is wrong.

    Also reports what each researched rule would do to this flow, so the answer to
    "what will happen" does not need a booking to be produced first.
    """
    flow = engine.flow(flow_id, room_id=room_id)
    vendor = str(flow["data"].get("vendor") or "")
    candidate = payload.get("nodes") if "nodes" in payload else flow["data"].get("nodes")
    report: dict[str, Any] = {
        "room_id": room_id,
        "flow_id": flow_id,
        "vendor": vendor,
        "path": flow["data"].get("path"),
        "ordering_quote": ORDERING_QUOTE,
    }
    try:
        from dsr.booking_crm.flow import describe_plan, normalise_nodes

        nodes = normalise_nodes(candidate, vendor=vendor)
    except WritebackError as exc:
        report.update({"valid": False, "error": type(exc).__name__, "detail": str(exc)})
        return report
    report.update({"valid": True, "plan": describe_plan(nodes, vendor=vendor), "nodes": nodes})
    return report


# --------------------------------------------------------------------------- #
# The writeback
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/flows/{flow_id}/writeback")
def writeback(
    room_id: str,
    flow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Run a declared flow against a booking, and record every node's outcome.

    The researched data flow, in the researched order: the meeting and its guest
    form Data Fields become a matched or created Lead or Contact, the Event or
    Engagement is written and related, the selected fields are updated, the
    CampaignMember is created or updated with status ``Booked``, and the record
    Owner is reassigned to the assignee.

    One run record per request, whatever the outcome - a failed run is a row, not a
    gap, because the failure is the thing a rep has to read.

    ``faults`` is deliberately not accepted here. A deployment's failures come
    from the CRM; a client that could inject them would be a client that could
    forge an audit record.
    """
    return engine.writeback(
        room_id,
        flow_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/flows/{flow_id}/writeback",
    )


@router.post("/rooms/{room_id}/writeback")
def writeback_for_path(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """The researched automation: the booking knows its path, the flow is found.

    [sourced] "writes fire on the scheduled, not-scheduled and disqualified paths
    automatically" - so a caller that only knows the booking does not have to know
    which flow serves it. A path with no declared flow is a ``428``, not a silent
    no-op: a tenant that wired up only the scheduled path should learn that from
    the response rather than from a booking that wrote nothing.
    """
    return engine.writeback_for_path(
        room_id, payload, actor=actor, source=f"POST {router.prefix}/rooms/{room_id}/writeback"
    )


# --------------------------------------------------------------------------- #
# The run log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/runs")
def list_runs(
    room_id: str,
    path: str | None = Query(default=None),
    meeting_type_id: str | None = Query(default=None),
    ok: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Every writeback attempted for this room, newest first.

    A failed run is a row, not a gap. The summary is computed over exactly the rows
    returned, so a filtered view does not report totals for the whole log.
    """
    runs = engine.runs(room_id, path=path, meeting_type_id=meeting_type_id, ok=ok, limit=limit)
    summary = {
        "runs": len(runs),
        "ok": sum(1 for run in runs if run["data"].get("ok")),
        "events_created": sum(len(run["data"].get("created_events") or []) for run in runs),
        "failed_nodes": sum(
            int((run["data"].get("counts") or {}).get("failed") or 0) for run in runs
        ),
    }
    return {
        "room_id": room_id,
        "count": len(runs),
        "summary": summary,
        "filter": {"path": path, "meeting_type_id": meeting_type_id, "ok": ok, "limit": limit},
        "runs": runs,
    }


@router.get("/rooms/{room_id}/runs/{run_id}")
def read_run(room_id: str, run_id: str, engine: BookingWriteback = EngineDep) -> dict[str, Any]:
    """One writeback in full: the booking, the record, every node's outcome, the
    related object and the rule that chose it, and the single actionable error."""
    return engine.run(room_id, run_id)


# --------------------------------------------------------------------------- #
# Meetings Activity -> Events History
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/events-history")
def events_history(
    room_id: str,
    status: str | None = Query(default=None),
    event_type_id: str | None = Query(default=None),
    meeting_type_id: str | None = Query(default=None),
    booking_ref: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: BookingWriteback = EngineDep,
) -> dict[str, Any]:
    """Every Event this workflow attempted, newest first.

    [sourced] "If the Event is successfully created, we will show when it happened.
    If the Event failed to be created, we will also show when it happened, alongside
    the detailed error." So ``when`` is on every row whatever its status, and a
    failure carries its error beside it.

    One row per Event, including the children - because "[sourced] Admin later
    retries any failed CRM Event" is per Event, and a child that failed has to be
    retryable without re-running the booking.
    """
    rows = engine.history(
        room_id,
        status=status,
        event_type_id=event_type_id,
        meeting_type_id=meeting_type_id,
        booking_ref=booking_ref,
        limit=limit,
    )
    return {
        "room_id": room_id,
        "count": len(rows),
        "failed": sum(1 for row in rows if row["data"].get("status") == "failed"),
        "created": sum(1 for row in rows if row["data"].get("status") == "created"),
        "retryable": sum(
            1
            for row in rows
            if row["data"].get("status") == "failed" and int(row["data"].get("attempt") or 1) == 1
        ),
        "filter": {
            "status": status,
            "event_type_id": event_type_id,
            "meeting_type_id": meeting_type_id,
            "booking_ref": booking_ref,
            "limit": limit,
        },
        "event_details": engine.events_history_available(),
        "history": rows,
    }


@router.get("/rooms/{room_id}/events-history/export.csv")
def export_events_history(
    room_id: str,
    status: str | None = Query(default=None),
    event_type_id: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
) -> PlainTextResponse:
    """Events History as CSV, for the export the research's feature list names.

    The same rows and the same filters as the JSON view - the export is the list a
    rep would hand to somebody else, so it must not be a second set of numbers. The
    header carries the researched column names, and a row a filter did not match is
    not in the file at all.
    """
    rows = engine.history(room_id, status=status, event_type_id=event_type_id, limit=1000)
    columns = (
        "when",
        "status",
        "meeting_type_name",
        "event_type_id",
        "event_type",
        "guest",
        "guest_name",
        "is_child",
        "subject",
        "crm_id",
        "related_to",
        "related_object",
        "related_object_crm_id",
        "activity_assigned_to",
        "activity_assigned_to_email",
        "booking_ref",
        "attempt",
        "retried_from",
        "error_code",
        "error",
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row["data"].get(key) for key in columns})
    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="events-history-{room_id}.csv"'},
    )


@router.get("/rooms/{room_id}/meeting-types/{meeting_type_id}/crm-sync-errors")
def crm_sync_errors(
    room_id: str, meeting_type_id: str, engine: BookingWriteback = EngineDep
) -> dict[str, Any]:
    """Cal's per-event-type CRM sync errors.

    [sourced] ``GET /v2/event-types/{id}/crm-sync-errors`` - "List CRM sync errors
    for an event type". The event type is what scopes the list, which is why this
    route is under the meeting type and not under a free filter: a deployment
    pointing at Cal needs one URL per event type, and this is the shape it takes.
    """
    meeting_type = engine.meeting_type(meeting_type_id, room_id=room_id)
    event_type_id = str(meeting_type["data"].get("event_type_id") or "")
    rows = engine.sync_errors(room_id, event_type_id)
    return {
        "room_id": room_id,
        "meeting_type_id": meeting_type_id,
        "meeting_type_name": meeting_type["data"].get("name"),
        "event_type_id": event_type_id,
        "count": len(rows),
        "errors": rows,
    }


@router.post("/rooms/{room_id}/events-history/{history_id}/retry")
def retry_event(
    room_id: str,
    history_id: str,
    engine: BookingWriteback = EngineDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Retry one failed Event, from Meetings Activity → Events History.

    [sourced] "Admin later retries any failed CRM Event from Meetings Activity →
    Events History." The retry re-runs the Event for *that* row only - a child that
    failed is retryable on its own, without re-running the booking and duplicating
    the Events that already succeeded.

    It appends a new history row rather than rewriting the old one, because the
    researched sentence is about the *first* attempt: "we will also show when it
    happened, alongside the detailed error". Two rows make "failed at T, retried at
    T, succeeded" readable, which is the conversation a rep is having. Retrying a
    row that succeeded is refused with the same sentence, because re-running it
    would create a second Event, which is not a retry.
    """
    result = engine.retry(
        room_id,
        history_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/events-history/{history_id}/retry",
    )
    if not result.get("retried"):
        # Not an error: the research offers retry on a failure, and this row is not
        # one. A 200 with the refusal keeps the page's single error path for real
        # failures and says plainly why the button was not there.
        result = {**result, "retry_quote": HISTORY_RETRY_QUOTE}
    return result


# --------------------------------------------------------------------------- #
# What the CRM holds
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/crm-records")
def list_crm_records(
    room_id: str,
    record_type: str | None = Query(default=None),
    vendor: str | None = Query(default=None),
    engine: BookingWriteback = EngineDep,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The CRM's own records, grouped by type.

    [sourced] the research's ``data_sources``: "Salesforce ``Lead``, ``Contact``,
    ``Account``, ``Opportunity``, ``Case``, ``Campaign``, ``CampaignMember``,
    ``Event``; HubSpot ``Contact``, ``Company``, ``Deal``, ``Ticket``, engagement".
    Every one of those is a type this route can filter to, so a rep can see the
    Events a writeback made and the Case one of them was related to, side by side.

    Soft-deleted rows are included, so an Event that a Delete Event setting removed
    still shows up - that is the difference between "created and then deleted" and
    "never created", and only one of them is a thing that happened.
    """
    engine.require_room(room_id)
    crm = LocalCrm(store, room_id=room_id)
    rows = crm.records(record_type, vendor=vendor, include_deleted=True)
    by_type: dict[str, int] = {}
    for row in rows:
        key = str(row["data"].get("type") or "unknown")
        by_type[key] = by_type.get(key, 0) + 1
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_type": by_type,
        "filter": {"record_type": record_type, "vendor": vendor},
        "collection": CRM_RECORD_COLLECTION,
        "conventions": describe_record_keys(),
        "records": rows,
    }


@router.get("/rooms/{room_id}/summary")
def summary(room_id: str, engine: BookingWriteback = EngineDep) -> dict[str, Any]:
    """The counts a page opens with: flows, runs, and the Events History split.

    Computed over the room's own rows and nothing else, so a filtered page can
    still show the totals without a second source of truth.
    """
    engine.require_room(room_id)
    meeting_types = engine.list_meeting_types(room_id)
    flows = engine.list_flows(room_id)
    runs = engine.runs(room_id, limit=1000)
    history = engine.history(room_id, limit=1000)
    by_path: dict[str, int] = {}
    for flow in flows:
        key = str(flow["data"].get("path") or "")
        by_path[key] = by_path.get(key, 0) + 1
    return {
        "room_id": room_id,
        "meeting_types": len(meeting_types),
        "meeting_types_syncing": sum(1 for row in meeting_types if row["data"].get("sync_to_crm")),
        "flows": len(flows),
        "flows_by_path": by_path,
        "runs": len(runs),
        "runs_ok": sum(1 for run in runs if run["data"].get("ok")),
        "events_created": sum(1 for row in history if row["data"].get("status") == "created"),
        "events_failed": sum(1 for row in history if row["data"].get("status") == "failed"),
        "events_retried": sum(1 for row in history if int(row["data"].get("attempt") or 1) > 1),
        "ordering_quote": ORDERING_QUOTE,
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The connector the demo registers. Salesforce, global, with a token - because the
#: researched "Global Salesforce connection required for Event details in Events
#: History" is only visible when one exists, and the demo has to show the other
#: half too, which is a second HubSpot connector that is *not* global.
DEMO_CONNECTORS: tuple[Mapping[str, Any], ...] = (
    {
        "name": "Salesforce production (global)",
        "vendor": "salesforce",
        "base_url": "https://acme.my.salesforce.example",
        "api_version": "v61.0",
        "global": True,
        "token": "00D-demo-global-salesforce",
    },
    {
        "name": "HubSpot (not global)",
        "vendor": "hubspot",
        "base_url": "https://api.hubapi.example",
        "global": False,
        "token": "pat-na1-demo-hubspot",
    },
)

#: The CRM's own records the demo seeds. Deliberately mixed, because a demo whose
#: Cases are all Open and whose Opportunities are all near teaches nothing about
#: the two selection rules:
#:
#: * a **Closed** Case that is the *newest* of all three, which must lose to an
#:   older Open one because the rule filters on status before it sorts on date;
#: * two Open Cases with different creation dates, so "most recently created" has
#:   something to choose between;
#: * three Opportunities at very different distances from the demo meeting, so
#:   "nearest Close Date" is visibly a measurement and not a sort;
#: * a Contact, a Lead and an Account, so the match order and the L2A rule have
#:   something to match;
#: * a HubSpot side - a Contact, a Company, a Deal, a Ticket - so the Deal and
#:   Ticket rules are exercised rather than only asserted.
DEMO_CRM_RECORDS: tuple[Mapping[str, Any], ...] = (
    # -- Salesforce ---------------------------------------------------------- #
    {
        "crm_id": "cnt-0001",
        "vendor": "salesforce",
        "type": "Contact",
        "name": "Amara Okonkwo",
        "email": "a.buyer@northwind.example",
        "account_id": "acc-0001",
        "owner": "dana@acme.example",
        "status": "Open",
        "fields": {"Rating": "Warm", "Status": "Open"},
        "created_on": "2026-05-02",
    },
    {
        "crm_id": "acc-0001",
        "vendor": "salesforce",
        "type": "Account",
        "name": "Northwind Traders",
        "owner": "sam@acme.example",
        "created_on": "2026-04-18",
    },
    {
        "crm_id": "lead-0001",
        "vendor": "salesforce",
        "type": "Lead",
        "name": "Bayo Toure",
        "email": "b.toure@northwind.example",
        "company": "Northwind Traders",
        "status": "Open",
        "owner": "dana@acme.example",
        "created_on": "2026-08-21",
    },
    {
        # The newest Case of all three, and Closed. It must lose: the rule filters
        # on Open *before* it sorts on creation date.
        "crm_id": "case-0001",
        "vendor": "salesforce",
        "type": "Case",
        "name": "Procurement blocked the order (closed)",
        "status": "Closed",
        "created_on": "2026-09-25",
    },
    {
        "crm_id": "case-0002",
        "vendor": "salesforce",
        "type": "Case",
        "name": "SSO provisioning question",
        "status": "Open",
        "created_on": "2026-09-11",
    },
    {
        "crm_id": "case-0003",
        "vendor": "salesforce",
        "type": "Case",
        "name": "Seat count dispute",
        "status": "Open",
        "created_on": "2026-01-04",
    },
    {
        "crm_id": "opp-0001",
        "vendor": "salesforce",
        "type": "Opportunity",
        "name": "Northwind - FY27 renewal",
        "close_date": "2027-02-28",
        "account_id": "acc-0001",
        "created_on": "2026-03-01",
    },
    {
        "crm_id": "opp-0002",
        "vendor": "salesforce",
        "type": "Opportunity",
        "name": "Northwind - Q4 enterprise",
        "close_date": "2026-10-07",
        "account_id": "acc-0001",
        "created_on": "2026-06-14",
    },
    {
        "crm_id": "opp-0003",
        "vendor": "salesforce",
        "type": "Opportunity",
        "name": "Northwind - pilot",
        "close_date": "2026-09-20",
        "account_id": "acc-0001",
        "created_on": "2026-02-02",
    },
    {
        "crm_id": "camp-0001",
        "vendor": "salesforce",
        "type": "Campaign",
        "name": "Q4 Enterprise",
        "created_on": "2026-09-01",
    },
    # -- HubSpot ------------------------------------------------------------ #
    {
        "crm_id": "hs-cnt-0001",
        "vendor": "hubspot",
        "type": "Contact",
        "name": "Priya Raman",
        "email": "priya.raman@contoso.example",
        "account_id": "comp-0001",
        "owner": "sam@acme.example",
        "fields": {"lifecyclestage": "salesqualifiedlead"},
        "created_on": "2026-05-20",
    },
    {
        "crm_id": "comp-0001",
        "vendor": "hubspot",
        "type": "Company",
        "name": "Contoso Health",
        "owner": "sam@acme.example",
        "created_on": "2026-05-01",
    },
    {
        "crm_id": "hs-deal-0001",
        "vendor": "hubspot",
        "type": "Deal",
        "name": "Contoso - security review",
        "close_date": "2026-10-09",
        "account_id": "comp-0001",
        "created_on": "2026-06-02",
    },
    {
        "crm_id": "hs-deal-0002",
        "vendor": "hubspot",
        "type": "Deal",
        "name": "Contoso - pilot",
        "close_date": "2026-12-15",
        "account_id": "comp-0001",
        "created_on": "2026-07-19",
    },
    {
        "crm_id": "hs-tick-0001",
        "vendor": "hubspot",
        "type": "Ticket",
        "name": "DPA request (closed)",
        "status": "Closed",
        "created_on": "2026-09-18",
    },
    {
        "crm_id": "hs-tick-0002",
        "vendor": "hubspot",
        "type": "Ticket",
        "name": "BAA countersignature",
        "status": "Open",
        "created_on": "2026-09-20",
    },
)

#: The meeting types and flows the demo declares. Chosen so the interesting states
#: are all reachable from a fresh seed:
#:
#: * a **clean scheduled** Salesforce run that matches, updates, relates to the
#:   newest Open Case, creates a child Event per additional guest, adds the
#:   CampaignMember and reassigns the Owner;
#: * a **not-scheduled** run on the same meeting type, so both researched paths
#:   are visibly live;
#: * a **disqualified** run on a *different* meeting type whose toggle is **off**,
#:   which is the researched Sync Meeting Type behaviour: the run is written and
#:   skipped, not silently dropped;
#: * a run that **matched nothing and created nothing**, so every node after the
#:   anchor skips and the run is *not* ok - the catch-all the ordering sentence
#:   forces into existence, and the one a demo without it would hide;
#: * a flow whose Related Object is an **Opportunity**, so "nearest Close Date" is
#:   exercised as a measurement against the meeting;
#: * a **HubSpot** flow with ``Activity Assigned to``, child Events, and a Ticket
#:   relation, so the HubSpot half of the vocabulary is live rather than asserted;
#: * a flow whose create branch is **Always create Lead**, which creates a Lead
#:   even though the email matched a Contact;
#: * and a **failed Event** - injected through the real engine, not written by hand
#:   - so Events History has a row with a detailed error and a retry to offer.
#:   Its guest's failure is only retried once, so the demo shows an attempt 1 that
#:   failed and an attempt 2 that did not.
DEMO_FLOWS: tuple[Mapping[str, Any], ...] = (
    {
        "room": 0,
        "meeting_type": {
            "name": "Enterprise demo",
            "vendor": "salesforce",
            "event_type_id": "445511",
            "sync_to_crm": True,
            "notes": "The researched toggle, on.",
        },
        "flow": {
            "name": "Northwind - scheduled writeback",
            "path": "scheduled",
            "vendor": "salesforce",
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                    "fields": [
                        {"field": "Status", "value": "Sales Qualified"},
                        {"field": "NumberOfEmployees", "from_data_field": "seats"},
                    ],
                },
                {"node": "related_object", "object": "Case"},
                {"node": "create_event", "child_events": True, "delete_event": "never"},
                {"node": "update_field", "fields": [{"field": "Rating", "value": "Hot"}]},
                {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
                {
                    "node": "update_ownership",
                    "assign_to": "assignee",
                    "fallback_mode": "relationship",
                },
            ],
        },
        "booking": {
            "booking_ref": "demo-northwind-1",
            "subject": "Northwind - enterprise walkthrough",
            "starts_at": "2026-10-05T09:00:00Z",
            "ends_at": "2026-10-05T09:45:00Z",
            "booker": {"name": "Amara Okonkwo", "email": "a.buyer@northwind.example"},
            "guests": [{"name": "Bayo Toure", "email": "b.toure@northwind.example"}],
            "host": {"name": "Dana", "email": "dana@acme.example"},
            "assignee": {"name": "Sam", "email": "sam@acme.example"},
            "data_fields": {"seats": "40", "region": "EMEA"},
        },
    },
    {
        "room": 0,
        "meeting_type": None,
        "flow": {
            "name": "Northwind - not-scheduled writeback",
            "path": "not_scheduled",
            "vendor": "salesforce",
            "notes": (
                "[sourced] writes fire on the not-scheduled path automatically, so the "
                "not-scheduled path can carry a flow of its own."
            ),
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_lead_only",
                    "create": "always_lead",
                },
                {"node": "create_event", "child_events": False},
            ],
        },
        "booking": {
            "booking_ref": "demo-northwind-2",
            "subject": "Northwind - intro call (no meeting booked yet)",
            "starts_at": "2026-10-12T14:00:00Z",
            "booker": {"name": "Amara Okonkwo", "email": "a.buyer@northwind.example"},
            "host": {"name": "Dana", "email": "dana@acme.example"},
            "assignee": {"name": "Sam", "email": "sam@acme.example"},
        },
    },
    {
        "room": 1,
        "meeting_type": {
            "name": "Security review",
            "vendor": "salesforce",
            "event_type_id": "445512",
            "sync_to_crm": False,
            "notes": (
                "Sync Meeting Type to the CRM, off. The disqualified path fires, and the "
                "run is written and skipped rather than dropped."
            ),
        },
        "flow": {
            "name": "Contoso - disqualified writeback",
            "path": "disqualified",
            "vendor": "salesforce",
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
                {"node": "create_event", "child_events": False},
            ],
        },
        "booking": {
            "booking_ref": "demo-contoso-1",
            "subject": "Contoso - disqualified (no budget)",
            "starts_at": "2026-10-19T11:00:00Z",
            "booker": {"name": "Nadia Haddad", "email": "nadia.haddad@contoso.example"},
            "host": {"name": "Sam", "email": "sam@acme.example"},
            "assignee": {"name": "Dana", "email": "dana@acme.example"},
        },
    },
    {
        "room": 1,
        "meeting_type": {
            "name": "Intro call (match only)",
            "vendor": "salesforce",
            "event_type_id": "445515",
            "sync_to_crm": True,
            "notes": (
                "A flow that declares no create branch, on a meeting type whose own "
                "toggle is on - so the run fails for the researched reason rather than "
                "the toggle's, and the two skips are told apart."
            ),
        },
        "flow": {
            "name": "Contoso - match and update only",
            "path": "scheduled",
            "vendor": "salesforce",
            "notes": (
                "No create branch. A booking for an address the CRM has never seen "
                "therefore produces no record, every node after the anchor skips, and the "
                "run is not ok - which is the catch-all the ordering sentence forces into "
                "existence."
            ),
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "none",
                    "fields": [{"field": "Status", "value": "Sales Qualified"}],
                },
                {"node": "related_object", "object": "Case"},
                {"node": "create_event", "child_events": True},
                {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
                {"node": "update_ownership", "assign_to": "assignee"},
            ],
        },
        "booking": {
            "booking_ref": "demo-contoso-2",
            "subject": "Contoso - unknown contact, no create branch",
            "starts_at": "2026-10-21T10:00:00Z",
            # An address the seeded CRM has never seen, so the anchor produces
            # nothing and the four nodes after it have nothing to write to.
            "booker": {"name": "Stranger", "email": "nobody@unseen-prospect.example"},
            "host": {"name": "Sam", "email": "sam@acme.example"},
            "assignee": {"name": "Dana", "email": "dana@acme.example"},
        },
    },
    {
        "room": 2,
        "meeting_type": {
            "name": "Renewal walkthrough",
            "vendor": "salesforce",
            "event_type_id": "445513",
            "sync_to_crm": True,
        },
        "flow": {
            "name": "Fabrikam - opportunity relation",
            "path": "scheduled",
            "vendor": "salesforce",
            "notes": (
                "A Related Object of Opportunity, so 'the one that has the nearest Close "
                "Date' is exercised as a measurement against the meeting's date."
            ),
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                    "l2a": True,
                },
                {"node": "related_object", "object": "Opportunity"},
                {"node": "create_event", "child_events": True, "delete_event": "on_failure"},
            ],
        },
        "booking": {
            "booking_ref": "demo-fabrikam-1",
            "subject": "Fabrikam - renewal walkthrough",
            # Mid-October: the Q4 enterprise deal closes two days after, the pilot
            # closed a fortnight before, and the FY27 renewal is months away.
            "starts_at": "2026-10-05T13:00:00Z",
            "booker": {"name": "Lena Fischer", "email": "lena.fischer@northwind.example"},
            "guests": [
                {"name": "Omar Haddad", "email": "omar.haddad@northwind.example"},
                {"name": "Ines Costa", "email": "ines.costa@northwind.example"},
            ],
            "host": {"name": "Sam", "email": "sam@acme.example"},
            "assignee": {"name": "Dana", "email": "dana@acme.example"},
            "data_fields": {"seats": "25"},
        },
        # A fault the real engine hits, so the demo's failed Event is a row this
        # workflow produced rather than one written by hand.
        "faults": {
            "create:Event": "INSUFFICIENT_ACCESS: the integration user cannot create Events"
        },
    },
    {
        "room": 3,
        "meeting_type": {
            "name": "Pilot",
            "vendor": "hubspot",
            "event_type_id": "445514",
            "sync_to_crm": True,
        },
        "flow": {
            "name": "Adventure Works - HubSpot engagement",
            "path": "scheduled",
            "vendor": "hubspot",
            "notes": (
                "The HubSpot half: Create or Update Contact, Related Object of Ticket, "
                "Create Engagement with Activity Assigned to and a child Event per "
                "additional guest."
            ),
            "nodes": [
                {
                    "node": "create_or_update_contact",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                    "fields": [
                        {"field": "lifecyclestage", "value": "salesqualifiedlead"},
                        {"field": "seats", "from_data_field": "seats"},
                    ],
                },
                {"node": "related_object", "object": "Ticket"},
                {
                    "node": "create_engagement",
                    "child_events": True,
                    "activity_assigned_to": "booker",
                },
                {
                    "node": "update_ownership",
                    "assign_to": "host",
                    "fallback_mode": "attributeRules",
                    "attribute_rules": [
                        {
                            "field": "lifecyclestage",
                            "equals": "salesqualifiedlead",
                            "owner": "dana@acme.example",
                        },
                        {"field": "seats", "owner": "sam@acme.example"},
                    ],
                },
            ],
        },
        "booking": {
            "booking_ref": "demo-contoso-hs",
            "subject": "Contoso Health - security review",
            "starts_at": "2026-10-06T15:00:00Z",
            "booker": {"name": "Priya Raman", "email": "priya.raman@contoso.example"},
            "guests": [{"name": "Tomas Novak", "email": "tomas.novak@contoso.example"}],
            "host": {"name": "Sam", "email": "sam@acme.example"},
            "assignee": {"name": "Dana", "email": "dana@acme.example"},
            "data_fields": {"seats": "12"},
        },
    },
)


def seed(db, context: dict[str, Any]) -> str:
    """Seed two connectors, a CRM with mixed records, six meeting types and flows,
    and six real writebacks through the real engine.

    Every run in the demo is produced by running
    :class:`~dsr.booking_crm.engine.BookingWriteback` over the real
    :class:`~dsr.booking_crm.local_crm.LocalCrm`, so the demo cannot show a shape the
    workflow would not produce, and seeding never opens a socket. The one failed
    Event is reached by injecting a fault into the engine, not by writing a failed
    history row by hand.

    It is deliberately mixed, because a demo showing only green teaches a reviewer
    nothing. Between them the six runs produce:

    * a **clean scheduled** Salesforce run that matched a Contact, updated it,
      related the Event to the newest **Open** Case - skipping the *newer* Closed
      one, which is the rule doing its work - created a child Event per additional
      guest, created the CampaignMember with status ``Booked`` and reassigned the
      Owner through the ``relationship`` fallback;
    * a **not-scheduled** run whose update branch is ``Only update matched Lead``
      against a *Contact* match, so the branch declines to write, and whose create
      branch is ``Always create Lead``, so a Lead appears even though the email
      matched;
    * a **disqualified** run on a meeting type whose toggle is **off**: the run is
      written, skipped, and says why, which is the researched Sync Meeting Type
      behaviour;
    * a run that matched an address the CRM had never seen, on a flow declaring **no
      create branch**: nothing was produced, so all four nodes after the anchor skip
      with the same reason and the run is **not ok** - the catch-all, shown rather
      than described;
    * a run relating the Event to an **Opportunity**, so the *nearest Close Date*
      rule is visibly a measurement against the meeting rather than a sort;
    * a **HubSpot** run with ``Activity Assigned to`` set to the booker, a Ticket
      relation chosen by the same most-recent-Open rule, and an Owner resolved by
      ``attributeRules``;
    * and a run whose **Event creation was refused**, so Events History has a row
      with the timestamp, the detailed error and a retry - which the demo then
      performs, leaving an attempt 1 that failed beside an attempt 2 that did not.

    Returns a short description of what was added, which the seeder prints.
    """
    from dsr.booking_crm import BookingWriteback as Engine, LocalCrm as Crm

    store = RecordStore(db)
    engine = Engine(store)
    actor = "dana"
    source = "seed"

    connectors = 0
    for spec in DEMO_CONNECTORS:
        if not any(row["data"].get("name") == spec["name"] for row in engine.list_connectors()):
            engine.create_connector(spec, actor=actor, source=source)
            connectors += 1

    rooms: list[str] = [
        str(room_id)
        for room_id, _account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        # No demo rooms to attach to. The connectors and the CRM are still worth
        # having, and the seeder prints what was skipped. Filtered by existence
        # rather than trusted, because a seeder that aborts a whole feature over one
        # stale room id leaves a page nobody can review.
        crm = Crm(store)
        crm.seed(DEMO_CRM_RECORDS, source=source)
        return (
            f"{connectors} connectors, {len(DEMO_CRM_RECORDS)} CRM records, 0 flows "
            "(no rooms to scope them to)"
        )

    # One CRM per room, seeded with the same records: a room's CRM is that room's
    # org, and the rules are the rules wherever the rows are.
    crms: dict[str, Crm] = {}
    for room_id in rooms:
        crm = Crm(store, room_id=room_id)
        crm.seed(DEMO_CRM_RECORDS, source=source)
        crms[room_id] = crm

    created_flows = 0
    runs_ok = 0
    runs_skipped = 0
    events_created = 0
    events_failed = 0
    retried = 0
    no_record = 0

    for spec in DEMO_FLOWS:
        room_id = rooms[int(spec["room"]) % len(rooms)]
        crm = crms[room_id]
        meeting_type_spec = spec.get("meeting_type")
        if meeting_type_spec:
            meeting_type = engine.create_meeting_type(
                room_id, dict(meeting_type_spec), actor=actor, source=source
            )
        else:
            # A second flow on a meeting type that already exists, so the demo shows
            # one meeting type serving two paths rather than one type per flow.
            existing = [
                row
                for row in engine.list_meeting_types(room_id)
                if row["data"].get("name") == "Enterprise demo"
            ]
            meeting_type = existing[0]
        flow_spec = dict(spec["flow"])
        flow = engine.create_flow(
            room_id,
            flow_spec | {"meeting_type_id": str(meeting_type["id"])},
            actor=actor,
            source=source,
        )
        created_flows += 1

        if spec.get("faults"):
            crm.faults.update(dict(spec["faults"]))
        run = engine.writeback(
            room_id,
            str(flow["id"]),
            dict(spec["booking"]) | {"path": str(flow_spec["path"])},
            actor=actor,
            source=source,
            crm=crm,
        )
        # Unconditionally, before the retry: a fault left on the client would leak
        # into the next spec that happens to land in this room, and a demo that
        # fails for a reason two specs back is a demo nobody can debug.
        for key in spec.get("faults") or {}:
            crm.faults.pop(key, None)
        if run["data"].get("ok"):
            runs_ok += 1
        elif any(step["reason"] == "meeting_type_sync_off" for step in run["data"]["steps"]):
            runs_skipped += 1
        if any(
            step["reason"] == "nothing_matched_and_no_create_branch"
            for step in run["data"]["steps"]
        ):
            no_record += 1
        events_created += len(run["data"].get("created_events") or [])
        events_failed += int((run["data"].get("counts") or {}).get("failed") or 0)

        # The retry the research's step 5 describes, performed through the same
        # engine: an admin opening Meetings Activity and retrying a failed Event.
        # Only once, so the demo shows an attempt 1 that failed beside an attempt 2
        # that did not - the pair that makes the history readable.
        for row in engine.history(room_id, status="failed", limit=10):
            if int(row["data"].get("attempt") or 1) > 1:
                continue
            engine.retry(room_id, str(row["id"]), actor=actor, source=source, crm=crm)
            retried += 1
            break

    return (
        f"{connectors} connectors, {len(DEMO_CRM_RECORDS)} CRM records per room, "
        f"{created_flows} flows, {created_flows} writebacks: {runs_ok} completed, "
        f"{runs_skipped} stopped by the Sync Meeting Type toggle, {no_record} where "
        f"nothing matched and no create branch ran, "
        f"{events_created} Events created, {events_failed} Event creations refused and "
        f"then retried, and the rest of the researched rules exercised (the newest "
        "Closed Case losing to an older Open one, Always create Lead against a Contact "
        "match, Only update matched Lead declining to write, nearest Close Date chosen "
        "against the meeting, and Activity Assigned to carrying the booker)"
    )
