"""WF-006: review buyer engagement and prioritise follow-up.

Ported from the ``feature/WF-006-...`` branch onto the plugin host. The domain
logic is untouched in :mod:`dsr.analytics`; this module is the HTTP surface and
the demo data, which is all the branch needed from the shared ``api.py`` and
``seed.py`` beyond a route table and an icon.

The error mapping stays inline per route rather than in ``EXCEPTION_HANDLERS``:
``UnknownRoom`` is this feature's own type, but the other two branches catch
builtins (``PermissionError`` -> 428, ``ValueError`` -> 400), and registering a
builtin globally would let this feature intercept exceptions raised anywhere in
the product.

The prefix is ``/api/wf-006`` rather than the branch's original ``/api/analytics``.
The contract asks for a ticket-derived prefix and the Jev gate for this port held
the feature at 'fix' partly for that reason, at confidence 0.88 for renaming over
loosening the rule. The original branch was never merged, so no client depends on
the old path and nothing external breaks.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query

from dsr import analytics
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-006-engagement-analytics",
    "ticket": "WF-006",
    "name": "Review buyer engagement and prioritise follow-up",
    "description": (
        "Consolidated engagement metrics across the pipeline, per-room drill-down, "
        "a shared timeline, and alerts for low engagement or approaching deadlines."
    ),
    "nav": [{"id": "engagement", "label": "Analytics"}],
}

router = APIRouter(prefix="/api/wf-006", tags=["wf-006"])


def _unknown_room(exc: analytics.UnknownRoom) -> HTTPException:
    return HTTPException(status_code=404, detail=f"room {exc} not found")


@router.get("/overview")
def analytics_overview(
    room_id: str | None = Query(default=None, description="Scope to one deal; omitted means All Rooms"),
    grain: Literal["day", "week"] = Query(default="day", description="Chart grain for visit frequency"),
    as_of: str | None = Query(default=None, description="ISO timestamp to evaluate recency against"),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The aggregate Analytics view: active deals, engagement, and alerts.

    Sourced from WF-006: "The Analytics view displays consolidated metrics
    across your pipeline, including: Total active deals. Recent buyer activity
    and engaged documents. Alerts for rooms with low engagement or approaching
    deadlines."
    """
    try:
        return analytics.overview(store, room_id=room_id, grain=grain, as_of=as_of)
    except analytics.UnknownRoom as exc:
        raise _unknown_room(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc


@router.get("/alerts")
def analytics_alerts(
    room_id: str | None = Query(default=None),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Low-engagement and approaching-deadline alerts, optionally room-scoped."""
    try:
        return analytics.alerts(store, room_id=room_id, as_of=as_of)
    except analytics.UnknownRoom as exc:
        raise _unknown_room(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc


@router.get("/rooms/{room_id}")
def analytics_room(
    room_id: str,
    grain: Literal["day", "week"] = Query(default="day"),
    as_of: str | None = Query(default=None),
    timeline_limit: int = Query(default=50, ge=1, le=500),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Per-room drill-down: stats, visitors, documents, charts, trend, timeline."""
    try:
        return analytics.room_engagement(
            store, room_id, grain=grain, as_of=as_of, timeline_limit=timeline_limit
        )
    except analytics.UnknownRoom as exc:
        raise _unknown_room(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=428, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/timeline")
def analytics_room_timeline(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The room Timeline: internal notes and buyer activity, newest first."""
    try:
        analytics.require_room(store, room_id)
        config = analytics.load_config(store)
        entries = analytics.timeline(
            store, config, room_id=room_id, now=analytics.resolve_now(as_of), limit=limit
        )
    except analytics.UnknownRoom as exc:
        raise _unknown_room(exc) from exc
    return {"room_id": room_id, "count": len(entries), "entries": entries}


@router.post("/rooms/{room_id}/timeline", status_code=201)
def analytics_add_timeline(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Append an internal update to the room Timeline. The write is audited."""
    try:
        return analytics.add_note(
            store,
            room_id,
            payload,
            actor=actor,
            source=f"POST {router.prefix}/rooms/{room_id}/timeline",
        )
    except analytics.UnknownRoom as exc:
        raise _unknown_room(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/events", status_code=201)
def analytics_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record one buyer interaction, the entry point of the data flow.

    The payload is stored verbatim, so a team can add a field nobody declared
    and still have it indexed and queryable with ``?where=``.
    """
    try:
        return analytics.record_event(
            store, payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/events"
        )
    except analytics.UnknownRoom as exc:
        raise _unknown_room(exc) from exc


@router.get("/config")
def analytics_config(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Effective configuration: stored overrides layered onto the defaults.

    The token is not echoed back; only whether one is present.
    """
    config = analytics.load_config(store)
    connection = dict(config.get("connection") or {})
    connection["token"] = "set" if connection.get("token") else ""
    return {"connected": analytics.is_connected(config), "config": {**config, "connection": connection}}


@router.patch("/config")
def analytics_update_config(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Merge a partial configuration patch. The write is audited like any other.

    Everything here is a record, not a column: thresholds, the action taxonomy,
    and the field-name synonyms are all overridable without a migration.
    """
    record = analytics.save_config(store, payload, actor=actor, source=f"PATCH {router.prefix}/config")
    return {"updated": True, "record": record}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

# The Analytics view reports no metrics until a token is present, so the demo
# dataset ships one. It is a placeholder, not a credential.
ANALYTICS_CONFIG = {
    "key": "default",
    "connection": {
        "token": "demo-environment-token",
        "environment": "demo",
        "connected_at": None,
    },
}

TIMELINE_NOTES = [
    "Shared the enterprise overview deck with the buying committee.",
    "Answered the security questionnaire; SOC 2 report requested.",
    "Pricing review booked with procurement.",
    "Asked for a mutual action plan before the pilot decision.",
]

ACTIONS = [
    "viewed_document",
    "downloaded_document",
    "completed_section",
    "watched_video",
    "returned_to_room",
]

DEVICES = ["desktop", "mobile", "tablet"]
COUNTRIES = ["AU", "US", "GB", "DE", "SG"]


def seed(db, context: dict[str, Any]) -> str:
    """Seed buyer activity as *sessions*, plus the config and timeline rows.

    The Analytics view derives a visit from the gap between one visitor's
    consecutive events, so activity written as an uncorrelated scatter makes
    every visit look like a single action and the Visit Frequency chart renders
    noise. Sessions are what the researched product actually measures.
    """
    room_ids = context["room_ids"]
    now = context["now"]
    rng = context["rng"]

    config = dict(ANALYTICS_CONFIG)
    config["connection"] = {**config["connection"], "connected_at": now.isoformat(timespec="seconds")}
    db.create(
        "analytics_config",
        config,
        record_id="analytics_config_default",
        actor="dana",
        source="seed",
    )

    notes = 0
    for index, (room_id, _account) in enumerate(room_ids):
        for offset, note in enumerate(TIMELINE_NOTES[: 2 + index % 2]):
            db.create(
                "timeline",
                {
                    "summary": note,
                    "actor": rng.choice(["dana", "sam"]),
                    "kind": "note",
                    "at": (now - timedelta(days=offset + 1, hours=index)).isoformat(timespec="seconds"),
                },
                room_id=room_id,
                actor="dana",
                source="seed",
            )
            notes += 1

    # One buyer per demo account, and that buyer only ever engages with its own
    # room. A Contoso address showing up on the Northwind deal would surface in
    # Most Active Visitors as nonsense.
    visitors = ["a.buyer@northwind.example", "procurement@contoso.example", "ops@fabrikam.example"]
    activities = 0
    for room_id, account in room_ids:
        for person in visitors[: rng.randint(1, len(visitors))]:
            for _ in range(5):
                entered = now - timedelta(minutes=rng.randint(0, 60 * 24 * 21))
                for step in range(rng.randint(1, 6)):
                    db.create(
                        "activity",
                        {
                            "person": person,
                            "account": account,
                            "action": rng.choice(ACTIONS),
                            "seconds_on_page": rng.randint(3, 900),
                            "device": rng.choice(DEVICES),
                            "country": rng.choice(COUNTRIES),
                            "occurred_at": (
                                entered + timedelta(minutes=step * rng.randint(1, 6))
                            ).isoformat(timespec="seconds"),
                        },
                        room_id=room_id,
                        actor="system",
                        source="seed",
                    )
                    activities += 1

    return f"analytics_config, {notes} timeline notes, {activities} activity events"
