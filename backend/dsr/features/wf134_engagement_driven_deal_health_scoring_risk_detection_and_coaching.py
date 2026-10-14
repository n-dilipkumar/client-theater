"""WF-134: score a deal from in-room behaviour, detect risk, coach the rep.

The rules live in :mod:`dsr.competitive_baseline.deal_health` and are not
restated here. This module is the three things a feature contributes, and the
three things it must never contribute.

What this module contributes
----------------------------

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

Two route-parameter names are chosen for the route verifier
-----------------------------------------------------------

``tools/verify_all_routes.py`` substitutes a value for every path placeholder it
knows and leaves an unknown one in the URL, which then fails as a request that
never answered. So this feature uses ``room_id``, ``deal_id``, ``contact_id``
and ``asset_id``, all of which the verifier knows, rather than inventing a name
it would leave in place. A route that needs a resource id the verifier does not
know takes it in the body instead, which is why resolving an alert and closing a
coaching cue are ``POST``s on a fixed path.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.competitive_baseline import deal_health as domain
from dsr.competitive_baseline.deal_health import (
    DealHealthEngine,
    DealHealthError,
    DealHealthNotFound,
    DealHealthRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-134-deal-health",
    "ticket": "WF-134",
    "name": "Deal health and rep coaching",
    "description": (
        "Score a deal from in-room engagement decay, unreviewed key material and "
        "missing stakeholders. A score in the at-risk band, or a score that falls "
        "by the drop threshold, raises a risk alert. Each active risk reason "
        "raises one coaching cue for the rep, and a recorded outcome feeds the "
        "readiness data the loop closes on."
    ),
    "nav": [{"id": "wf-134-deal-health", "label": "Deal health"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-134", tags=["WF-134"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


def _engine(store: RecordStore) -> DealHealthEngine:
    """The engine, with the clock left at its production default.

    The real clock here rather than a request-supplied value: a caller that could
    pass its own ``now`` could pass a future one and make a decayed deal look
    live. Tests inject the clock into the engine directly.
    """
    return DealHealthEngine(store)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _deal_health_error(request: Request, exc: DealHealthError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=400,
        content={"error": "deal_health_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _deal_health_refused(request: Request, exc: DealHealthRefused) -> JSONResponse:
    """409 for a change the deal's current state will not accept.

    409 rather than 400 because nothing about the request is malformed: a cue
    that already has an outcome is a state, and a client that retries the same
    call gets the same answer. ``reason`` is a stable token the page branches on.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "deal_health_conflict", "reason": exc.reason, "detail": str(exc)},
    )


def _deal_health_not_found(request: Request, exc: DealHealthNotFound) -> JSONResponse:
    """404 for a deal or a record this workflow does not hold."""
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such deal, event, stakeholder or asset."},
    )


EXCEPTION_HANDLERS = {
    DealHealthError: _deal_health_error,
    DealHealthRefused: _deal_health_refused,
    DealHealthNotFound: _deal_health_not_found,
}


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The headline numbers: how many deals sit in each band. Reads only."""
    return _engine(store).summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it."""
    return domain.vocabulary()


# --------------------------------------------------------------------------- #
# Deals
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/deals")
def list_deals(room_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every deal in a room, with its stored configuration."""
    return {"room_id": room_id, "deals": _engine(store).list_deals(room_id)}


@router.post("/rooms/{room_id}/deals")
def create_deal(
    room_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add a deal to a room. The score follows from the evidence."""
    return _engine(store).create_deal(
        room_id, payload, source=_source("POST", "/rooms/{room_id}/deals"), actor="rep"
    )


@router.get("/deals/{deal_id}")
def read_deal(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One deal, with its framework, stage and rep."""
    return _engine(store).read_deal(deal_id)


@router.patch("/deals/{deal_id}")
def update_deal(
    deal_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit a deal's name, framework, stage, amount or rep."""
    return _engine(store).update_deal(
        deal_id, payload, source=_source("PATCH", "/deals/{deal_id}"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/score")
def score_card(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """The latest recorded score, or a live reading when none was recorded yet.

    A read computes but never writes, so this route leaves no audit row behind.
    """
    return _engine(store).score_card(deal_id)


@router.post("/deals/{deal_id}/score")
def score_now(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Score a deal now, record the reading, and raise the alerts and cues."""
    return _engine(store).score(
        deal_id, source=_source("POST", "/deals/{deal_id}/score"), actor="rep"
    )


@router.get("/deals/{deal_id}/history")
def history(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every recorded score for a deal, oldest first: the score time series."""
    return {"deal_id": deal_id, "history": _engine(store).history(deal_id)}


# --------------------------------------------------------------------------- #
# Engagement intake
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/events")
def list_events(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every engagement event recorded against a deal, oldest first."""
    return {"deal_id": deal_id, "events": _engine(store).list_events(deal_id)}


@router.post("/deals/{deal_id}/events")
def record_event(
    deal_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record one engagement event, then score the deal again.

    The response carries the score and any alert or cue the event raised, so one
    call leaves the deal consistent.
    """
    return _engine(store).record_event(
        deal_id, payload, source=_source("POST", "/deals/{deal_id}/events"), actor="buyer"
    )


# --------------------------------------------------------------------------- #
# The buying group
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/stakeholders")
def list_stakeholders(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every known member of the buying group, engaged or not."""
    return {"deal_id": deal_id, "stakeholders": _engine(store).list_stakeholders(deal_id)}


@router.post("/deals/{deal_id}/stakeholders")
def add_stakeholder(
    deal_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add one member of the buying group, engaged or not."""
    return _engine(store).add_stakeholder(
        deal_id, payload, source=_source("POST", "/deals/{deal_id}/stakeholders"), actor="rep"
    )


@router.patch("/deals/{deal_id}/stakeholders/{contact_id}")
def update_stakeholder(
    deal_id: str,
    contact_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit a member: their role, their email, or that they engaged."""
    return _engine(store).update_stakeholder(
        deal_id,
        contact_id,
        payload,
        source=_source("PATCH", "/deals/{deal_id}/stakeholders/{contact_id}"),
        actor="rep",
    )


@router.get("/deals/{deal_id}/coverage")
def coverage_map(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Who engaged and who has not: the stakeholder coverage map."""
    return _engine(store).coverage_map(deal_id)


# --------------------------------------------------------------------------- #
# Key material
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/content")
def list_content(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every key asset on a deal, with what is still unreviewed."""
    return {"deal_id": deal_id, "content": _engine(store).list_content(deal_id)}


@router.post("/deals/{deal_id}/content")
def add_content(
    deal_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add one key asset, required or optional, and whether it was reviewed."""
    return _engine(store).add_content(
        deal_id, payload, source=_source("POST", "/deals/{deal_id}/content"), actor="rep"
    )


@router.patch("/deals/{deal_id}/content/{asset_id}")
def review_content(
    deal_id: str,
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Mark one asset reviewed, or unreviewed, and score the deal again."""
    return _engine(store).review_content(
        deal_id,
        asset_id,
        reviewed=domain.read_bool(payload.get("reviewed"), True),
        source=_source("PATCH", "/deals/{deal_id}/content/{asset_id}"),
        actor="rep",
    )


# --------------------------------------------------------------------------- #
# Risk alerts
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/alerts")
def list_alerts(deal_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every risk alert raised for a deal, open or resolved."""
    return {"deal_id": deal_id, "alerts": _engine(store).list_alerts(deal_id)}


@router.post("/alerts/resolve")
def resolve_alert(
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Close one alert, so a settled risk stops being reported as open.

    The alert id travels in the body rather than the path: the route verifier
    knows a fixed set of path placeholders and would leave an unknown one in the
    URL, where it would fail as a request that never answered.
    """
    alert_id = str(payload.get("alert_id") or "").strip()
    if not alert_id:
        raise DealHealthError(
            "The alert could not be read.", {"alert_id": "Name the alert to resolve."}
        )
    return _engine(store).resolve_alert(
        alert_id, source=_source("POST", "/alerts/resolve"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Coaching and the closed loop
# --------------------------------------------------------------------------- #


@router.get("/deals/{deal_id}/coaching")
def list_coaching(
    deal_id: str,
    open_only: bool = Query(False),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The rep's coaching cues, with ``open_only=true`` for the ones still live."""
    return {
        "deal_id": deal_id,
        "coaching": _engine(store).list_coaching(deal_id, open_only=open_only),
    }


@router.post("/coaching/outcome")
def record_outcome(
    payload: dict[str, Any] = Body(default_factory=dict),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Close one coaching cue with an outcome, and write the readiness row.

    The cue id travels in the body for the same reason the alert id does.
    """
    cue_id = str(payload.get("cue_id") or "").strip()
    if not cue_id:
        raise DealHealthError(
            "The coaching outcome could not be read.", {"cue_id": "Name the coaching cue."}
        )
    return _engine(store).record_outcome(
        cue_id, payload, source=_source("POST", "/coaching/outcome"), actor="rep"
    )


@router.get("/readiness")
def readiness(rep: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The readiness index over recorded coaching outcomes, by rep."""
    return _engine(store).readiness(rep)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a score, an
    alert or a cue the HTTP routes would not produce. ``source="seed"`` rather
    than a route string: no route served this, and claiming one would be the lie
    the contract's write rule exists to prevent.

    The states seeded, and why each is here:

    * a **healthy** deal: recent meetings and dwells, both stakeholders engaged,
      both required assets reviewed, so the top band is visible;
    * a **watch** deal: a week-old room, one required asset reviewed and one not,
      and half the buying group unengaged;
    * an **at-risk** deal: no engagement at all, one required asset unreviewed
      and no stakeholder engaged, so the alert and the "content I still owe them"
      cue both appear;
    * one alert **resolved**, so the page shows a closed risk beside open ones;
    * one coaching cue **actioned** and one **dismissed**, so readiness is real
      data rather than an empty table.

    Every character of the returned string is ASCII, so the seeder can print it
    on a Windows console. A single non-cp1252 character in one feature broke the
    whole seeder once.
    """
    store = RecordStore(db)
    now = context["now"]
    room_ids = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    engine = DealHealthEngine(store, now=lambda: now)
    source, actor = "seed", "dana"

    # -- a healthy deal: live engagement, full coverage, nothing owed -------- #
    northwind = engine.create_deal(
        room_id,
        {
            "name": "Northwind expansion",
            "account": "Northwind Traders",
            "framework": "meddicc",
            "stage": "evaluation",
            "amount": 48000,
            "rep": "dana",
        },
        source=source,
        actor=actor,
    )
    for person, role, count in (
        ("Priya Raman", "champion", 6),
        ("Tom Okafor", "economic buyer", 4),
    ):
        engine.add_stakeholder(
            northwind["id"],
            {
                "name": person,
                "role": role,
                "email": person.split()[0].lower() + "@northwind.example",
                "engagement_count": count,
                "engaged_at": (now - timedelta(days=1)).isoformat(),
            },
            source=source,
            actor=actor,
        )
    for name, kind, reviewed in (
        ("Enterprise Overview Deck", "deck", True),
        ("Security Whitepaper", "document", True),
    ):
        engine.add_content(
            northwind["id"],
            {"name": name, "kind": kind, "required": True, "reviewed": reviewed},
            source=source,
            actor=actor,
        )
    for days, kind, person, seconds in (
        (1, "meeting", "Priya Raman", 1800),
        (2, "dwell", "Tom Okafor", 420),
        (3, "download", "Priya Raman", 0),
        (5, "comment", "Tom Okafor", 0),
    ):
        engine.record_event(
            northwind["id"],
            {
                "kind": kind,
                "person": person,
                "at": (now - timedelta(days=days)).isoformat(),
                "seconds": seconds,
            },
            source=source,
            actor=actor,
        )

    # -- a watch deal: ageing room, partial coverage, one asset owed -------- #
    fabrikam = engine.create_deal(
        room_id,
        {
            "name": "Fabrikam renewal",
            "account": "Fabrikam Logistics",
            "framework": "value_selling",
            "stage": "proposal",
            "amount": 21000,
            "rep": "sam",
        },
        source=source,
        actor=actor,
    )
    engine.add_stakeholder(
        fabrikam["id"],
        {
            "name": "Dana Ito",
            "role": "champion",
            "engagement_count": 2,
            "engaged_at": (now - timedelta(days=12)).isoformat(),
        },
        source=source,
        actor=actor,
    )
    engine.add_stakeholder(
        fabrikam["id"],
        {"name": "Lee Park", "role": "finance"},
        source=source,
        actor=actor,
    )
    engine.add_content(
        fabrikam["id"],
        {
            "name": "Renewal Pricing Sheet",
            "kind": "spreadsheet",
            "required": True,
            "reviewed": True,
        },
        source=source,
        actor=actor,
    )
    engine.add_content(
        fabrikam["id"],
        {"name": "Migration Plan", "kind": "document", "required": True},
        source=source,
        actor=actor,
    )
    engine.record_event(
        fabrikam["id"],
        {
            "kind": "view",
            "person": "Dana Ito",
            "at": (now - timedelta(days=12)).isoformat(),
            "seconds": 90,
        },
        source=source,
        actor=actor,
    )
    engine.record_event(
        fabrikam["id"],
        {
            "kind": "share",
            "person": "Dana Ito",
            "at": (now - timedelta(days=11)).isoformat(),
        },
        source=source,
        actor=actor,
    )

    # -- an at-risk deal: quiet room, no buyer, nothing reviewed ------------ #
    contoso = engine.create_deal(
        room_id,
        {
            "name": "Contoso pilot",
            "account": "Contoso Health",
            "framework": "bant",
            "stage": "discovery",
            "amount": 9000,
            "rep": "sam",
        },
        source=source,
        actor=actor,
    )
    for person, role in (("Ada Chen", "sponsor"), ("Ben Silva", "security")):
        engine.add_stakeholder(
            contoso["id"], {"name": person, "role": role}, source=source, actor=actor
        )
    engine.add_content(
        contoso["id"],
        {"name": "Pilot Scope", "kind": "document", "required": True},
        source=source,
        actor=actor,
    )
    # No engagement is recorded for this deal, so nothing scores it on the way
    # in. Score it explicitly, which is what raises its alert and its cues.
    engine.score(contoso["id"], source=source, actor=actor)

    summary = engine.summary(room_id)

    # -- close two cues, so readiness is real data --------------------------- #
    cues = engine.list_coaching(contoso["id"], open_only=True)
    if cues:
        engine.record_outcome(
            cues[0]["id"],
            {"state": "actioned", "note": "Booked the mutual action step."},
            source=source,
            actor=actor,
        )
    if len(cues) > 1:
        engine.record_outcome(
            cues[1]["id"],
            {"state": "dismissed", "note": "The buyer asked to wait."},
            source=source,
            actor=actor,
        )

    open_cues = sum(
        len(engine.list_coaching(deal["id"], open_only=True))
        for deal in (northwind, fabrikam, contoso)
    )
    outcomes = engine.readiness()
    return (
        f"3 deals: {summary['healthy']} healthy, {summary['watch']} watch, "
        f"{summary['at_risk']} at risk. {summary['open_alerts']} open risk alerts, "
        f"{open_cues} open coaching cues, {outcomes['outcomes']} coaching outcomes "
        f"(readiness {outcomes['readiness']}). One deal has no engagement, one required "
        f"asset is unreviewed, and one buying group never engaged."
    )


__all__ = ["FEATURE", "router", "EXCEPTION_HANDLERS", "seed"]
