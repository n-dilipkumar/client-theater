"""WF-036: provision the sales-room engagement object and its fields into the CRM.

The researched workflow, in full. An admin runs *Integrations -> <connection> ->
Install integration package* (or a CI job calls the same endpoint); the sales room
compares its own object descriptor against the CRM's live schema, **creates only
what is missing**, and records the mapping from room-object-id to CRM-object-id so
later syncs write into a first-class CRM-native object rather than free text.

The domain logic is in :mod:`dsr.crm_provisioning`, which this module does not own
and which no other feature could have written into its own path. What lives here is
the three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-036``, and provisioning is scoped to a *connection*.**
The research never mentions a room in this workflow. The engagement object is one
artefact per CRM account, and putting a room in the key would let two rooms in one
account install it twice under two ids - which is the same trap the intent-signal
registry hit when a registration key included the room. So the installer is
connection-scoped and the room appears on the *reads*, under
``/rooms/{room_id}/...``, which is where the research actually puts it: "the room
now has a first-class, CRM-native object to write engagement rows into" is a
question about the room.

**``source=`` comes from the route.** Every write below passes a string built from
``router.prefix`` and the route's own path template, so the audit row names the
route that actually served it. A hardcoded string inside a domain method is a
defect, and the same class of bug has shipped in this codebase before: a feature's
audit log kept naming a path the app had stopped serving. ``source`` is a
*required* keyword on every writing method of
:class:`~dsr.crm_provisioning.engine.ProvisioningEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.** ``ProvisioningError`` is the base of
every refusal in :mod:`dsr.crm_provisioning`, and each carries its own ``status``
and ``code`` on the exception, so one handler can answer 400 for a manifest it
cannot read, 409 for a manifest version already published with a different body,
and 422 for a vendor the research could not source - without being told which. It
is a domain type, so registering it globally cannot intercept anything unrelated
elsewhere in the product. ``RecordNotFound`` is deliberately *not* claimed: the
core app already maps it to 404, and two handlers for one type is a collision the
host refuses.

**The diff is a read, and a preview is a run record.** ``GET /diff`` and
``POST /install`` are two routes for one decision, because the research lists both
surfaces: the "sales-room package-installer surface with a dry-run diff view" and
the "same endpoint" a CI job calls. ``POST /install`` accepts ``dry_run``, stores
one run record saying the run was a preview and created nothing, and writes no
object, no property and no key - which is checkable, because the run says
``created: 0`` and the other collections are untouched.

**A manifest is data.** The research's extensibility claim is that "the room's
object descriptor is data, not code - a deployment ships its own manifest", so
``POST /manifests`` takes an arbitrary descriptor and ``GET /manifests/{id}``
reports its findings for both researched vendors before anything is installed. The
shipped engagement manifest is a constant in *this* module purely so the seeder
can register it, and it is registered as a record like any team's would be.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm_provisioning import ProvisioningEngine
from dsr.crm_provisioning.errors import ProvisioningError
from dsr.crm_provisioning.gateway import SimulatedCrm
from dsr.crm_provisioning.inferences import describe as describe_inferences
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-036-provision-the-sales-room-engagement-ob",
    "ticket": "WF-036",
    "name": "Provision the sales-room engagement object and its fields into the CRM",
    "description": (
        "Install a versioned manifest into a CRM connection: read the live schema, create only "
        "the object and properties that are missing, request the sync key where one is sourced, "
        "and record the room-object-id to CRM-object-id mapping for later syncs. Re-running the "
        "installer is a no-op, and no field is ever renamed or dropped."
    ),
    "nav": [{"id": "crm-provisioning", "label": "CRM provisioning"}],
}

router = APIRouter(prefix="/api/wf-036", tags=["wf036"])


def get_engine(store: RecordStore = StoreDep) -> ProvisioningEngine:
    """A :class:`ProvisioningEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the vendor seam a plain constructor argument, which is what a
    test substitutes.
    """
    return ProvisioningEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _provisioning_error(request: Request, exc: ProvisioningError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``ProvisioningError`` is the base of every
    refusal in :mod:`dsr.crm_provisioning` - a manifest that cannot be read, a
    version already published with a different body, a key that violates the
    vendor's own index limit, a vendor the research could not source - and all of
    them are the caller's to fix. The status rides on the exception rather than
    being decided here, because a re-registration of a published manifest version
    and a manifest with no object name are both this package's errors and only one
    of them conflicts with state that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {ProvisioningError: _provisioning_error}


# --------------------------------------------------------------------------- #
# Published vocabulary and the judgement register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """Every value this workflow enforces against, plus both vendor surfaces.

    Including each adapter's full type map, so a reader can check the diff's
    request bodies against what a vendor expects without reading the code. A team
    adding a field type ships a mapping here rather than a change to any page.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the endpoints, the required property fields, the two key
    limits and the four index statuses, and it states its own gap about Salesforce
    in those words. It says nothing about a simulated vendor, about which manifest
    problems refuse a run, or about how a key column's width is measured. Those are
    collected in :mod:`dsr.crm_provisioning.inferences` and served here, beside the
    sourced half, so the line between them is something a reviewer reads rather
    than something they infer from a diff.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_connections(
    room_id: str | None = Query(default=None, description="only connections bound to this room"),
    vendor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """The CRM accounts this deployment can install into.

    A connection for a vendor the research could not source is listed rather than
    hidden, carrying the reason in ``unsupported_reason``. Hiding it would make the
    page look tidy and leave the refusal to be discovered at install time, with no
    context for why.
    """
    listed = engine.connections(room_id=room_id, vendor=vendor)
    return {
        "count": len(listed),
        "unsupported": sum(1 for row in listed if not row.get("supported", True)),
        "connections": listed,
    }


@router.post("/connections", status_code=201)
def create_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Register a CRM account the installer can provision into.

    The researched flow opens at an admin pressing Install on a connection that
    already exists, so this route is the minimum needed to make the workflow
    runnable here rather than a claim about the research. A vendor outside the
    researched pair is *accepted* and reported as unsupported, so the account can
    be recorded; the refusal comes from the install a human actually runs.
    """
    return engine.register_connection(
        payload, actor=actor, source=f"POST {router.prefix}/connections"
    )


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One connection, with what has been installed into it."""
    connection = engine.connection(connection_id)
    objects = engine.objects(connection_id=connection_id)
    return {
        "connection": connection,
        "objects": objects,
        "keys": engine.keys(connection_id=connection_id),
        "installations": len(engine.runs(connection_id=connection_id)),
    }


# --------------------------------------------------------------------------- #
# Manifests: the versioned package descriptor
# --------------------------------------------------------------------------- #


@router.get("/manifests")
def list_manifests(
    manifest_id: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Every registered manifest version, with how many versions each id has.

    More than one version is the normal state, not a mistake: the research calls
    the descriptor "a sales-room package manifest (versioned)", so a deployment
    that has added a field ships a new version and the old one stays readable.
    """
    listed = engine.manifests(manifest_id=manifest_id)
    versions: dict[str, list[str]] = {}
    for row in listed:
        versions.setdefault(str(row.get("manifest_id")), []).append(str(row.get("version")))
    return {"count": len(listed), "versions": versions, "manifests": listed}


@router.post("/manifests", status_code=201)
def create_manifest(
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Register a versioned manifest, then see what it would do.

    Re-registering a version with the *same* body answers 200 and reports
    ``unchanged``: a pipeline that runs on every deploy must not fail on a file
    that has not moved. A different body for a version already on file is 409,
    because an install that ran against version 1.0.0 must still be able to say
    later that the manifest it installed was this body.

    The response carries the manifest's findings for both researched vendors, so a
    team learns that a manifest skips a property on HubSpot before it installs
    anything rather than after.
    """
    result = engine.register_manifest(
        payload, actor=actor, source=f"POST {router.prefix}/manifests"
    )
    if result.get("outcome") == "unchanged" and response is not None:
        response.status_code = 200
    report = engine.manifest_report(str(result.get("manifest_id")), str(result.get("version")))
    return {**result, "report": report}


@router.get("/manifests/{manifest_id}")
def read_manifest(
    manifest_id: str,
    version: str | None = Query(default=None, description="omit for the highest declared version"),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """One manifest, and every finding against every vendor.

    The last vendor in the list is one the research names and could not source.
    It appears with ``installable: false`` and the research's own gap sentence, so
    a Salesforce deployment reads why rather than finding an empty entry.
    """
    return engine.manifest_report(manifest_id, version)


# --------------------------------------------------------------------------- #
# The installer
# --------------------------------------------------------------------------- #


@router.get("/diff")
def diff(
    connection_id: str = Query(...),
    manifest_id: str = Query(...),
    version: str | None = Query(default=None, description="omit for the highest declared version"),
    vendor: str | None = Query(
        default=None, description="diff against this vendor instead of the connection's"
    ),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """The dry-run diff view: exactly what an install would do, writing nothing.

    Steps two and three of the researched flow, split so the decision can be read
    before it is taken. Every manifest property is ``create``, ``unchanged``,
    ``conflict``, ``unmappable`` or ``left_in_place``, and the object is ``create``
    or ``unchanged``. There is no sixth action, because there is no code path that
    would update or drop a field.

    A read rather than a flag: this route cannot write, so there is no path by
    which asking for a preview can create something. The ``service_document`` in
    the response is the researched step-two read, answered with the connection's
    vendor and environment.
    """
    return engine.plan(connection_id, manifest_id, version, vendor=vendor)


@router.post("/install", status_code=201)
def install(
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Install a manifest into a connection: create only what is missing.

    The same endpoint an admin presses and a CI job calls, which is how the
    research describes it. The run reads the live schema, computes the diff, POSTs
    the creates, requests the key where one is sourced, and records the
    room-object-id to CRM-object-id mapping.

    A first install answers 201. A re-install that created nothing answers **200**
    with ``outcome: "unchanged"``, because 201 would tell a deploy pipeline that a
    new contract was created when nothing was. A ``dry_run`` answers 200 too, and
    stores one run record saying the run was a preview and created nothing.

    The response carries the exact requests that were sent - the researched paths
    and bodies - so a reviewer can see what a deployment would have done without
    reading the code that decided it.
    """
    connection_id = str(payload.get("connection_id") or "")
    manifest_id = str(payload.get("manifest_id") or "")
    if not connection_id or not manifest_id:
        raise HTTPException(status_code=400, detail="connection_id and manifest_id are required")
    result = engine.install(
        connection_id,
        manifest_id,
        payload.get("version"),
        dry_run=bool(payload.get("dry_run", False)),
        actor=actor,
        source=f"POST {router.prefix}/install",
    )
    if result["outcome"] in {"unchanged", "dry_run"} and response is not None:
        response.status_code = 200
    return result


# --------------------------------------------------------------------------- #
# Objects, and the mapping
# --------------------------------------------------------------------------- #


@router.get("/objects")
def list_objects(
    connection_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """The installed objects, which are also the mapping records.

    "sales room records the mapping of room-object-id -> CRM-object-id for
    subsequent syncs" - so each row carries both halves, and a sync that needs to
    write into the CRM reads them from here rather than re-deriving the id.
    """
    listed = engine.objects(connection_id=connection_id, room_id=room_id)
    return {
        "count": len(listed),
        "incomplete": sum(1 for row in listed if row.get("complete") is False),
        "objects": listed,
    }


@router.get("/objects/{object_id}")
def read_object(object_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One object: its mapping, its properties, and its key if it has one."""
    record = engine.object(object_id)
    connection = engine.connection(str(record.get("connection_id")))
    keys = [
        key
        for key in engine.keys()
        if str(key.get("crm_object_id")) == str(record.get("crm_object_id"))
    ]
    return {
        "object": record,
        "connection": {
            "id": connection["id"],
            "name": connection.get("name"),
            "vendor": connection.get("vendor"),
        },
        "properties": engine.properties(object_id),
        "keys": keys,
    }


@router.get("/objects/{object_id}/properties")
def list_object_properties(
    object_id: str, engine: ProvisioningEngine = EngineDep
) -> dict[str, Any]:
    """The properties this workflow created on one object.

    Only this workflow's own creations. A property a tenant added in HubSpot's UI
    is found by the diff and is deliberately *not* listed here, because this
    product did not create it and must not imply that it did.
    """
    listed = engine.properties(object_id)
    return {"count": len(listed), "properties": listed}


@router.post("/objects/{object_id}/properties", status_code=201)
def add_object_property(
    object_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Add one property to an installed object, outside the installer.

    "A deployment ships its own manifest, so 'install CRM fields for rooms' is a
    config change" - and a field a team needs this afternoon should not have to
    wait for a manifest version. The rules the installer holds to are the rules
    here: a property that already exists at the vendor is refused with 409 rather
    than changed, because "never destructively renaming or dropping existing
    fields" is a property of the workflow and not of the entry point.
    """
    return engine.add_property(
        object_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/objects/{{object_id}}/properties",
    )


# --------------------------------------------------------------------------- #
# The sync key and its background index
# --------------------------------------------------------------------------- #


@router.get("/keys")
def list_keys(
    connection_id: str | None = Query(default=None),
    object_id: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Every alternate key, with the state of its background index build.

    "``EntityKeyMetadata.EntityKeyIndexStatus`` ... Pending / In Progress / Active /
    Failed." The summary counts the four, so a half-provisioned key is a number on
    the page rather than something a reader has to notice.
    """
    listed = engine.keys(connection_id=connection_id, object_id=object_id)
    summary = {"Pending": 0, "In Progress": 0, "Active": 0, "Failed": 0}
    for key in listed:
        status = str(key.get("status"))
        summary[status] = summary.get(status, 0) + 1
    return {"count": len(listed), "summary": summary, "keys": listed}


@router.get("/keys/{key_id}")
def read_key(key_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One key: its columns, its async job, and how many looks it has had."""
    key = engine.key(key_id)
    return {"key": key, "supported_here": bool(key.get("key_id"))}


@router.post("/keys/{key_id}/poll")
def poll_key(
    key_id: str,
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Look once at the background index build.

    The research is explicit that "creating an index can take a long time" and
    that the customization UI is kept responsive by building it in a background
    process, so an install does not block on the key. This is the other half of
    that: the build's state, read on demand. Each call advances the simulation one
    step - ``Pending`` to ``In Progress`` to ``Active``, or to ``Failed`` on a build
    that will not complete - and the cadence is this build's choice, recorded in the
    inference register rather than asserted as the vendor's.
    """
    return engine.poll_key(key_id, actor=actor, source=f"POST {router.prefix}/keys/{{key_id}}/poll")


@router.post("/keys/{key_id}/reactivate")
def reactivate_key(
    key_id: str,
    actor: str | None = Query(default=None),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """``ReactivateEntityKey``: repair a half-provisioned key.

    The research names this as "the extension point for repairing a half-provisioned
    key", and it is idempotent in the same direction the installer is. A key whose
    index is already ``Active`` is left alone and reports ``reactivated: false`` -
    reactivating a working key is not repairing one. A key still building is left
    building, because it is not broken. Only ``Failed`` is re-armed, back to
    ``Pending`` with a fresh ``AsyncJob``.
    """
    return engine.reactivate_key(
        key_id, actor=actor, source=f"POST {router.prefix}/keys/{{key_id}}/reactivate"
    )


# --------------------------------------------------------------------------- #
# The run log
# --------------------------------------------------------------------------- #


@router.get("/installations")
def list_installations(
    connection_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None, description="created | unchanged | dry_run"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ProvisioningEngine = EngineDep,
) -> dict[str, Any]:
    """Every installer run, newest first, with the requests it sent.

    The counts are over the rows returned rather than the whole log, so a filtered
    view does not report totals for something it is not showing. ``conflicts`` and
    ``left_in_place`` are counted separately from ``created`` on purpose: a run that
    created four fields and left a fifth in place is not a run that created four
    fields and a fifth one was quietly handled.
    """
    listed = engine.runs(connection_id=connection_id, outcome=outcome, limit=limit)
    totals = {
        "created": sum(int((run.get("counts") or {}).get("created") or 0) for run in listed),
        "conflicts": sum(int((run.get("counts") or {}).get("conflicts") or 0) for run in listed),
        "skipped": sum(int((run.get("counts") or {}).get("skipped") or 0) for run in listed),
        "left_in_place": sum(
            int((run.get("counts") or {}).get("left_in_place") or 0) for run in listed
        ),
        "dry_runs": sum(1 for run in listed if run.get("dry_run")),
    }
    return {"count": len(listed), "totals": totals, "installations": listed}


@router.get("/installations/{run_id}")
def read_installation(run_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """One run: its plan, its findings, and the exact requests that were sent."""
    return {"installation": engine.run(run_id)}


# --------------------------------------------------------------------------- #
# Room-scoped reads
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/objects")
def room_objects(room_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """What this room's connections have in their CRMs.

    The room's own answer to "does this room have a first-class CRM-native object
    to write engagement rows into". Provisioning is connection-scoped; this is the
    read where the room appears, because the result of the researched flow is
    about what a room can do afterwards rather than about how the object was
    installed.
    """
    payload = engine.room_objects(room_id)
    return {
        **payload,
        "unsupported": [
            {"id": row["id"], "name": row.get("name"), "reason": row.get("unsupported_reason")}
            for row in payload["connections"]
            if not row.get("supported", True)
        ],
    }


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: ProvisioningEngine = EngineDep) -> dict[str, Any]:
    """Counts for one room, and what still needs a person.

    Every count is scoped to this room rather than to the whole collection, so a
    room's header says what happened to that room. ``needs_repair`` counts keys
    that are ``Failed`` and keys still building, because those are the states no
    deploy is going to move on its own.
    """
    return engine.room_summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The shipped engagement manifest, version 1.0.0.
#:
#: A constant in *this* module only so the seeder can register it. The contract is
#: that a manifest is data: it goes into ``crm_manifest`` as a record, is read back
#: through ``GET /manifests/{id}``, and a team ships their own the same way. The
#: descriptor itself is the researched one - "name, labels, field types, option
#: sets" - plus a sync key, because the research's own data flow ends with
#: "EntityKeyMetadata + CreateEntityKey to create the sync key on the new table".
ENGAGEMENT_MANIFEST_V1: dict[str, Any] = {
    "manifest_id": "dsr_engagement",
    "version": "1.0.0",
    "name": "Sales-room engagement",
    "description": (
        "One row per buyer interaction with a room's content. This is the object a "
        "sync writes into instead of a free-text note."
    ),
    "room_object_id": "dsr.engagement",
    "object": {
        "name": "dsr_engagement",
        "label": "Sales-room engagement",
        "description": "Buyer interactions with a digital sales room.",
    },
    "properties": [
        {
            "name": "engagement_id",
            "label": "Engagement id",
            "type": "string",
            "group_name": "dsr-engagement",
            "length": 64,
            "required": True,
            "description": "The sales room's own row id. Carries the sync key.",
        },
        {
            "name": "room_name",
            "label": "Room name",
            "type": "string",
            "group_name": "dsr-engagement",
            "length": 120,
        },
        {
            "name": "buyer_email",
            "label": "Buyer email",
            "type": "string",
            "group_name": "dsr-engagement",
            "length": 180,
        },
        {
            "name": "action",
            "label": "Action",
            "type": "enumeration",
            "group_name": "dsr-engagement",
            "options": [
                {"label": "Viewed", "value": "viewed"},
                {"label": "Downloaded", "value": "downloaded"},
                {"label": "Accepted", "value": "accepted"},
            ],
        },
        {
            "name": "seconds_on_page",
            "label": "Seconds on page",
            "type": "number",
            "group_name": "dsr-engagement",
        },
    ],
    "sync_key": {"columns": [{"name": "engagement_id", "length": 64}]},
}


def _version_two() -> dict[str, Any]:
    """Version 1.1.0: a field added, and a label changed.

    Both halves of what happens when a deployment ships a new manifest, and they
    are different in kind. The new property is missing, so it is created. The
    changed label belongs to a property that already exists, so it is **not**
    changed - it is reported as a conflict, because "never destructively renaming or
    dropping existing fields" means a label a person typed in the CRM's own UI
    survives a deploy that disagrees with it.

    Seeded on purpose: a demo whose only surprise is "everything went in" teaches a
    reviewer nothing about the rule that matters most here.
    """
    relabelled = [
        {**prop, "label": "Engagement action"} if prop["name"] == "action" else prop
        for prop in ENGAGEMENT_MANIFEST_V1["properties"]
    ]
    return {
        **ENGAGEMENT_MANIFEST_V1,
        "version": "1.1.0",
        "description": "Adds the campaign the buyer came from, and relabels the action field.",
        "properties": [
            *relabelled,
            {
                "name": "campaign_touch",
                "label": "Campaign touch",
                "type": "string",
                "group_name": "dsr-engagement",
                "length": 80,
            },
        ],
    }


def _version_three() -> dict[str, Any]:
    """Version 1.2.0: a field *renamed*, which is the case worth being afraid of.

    ``room_name`` becomes ``room_label``. The new name is genuinely missing, so it
    is created. The old one is left in place and reported, because a deploy that
    drops a field the manifest stopped mentioning is the most likely way to destroy
    a tenant's data by accident. The diff names both halves so a human can see the
    rename happened before deciding whether the old column should ever go.
    """
    body = {**ENGAGEMENT_MANIFEST_V1, "version": "1.2.0"}
    return {
        **body,
        "description": "Renames room_name to room_label. The old column is left in place.",
        "properties": [
            {**prop, "name": "room_label", "label": "Room label"}
            if prop["name"] == "room_name"
            else prop
            for prop in ENGAGEMENT_MANIFEST_V1["properties"]
        ],
    }


#: The two rooms' worth of interesting state is produced by running three versions
#: of one manifest against one connection, so a reviewer can see a field added, a
#: label refused, a field renamed and the old column kept, all in the run log.


def _wide_key_manifest() -> dict[str, Any]:
    """A manifest whose sync key the vendor's own limit would reject.

    Five string columns at 200 characters is 1000 bytes, past the researched "900
    bytes per key". It is seeded deliberately: a constraint nobody has ever tripped
    over is a constraint that is not understood, and the manifest report shows the
    refusal before anything is installed rather than leaving it to be discovered on
    a new tenant by a half-provisioned object.
    """
    columns = [
        {
            "name": "engagement_id",
            "label": "Engagement id",
            "type": "string",
            "group_name": "dsr-engagement",
            "length": 200,
        }
    ]
    for index in range(1, 5):
        columns.append(
            {
                "name": f"scoped_context_{index}",
                "label": f"Scoped context {index}",
                "type": "string",
                "group_name": "dsr-engagement",
                "length": 200,
            }
        )
    return {
        "manifest_id": "dsr_engagement_scoped",
        "version": "0.1.0",
        "name": "Sales-room engagement (scoped key)",
        "description": "Five key columns at 200 characters. Over the vendor's 900 byte limit.",
        "object": {"name": "dsr_engagement_scoped", "label": "Sales-room engagement (scoped key)"},
        "properties": columns,
        "sync_key": {"columns": [{"name": column["name"], "length": 200} for column in columns]},
    }


#: The three connections the demo registers, and why each exists.
#:
#: One per researched vendor, plus the one the research names and could not source.
#: The Salesforce row is the point: an account is recorded, the page shows why it
#: cannot be provisioned into, and a reviewer can press Install and read the
#: refusal instead of wondering whether the gap was noticed.
DEMO_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {"vendor": "hubspot", "name": "Northwind Traders — HubSpot", "environment": "production"},
    {
        "vendor": "dataverse",
        "name": "Contoso Health — Dataverse",
        "environment": "production",
        "simulate": {"key_index": "failed"},
    },
    {"vendor": "salesforce", "name": "Fabrikam Logistics — Salesforce", "environment": "sandbox"},
)

#: Which room each demo connection belongs to. ``backend/seed.py`` hands over
#: ``[(room_id, account), ...]``, and the rooms in the core dataset are already the
#: three accounts above.
DEMO_CONNECTION_ROOMS: tuple[str, ...] = (
    "Northwind Traders",
    "Contoso Health",
    "Fabrikam Logistics",
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three connections, three manifest versions, and six installer runs.

    The rows are produced by running the real
    :class:`~dsr.crm_provisioning.engine.ProvisioningEngine`, so the demo cannot
    show a shape this workflow would not produce, and seeding never opens a socket.

    It is deliberately not all green, because a demo of only successes teaches a
    reviewer nothing about the states the research says matter:

    * a first install that creates the object and five properties;
    * a **re-install that creates nothing**, so idempotency is a row rather than a
      claim;
    * a **dry run** of the next version, so the preview surface is a row;
    * a **key whose background build fails**, and the ``ReactivateEntityKey``
      repair that brings it back, so the half-provisioned state the research calls
      worth repairing is visible;
    * a version that **adds** a property (created) and **relabels** one that already
      exists (a conflict, untouched);
    * a version that **renames** a field, which creates the new name and leaves the
      old one in place;
    * a **manifest whose key is over the vendor's byte limit**, refused before
      anything is installed;
    * a **Salesforce connection**, accepted and reported unsupported, with the
      research's own gap as the reason.
    """
    store = RecordStore(db)
    rng: random.Random = context.get("rng") or random.Random("wf036")
    engine = ProvisioningEngine(store, crm=SimulatedCrm(store, rng=rng))
    source = "seed"

    rooms: dict[str, str] = {}
    for entry in context.get("room_ids") or []:
        room_id, account = entry if isinstance(entry, (tuple, list)) else (entry, "")
        if account:
            rooms[account] = room_id

    connections: dict[str, dict[str, Any]] = {}
    for position, spec in enumerate(DEMO_CONNECTIONS):
        account = DEMO_CONNECTION_ROOMS[position] if position < len(DEMO_CONNECTION_ROOMS) else ""
        payload = {**spec, "room_id": rooms.get(account)}
        connections[spec["vendor"]] = engine.register_connection(
            payload, actor="dana", source=source
        )

    manifest = engine.register_manifest(ENGAGEMENT_MANIFEST_V1, actor="dana", source=source)
    engine.register_manifest(_version_two(), actor="dana", source=source)
    engine.register_manifest(_version_three(), actor="dana", source=source)
    over_wide = engine.register_manifest(_wide_key_manifest(), actor="dana", source=source)

    def run(vendor: str, version: str | None, **flags: Any) -> dict[str, Any]:
        return engine.install(
            connections[vendor]["id"],
            str(manifest.get("manifest_id")),
            version,
            actor="dana",
            source=source,
            **flags,
        )

    # -- the healthy path, and its own proof of idempotency ------------------ #
    first = run("hubspot", "1.0.0")
    again = run("hubspot", "1.0.0")

    # -- the preview surface, as a row -------------------------------------- #
    preview = run("hubspot", "1.1.0", dry_run=True)

    # -- the key that will not build, and the repair ------------------------- #
    dataverse = run("dataverse", "1.0.0")
    key_id = str((dataverse.get("key") or {}).get("id") or "")
    failed = engine.poll_key(key_id, actor="dana", source=source) if key_id else None

    # -- a version that adds a field and relabels another ------------------- #
    added = run("dataverse", "1.1.0")

    # -- a version that renames a field ------------------------------------- #
    renamed = run("dataverse", "1.2.0")

    # -- and the repair, once there is a failed key to repair ---------------- #
    repaired = engine.reactivate_key(key_id, actor="dana", source=source) if key_id else None
    polled = None
    if key_id:
        for _ in range(2):
            polled = engine.poll_key(key_id, actor="dana", source=source)

    # -- a manifest the vendor's own key limit would reject ------------------ #
    refused_key = engine.manifest_report(
        str(over_wide.get("manifest_id")), str(over_wide.get("version"))
    )

    return (
        f"{len(connections)} connections (1 of them unsupported by this research), "
        f"4 manifest versions "
        f"({0 if refused_key['sync_key']['ok'] else 1} refused for a key over the 900 byte limit), "
        f"6 installer runs, "
        f"{first['created']} objects and properties created, "
        f"{again['created']} created by the re-install (idempotent), "
        f"{preview['created']} the dry run would have created and {preview['applied']} it did, "
        f"{added['created']} added by a new version "
        f"({len(added['run']['conflicts'])} relabelled field left untouched), "
        f"{renamed['created']} created by a rename "
        f"({renamed['run']['counts']['left_in_place']} old column(s) left in place), "
        f"1 key build failed then was reactivated "
        f"({(failed or {}).get('status')} -> {(repaired or {}).get('status')} -> {(polled or {}).get('status')})"
    )
