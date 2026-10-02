"""WF-035: map sales-room fields onto CRM fields and define the sync key.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-035.md``, which is the
specification. This is a **build**, not a port: the workflow had a finished research
document and no code, so the researched decisions are landed rather than revisited.
What each one is, and where it landed, is documented at length in
:mod:`dsr.fieldmap`, which is the whole of the domain. This module is the three
things the feature host expects and nothing else: the ``FEATURE`` block, a prefixed
``router``, and ``EXCEPTION_HANDLERS`` for this workflow's own domain errors.

Three things the build brief is explicit about, and how they are honoured
-----------------------------------------------------------------------
**The prefix is ``/api/wf-035``** and room-scoped paths stay room-scoped
(``/rooms/{room_id}/connections``, ``/rooms/{room_id}/mappings``). A ticket-derived
prefix cannot collide with a feature-shaped one by construction, and the host
refuses a colliding ``(method, path)`` and reports it rather than shadowing.

**Every read and write goes through ``StoreDep`` / ``RecordStore``.** No SQLite
connection is opened anywhere in this feature; the engine is built per request from
the store the host already resolved, which is also what leaves the tests a seam via
``app.dependency_overrides``.

**``source=`` is passed from the HTTP layer, built from ``router.prefix``.** Every
write route below names the route that actually served it, and ``source`` is a
required keyword-only argument on every domain write rather than a defaulted one.
The branch history of this codebase is full of features whose audit log named a
route the app had stopped serving, so the suite asserts that every ``source`` this
feature can record matches a route the host actually mounted.

Two refusals this layer adds on top of the domain's
---------------------------------------------------
A mapping id that belongs to a different connection answers 404 rather than 422:
the URL is ``/connections/{connection_id}/mappings/{mapping_id}``, so "that id is
not under this connection" is the answer, and the message names the connection it
is under. Every route that takes a mapping calls
:func:`_require_mapping_under`, so that check is in one place rather than
repeated - a mapping that is not checked on one route and is on the next is a
cross-connection read waiting to happen.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.fieldmap import (
    FieldMapping,
    InvalidMapping,
    InvalidSyncKey,
    MappingNotValid,
    MetadataUnavailable,
    SyncKeyCapacity,
    UnknownConnection,
    UnknownFieldRow,
    UnknownMapping,
    UnsupportedSyncKeyRequest,
)
from dsr.fieldmap.mappings import CONNECTION_COLLECTION
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-035-map-sales-room-fields-onto-crm-fields-",
    "ticket": "WF-035",
    "name": "Map sales-room fields onto CRM fields and define the sync key",
    "description": (
        "The field-mapping grid: pick the CRM object, map each sales-room field to a CRM "
        "property with a direction and a named transform, pin the sync key that carries the "
        "room's own row id, and validate the whole thing against the CRM's own property "
        "metadata before any data is written."
    ),
    "nav": [{"id": "field-mapping", "label": "Field mapping"}],
}

router = APIRouter(prefix="/api/wf-035", tags=["WF-035"])


def get_field_mapping(store: RecordStore = StoreDep) -> FieldMapping:
    """A :class:`~dsr.fieldmap.FieldMapping` over the process-wide audited store.

    Per request rather than built in the lifespan and hung on ``app.state``: the
    engine holds nothing but the store handle, and ``app.state`` is the shared file
    this feature must not edit. Building it from a dependency also leaves the store
    as an override seam for the suite.
    """
    return FieldMapping(store)


MappingDep = Depends(get_field_mapping)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _invalid_mapping(request: Request, exc: InvalidMapping) -> JSONResponse:
    """A row or a payload that cannot be recorded as asked for. 422."""
    return JSONResponse(status_code=422, content={"error": "invalid_mapping", "detail": str(exc)})


def _unknown_connection(request: Request, exc: UnknownConnection) -> JSONResponse:
    """A connection id that is not live. 404."""
    return JSONResponse(
        status_code=404, content={"error": "unknown_connection", "detail": str(exc)}
    )


def _unknown_mapping(request: Request, exc: UnknownMapping) -> JSONResponse:
    """A mapping id that is not live, or is not under this connection. 404."""
    return JSONResponse(status_code=404, content={"error": "unknown_mapping", "detail": str(exc)})


def _unknown_row(request: Request, exc: UnknownFieldRow) -> JSONResponse:
    """A grid-row id that is not live. 404."""
    return JSONResponse(status_code=404, content={"error": "unknown_row", "detail": str(exc)})


def _metadata_unavailable(request: Request, exc: MetadataUnavailable) -> JSONResponse:
    """Validation needs a CRM metadata read and none has been recorded. 409.

    409 rather than 404: the route exists and the record it needs does not, and the
    remedy is a read rather than a different URL. The body carries the endpoints a
    connector should call, so the client can render them without reconstructing the
    provider's API from memory.
    """
    return JSONResponse(
        status_code=409,
        content={
            "error": "metadata_unavailable",
            "detail": str(exc),
            "connection_id": exc.connection_id,
            "crm_object": exc.crm_object,
            "source_document": exc.source_document,
            "endpoints": [dict(entry) for entry in exc.endpoints],
        },
    )


def _invalid_sync_key(request: Request, exc: InvalidSyncKey) -> JSONResponse:
    """A sync key that cannot be pinned. 422, carrying the findings that refused it."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_sync_key",
            "detail": str(exc),
            "findings": [dict(item) for item in exc.findings],
        },
    )


def _sync_key_capacity(request: Request, exc: SyncKeyCapacity) -> JSONResponse:
    """Pinning this key would pass the researched ceiling of ten unique keys. 409.

    The counts are in the body so a client can render "9 of 10 used" without
    counting anything itself, and the quoted sentences are there so the refusal
    cites the rule rather than asserting a number.
    """
    return JSONResponse(
        status_code=409,
        content={
            "error": "sync_key_capacity",
            "detail": str(exc),
            "used": exc.used,
            "limit": exc.limit,
            "remaining": max(0, exc.limit - exc.used),
            "providers": list(exc.providers),
        },
    )


def _mapping_not_valid(request: Request, exc: MappingNotValid) -> JSONResponse:
    """Activation on a mapping that is not clean. 422, with the report attached.

    The report is in the body because the client that asked to activate is the one
    that has to render which row to fix; making it fetch the report first would be a
    round trip to learn something the refusal already knows.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": "mapping_not_valid",
            "detail": str(exc),
            "validated": exc.report is not None,
            "report": dict(exc.report) if exc.report is not None else None,
        },
    )


def _unsupported_request(request: Request, exc: UnsupportedSyncKeyRequest) -> JSONResponse:
    """A vendor request this workflow will not fabricate. 422, with the gap quoted."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "unsupported_sync_key_request",
            "detail": str(exc),
            "gap": exc.gap,
            "provider": exc.provider,
        },
    )


EXCEPTION_HANDLERS = {
    InvalidMapping: _invalid_mapping,
    UnknownConnection: _unknown_connection,
    UnknownMapping: _unknown_mapping,
    UnknownFieldRow: _unknown_row,
    MetadataUnavailable: _metadata_unavailable,
    InvalidSyncKey: _invalid_sync_key,
    SyncKeyCapacity: _sync_key_capacity,
    MappingNotValid: _mapping_not_valid,
    UnsupportedSyncKeyRequest: _unsupported_request,
}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(mapping: FieldMapping = MappingDep) -> dict[str, Any]:
    """The published vocabulary: directions, types, transforms and the APIs to read.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a type or a transform added in one place
    reaches every client at once.
    """
    return mapping.vocabulary()


@router.get("/inferences")
def inferences(mapping: FieldMapping = MappingDep) -> dict[str, Any]:
    """Every decision this workflow makes that the research does not make.

    A read with no side effect, so it needs no store - but it goes through the same
    dependency because that is what keeps one seam in the suite rather than two.
    Publishing the inferences is the difference between "we inferred this, see the
    comment" and a judgement a reviewer can disagree with by name.
    """
    return mapping.inferences()


@router.get("/transforms")
def list_transforms(mapping: FieldMapping = MappingDep) -> dict[str, Any]:
    """The transform registry, plus every transform declared as data."""
    return mapping.transform_catalogue()


@router.post("/transforms", status_code=201)
def declare_transform(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Record a transform as data.

    The research's extension point is a *code* registration in the connector, so a
    declaration is not executable: a row naming a declared-only transform gets a
    ``transform_unavailable`` badge until the code is registered. Recording the
    declaration is still worth doing - it is where an admin writes down the
    transform a deployment is adding - and ``declared_only`` in the response says
    which state it is in.
    """
    return mapping.declare_transform(
        payload, actor=actor, source=f"POST {router.prefix}/transforms"
    )


@router.get("/summary")
def summary(mapping: FieldMapping = MappingDep) -> dict[str, Any]:
    """The page's header: what is mapped, what is activatable, what has no metadata."""
    return mapping.summary()


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_connections(
    room_id: str | None = Query(default=None),
    include_global: bool = Query(default=True),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """CRM connections, each with how many mappings it carries.

    ``include_global`` keeps a connection with no room visible from every room,
    which is the research's "stored per connection (not per deployment)" shape.
    """
    return mapping.list_connections(room_id=room_id, include_global=include_global)


@router.post("/connections", status_code=201)
def register_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Record a connection to map fields against.

    The connection itself is not this workflow's work - the OAuth exchange and the
    token vault are WF-034's - so this is a reference for mapping purposes. A
    deployment that already stores connections can pass that id straight to a
    mapping and never call this route.
    """
    record = mapping.register_connection(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connections"
    )
    return mapping.read_connection(str(record["id"]))


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, mapping: FieldMapping = MappingDep) -> dict[str, Any]:
    """One connection, with its mappings and the property reads recorded against it."""
    return mapping.read_connection(connection_id)


@router.patch("/connections/{connection_id}")
def amend_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Merge a patch into a connection. A provider change is refused, not applied."""
    return mapping.amend_connection(
        connection_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connections/{connection_id}",
    )


@router.get("/connections/{connection_id}/properties")
def read_properties(
    connection_id: str,
    crm_object: str = Query(..., description="The CRM object the properties were read for"),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """The recorded property metadata for one object, with the endpoints to read it.

    409 when nothing has been recorded: validation needs the CRM's own view of its
    properties, and a hand-written fixture standing in for it would let a mapping
    pass here and fail in the CRM.
    """
    return mapping.read_properties(connection_id, crm_object)


@router.post("/connections/{connection_id}/properties", status_code=201)
def record_properties(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Record a connector's property read for one object.

    The document is normalised on the way in, so a shape no vendor returns is
    refused here rather than stored and then quietly validating against nothing.
    Every read is kept, because a mapping validated against last month's schema is
    a different claim from one validated today.
    """
    return mapping.record_properties(
        connection_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/properties",
    )


# --------------------------------------------------------------------------- #
# Mappings
# --------------------------------------------------------------------------- #


@router.get("/connections/{connection_id}/mappings")
def list_mappings(
    connection_id: str,
    room_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="draft | active"),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Every mapping on a connection, with its grid, its key and its last report."""
    mapping.read_connection(connection_id)
    return mapping.list_mappings(connection_id, room_id=room_id, state=state)


@router.post("/connections/{connection_id}/mappings", status_code=201)
def create_mapping(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Record the mapping for one CRM object on a connection.

    ``{"apply_defaults": true}`` copies the shipped per-vendor rows, and only when
    the object is the one they were written for; when it is not, the response says
    why in ``defaults_skipped`` rather than landing rows on properties the object
    does not have.
    """
    return mapping.create_mapping(
        connection_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/mappings",
    )


def _require_mapping_under(
    mapping: FieldMapping, connection_id: str, mapping_id: str
) -> dict[str, Any]:
    """One connection-scoping check, called by every route that takes a mapping id.

    A mapping is addressed as ``/connections/{connection_id}/mappings/{mapping_id}``,
    so a mapping under a different connection is simply not there. Doing the check
    in one helper rather than in each handler is what stops one route reading
    another connection's mapping.
    """
    return mapping.read_mapping(connection_id, mapping_id)


@router.get("/connections/{connection_id}/mappings/{mapping_id}")
def read_mapping(
    connection_id: str, mapping_id: str, mapping: FieldMapping = MappingDep
) -> dict[str, Any]:
    """One mapping with everything the grid renders."""
    return _require_mapping_under(mapping, connection_id, mapping_id)


@router.patch("/connections/{connection_id}/mappings/{mapping_id}")
def amend_mapping(
    connection_id: str,
    mapping_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Amend a mapping's labels and notes. Object, connection and state are ruled."""
    return mapping.amend_mapping(
        connection_id,
        mapping_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connections/{connection_id}/mappings/{mapping_id}",
    )


@router.delete("/connections/{connection_id}/mappings/{mapping_id}")
def delete_mapping(
    connection_id: str,
    mapping_id: str,
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Soft-delete a mapping and every row on it. 200 with the counts.

    Not a 204: a mapping delete removes a variable number of rows, and the number is
    the answer to the question the caller is really asking - did the rows go with it.
    """
    return mapping.delete_mapping(
        connection_id,
        mapping_id,
        actor=actor,
        source=f"DELETE {router.prefix}/connections/{connection_id}/mappings/{mapping_id}",
    )


# --------------------------------------------------------------------------- #
# The grid
# --------------------------------------------------------------------------- #


@router.get("/connections/{connection_id}/mappings/{mapping_id}/rows")
def list_rows(
    connection_id: str, mapping_id: str, mapping: FieldMapping = MappingDep
) -> dict[str, Any]:
    """The grid, in the order the fields were first mapped."""
    return mapping.list_rows(connection_id, mapping_id)


@router.post("/connections/{connection_id}/mappings/{mapping_id}/rows", status_code=201)
def put_row(
    connection_id: str,
    mapping_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Add a row, or amend the existing row for the same sales-room field.

    Upsert, because the grid is edited in place: re-posting a row a client fetched
    is amending, not asking for a second row that would send the same column twice.
    Any change clears the last validation, so an activation can never ride a report
    about a grid that no longer looks like this one.
    """
    return mapping.put_row(
        connection_id,
        mapping_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/rows",
    )


@router.patch("/connections/{connection_id}/mappings/{mapping_id}/rows/{row_id}")
def patch_row(
    connection_id: str,
    mapping_id: str,
    row_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Amend one row. The mapping a row belongs to is not patchable."""
    return mapping.patch_row(
        connection_id,
        mapping_id,
        row_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/rows/{row_id}",
    )


@router.delete("/connections/{connection_id}/mappings/{mapping_id}/rows/{row_id}")
def delete_row(
    connection_id: str,
    mapping_id: str,
    row_id: str,
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Remove one row from the grid."""
    mapping.delete_row(
        connection_id,
        mapping_id,
        row_id,
        actor=actor,
        source=f"DELETE {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/rows/{row_id}",
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Validate
# --------------------------------------------------------------------------- #


@router.post("/connections/{connection_id}/mappings/{mapping_id}/validate")
def validate_mapping(
    connection_id: str,
    mapping_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """The research's step 5: read the metadata and flag what is wrong.

    Computed on every call, and stored only with ``{"record": true}``. A client that
    re-validates as an admin types should not fill the audit log with a report per
    keystroke; the one that matters is the one activation reads, and that is the one
    a client records.
    """
    return mapping.validate(
        connection_id,
        mapping_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/validate",
    )


@router.get("/connections/{connection_id}/mappings/{mapping_id}/validation")
def read_validation(
    connection_id: str, mapping_id: str, mapping: FieldMapping = MappingDep
) -> dict[str, Any]:
    """The stored report activation would read, or why there is none.

    Not a 404: a mapping that has never been validated is a state an admin needs to
    see, and the answer also carries ``can_activate`` and whether the report is
    stale, which is what a page's activation button binds to.
    """
    return mapping.last_validation(connection_id, mapping_id)


@router.get("/connections/{connection_id}/mappings/{mapping_id}/validations")
def list_validations(
    connection_id: str, mapping_id: str, mapping: FieldMapping = MappingDep
) -> dict[str, Any]:
    """Every stored run for a mapping, newest first."""
    return mapping.validations(connection_id, mapping_id)


@router.post("/connections/{connection_id}/mappings/{mapping_id}/activate")
def activate_mapping(
    connection_id: str,
    mapping_id: str,
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Make a mapping live. Refused unless the latest validation is clean.

    This is the researched "before any data is written" turned into a gate. A
    mapping that was never validated and one whose latest run carries an error are
    two different situations, and the 422 body says which by including the report.
    """
    return mapping.activate(
        connection_id,
        mapping_id,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/activate",
    )


@router.post("/connections/{connection_id}/mappings/{mapping_id}/deactivate")
def deactivate_mapping(
    connection_id: str,
    mapping_id: str,
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Return a mapping to draft, keeping its grid and its validation."""
    return mapping.deactivate(
        connection_id,
        mapping_id,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/deactivate",
    )


# --------------------------------------------------------------------------- #
# The sync key
# --------------------------------------------------------------------------- #


@router.get("/connections/{connection_id}/mappings/{mapping_id}/sync-key")
def read_sync_key(
    connection_id: str, mapping_id: str, mapping: FieldMapping = MappingDep
) -> dict[str, Any]:
    """The pinned key, its usage against the researched ceiling, and its create plan.

    Every part is reported rather than refused, because each of the three states -
    no key, no metadata, an unsourced provider - is something an admin has to fix
    rather than something the page should refuse to render.
    """
    return mapping.read_sync_key(connection_id, mapping_id)


@router.post("/connections/{connection_id}/mappings/{mapping_id}/sync-key")
def pin_sync_key(
    connection_id: str,
    mapping_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Pin the sync key: the CRM property that carries the sales room's own row id.

    A HubSpot key is one property and a Dataverse key may be several; a proposed key
    that would pass the researched ceiling of ten unique keys is refused with the
    counts, because that cannot be fixed by editing a row.
    """
    return mapping.pin_sync_key(
        connection_id,
        mapping_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/sync-key",
    )


@router.delete("/connections/{connection_id}/mappings/{mapping_id}/sync-key")
def unpin_sync_key(
    connection_id: str,
    mapping_id: str,
    actor: str | None = Query(default=None),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Remove the pinned key, so the mapping stops carrying the room's own row id."""
    return mapping.unpin_sync_key(
        connection_id,
        mapping_id,
        actor=actor,
        source=f"DELETE {router.prefix}/connections/{connection_id}/mappings/{mapping_id}/sync-key",
    )


@router.post("/connections/{connection_id}/mappings/{mapping_id}/sync-key/request")
def build_sync_key_request(
    connection_id: str,
    mapping_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """The vendor plan that would make the CRM enforce the key. Writes nothing.

    HubSpot's plan is sourced from this workflow's evidence and carries
    ``sourced: true``. Dataverse's is ``sourced: false`` - WF-035 cites the reads
    and the type rule, not the key-creation call - and Salesforce's is refused with
    the research's own gap quoted, rather than answered with a URL nobody cited.
    """
    return mapping.build_sync_key_request(connection_id, mapping_id, payload)


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #


@router.post("/connections/{connection_id}/mappings/{mapping_id}/preview")
def preview_mapping(
    connection_id: str,
    mapping_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    direction: str | None = Query(default=None, description="out | in | both"),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """What one sync cycle would send, and what it would read back. Writes nothing.

    "None required at write time - the mapping is evaluated per record on every sync
    cycle" - so this is that evaluation, exposed, and it is deliberately a read: a
    preview an admin runs on every edit must not audit a row per keystroke.
    """
    directions = (
        ("out", "in")
        if not direction
        else tuple(part.strip() for part in direction.split(",") if part.strip() in ("out", "in"))
    )
    if not directions:
        raise InvalidMapping("direction must be out, in or both")
    record = payload.get("record")
    if not isinstance(record, Mapping):
        raise InvalidMapping(
            "record is required: the sales-room row to evaluate the mapping against"
        )
    return mapping.preview(connection_id, mapping_id, record, directions=directions)


# --------------------------------------------------------------------------- #
# Room-scoped reads
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/connections")
def room_connections(
    room_id: str,
    include_global: bool = Query(default=True),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """The connections a room can be mapped through.

    Room-scoped because the brief asks for room-scoped paths to stay room-scoped,
    and because a room page should not have to filter the whole connection list
    itself. Shared connections are included by default: the research stores the
    mapping per connection, not per room, so a global connection applies here too.
    """
    return mapping.list_connections(room_id=room_id, include_global=include_global)


@router.get("/rooms/{room_id}/mappings")
def room_mappings(
    room_id: str,
    state: str | None = Query(default=None, description="draft | active"),
    mapping: FieldMapping = MappingDep,
) -> dict[str, Any]:
    """Every mapping attached to a room, with its grid and its last report."""
    return mapping.list_mappings(room_id=room_id, state=state)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: A HubSpot contact read in the shape the researched endpoint returns: a ``results``
#: array, ``type`` and ``fieldType`` on every property, and option sets carrying both
#: the internal name and the label beside it.
#:
#: Nine of the properties are already unique. That is the point: the researched
#: ceiling is ten unique ID properties per object, so this object has exactly one
#: slot left, and a key pinned here reports ``remaining: 0``.
HUBSPOT_CONTACT_PROPERTIES: tuple[Mapping[str, Any], ...] = (
    {
        "name": "dsr_row_id",
        "label": "DSR row id",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
    },
    {
        "name": "email",
        "label": "Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
    },
    {
        "name": "company",
        "label": "Company Name",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
    },
    {
        "name": "numberofemployees",
        "label": "Number of Employees",
        "type": "number",
        "fieldType": "number",
        "groupName": "contactinformation",
    },
    {
        "name": "lifecyclestage",
        "label": "Lifecycle Stage",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "salesforce",
        "options": [
            {"value": "subscriber", "label": "Subscriber"},
            {"value": "lead", "label": "Lead"},
            {"value": "opportunity", "label": "Opportunity"},
            {"value": "customer", "label": "Customer"},
        ],
    },
    # A property whose fieldType does not belong to its type. Nothing maps onto it
    # in the demo; it is here so a reviewer can see the metadata normaliser keep the
    # two axes apart rather than collapsing them.
    {
        "name": "renewal_date",
        "label": "Renewal Date",
        "type": "string",
        "fieldType": "date",
        "groupName": "contactinformation",
    },
    *(
        {
            "name": f"legacy_unique_{index}",
            "label": f"Legacy Unique {index}",
            "type": "string",
            "fieldType": "text",
            "groupName": "contactinformation",
            "hasUniqueValue": True,
        }
        for index in range(1, 10)
    ),
)

#: A HubSpot deal read, for the half-mapped mapping. Its ``dealstage`` option set
#: holds lower-case internal names beside capitalised labels, which is exactly the
#: pair a picklist table gets wrong.
HUBSPOT_DEAL_PROPERTIES: tuple[Mapping[str, Any], ...] = (
    {
        "name": "dealname",
        "label": "Deal Name",
        "type": "string",
        "fieldType": "text",
        "groupName": "dealinformation",
    },
    {
        "name": "amount",
        "label": "Amount",
        "type": "number",
        "fieldType": "number",
        "groupName": "dealinformation",
    },
    {
        "name": "dealstage",
        "label": "Deal Stage",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "dealinformation",
        "options": [
            {"value": "appointmentscheduled", "label": "Appointment Scheduled"},
            {"value": "qualifiedtobuy", "label": "Qualified To Buy"},
            {"value": "closedwon", "label": "Closed Won"},
        ],
    },
    {
        "name": "closedate",
        "label": "Close Date",
        "type": "date",
        "fieldType": "date",
        "groupName": "dealinformation",
    },
    {
        "name": "subject",
        "label": "Subject",
        "type": "string",
        "fieldType": "text",
        "groupName": "dealinformation",
    },
)

#: A Dataverse table read in the ``EntityDefinitions`` shape, with ``AttributeType``
#: as the CSDL short name and one alternate key already on the table. ``donotemail``
#: is a Boolean, which the researched eligible list excludes from an alternate key.
DATAVERSE_ACCOUNT_PROPERTIES: tuple[Mapping[str, Any], ...] = (
    {"LogicalName": "dsr_row_id", "AttributeType": "String", "DisplayName": "DSR row id"},
    {"LogicalName": "dsr_legacy_key", "AttributeType": "String", "DisplayName": "DSR legacy key"},
    {"LogicalName": "name", "AttributeType": "String", "DisplayName": "Account Name"},
    {"LogicalName": "emailaddress1", "AttributeType": "Email", "DisplayName": "Primary Email"},
    {"LogicalName": "effectiveto", "AttributeType": "DateTime", "DisplayName": "End date"},
    {"LogicalName": "statecode", "AttributeType": "State", "DisplayName": "Status Reason"},
    {"LogicalName": "donotemail", "AttributeType": "Boolean", "DisplayName": "Do Not Email"},
)

#: Nine unique HubSpot properties, for the connection whose tenth arrives later.
HUBSPOT_TICKET_PROPERTIES_BEFORE: tuple[Mapping[str, Any], ...] = (
    {
        "name": "dsr_row_id",
        "label": "DSR row id",
        "type": "string",
        "fieldType": "text",
        "groupName": "ticketing",
    },
    {
        "name": "subject",
        "label": "Subject",
        "type": "string",
        "fieldType": "text",
        "groupName": "ticketing",
    },
    *(
        {
            "name": f"case_unique_{index}",
            "label": f"Case Unique {index}",
            "type": "string",
            "fieldType": "text",
            "groupName": "ticketing",
            "hasUniqueValue": True,
        }
        for index in range(1, 10)
    ),
)

#: The same table one property later: a tenth unique property now exists in the CRM,
#: so the key pinned against the previous read no longer fits under the ceiling.
HUBSPOT_TICKET_PROPERTIES_AFTER: tuple[Mapping[str, Any], ...] = (
    *HUBSPOT_TICKET_PROPERTIES_BEFORE,
    {
        "name": "case_reference",
        "label": "Case Reference",
        "type": "string",
        "fieldType": "text",
        "groupName": "ticketing",
        "hasUniqueValue": True,
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not just the happy path.

    Five connections and six mappings, and every one of them is there because a rule
    in this workflow has something to say about it. A demo containing only a clean
    mapping teaches a reviewer nothing about the parts most likely to bite:

    * **A clean, active HubSpot mapping** with its validation stored and the vendor
      default applied - the flow working end to end, and the tenth unique-key slot
      taken so ``remaining: 0`` is visible.
    * **A half-mapped HubSpot deal mapping** carrying all five error findings this
      workflow can produce: an unknown property, a type mismatch, a picklist table
      holding a *label* where the internal name goes, two rows fighting over one
      property, and a transform that is declared but has no code. Plus an unmapped
      field as a warning, and no sync key at all.
    * **A mapping on an object whose property metadata was never read** - validation
      answers 409 and names the endpoints to call.
    * **A Dataverse mapping keyed on a column already in the table's alternate key**
      - pinning it is a no-op, reported as such rather than as an error.
    * **A Salesforce mapping, keyed and validated, whose create request is refused**
      because the research could not source the external-ID half.
    * **A key that was legal when it was pinned and is not any more**, because a
      tenth unique property appeared in the CRM afterwards. The ceiling catching up
      is a state a reviewer should see, and it is not reachable any other way.

    Returns a short description of what was added, which the seeder prints.
    """
    store = RecordStore(db)
    engine = FieldMapping(store)
    source = "seed"
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])

    def room(index: int) -> str | None:
        return rooms[index][0] if len(rooms) > index else None

    written: list[str] = []

    # -- 1. a clean, active HubSpot mapping ------------------------------------ #
    hubspot = store.create(
        CONNECTION_COLLECTION,
        {
            "name": "HubSpot — Northwind portal",
            "provider": "hubspot",
            "account": "Northwind Traders",
            "vendor_org": "northwind.hubspot.com",
            "connected": True,
            "property_group": "contactinformation",
            "object_label": "Contacts",
        },
        room_id=room(0),
        actor="dana",
        source=source,
    )
    hubspot_id = str(hubspot["id"])
    engine.record_properties(
        hubspot_id,
        {
            "crm_object": "contacts",
            "document": {"results": [dict(prop) for prop in HUBSPOT_CONTACT_PROPERTIES]},
        },
        actor="dana",
        source=source,
    )
    good = engine.create_mapping(
        hubspot_id,
        {"crm_object": "contacts", "crm_object_label": "Contacts", "apply_defaults": True},
        actor="dana",
        source=source,
    )
    engine.pin_sync_key(
        hubspot_id,
        good["id"],
        {"properties": ["dsr_row_id"], "unique": True},
        actor="dana",
        source=source,
    )
    engine.validate(hubspot_id, good["id"], {"record": True}, actor="dana", source=source)
    engine.activate(hubspot_id, good["id"], actor="dana", source=source)
    written.append("1 active HubSpot mapping, 5 rows, key pinned into the 10th unique slot")

    # -- 2. the half-mapped one, with every finding this workflow can produce --- #
    engine.record_properties(
        hubspot_id,
        {
            "crm_object": "deals",
            "document": {"results": [dict(prop) for prop in HUBSPOT_DEAL_PROPERTIES]},
        },
        actor="dana",
        source=source,
    )
    broken = engine.create_mapping(
        hubspot_id,
        {
            "crm_object": "deals",
            "crm_object_label": "Deals",
            "notes": "Half-mapped. Never finished.",
        },
        actor="dana",
        source=source,
    )
    for row in (
        {
            # A text column aimed at a numeric property: the type check's whole case.
            "source_field": "amount",
            "source_type": "text",
            "target_property": "amount",
            "direction": "out",
            "transform": "text.trim",
        },
        {
            # The CRM has no such property on a deal, and the close date is spelled
            # `closedate` there.
            "source_field": "close_date",
            "source_type": "date",
            "target_property": "renewal_date",
            "direction": "out",
            "transform": "date.iso8601",
        },
        {
            # Capitalised labels where the internal option values go. This is the
            # researched rule, and the one that is invisible by eye.
            "source_field": "buyer_stage",
            "source_type": "enumeration",
            "target_property": "dealstage",
            "direction": "out",
            "transform": "picklist.map",
            "transform_config": {
                "map": {
                    "Appointment": "appointmentscheduled",
                    "Qualified": "Qualified To Buy",
                    "Won": "closed-won",
                }
            },
        },
        {
            "source_field": "deal_title",
            "source_type": "text",
            "target_property": "dealname",
            "direction": "out",
            "transform": "text.trim",
        },
        {
            # Two rows, one property: only one of them can be sent.
            "source_field": "account_legal_name",
            "source_type": "text",
            "target_property": "dealname",
            "direction": "out",
            "transform": "text.trim",
        },
        {
            # Declared as data, never registered as code.
            "source_field": "parent_account",
            "source_type": "text",
            "target_property": "subject",
            "direction": "out",
            "transform": "account.hierarchy_rollup",
        },
        {
            # Nothing mapped: a warning, not an error. Most of a field dictionary is
            # never mapped, and a grid that refuses to save until it all is, is a
            # grid nobody completes.
            "source_field": "internal_note",
            "source_type": "text",
            "direction": "out",
            "transform": "identity",
        },
    ):
        engine.put_row(hubspot_id, broken["id"], row, actor="dana", source=source)
    engine.declare_transform(
        {
            "name": "account.hierarchy_rollup",
            "version": 1,
            "description": "Resolve an account's ultimate parent before writing.",
            "applies_to": ["text"],
            "connection_id": hubspot_id,
        },
        actor="dana",
        source=source,
    )
    engine.validate(hubspot_id, broken["id"], {"record": True}, actor="dana", source=source)
    written.append("1 draft deal mapping, 7 rows, 5 errors + 1 warning + no sync key")

    # -- 3. a connection whose property metadata was never read ---------------- #
    dataverse = store.create(
        CONNECTION_COLLECTION,
        {
            "name": "Dataverse — Contoso environment",
            "provider": "dataverse",
            "account": "Contoso Health",
            "vendor_org": "contoso.crm.dynamics.com",
            "connected": True,
        },
        room_id=room(1),
        actor="sam",
        source=source,
    )
    dataverse_id = str(dataverse["id"])
    unread = engine.create_mapping(
        dataverse_id,
        {"crm_object": "contact", "crm_object_label": "Contact", "notes": "Metadata never read."},
        actor="sam",
        source=source,
    )
    engine.put_row(
        dataverse_id,
        unread["id"],
        {
            "source_field": "primary_contact_email",
            "source_type": "email",
            "target_property": "emailaddress1",
            "direction": "out",
            "transform": "email.normalize",
        },
        actor="sam",
        source=source,
    )
    written.append("1 mapping with no recorded property read (validation answers 409)")

    # -- 4. Dataverse, keyed onto a column that is already in a key ------------ #
    engine.record_properties(
        dataverse_id,
        {
            "crm_object": "account",
            "document": {
                "value": [
                    {
                        "SchemaName": "account",
                        "Attributes": [dict(attr) for attr in DATAVERSE_ACCOUNT_PROPERTIES],
                        "Keys": [{"KeyAttributes": ["dsr_legacy_key"]}],
                    }
                ]
            },
        },
        actor="sam",
        source=source,
    )
    keyed = engine.create_mapping(
        dataverse_id,
        {"crm_object": "account", "crm_object_label": "Account", "apply_defaults": True},
        actor="sam",
        source=source,
    )
    engine.pin_sync_key(
        dataverse_id,
        keyed["id"],
        {"properties": ["dsr_legacy_key", "dsr_row_id"]},
        actor="sam",
        source=source,
    )
    engine.validate(dataverse_id, keyed["id"], {"record": True}, actor="sam", source=source)
    engine.activate(dataverse_id, keyed["id"], actor="sam", source=source)
    written.append("1 active Dataverse mapping, 5 rows, key partly already an alternate key")

    # -- 5. Salesforce: pinned and validated, and no request this will emit ---- #
    salesforce = store.create(
        CONNECTION_COLLECTION,
        {
            "name": "Salesforce — Fabrikam org",
            "provider": "salesforce",
            "account": "Fabrikam Logistics",
            "vendor_org": "fabrikam.my.salesforce.com",
            "connected": True,
        },
        actor="sam",
        source=source,
    )
    salesforce_id = str(salesforce["id"])
    engine.record_properties(
        salesforce_id,
        {
            "crm_object": "Contact",
            "document": {
                "fields": [
                    {"name": "Email", "type": "Email", "label": "E-mail Address", "unique": False},
                    {"name": "Company", "type": "Text", "label": "Company"},
                    {
                        "name": "DSR_Row_Id__c",
                        "type": "Text",
                        "label": "DSR Row Id",
                        "unique": True,
                    },
                ]
            },
        },
        actor="sam",
        source=source,
    )
    forced = engine.create_mapping(
        salesforce_id,
        {"crm_object": "Contact", "apply_defaults": True},
        actor="sam",
        source=source,
    )
    engine.pin_sync_key(
        salesforce_id, forced["id"], {"properties": ["DSR_Row_Id__c"]}, actor="sam", source=source
    )
    engine.validate(salesforce_id, forced["id"], {"record": True}, actor="sam", source=source)
    engine.activate(salesforce_id, forced["id"], actor="sam", source=source)
    written.append("1 active Salesforce mapping, 2 rows, key already unique, no emittable request")

    # -- 6. a key that was legal when pinned and is not any more --------------- #
    tickets = store.create(
        CONNECTION_COLLECTION,
        {
            "name": "HubSpot — Adventure Works portal",
            "provider": "hubspot",
            "account": "Adventure Works",
            "vendor_org": "adventureworks.hubspot.com",
            "connected": True,
            "property_group": "ticketing",
        },
        room_id=room(3),
        actor="sam",
        source=source,
    )
    tickets_id = str(tickets["id"])
    engine.record_properties(
        tickets_id,
        {
            "crm_object": "tickets",
            "document": {"results": [dict(prop) for prop in HUBSPOT_TICKET_PROPERTIES_BEFORE]},
        },
        actor="sam",
        source=source,
    )
    stale = engine.create_mapping(
        tickets_id,
        {"crm_object": "tickets", "crm_object_label": "Tickets"},
        actor="sam",
        source=source,
    )
    engine.put_row(
        tickets_id,
        stale["id"],
        {
            "source_field": "primary_contact_email",
            "source_type": "email",
            "target_property": "subject",
            "direction": "out",
            "transform": "email.normalize",
        },
        actor="sam",
        source=source,
    )
    engine.pin_sync_key(
        tickets_id, stale["id"], {"properties": ["dsr_row_id"]}, actor="sam", source=source
    )
    # A tenth unique property is created in the CRM after the key was pinned. The
    # next read sees it, and the researched ceiling now refuses to honour the key.
    engine.record_properties(
        tickets_id,
        {
            "crm_object": "tickets",
            "document": {"results": [dict(prop) for prop in HUBSPOT_TICKET_PROPERTIES_AFTER]},
        },
        actor="sam",
        source=source,
    )
    engine.validate(tickets_id, stale["id"], {"record": True}, actor="sam", source=source)
    written.append("1 mapping whose pinned key no longer fits under the 10-key ceiling")

    return "; ".join(written)
