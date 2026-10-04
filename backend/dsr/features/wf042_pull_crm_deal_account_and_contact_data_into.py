"""WF-042: pull CRM deal, account and contact records into the room for display.

The researched workflow, in full. A buyer opens the room; the room resolves which
CRM records that buyer belongs to; it issues a **read-only, field-scoped**, paged
read against the vendor; it normalises the vendor's paged response into its own
view model; it asks for the *display labels* of the option columns so the panel
says "Proposal sent" rather than the integer ``2``; and it caches the result per
buyer for a short TTL. A room whose seller authored it without CRM context still
opens, and its deal panel says why it is empty instead of failing.

The domain logic is in :mod:`dsr.crm_integration`, which this module does not own
and which no other feature could have written into its own path. What lives here
is the three things a workflow has to take out of shared files: the HTTP surface,
the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-042``, and the read is room-scoped.** The research is
about a buyer opening *a room*, so identity, the vendor tables, the panel and the
query log all sit under ``/rooms/{room_id}/...``. Nothing here is org-level
except the published vocabulary and the per-vendor capability report, which
describe vendors rather than rooms.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served
it. ``source`` is a *required* keyword on every writing method of
:class:`~dsr.crm_integration.engine.CrmReadEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.** ``CrmIntegrationError`` is the base
of every refusal in :mod:`dsr.crm_integration`, and each carries its own
``status`` and ``code`` on the exception, so one handler can answer 400 for a
field the map does not carry and 409 for a second identity for one buyer.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
404, and two handlers for one type is a collision the host refuses.

**The room with no CRM context is a 200.** ``GET /rooms/{room_id}/panel`` and
``POST /rooms/{room_id}/panel/pull`` both answer 200 with ``crm_context: false``
when no identity is registered. That is the research's own summary of the
workflow - "the room still works when a seller authors it without CRM context" -
and a page that 404s for the most ordinary room in the product would be a bug
this feature chose not to ship.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.crm_integration import cache as cache_rules, vocabulary
from dsr.crm_integration.engine import CrmReadEngine
from dsr.crm_integration.errors import CrmIntegrationError
from dsr.crm_integration.fieldmap import default_field_map
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-042-pull-crm-deal-account-and-contact-data-into",
    "ticket": "WF-042",
    "name": "Pull CRM deal, account and contact records into the room for display",
    "description": (
        "Resolve which CRM records a buyer's view belongs to, issue a read-only "
        "field-scoped paged read against the vendor, normalise the paged response into "
        "the room's view model, label every option value from the vendor's display "
        "annotation or the room's own option sets, and cache the panel per buyer for a "
        "short TTL. A room authored without CRM context still renders."
    ),
    "nav": [{"id": "crm-read-panel", "label": "CRM read panel"}],
}

router = APIRouter(prefix="/api/wf-042", tags=["wf042"])


def get_engine(store: RecordStore = StoreDep) -> CrmReadEngine:
    """A :class:`CrmReadEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return CrmReadEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _crm_integration_error(request: Request, exc: CrmIntegrationError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``CrmIntegrationError`` is the base of
    every refusal in :mod:`dsr.crm_integration` - a system outside the researched
    three, a field the map does not carry, a page larger than the vendor returns, a
    second identity for one buyer - and all of them are the caller's to fix. The
    status rides on the exception rather than being decided here, because a
    malformed request and a conflict with state that already exists are both this
    package's errors and only one of them is malformed.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {CrmIntegrationError: _crm_integration_error}


# --------------------------------------------------------------------------- #
# Vocabulary, inferences and vendors
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route(engine: CrmReadEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The three systems, the three objects, each vendor's own spelling of them, the
    identity sources, the display-label capability per vendor, the paging shape
    per vendor, the cache states, the read outcomes and every vendor limit with
    the number the research fixed. A client renders its pickers from this rather
    than from a list compiled into a page, so a value added here reaches every
    client at once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: CrmReadEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the identity source without choosing it, says "short TTL"
    without a number, records both pull and push without preferring either, and
    publishes no ``done`` for two of the three vendors. Those four edges are
    served here rather than left for a reader to reconstruct from a diff,
    alongside the sourced half so the line is visible.
    """
    return engine.inferences()


@router.get("/vendors")
def vendors(engine: CrmReadEngine = EngineDep) -> dict[str, Any]:
    """Each vendor's read endpoints, limits and display-label capability."""
    return engine.vendors()


# --------------------------------------------------------------------------- #
# Identities: the research's step two
# --------------------------------------------------------------------------- #


@router.get("/identities")
def list_identities(
    room_id: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """The registered CRM identities, optionally for one room."""
    listed = engine.identities(room_id)
    return {
        "count": len(listed),
        "room_id": room_id,
        "identity_source": vocabulary.IDENTITY_SOURCE,
        "identity_sources": list(vocabulary.IDENTITY_SOURCES),
        "identities": listed,
    }


@router.post("/identities", status_code=201)
def register_identity(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Register which CRM records a buyer's view belongs to.

    The room mapping the research names as one of its two identity sources. It
    carries the vendor, the three record ids the panel reads, the display-label
    capability flag, the TTL the room wants, and the field map the read set is
    derived from - which is what makes "a deployment that adds a field
    automatically gets it in the panel" true rather than aspirational.

    A second identity for one buyer on one vendor is refused with 409: the read
    is scoped by ``(room_id, buyer_email)`` and the cache is keyed the same way.
    """
    return engine.register_identity(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/identities"
    )


@router.get("/identities/{identity_id}")
def read_identity(
    identity_id: str,
    room_id: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """One identity, with its vendor capability flags and object list."""
    return engine.read_identity(identity_id, room_id)


@router.patch("/identities/{identity_id}")
def patch_identity(
    identity_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Change an identity's record ids, capability flag, TTL or field map.

    A shallow merge onto the stored payload, then re-validated, so a field this
    build does not know survives the patch and a known field cannot be set to
    something invalid.
    """
    return engine.patch_identity(
        identity_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"PATCH {router.prefix}/identities/{{identity_id}}",
    )


@router.delete("/identities/{identity_id}")
def delete_identity(
    identity_id: str,
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Soft-delete an identity, and the snapshot keyed to it.

    A soft delete, so the cancellation is audited. The cached panel goes with it,
    because a snapshot for an identity the room no longer serves is a row that
    anyone holding its id can still read.
    """
    return engine.delete_identity(
        identity_id,
        room_id=room_id,
        actor=actor,
        source=f"DELETE {router.prefix}/identities/{{identity_id}}",
    )


# --------------------------------------------------------------------------- #
# Option sets: the room's own display labels
# --------------------------------------------------------------------------- #


@router.get("/option-sets")
def list_option_sets(
    system: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """The room's own option sets, and which vendor each one is for."""
    listed = engine.option_sets(system, room_id)
    return {
        "count": len(listed),
        "labellable_fields": list(vocabulary.CRM_OBJECTS),
        "option_sets": listed,
    }


@router.post("/option-sets", status_code=201)
def register_option_set(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Register the room's own labels for one option field.

    User-flow step five's fallback, and a working part of the read path rather
    than a degradation notice. "Vendors expose a capability flag for
    'display-label annotations' that the room uses when available and falls back
    to its own option-set map when not" - and two of the three researched vendors
    have no annotation to request.

    The key is the *room* field name rather than the vendor column, because the
    option set is the room's own vocabulary and has to mean the same thing
    whichever vendor spelled the column.
    """
    return engine.register_option_set(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/option-sets"
    )


@router.get("/option-sets/{option_set_id}")
def read_option_set(
    option_set_id: str,
    room_id: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """One option set, as the room's own fallback reads it."""
    try:
        return engine.read_option_set(option_set_id, room_id=room_id)
    except CrmIntegrationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.delete("/option-sets/{option_set_id}")
def delete_option_set(
    option_set_id: str,
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Drop one option set, and accept that its fields go back to raw values.

    Allowed, because a room deciding that "Proposal sent" was the wrong wording
    for its own pipeline is the room's decision. The panel then reports the field
    as unlabelled rather than silently showing the option code.
    """
    return engine.delete_option_set(
        option_set_id,
        room_id=room_id,
        actor=actor,
        source=f"DELETE {router.prefix}/option-sets/{{option_set_id}}",
    )


# --------------------------------------------------------------------------- #
# The vendor's tables
# --------------------------------------------------------------------------- #


@router.get("/tables")
def list_tables(
    room_id: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Every vendor table, with the rows the room holds and the page ceiling."""
    listed = engine.tables(room_id)
    return {
        "count": len(listed),
        "room_id": room_id,
        "read_only": True,
        "tables": listed,
    }


@router.post("/records", status_code=201)
def declare_record(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Declare one row of a vendor table.

    This product holds no OAuth connection to a vendor org, so the vendor's three
    tables are held here. This route is how a connector - or a test - puts a row
    in one. It writes a row the read path then *reads*; it is not the read path
    and it never is a vendor write.
    """
    return engine.declare_record(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/records"
    )


@router.get("/records")
def list_records(
    system: str | None = Query(default=None),
    object_name: str | None = Query(default=None, alias="object"),
    room_id: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """The rows the room holds for one vendor table, or for all of them."""
    listed = engine.tables(room_id)
    if system or object_name:
        wanted_system = vocabulary.require_system(system) if system else None
        wanted_object = vocabulary.require_object(object_name) if object_name else None
        listed = [
            row
            for row in listed
            if (wanted_system is None or row["system"] == wanted_system)
            and (wanted_object is None or row["object"] == wanted_object)
        ]
    return {"count": len(listed), "room_id": room_id, "tables": listed}


# --------------------------------------------------------------------------- #
# The read path, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/panel")
def read_panel(
    room_id: str,
    identity_id: str | None = Query(default=None),
    buyer_email: str | None = Query(default=None),
    refresh: bool = Query(default=False, description="force the read, ignoring a fresh cache"),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """The buyer's deal panel, read through the cache.

    Step four of the user flow: "Room caches the result per buyer with a short TTL
    and renders the deal panel." A fresh cache serves without touching the
    vendor. ``refresh=true`` is the scheduler's door, because "Read-through cache
    refresh on a room scheduler" means the scheduler asks.

    A room with no identity for this buyer answers 200 with ``crm_context:
    false`` and a sentence saying why. The status code for "this room was
    authored without CRM context" is 200, because the room is fine.
    """
    return engine.read_panel(
        room_id,
        identity_id=identity_id,
        buyer_email=buyer_email,
        refresh=refresh,
        actor=actor,
        source=f"GET {router.prefix}/rooms/{{room_id}}/panel",
    )


@router.post("/rooms/{room_id}/panel/pull")
def pull_panel(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    identity_id: str | None = Query(default=None),
    buyer_email: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Force the read: issue the plans, normalise, label, cache, render.

    The whole flow in one route, in the order the research gives it. The body is
    the read's own knobs - ``limit`` for the page, ``refresh`` accepted and
    reported - and the identity may come from the body or the query string.

    Two limits are enforced here rather than requested: the page is clamped to the
    vendor's own ceiling, and a ``limit`` of zero or less is refused. Asking for
    more than a vendor returns has asked for the largest page available; asking
    for none has not decided yet, and defaulting that would hide it.
    """
    return engine.pull(
        room_id,
        identity_id=identity_id or payload.get("identity_id"),
        buyer_email=buyer_email or payload.get("buyer_email"),
        limit=payload.get("limit"),
        refresh=bool(payload.get("refresh", True)),
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/panel/pull",
    )


@router.get("/rooms/{room_id}/deal-panel")
def deal_panel(
    room_id: str,
    actor: str | None = Query(default=None),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """The whole room's deal panel, one entry per registered identity.

    "the room needs deal name, stage, amount, primary contact, account industry"
    is per buyer, so this is the buyer's panels side by side. Each is read through
    its own cache, so one stale buyer does not force a read of the other three.
    """
    return engine.deal_panel(room_id)


@router.get("/rooms/{room_id}/queries")
def list_queries(
    room_id: str,
    object_name: str | None = Query(default=None, alias="object"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Every read plan this room issued, newest first.

    One row per plan, carrying the exact SOQL, the OData query options, or the
    HubSpot body the vendor would have received, plus the page's ``done``, its
    row count and its total. A read path nobody can account for is
    indistinguishable from a read path that never ran.

    ``object`` filters through the dynamic index, so a field a team added to the
    log later is queryable without a change to this route.
    """
    listed = engine.queries(room_id, object_name, limit)
    return {
        "room_id": room_id,
        "count": len(listed),
        "read_only": True,
        "paging_modes": dict(vocabulary.PAGING_MODES),
        "queries": listed,
    }


@router.get("/rooms/{room_id}/summary")
def summary(
    room_id: str,
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """Counts for the room: identities by vendor, option sets, rows, cache states."""
    return engine.summary(room_id)


@router.get("/rooms/{room_id}/cache")
def read_cache(
    room_id: str,
    engine: CrmReadEngine = EngineDep,
) -> dict[str, Any]:
    """How the room's cache is doing, by state, and the TTL bounds it enforces."""
    return {"room_id": room_id, **cache_rules.cache_summary(engine.store, room_id)}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The demo identities. Three buyers across all three vendors, because a demo of
#: one vendor leaves the capability check and the two paging shapes untested by
#: anything a reviewer can look at.
DEMO_IDENTITIES: tuple[dict[str, Any], ...] = (
    {
        "system": "salesforce",
        "buyer_email": "dana.okafor@northwind.example",
        "buyer_name": "Dana Okafor",
        "account_id": "001-nw-0001",
        "contact_id": "003-nw-1001",
        "deal_id": "006-nw-5001",
        "owner_id": "005-dana",
    },
    {
        "system": "dataverse",
        "buyer_email": "raj.patel@contoso-health.example",
        "buyer_name": "Raj Patel",
        "account_id": "contoso-2001",
        "contact_id": "contoso-ct-3001",
        "deal_id": "contoso-op-4001",
    },
    {
        "system": "hubspot",
        "buyer_email": "mei.tanaka@fabrikam.example",
        "buyer_name": "Mei Tanaka",
        "account_id": "fabrikam-7001",
        "contact_id": "fabrikam-ct-8001",
        "deal_id": "fabrikam-dl-9001",
    },
)

#: The room's own option sets, for the two vendors that publish no annotation and
#: for the one Dataverse field whose stored value is a code.
DEMO_OPTION_SETS: tuple[dict[str, Any], ...] = (
    {
        "system": "salesforce",
        "object": "deal",
        "field": "stage",
        "values": {
            "Qualification": "Qualification",
            "Proposal": "Proposal sent",
            "Negotiation": "In negotiation",
            "Closed Won": "Closed won",
        },
    },
    {
        "system": "salesforce",
        "object": "account",
        "field": "account_industry",
        "values": {"Manufacturing": "Manufacturing", "Software": "Software"},
    },
    {
        "system": "hubspot",
        "object": "deal",
        "field": "stage",
        "values": {
            "appointmentscheduled": "Appointment scheduled",
            "qualifiedtobuy": "Qualified to buy",
            "presentationscheduled": "Presentation scheduled",
            "contractsent": "Contract sent",
        },
    },
    {
        "system": "dataverse",
        "object": "deal",
        "field": "stage",
        "values": {"1": "Proposal sent", "2": "In negotiation", "3": "Closed won"},
    },
)


def _demo_rows() -> tuple[dict[str, Any], ...]:
    """The demo vendor rows, one deal, contact and account per buyer."""
    salesforce = {
        "system": "salesforce",
        "deal": {
            "external_id": "006-nw-5001",
            "owner_id": "005-dana",
            "fields": {
                "Name": "Northwind platform rollout",
                "StageName": "Negotiation",
                "Amount": 48000,
                "CloseDate": "2026-11-30",
                "Probability": 70,
            },
        },
        "contact": {
            "external_id": "003-nw-1001",
            "email": "dana.okafor@northwind.example",
            "fields": {
                "Name": "Dana Okafor",
                "Title": "VP Operations",
                "Email": "dana.okafor@northwind.example",
            },
        },
        "account": {
            "external_id": "001-nw-0001",
            "fields": {
                "Name": "Northwind Traders",
                "Industry": "Manufacturing",
                "BillingCountry": "US",
            },
        },
    }
    dataverse = {
        "system": "dataverse",
        "deal": {
            "external_id": "contoso-op-4001",
            "fields": {
                "name": "Contoso clinical data pilot",
                "stepname": "1",
                "estimatedvalue": 27500,
                "estimatedclosedate": "2026-12-15",
            },
            "labels": {"stepname": "Proposal sent"},
        },
        "contact": {
            "external_id": "contoso-ct-3001",
            "email": "raj.patel@contoso-health.example",
            "fields": {
                "fullname": "Raj Patel",
                "jobtitle": "Clinical Data Lead",
                "emailaddress1": "raj.patel@contoso-health.example",
            },
        },
        "account": {
            "external_id": "contoso-2001",
            "fields": {
                "name": "Contoso Health",
                "industrycode": "Healthcare",
                "address1_country": "GB",
            },
            "labels": {"industrycode": "Healthcare services"},
        },
    }
    hubspot = {
        "system": "hubspot",
        "deal": {
            "external_id": "fabrikam-dl-9001",
            "fields": {
                "dealname": "Fabrikam logistics workspace",
                "dealstage": "presentationscheduled",
                "amount": 12400,
                "closedate": "2027-01-20",
                "hubspot_owner_id": "991",
            },
        },
        "contact": {
            "external_id": "fabrikam-ct-8001",
            "email": "mei.tanaka@fabrikam.example",
            "fields": {
                "email": "mei.tanaka@fabrikam.example",
                "jobtitle": "Director of Logistics",
                "phone": "+44 20 7946 0101",
            },
        },
        "account": {
            "external_id": "fabrikam-7001",
            "fields": {"name": "Fabrikam Logistics", "industry": "Software", "country": "JP"},
        },
    }
    return salesforce, dataverse, hubspot


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three buyers on three vendors, and the states that are not all successes.

    The rows are produced by running the real
    :class:`~dsr.crm_integration.engine.CrmReadEngine`, so the demo cannot show a
    shape this workflow would not produce, and seeding never opens a socket. It
    is deliberately mixed, because a demo of only success teaches a reviewer
    nothing:

    * all **three vendors**, so all three paging shapes and the display-label
      capability check are rows rather than claims;
    * a **Dataverse** deal whose stage is the integer ``1`` and whose label comes
      from the vendor's own ``FormattedValue`` annotation - the researched step
      five, working;
    * a **Salesforce** and a **HubSpot** deal whose stage has no vendor
      annotation, so the label comes from the room's own option sets - the
      researched fallback, working;
    * an identity whose **field map adds a column** nobody's defaults carry, so
      the extensibility claim is a row in the panel rather than a sentence in a
      docstring;
    * a buyer with **no CRM identity**, which is the room the research describes
      and the reason the panel route answers 200 rather than 404.
    """
    store = RecordStore(db)
    engine = CrmReadEngine(store)
    context.get("rng") or random.Random("wf042")
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

    for option_set in DEMO_OPTION_SETS:
        engine.register_option_set(option_set, room_id=None, actor=actor, source=source)

    if not rooms:
        return (
            f"{len(DEMO_OPTION_SETS)} option sets, "
            f"{len(DEMO_IDENTITIES)} identities not registered and "
            f"0 vendor rows (no rooms to scope them to)"
        )

    room_id = rooms[0][0]
    tables = _demo_rows()

    registered: list[tuple[str, str]] = []
    for index, declared in enumerate(DEMO_IDENTITIES):
        target_room = rooms[index][0] if index < len(rooms) else room_id
        payload = dict(declared)
        if index == 0:
            # The extensibility claim, as a row: one column added to the write
            # map and it reaches the panel with no change to any API.
            extended = default_field_map("salesforce")
            extended["deal"]["Region__c"] = "deal_region"
            payload["field_map"] = extended
        try:
            identity_row = engine.register_identity(
                payload, room_id=target_room, actor=actor, source=source
            )
        except CrmIntegrationError as exc:
            registered.append((f"refused:{exc.code}", target_room))
            continue
        registered.append((identity_row["id"], target_room))

        table = tables[index]
        for object_name in vocabulary.CRM_OBJECTS:
            # ``system`` comes from the table, not from the row: each table is
            # ``{"system": ..., "deal": {...}, "contact": {...}, "account": {...}}``
            # and the inner dicts carry only the row's own columns.
            engine.declare_record(
                {"system": table["system"], "object": object_name, **table[object_name]},
                room_id=target_room,
                actor=actor,
                source=source,
            )

    # The pull, run for real so the cached panels in the demo are the ones this
    # workflow produces. Each identity is pulled **in its own room**: the read is
    # room-scoped, so pulling a second room's buyer from the first room would
    # find nothing and the demo would show two empty panels where two working
    # ones belong.
    pulled: list[str] = []
    for identity_id, target_room in registered:
        if identity_id.startswith("refused:"):
            continue
        result = engine.pull(
            target_room,
            identity_id=identity_id,
            refresh=True,
            now=base,
            actor=actor,
            source=source,
        )
        pulled.append(str(result.get("outcome")))

    # The fourth buyer has no identity at all: the room the research describes,
    # and the reason the panel route is a 200 rather than a 404.
    without_context = engine.read_panel(
        rooms[0][0],
        buyer_email="no.crm.context@example.test",
        now=base,
        actor=actor,
        source=source,
    )

    registered_count = sum(1 for name, _ in registered if not name.startswith("refused:"))
    return (
        f"{len(DEMO_IDENTITIES)} CRM identities across {len(vocabulary.CRM_SYSTEMS)} vendors "
        f"({registered_count} registered), "
        f"{sum(len(table) for table in tables)} vendor rows, "
        f"{len(DEMO_OPTION_SETS)} room option sets, "
        f"pulls: {', '.join(pulled) if pulled else 'none'}, "
        f"display labels: vendor annotations on dataverse, room option sets elsewhere, "
        f"a buyer with no CRM identity returns {without_context.get('outcome')} "
        "rather than an error"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
