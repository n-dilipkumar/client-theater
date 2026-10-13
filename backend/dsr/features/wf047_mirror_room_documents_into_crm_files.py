"""WF-047: mirror room documents into CRM files.

The researched workflow, in full. A rep uploads a deck or a proposal into the
room, or an asset is generated; the room uploads the binary to the CRM files
endpoint with ``multipart/form-data``; the CRM stores the file and returns a file
id; the room writes that id onto the CRM record that owns the attachment, either
as a ``file``-type property value or as a note attached to the record; and the
room stores ``crm_file_id`` on the asset row, so re-uploads are skipped and
revisions create a new file.

The domain logic is in :mod:`dsr.crm_file_sync`, which this module does not own
and which no other feature could have written into its own path. What lives here
is the three things a workflow has to take out of shared files: the HTTP surface,
the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
--------------------------------------
**The prefix is ``/api/wf-047``, and the reads are room-scoped.** The research is
about a room's documents and the CRM record that owns them, so the asset list, the
Documents panel, the CRM Files panel, the mirror log and the reconciliation pass
all sit under ``/rooms/{room_id}/...``. Only the published vocabulary and the
per-vendor capability report are org-level, because they describe vendors rather
than rooms.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served
it. A write whose ``source`` is a word rather than a route is a row nobody can
trace back to a call.

**One handler for the whole error hierarchy.** :class:`CrmFileSyncError` is the
base of every refusal in :mod:`dsr.crm_file_sync` and each carries its own
``status`` and ``code``, so one handler answers 400 for an asset with no filename
and 409 for a connection with no owning record and 422 for a system the research
could not source. ``RecordNotFound`` is deliberately *not* claimed: the core app
already maps it to 404, and two handlers for one type is a collision the host
refuses.

**An asset with no owning record is a 200 with a named outcome, not a 404.** The
issue names the dependency on WF-042: without a deal, account or contact record
"the returned file id has nowhere to be written". A mirror in that state records
``no_owner_record`` on its mirror row and answers 200, because the document is not
lost and the rep needs to see it waiting rather than see an error and guess.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.crm_file_sync import vocabulary
from dsr.crm_file_sync.engine import CrmFileSyncEngine
from dsr.crm_file_sync.errors import CrmFileSyncError
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-047-mirror-room-documents-into-crm-files",
    "ticket": "WF-047",
    "name": "Mirror room documents into CRM files",
    "description": (
        "Upload a room asset to the CRM files endpoint as multipart/form-data, write the "
        "returned file id onto the CRM record that owns it as a file-type property value or "
        "as an attached note, and store the vendor-agnostic crm_file_id on the asset row so "
        "re-uploads are skipped and revisions create a new file. A reconciliation pass finds "
        "room assets with no crm_file_id and pushes them. Vendors whose file API the research "
        "could not source are refused by name rather than invented."
    ),
    "nav": [{"id": "crm-file-mirror", "label": "CRM file mirror"}],
}

router = APIRouter(prefix="/api/wf-047", tags=["wf047"])


def get_engine(store: RecordStore = StoreDep) -> CrmFileSyncEngine:
    """A :class:`CrmFileSyncEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return CrmFileSyncEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _crm_file_sync_error(request: Request, exc: CrmFileSyncError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. Every refusal in
    :mod:`dsr.crm_file_sync` carries its own ``status`` and ``code`` on the
    exception, because a malformed payload, a conflict with state that already
    exists, an unsourced vendor and a vendor that declined are four different
    problems and only one of them is the caller's typo.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {CrmFileSyncError: _crm_file_sync_error}


# --------------------------------------------------------------------------- #
# Vocabulary, inferences and vendors
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route(engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """Every published word this workflow uses, served as data.

    The systems, which of them can receive a file, the upload path and its field
    names, the folder recommendation and its caveat, the two attach modes with the
    vendor's own words for each, the chip states with a plain sentence apiece, the
    mirror outcomes, the reconciliation rule and its floor, and the two connector
    methods. A client renders its pickers from this rather than from a list
    compiled into a page, so a state added here reaches every client at once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the two-method connector interface without narrowing it,
    says "re-uploads are skipped and revisions create a new file" without saying
    how a revision is detected, gives the reconciliation rule without a cadence,
    recommends a folder without requiring one, and refuses to source two of the
    three vendors at all. Those six edges are served here rather than left for a
    reader to reconstruct from a diff.
    """
    return engine.inferences()


@router.get("/vendors")
def vendors(engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """Each vendor's file capability, with the gap named where there is one."""
    return engine.vendors()


# --------------------------------------------------------------------------- #
# Connections: the vendor, the owning record, and the folder
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_connections(
    room_id: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """The connections a room pushes its documents through."""
    listed = engine.connections(room_id)
    return {
        "count": len(listed),
        "room_id": room_id,
        "systems": list(vocabulary.SYSTEMS),
        "file_capable_systems": list(vocabulary.FILE_CAPABLE_SYSTEMS),
        "unsourced_systems": dict(vocabulary.UNSOURCED_SYSTEMS),
        "connections": listed,
    }


@router.post("/connections", status_code=201)
def save_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Create or update the connection a room pushes its documents through.

    Names the vendor, the CRM record that owns the attachment, the attach mode and
    an optional folder id. The folder is optional because the vendor says so: "a
    specific folder ID is not required at upload, it's recommended to upload files
    into a folder and not the root directory". The row records whether the
    recommendation was followed either way.

    A second connection for the same room, system and owning record is refused
    with a 409: two connections pushing the same document into the same record is
    the duplicate-file problem the idempotency rule exists to prevent.
    """
    return engine.save_connection(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connections"
    )


@router.get("/connections/{connection_id}")
def read_connection(
    connection_id: str,
    room_id: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """One connection, with its assets and each asset's chip state."""
    return engine.read_connection(connection_id, room_id=room_id)


@router.delete("/connections/{connection_id}")
def delete_connection(
    connection_id: str,
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete a connection, and keep the file ids its assets already hold.

    The assets stay. A ``crm_file_id`` is a fact about the CRM rather than about
    the connection, and a buyer may already have seen the file, so deleting it
    would make the room forget something that is still true.
    """
    return engine.delete_connection(
        connection_id,
        room_id=room_id,
        actor=actor,
        source=f"DELETE {router.prefix}/connections/{{connection_id}}",
    )


# --------------------------------------------------------------------------- #
# Assets: the room's documents
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/assets", status_code=201)
def register_asset(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    connection_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    mirror: bool = Query(default=False, description="push it to the CRM now as well"),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Register a room asset, and optionally mirror it now.

    User-flow step one. Registering a document is not the same decision as
    publishing it to the CRM, so ``mirror`` is False by default and a caller that
    wants both asks for both.
    """
    return engine.register_asset(
        payload,
        connection_id=connection_id,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/assets",
        mirror=mirror,
    )


@router.get("/rooms/{room_id}/assets")
def list_assets(
    room_id: str,
    connection_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="filter on the Push to CRM chip"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """The room's assets, each with its "Push to CRM" chip.

    ``state`` filters on the derived chip, which is a read of a value no column
    holds, so the filter runs here while ``connection_id`` is pushed down to the
    dynamic index.
    """
    listed = engine.list_assets(
        room_id=room_id, connection_id=connection_id, state=state, limit=limit
    )
    return {
        "room_id": room_id,
        "count": len(listed),
        "connection_id": connection_id,
        "state": state,
        "states": list(vocabulary.MIRROR_STATES),
        "state_notes": dict(vocabulary.MIRROR_STATE_NOTES),
        "assets": listed,
    }


@router.get("/rooms/{room_id}/assets/{asset_id}")
def read_asset(
    room_id: str,
    asset_id: str,
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """One asset, with its chip state and every mirror attempt it has made."""
    return engine.read_asset(asset_id, room_id=room_id)


@router.patch("/rooms/{room_id}/assets/{asset_id}")
def patch_asset(
    room_id: str,
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    mirror: bool = Query(default=False, description="push the change to the CRM now as well"),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Switch "Push to CRM" on or off, or replace the asset's bytes.

    Both are the researched flow. Step two is the toggle; a revision is a byte
    change, which moves the content digest and turns the chip to ``stale`` until
    the next mirror creates a new CRM file.

    Switching the toggle off withdraws nothing from the CRM. The file the room
    already uploaded stays where it is, because the vendor publishes no delete in
    the API this workflow uses, and a row claiming otherwise would be lying about
    the vendor.
    """
    if "content_base64" in payload or "content" in payload:
        return engine.replace_content(
            asset_id,
            payload,
            room_id=room_id,
            actor=actor,
            source=f"PATCH {router.prefix}/rooms/{{room_id}}/assets/{{asset_id}}",
            mirror=mirror,
        )
    if "push_to_crm" in payload:
        return engine.set_push(
            asset_id,
            bool(payload.get("push_to_crm")),
            room_id=room_id,
            actor=actor,
            source=f"PATCH {router.prefix}/rooms/{{room_id}}/assets/{{asset_id}}",
        )
    raise CrmFileSyncError(
        "send push_to_crm to switch the mirror off or on, or content_base64 to replace the file",
        code="nothing_to_change",
        status=400,
    )


@router.post("/rooms/{room_id}/assets/{asset_id}/mirror")
def mirror_asset(
    room_id: str,
    asset_id: str,
    force: bool = Query(
        default=False, description="mirror even when the stored file id already matches"
    ),
    actor: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Upload one asset and write the returned file id onto the owning record.

    Steps three and four in one call, in the order the research gives them: check,
    upload, attach, store. The answer names the outcome and carries the exact
    request that would go over the wire, so a reviewer can check it against the
    vendor's guide without a CRM account.

    An unchanged asset answers ``skipped_unchanged`` and the vendor is not asked.
    That is the researched "re-uploads are skipped" applied before the call rather
    than after it.
    """
    return engine.mirror_asset(
        asset_id,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/assets/{{asset_id}}/mirror",
        force=force,
    )


# --------------------------------------------------------------------------- #
# The mirror log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/mirrors")
def list_mirrors(
    room_id: str,
    outcome: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Every mirror attempt this room made, newest first.

    One row per attempt, carrying the exact multipart request the connector built,
    the vendor's answers, and whether a simulation served it. A mirror path nobody
    can account for is indistinguishable from one that never ran.
    """
    listed = engine.list_mirrors(room_id=room_id, outcome=outcome, limit=limit)
    return {
        "room_id": room_id,
        "count": len(listed),
        "outcome": outcome,
        "outcomes": list(vocabulary.MIRROR_OUTCOMES),
        "mirrors": listed,
    }


@router.get("/mirrors/{mirror_id}")
def read_mirror(mirror_id: str, engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """One mirror attempt, with the request it made and the vendor's answers."""
    return engine.read_mirror(mirror_id)


# --------------------------------------------------------------------------- #
# Reconciliation: the quoted rule
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/reconcile")
def reconcile(
    room_id: str,
    interval_seconds: int | None = Query(
        default=None, description="override the connection's reconciliation floor"
    ),
    force: bool = Query(default=False, description="skip the floor"),
    actor: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """Find room assets with no ``crm_file_id`` and push them.

    The quoted reconciliation rule, as a route a scheduler calls. Nothing in this
    package starts a thread: a timer inside a web process double-writes the moment
    two workers exist, and it cannot be paused by an operator. A call made before
    the floor is due is answered ``not_due`` and asks the vendor for nothing.
    """
    return engine.reconcile(
        room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/reconcile",
        interval_seconds=interval_seconds,
        force=force,
    )


# --------------------------------------------------------------------------- #
# Panels and summaries
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/documents")
def documents_panel(room_id: str, engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """The room's Documents page, with a "Push to CRM" chip per file.

    The first researched product surface: "Room **Documents** page with a 'Push to
    CRM' status chip per file". Every chip carries a plain sentence as well as a
    state word, so the status never depends on colour alone.
    """
    return engine.documents_panel(room_id)


@router.get("/rooms/{room_id}/files")
def crm_files_panel(room_id: str, engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """The CRM Files panel: what the room has pushed, and to which record.

    The second researched surface, "CRM **Files** panel", reached through the room
    because the room holds the vendor-agnostic ``crm_file_id`` for each file.
    """
    return engine.crm_files_panel(room_id)


@router.get("/remote-files")
def remote_files(
    folder_id: str | None = Query(default=None),
    engine: CrmFileSyncEngine = EngineDep,
) -> dict[str, Any]:
    """What the simulated CRM's own file storage holds.

    Every row is served by the same connector the mirror uses, so this is the
    connector's state rather than a second copy of it built for display. The
    payload bytes are left out of the answer: a panel wants to know which files
    exist, and a log row that carried the base64 of every proposal would be a
    document store nobody asked for.
    """
    return engine.remote_files(folder_id)


@router.get("/rooms/{room_id}/summary")
def summary(room_id: str, engine: CrmFileSyncEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room: assets by chip state and mirrors by outcome."""
    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The demo connections. Three vendors, because a demo of one vendor leaves the
#: sourced-and-unsourced distinction untested by anything a reviewer can look at.
#: Only HubSpot can mirror; the other two exist so the refusal is a row in the
#: page rather than a sentence in a docstring.
DEMO_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "Fabrikam deal files",
        "system": "hubspot",
        "owner_object": "deal",
        "crm_record_id": "fabrikam-dl-9001",
        "attach_mode": "note",
        "folder_id": "",
        "mirror_all": False,
    },
    {
        "name": "Fabrikam contact files",
        "system": "hubspot",
        "owner_object": "contact",
        "crm_record_id": "fabrikam-ct-8001",
        "attach_mode": "file_property",
        "file_property": "proposal_attachment",
        "folder_id": "fabrikam-folder-3300",
        "mirror_all": True,
    },
    {
        "name": "Northwind deal files",
        "system": "salesforce",
        "owner_object": "deal",
        "crm_record_id": "006-nw-5001",
        "attach_mode": "note",
    },
    # A connection whose owning record has not arrived yet. The mirror records
    # no_owner_record rather than refusing the document, which is the dependency
    # the issue names on WF-042 produced as a row rather than described.
    {
        "name": "Contoso account files",
        "system": "hubspot",
        "owner_object": "account",
        "crm_record_id": "",
        "attach_mode": "note",
    },
)

#: The demo assets, chosen so all five chip states appear in the seeded room.
#:
#: One per state the Documents page can show, so a reviewer sees ``mirrored``,
#: ``stale``, ``withheld``, ``failed`` and ``pending`` without having to drive the
#: page into each one.
DEMO_ASSETS: tuple[dict[str, Any], ...] = (
    # Mirrored, then mirrored again so the researched idempotency is a row.
    {
        "filename": "northwind-rollout-deck.pdf",
        "mime_type": "application/pdf",
        "content_base64": "UERGIC0xMQSBXUi0wMDAx",
        "push_to_crm": True,
    },
    # Mirrored, then its bytes are replaced, so the chip reads stale.
    {
        "filename": "fabrikam-pricing-2026.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content_base64": "UERGIC0xMQSBGICBMSQ==",
        "push_to_crm": True,
    },
    # Withheld: the toggle is off, so nothing is sent.
    {
        "filename": "contoso-security-questionnaire.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "content_base64": "UERGIC0xMQSBXUi0wMDMy",
        "push_to_crm": False,
    },
    # Failed: the Salesforce connection, whose file API could not be sourced.
    {
        "filename": "unsourced-system-proposal.pdf",
        "mime_type": "application/pdf",
        "content_base64": "UERGIC0xMQSBXUi0wMDA1",
        "push_to_crm": True,
    },
    # Pending: registered, never pushed. The reconciliation pass finds it.
    {
        "filename": "mutual-action-plan.pdf",
        "mime_type": "application/pdf",
        "content_base64": "UERGIC0xMQSBXUi0wMDA0",
        "push_to_crm": True,
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three vendors, five chip states, and an outcome that is not a success.

    The rows are produced by running the real
    :class:`~dsr.crm_file_sync.engine.CrmFileSyncEngine`, so the demo cannot show a
    shape this workflow would not produce, and seeding never opens a socket. It is
    deliberately mixed, because a demo of only success teaches a reviewer nothing:

    * all **three vendors**, so the sourced and unsourced distinction is a row in
      the page and not a claim in a docstring;
    * both **attach modes**, note and ``file_property``, so the room's two ways of
      writing the file id are both rows;
    * a connection with a **folder id** and one without, so the vendor's own
      "recommended, not required" recommendation is visible either way;
    * all **five chip states**: a mirrored asset, a stale one whose bytes changed
      after the push, a withheld one whose toggle is off, a failed one on the
      unsourced vendor, and a pending one the reconciliation has not reached yet;
    * a repeated mirror that is **skipped**, which is the researched idempotency
      rule working rather than asserted;
    * a **no_owner_record** outcome, which is the dependency the issue names on
      WF-042, produced by a connection with the owning record removed.
    """
    store = RecordStore(db)
    engine = CrmFileSyncEngine(store)
    # ``backend/seed.py`` passes ``[(room_id, account), ...]``. A bare id is
    # accepted too, because a caller assembling a context by hand should not have
    # to know the tuple shape to seed a feature.
    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    stamp = (context.get("now") or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")
    source = "seed"
    actor = "dana"

    if not rooms:
        return (
            f"{len(DEMO_CONNECTIONS)} connections, "
            f"{len(DEMO_ASSETS)} assets not registered and "
            "0 mirrors (no rooms to scope them to)"
        )

    room_id = rooms[0][0]
    connections: dict[str, str] = {}
    refused_connections: list[str] = []
    for declared in DEMO_CONNECTIONS:
        try:
            saved = engine.save_connection(declared, room_id=room_id, actor=actor, source=source)
            connections[str(declared["name"])] = saved["id"]
        except CrmFileSyncError as exc:
            refused_connections.append(f"{declared['name']}:{exc.code}")

    outcomes: list[str] = []

    def record(asset_id: str) -> dict[str, Any]:
        """Mirror one asset and note the outcome it produced.

        Returns the result so a caller that needs the asset's state after the
        mirror can read it. The state is deliberately not counted here: a summary
        that counted states after each mirror would count one asset twice.
        """
        try:
            result = engine.mirror_asset(
                asset_id, room_id=room_id, actor=actor, source=source, now=stamp
            )
        except CrmFileSyncError as exc:
            outcomes.append(f"refused:{exc.code}")
            return {}
        outcomes.append(str(result["outcome"]))
        return result

    def register(declared: dict[str, Any], connection_name: str | None) -> dict[str, Any]:
        """Register one demo asset on a named connection."""
        return engine.register_asset(
            declared,
            connection_id=connections.get(connection_name) if connection_name else None,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    # Asset 0 goes through a HubSpot deal connection twice: once to mirror it and
    # once to show the researched idempotency, where the second mirror sends
    # nothing and creates nothing.
    first = register(DEMO_ASSETS[0], "Fabrikam deal files")
    record(first["asset"]["id"])
    record(first["asset"]["id"])

    # Asset 1 mirrors, then its bytes are replaced. That is a revision, and it is
    # left there: the chip reads stale, the CRM still holds the old file, and the
    # reconciliation below is what pushes the new one. A demo that mirrored it a
    # third time would hide the state the workflow exists to show.
    second = register(DEMO_ASSETS[1], "Fabrikam contact files")
    record(second["asset"]["id"])
    engine.replace_content(
        second["asset"]["id"],
        {
            "filename": DEMO_ASSETS[1]["filename"],
            "mime_type": DEMO_ASSETS[1]["mime_type"],
            "content_base64": "UERGIC0xMQSBHICBNSQ1WUi0wMDAx",
        },
        room_id=room_id,
        actor=actor,
        source=source,
    )

    # Asset 2 has the toggle off, so a mirror is withheld and the vendor is never
    # asked. The researched toggle, as a row.
    third = register(DEMO_ASSETS[2], "Fabrikam deal files")
    record(third["asset"]["id"])

    # Asset 3 sits on the Salesforce connection, whose file API the research could
    # not source. The refusal is named on the mirror row rather than invented.
    fourth = register(DEMO_ASSETS[3], "Northwind deal files")
    record(fourth["asset"]["id"])

    # Asset 4 is registered and never pushed, so the chip reads pending and the
    # reconciliation has something real to find.
    register(DEMO_ASSETS[4], "Fabrikam deal files")

    # One asset on the connection whose owning record has not arrived. The mirror
    # has nowhere to write the returned file id, and records that by name instead
    # of refusing the document.
    record(register(DEMO_ASSETS[0], "Contoso account files")["asset"]["id"])

    # One reconciliation run, so the page shows the researched rule firing on the
    # assets whose event never arrived. It is forced because a seeded run is not a
    # scheduled one: nothing in this package starts a thread, so a demo that waited
    # for the floor would push nothing.
    reconciled = engine.reconcile(room_id, actor=actor, source=source, now=stamp, force=True)

    # The final chip state of each asset, not the state after each mirror. An
    # asset mirrored twice counts once here and twice in the outcome list, and a
    # summary that counted the transitions would report more states than there are
    # files.
    chips: dict[str, int] = {}
    for entry in engine.list_assets(room_id=room_id, limit=1000):
        state = str(entry["state"])
        chips[state] = chips.get(state, 0) + 1

    chip_states = ", ".join(f"{state}={count}" for state, count in sorted(chips.items())) or "none"
    refused = ", ".join(refused_connections) or "none"
    return (
        f"{len(connections)} connections across {len(vocabulary.SYSTEMS)} vendors "
        f"(refused: {refused}), "
        f"{len(DEMO_ASSETS)} assets, "
        f"chips: {chip_states}, "
        f"mirror outcomes: {', '.join(outcomes) if outcomes else 'none'}, "
        f"reconcile pushed {reconciled.get('mirrored', 0)} of "
        f"{reconciled.get('candidates', 0)} candidates"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
