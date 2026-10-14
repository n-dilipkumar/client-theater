"""WF-125: CRM-driven room lifecycle.

The rules live in :mod:`dsr.competitive_baseline.room_sync` and are not restated
here. This module is the three things a feature contributes, and the three things
it must never contribute.

What this module contributes
-----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the
  write.

Route parameters are chosen for the route verifier
--------------------------------------------------

``tools/verify_all_routes.py`` substitutes a value for every path placeholder it
knows and leaves an unknown one in the URL, which then fails as a request that
never answered. So this feature uses ``id`` and ``deal_id``, both of which the
verifier knows, rather than inventing a name it would leave in place.

The route prefix is ``/api/wf-125``
-----------------------------------

The brief pins the prefix to ``/api/wf-125``, matching the other hyphenated
ticket prefixes (``/api/wf-122``). The issue text writes ``/api/wf125`` once;
the brief is the operative instruction, and the hyphenated form is what every
sibling ticket uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from dsr.competitive_baseline import room_sync as sync
from dsr.competitive_baseline.room_sync import (
    RoomSyncEngine,
    RoomSyncError,
    RoomSyncNotFound,
    RoomSyncRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-125-room-sync",
    "ticket": "WF-125",
    "name": "CRM-driven room lifecycle",
    "description": (
        "Generate a sales room from a mirrored Salesforce, HubSpot or Dynamics "
        "deal, create rooms automatically when a data-driven rule matches, and "
        "write every view, download and share back to the CRM's standard and "
        "custom objects so the rep never logs activity by hand."
    ),
    "nav": [{"id": "wf-125-room-sync", "label": "CRM room sync"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes.
router = APIRouter(prefix="/api/wf-125", tags=["WF-125"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: a domain function that
    hardcoded a URL string would leave the audit log naming a route the app
    stopped serving.
    """
    return f"{method} {router.prefix}{path}"


def _engine(store: RecordStore) -> RoomSyncEngine:
    """The engine, with the clock left at its production default.

    Tests inject the clock into the engine directly; a request may not pass one,
    because a caller that could pass its own ``now`` could backdate an event.
    """
    return RoomSyncEngine(store)


def _connector_vocabulary() -> dict[str, Any]:
    """The merged WF-042 connector's vendor and object names.

    The reuse is deliberate: this workflow consumes the connector's vocabulary
    rather than defining a second list that could drift. It is imported here
    rather than at module scope so a fault in the connector cannot stop this
    feature's routes from loading.
    """
    from dsr.crm_integration import vocabulary as connector

    return {
        "systems": list(connector.CRM_SYSTEMS),
        "objects": list(connector.CRM_OBJECTS),
    }


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _sync_error(request: Request, exc: RoomSyncError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "room_sync_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _sync_refused(request: Request, exc: RoomSyncRefused) -> JSONResponse:
    """409 for a change the current state will not accept.

    409 rather than 400 because nothing about the request is malformed: a
    disabled rule, an absent mirror or an already-synced write-back is a state,
    and a client that retries the same call gets the same answer. ``reason`` is
    a stable token the page branches on.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "room_sync_conflict", "reason": exc.reason, "detail": str(exc)},
    )


def _sync_not_found(request: Request, exc: RoomSyncNotFound) -> JSONResponse:
    """404, unnamed, so it does not confirm what does not exist."""
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such rule, CRM record or write-back."},
    )


EXCEPTION_HANDLERS = {
    RoomSyncError: _sync_error,
    RoomSyncRefused: _sync_refused,
    RoomSyncNotFound: _sync_not_found,
}


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(store: RecordStore = StoreDep) -> dict[str, Any]:
    """The headline numbers. Reads only."""
    return _engine(store).summary()


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it."""
    served = sync.vocabulary()
    served["connector"] = _connector_vocabulary()
    return served


# --------------------------------------------------------------------------- #
# Room-creation rules
# --------------------------------------------------------------------------- #


@router.get("/rules")
def list_rules(enabled: bool | None = None, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every room-creation rule, in the order it was written."""
    return {"rules": _engine(store).list_rules(enabled)}


@router.post("/rules")
def create_rule(
    payload: dict[str, Any] = Body(...), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Write a data-driven rule: a CRM, an object, a condition set and a template."""
    return _engine(store).create_rule(payload, source=_source("POST", "/rules"), actor="rep")


@router.get("/rules/{id}")
def read_rule(id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One rule, with the conditions it evaluates."""
    return _engine(store).read_rule(id)


@router.patch("/rules/{id}")
def update_rule(
    id: str, payload: dict[str, Any] = Body(...), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Change a rule's conditions, target object, template or enabled flag."""
    return _engine(store).update_rule(
        id, payload, source=_source("PATCH", "/rules/{id}"), actor="rep"
    )


@router.delete("/rules/{id}")
def delete_rule(id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Remove a rule. The rooms it already made are left alone."""
    return _engine(store).delete_rule(id, source=_source("DELETE", "/rules/{id}"), actor="rep")


@router.post("/evaluate")
def evaluate(
    payload: dict[str, Any] = Body(default_factory=dict), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Apply the enabled rules and provision a room per matching record.

    Idempotent by (rule, deal). Pass ``{"rule_id": "..."}`` to apply one rule.
    """
    rule_id = payload.get("rule_id")
    return _engine(store).evaluate(
        str(rule_id) if rule_id else None,
        source=_source("POST", "/evaluate"),
        actor="rep",
    )


# --------------------------------------------------------------------------- #
# Mirrored CRM records
# --------------------------------------------------------------------------- #


@router.get("/records")
def list_records(
    object: str | None = None, crm: str | None = None, store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Every mirrored CRM record, standard and custom alike."""
    return {"records": _engine(store).list_records(object, crm)}


@router.post("/records")
def ingest_record(
    payload: dict[str, Any] = Body(...), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Upsert one mirrored CRM record, keyed by (crm, object, external_id)."""
    return _engine(store).ingest_record(payload, source=_source("POST", "/records"), actor="rep")


@router.get("/records/{id}")
def read_record(id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One mirrored CRM record, with its open payload."""
    return _engine(store).read_record(id)


# --------------------------------------------------------------------------- #
# Write-back targets
# --------------------------------------------------------------------------- #


@router.get("/targets")
def list_targets(enabled: bool | None = None, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every registered write-back target, standard and custom alike."""
    return {"targets": _engine(store).list_targets(enabled)}


@router.post("/targets")
def create_target(
    payload: dict[str, Any] = Body(...), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Register one write-back target, naming the fields a custom object receives."""
    return _engine(store).create_target(payload, source=_source("POST", "/targets"), actor="rep")


# --------------------------------------------------------------------------- #
# Rooms
# --------------------------------------------------------------------------- #


@router.get("/rooms")
def list_rooms(deal_id: str | None = None, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every generated room, and the deal and path that made it."""
    return {"rooms": _engine(store).list_rooms(deal_id)}


@router.post("/deals/{deal_id}/room")
def provision_room(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The explicit seller pick: one deal, one room. Idempotent by deal."""
    return _engine(store).provision_deal(
        deal_id, source=_source("POST", "/deals/{deal_id}/room"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Engagement events and write-back
# --------------------------------------------------------------------------- #


@router.get("/events")
def list_events(deal_id: str | None = None, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every recorded engagement event, oldest first."""
    return {"events": _engine(store).list_events(deal_id)}


@router.post("/deals/{deal_id}/events")
def record_event(
    deal_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record one view, download or share and write it back to every target."""
    return _engine(store).record_event(
        deal_id, payload, source=_source("POST", "/deals/{deal_id}/events"), actor="rep"
    )


@router.get("/writebacks")
def list_writebacks(
    event_id: str | None = None,
    state: str | None = None,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every write-back, including the pending and failed ones."""
    return {"writebacks": _engine(store).list_writebacks(event_id, state)}


@router.post("/writebacks/{id}/retry")
def retry_writeback(id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Re-attempt one pending or failed write-back. A synced one refuses."""
    return _engine(store).retry_writeback(
        id, source=_source("POST", "/writebacks/{id}/retry"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a rule, a
    mirror or a write-back state the HTTP routes would not produce.
    ``source="seed"`` rather than a route string: no route served this, and
    claiming one would be the lie the write rule exists to prevent.

    The states seeded, and why each is here:

    * a rule that matches one opportunity and **not** a second, so the rule is
      visibly doing the selecting rather than creating a room for every deal;
    * a **disabled** rule, so a rule that provisions nothing is visible;
    * a **standard-object** target and two **custom-object** targets, so custom
      objects are shown as the acceptance bar rather than described;
    * a **seller pick** beside the rule-made room, so both creation paths show;
    * write-backs in **all three states**: synced, pending (a target with
      ``auto_sync`` false) and failed (a custom object with no mirror yet).

    Every character of the returned string is ASCII, so the seeder can print it
    on a Windows console. A single non-cp1252 character in one feature broke the
    whole seeder once.
    """
    store = RecordStore(db)
    now = context["now"]
    engine = RoomSyncEngine(store, now=lambda: now)
    source, actor = "seed", "dana"

    # Write-back targets: six fields on the deal, plus two custom objects.
    engine.create_target(
        {
            "object": "deal",
            "fields": {
                "Room_Views__c": "view_count",
                "Room_Downloads__c": "download_count",
                "Room_Shares__c": "share_count",
                "Room_Last_Activity__c": "last_activity_at",
                "Room_Last_Event__c": "kind",
            },
            "create_mirror": True,
            "auto_sync": True,
        },
        source=source,
        actor=actor,
    )
    engine.create_target(
        {
            "object": "Deal_Room__c",
            "fields": {"Engagement_Count__c": "view_count", "Last_Event__c": "kind"},
            "create_mirror": True,
            "auto_sync": True,
        },
        source=source,
        actor=actor,
    )
    engine.create_target(
        {
            "object": "Room_Engagement__c",
            "fields": {"Event_Kind__c": "kind", "Occurred_At__c": "at"},
            "create_mirror": False,
            "auto_sync": True,
        },
        source=source,
        actor=actor,
    )
    engine.create_target(
        {
            "object": "Room_Task__c",
            "fields": {"Task_State__c": "kind"},
            "create_mirror": True,
            "auto_sync": False,
        },
        source=source,
        actor=actor,
    )

    # Mirrored CRM records. The first matches the rule; the second does not.
    northwind = engine.ingest_record(
        {
            "crm": "salesforce",
            "object": "deal",
            "external_id": "006-NORTHWIND",
            "name": "Northwind Traders - Enterprise Evaluation",
            "account": "Northwind Traders",
            "stage": "Qualification",
            "value": 120000,
            "currency": "USD",
            "contacts": ["a.buyer@northwind.example", "b.buyer@northwind.example"],
            "products": ["Enterprise", "Implementation"],
            "industry": "Logistics",
            "procurement_ticket": "PR-4471",
        },
        source=source,
        actor=actor,
    )
    contoso = engine.ingest_record(
        {
            "crm": "salesforce",
            "object": "deal",
            "external_id": "006-CONTOSO",
            "name": "Contoso Health - Security Review",
            "account": "Contoso Health",
            "stage": "Closed Lost",
            "value": 40000,
            "currency": "USD",
            "contacts": ["procurement@contoso.example"],
            "products": ["Security review"],
        },
        source=source,
        actor=actor,
    )

    # A rule that selects the open opportunity over 50k, and one that is disabled.
    engine.create_rule(
        {
            "name": "Auto-create a room for every open opportunity over 50k",
            "crm": "salesforce",
            "object": "deal",
            "conditions": [
                {"field": "stage", "op": "eq", "value": "Qualification"},
                {"field": "value", "op": "gte", "value": 50000},
            ],
            "template_id": "tpl-enterprise",
            "enabled": True,
        },
        source=source,
        actor=actor,
    )
    engine.create_rule(
        {
            "name": "Renewal room on a won deal",
            "crm": "salesforce",
            "object": "deal",
            "conditions": [{"field": "stage", "op": "eq", "value": "Closed Won"}],
            "enabled": False,
        },
        source=source,
        actor=actor,
    )

    # Rule path: one room. Seller path: one room for the deal the rule ignored.
    engine.evaluate(source=source, actor=actor)
    engine.provision_deal(contoso["id"], source=source, actor=actor)

    # One event of each kind, so all three counters and all three back-states show.
    engine.record_event(
        northwind["id"], {"kind": "view", "at": now.isoformat()}, source=source, actor=actor
    )
    engine.record_event(
        northwind["id"], {"kind": "download", "at": now.isoformat()}, source=source, actor=actor
    )
    engine.record_event(
        northwind["id"], {"kind": "share", "at": now.isoformat()}, source=source, actor=actor
    )
    engine.record_event(
        contoso["id"], {"kind": "view", "at": now.isoformat()}, source=source, actor=actor
    )

    counted = engine.summary()
    return (
        f"{counted['rules']} rules ({counted['rules_enabled']} enabled), "
        f"{counted['records']} mirrored records ({counted['custom_objects']} custom object), "
        f"{counted['targets']} write-back targets ({counted['custom_targets']} custom), "
        f"{counted['rooms']} rooms (1 from a rule, 1 seller pick), "
        f"{counted['events']} engagement events, "
        f"{counted['writebacks']} write-backs ({counted['pending']} pending, "
        f"{counted['synced']} synced, {counted['failed']} failed)"
    )
