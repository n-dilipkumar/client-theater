"""WF-133: real-time buyer-intent alerting and routing.

A target account's stakeholder returns to the room and reads the pricing section
for ninety seconds. This feature raises a signal, tells the account owner which
pages, how long and which stakeholder, and puts a follow-up task on the
opportunity, so the rep acts while the interest is live.

The domain logic is in :mod:`dsr.intent_routing`, which this module does not own
and which no other feature could have written into its own path. What lives here is
the three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-133``, and everything except the watchlists is
room-scoped.** A buyer's interest is always interest in a particular room, and an
alert that cannot say which room is not actionable. The read helpers are
room-scoped because a room holds a seller's whole audience.

**``source=`` names the method and the route.** Every write below passes the HTTP
method and ``router.prefix``, so the audit row names the route that actually served
it, in the form the rest of the product uses. A hardcoded string inside a domain
method is a defect, and the same class of bug has shipped in this codebase before: a
feature's audit log kept naming a path the app had stopped serving. The method is
part of the name because two features may share a prefix while their concrete paths
differ, so a bare path cannot say which of them wrote a row.

**Two routes for one decision, on purpose.** ``POST /signals/evaluate`` is the
strict classifier and reports its decision without writing. ``POST /signals``
commits. The reason is the same one WF-027 gives: a buyer who watched 40 percent of
a video is a fact rather than a caller error, so a caller watching several
companies before deciding who to alert gets its decisions without creating six
alerts and suppressing five of them.

**One handler for the whole error hierarchy.** ``IntentRoutingError`` is the base
of every refusal in :mod:`dsr.intent_routing`, and each carries its own ``status``
and ``code``, so one handler answers 422 for a malformed observation and 409 for
one that conflicts with state that already exists without being told which.
``RecordNotFound`` is deliberately *not* claimed: the core app already maps it to
404, and two handlers for one type is a collision the host refuses.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.intent_routing import IntentRouter
from dsr.intent_routing.errors import IntentRoutingError
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-133-real-time-buyer-intent-alerting-and-routing",
    "ticket": "WF-133",
    "name": "Alert the seller in real time when a target account shows intent",
    "description": (
        "Evaluate room engagement against the researched thresholds, resolve the account and the "
        "stakeholder, dispatch an alert to whoever the routing rules name, and create the "
        "follow-up task on the opportunity. Email is queued as a real outbox row and Slack is "
        "recorded as held, because this product has no outbound transport for either."
    ),
    "nav": [{"id": "intent-alerts", "label": "Intent alerts"}],
}

router = APIRouter(prefix="/api/wf-133", tags=["wf133"])


def get_router(store: RecordStore = StoreDep) -> IntentRouter:
    """An :class:`IntentRouter` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it here
    also leaves the engine a plain object, which is what a test constructs.
    """
    return IntentRouter(store)


RouterDep = Depends(get_router)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _intent_error(request: Request, exc: IntentRoutingError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``IntentRoutingError`` is the base of every
    refusal in :mod:`dsr.intent_routing`, and all of them are the caller's to fix.
    The status rides on the exception rather than being decided here, because "this
    observation carries a field nobody thresholds on" and "this account has no
    opportunity to attach a task to" are both this package's errors and only one of
    them conflicts with state that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {IntentRoutingError: _intent_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """Every name, number and threshold this workflow uses, served as data.

    The five thresholds with the number each one has and how it was arrived at, the
    three quoted alert-context fields, the notification channels, the routing rule
    kinds and the suppression window. A client renders its pickers from this rather
    than from a list compiled into the page, so a threshold changed in the domain
    module reaches every client at once.
    """
    return router_.vocabulary()


@router.get("/inferences")
def inferences(router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research quotes one number and names four automations, two product surfaces
    and eleven open-source tools without defining the rules any of them run on. Those
    edges are collected here, named, traceable and served, rather than left as
    comments in function bodies. Fourteen of the fifteen entries are inferences and
    each says so; the sourced half comes back beside the inferred half, because the
    point of the endpoint is to see where the line falls.
    """
    return router_.inferences()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The room at a glance: watched accounts, open alerts, and work outstanding.

    Every number here is a count over this feature's own collections. Nothing is
    read from another feature, so a room whose dependencies have not seeded reads as
    zeros rather than failing.
    """
    watchlists = router_.watchlists(room_id=room_id)
    alerts = router_.alerts(room_id=room_id)
    tasks = router_.tasks(room_id=room_id)
    states: dict[str, int] = {}
    for alert in alerts:
        key = str(alert.get("state") or "open")
        states[key] = states.get(key, 0) + 1
    channels: dict[str, int] = {}
    for alert in alerts:
        for channel, state in (alert.get("channel_states") or {}).items():
            channels[f"{channel}:{state}"] = channels.get(f"{channel}:{state}", 0) + 1
    return {
        "room_id": room_id,
        "watchlists": len(watchlists),
        "watched_accounts": sum(entry["account_count"] for entry in watchlists),
        "alerts": len(alerts),
        "alert_states": states,
        "channel_states": channels,
        "tasks": len(tasks),
        "actions": len(router_.actions(room_id=room_id)),
        "sends_mail": False,
        "sends_slack": False,
    }


# --------------------------------------------------------------------------- #
# Target-account watchlists
# --------------------------------------------------------------------------- #


@router.get("/watchlists")
def list_watchlists(
    room_id: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The target-account watchlists, and how many accounts each one watches."""
    listed = router_.watchlists(room_id=room_id)
    return {
        "count": len(listed),
        "watched_accounts": sum(entry["account_count"] for entry in listed),
        "watchlists": listed,
    }


@router.post("/watchlists", status_code=201)
def create_watchlist(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Watch a set of accounts in a room.

    Membership is the rule behind the researched "target-account watchlists", which
    the research names as a surface and states no rule for. Only an account on a
    watchlist raises an alert, because an alerting workflow that alerts on every
    visitor is one a rep turns off in a week.
    """
    return router_.create_watchlist(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/watchlists"
    )


@router.get("/watchlists/{watchlist_id}")
def read_watchlist(watchlist_id: str, router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """One watchlist and the accounts it watches."""
    return router_.read_watchlist(watchlist_id)


@router.delete("/watchlists/{watchlist_id}")
def delete_watchlist(
    watchlist_id: str,
    actor: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Stop watching a set of accounts.

    A soft delete, so the audit trail keeps the list that was in force when each
    alert was raised.
    """
    return router_.delete_watchlist(
        watchlist_id, actor=actor, source=f"DELETE {router.prefix}/watchlists/{{watchlist_id}}"
    )


# --------------------------------------------------------------------------- #
# Alert routing rules
# --------------------------------------------------------------------------- #


@router.get("/rules")
def list_rules(
    room_id: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The routing chain, in the order it is consulted."""
    listed = router_.rules(room_id=room_id)
    return {"count": len(listed), "rules": listed}


@router.post("/rules", status_code=201)
def create_rule(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Add one link to the routing chain.

    Four kinds are accepted: ``crm_owner``, ``team``, ``watchlist`` and
    ``fallback``. A rule that matches on a key no rule reads is refused rather than
    stored, because a rule that silently never fires is worse than a rule nobody
    configured.
    """
    return router_.create_rule(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/rules"
    )


@router.get("/rules/{rule_id}")
def read_rule(rule_id: str, router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """One routing rule."""
    return router_.read_rule(rule_id)


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str,
    actor: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Remove one link from the routing chain."""
    return router_.delete_rule(
        rule_id, actor=actor, source=f"DELETE {router.prefix}/rules/{{rule_id}}"
    )


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #


@router.post("/signals/evaluate")
def evaluate_signal(
    payload: dict[str, Any] = Body(default_factory=dict),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Say what the thresholds decide, and write nothing.

    The report and the commit are two routes on purpose. This one never creates an
    alert, so a caller watching several companies before deciding who to alert gets
    every decision without creating six alerts and suppressing five of them. The
    ``reason`` says which of the four gates stopped it: below a threshold, not on a
    watchlist, or no opportunity attached.
    """
    return router_.evaluate(payload)


@router.post("/signals", status_code=201)
def raise_signal(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Evaluate an observation and commit the whole researched chain.

    Thresholds, then account and stakeholder resolution, then the routing chain,
    then the alert, then the follow-up task on the opportunity. Refuses with 404
    for a company nobody has identified and 409 for one that cannot be routed,
    rather than writing a partial chain: a follow-up task with no opportunity is a
    row nobody will ever see.

    A 201 here means three records were written and audited. A 200 with
    ``raised: false`` means the thresholds were not met, nothing was written, and
    ``reason`` says why.
    """
    return router_.ingest(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/signals"
    )


@router.get("/signals")
def list_signals(
    room_id: str | None = Query(default=None),
    company_key: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The signals this room has raised, newest first."""
    listed = router_.signals(room_id=room_id, company_key=company_key, limit=limit)
    return {"count": len(listed), "signals": listed}


@router.get("/signals/{signal_id}")
def read_signal(signal_id: str, router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """One signal: the measurements, the crossings and the arithmetic behind them."""
    return router_.read_signal(signal_id)


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #


@router.get("/alerts")
def list_alerts(
    room_id: str | None = Query(default=None),
    state: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The alerts this room has raised, newest first."""
    listed = router_.alerts(room_id=room_id, state=state, limit=limit)
    return {
        "count": len(listed),
        "by_state": _tally(alert["state"] for alert in listed),
        "by_channel": _tally(
            f"{channel}:{value}"
            for alert in listed
            for channel, value in (alert.get("channel_states") or {}).items()
        ),
        "alerts": listed,
    }


@router.get("/alerts/{alert_id}")
def read_alert(alert_id: str, router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """One alert, with the three researched facts and what happened on each channel.

    ``channel_states`` is the honest part. ``queued`` means a real outbox row exists
    with a recipient, a subject and a body. ``held_for_integration`` means the
    channel is named by the research and this build has no surface for it.
    ``skipped`` means the accountable party is known and has no address on file.
    ``suppressed`` means somebody was told within the window already.
    """
    return router_.read_alert(alert_id)


# --------------------------------------------------------------------------- #
# Follow-up tasks
# --------------------------------------------------------------------------- #


@router.get("/tasks")
def list_tasks(
    room_id: str | None = Query(default=None),
    company_key: str = Query(default=""),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The follow-up tasks this room put on opportunities."""
    listed = router_.tasks(room_id=room_id, company_key=company_key)
    return {"count": len(listed), "tasks": listed}


@router.get("/tasks/{task_id}")
def read_task(task_id: str, router_: IntentRouter = RouterDep) -> dict[str, Any]:
    """One follow-up task, carrying the alert payload so the CRM needs no second call."""
    return router_.read_task(task_id)


# --------------------------------------------------------------------------- #
# Who is engaged and who is not
# --------------------------------------------------------------------------- #


@router.get("/engagement")
def engagement(
    room_id: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Every target account in the room, and whether it currently has a live alert.

    The researched surface with the least definition in it. The derived reading is
    that the list is the **watchlist**, not the set of accounts that happened to
    trigger something: a view built from the alerts alone can only ever show who is
    engaged, so the half of the screen the research asks for by name would be
    empty. An account nobody has engaged appears with an empty last-seen moment,
    which is the information a seller most needs.
    """
    return router_.engagement(room_id=room_id)


# --------------------------------------------------------------------------- #
# Rep action logged
# --------------------------------------------------------------------------- #


@router.get("/actions")
def list_actions(
    room_id: str | None = Query(default=None),
    signal_id: str = Query(default=""),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """The append-only log of what reps did about signals."""
    listed = router_.actions(room_id=room_id, signal_id=signal_id)
    return {
        "count": len(listed),
        "by_kind": _tally(entry["kind"] for entry in listed),
        "actions": listed,
    }


@router.post("/signals/{signal_id}/actions", status_code=201)
def record_action(
    signal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    router_: IntentRouter = RouterDep,
) -> dict[str, Any]:
    """Record what a rep did about a signal, and move the alert's state.

    The researched data flow ends at "rep action logged" and nothing follows it, so
    this workflow records and does not decide: there is no next step for it to own.
    Four kinds are accepted, a dismissal needs a note, and the alert's state is
    derived from the log rather than set directly, so an alert can never hold a
    state its own log does not support.
    """
    return router_.log_action(
        signal_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/signals/{{signal_id}}/actions",
    )


def _tally(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _ago(now: datetime, **back: float) -> str:
    """An ISO 8601 moment ``back`` from ``now``.

    Every string this seed writes carries an offset, and every string this seed
    returns is ASCII, because ``seed.py`` prints the return value on a Windows
    console and one rightwards arrow in one recovered feature broke the whole
    seeder.
    """
    return (now - timedelta(**back)).isoformat()


def _ensure_company(db: AuditedDatabase, key: str, data: dict[str, Any]) -> None:
    """Write a WF-031 company record only when one is not already there.

    The seeder runs every feature in alphabetical module order, so WF-031 may or may
    not have created this company by the time this one runs. Writing it here makes
    this feature's demo data self-contained, and the existence check stops a second
    seed run from producing two companies under one key.
    """
    existing = db.find("identified_company", {"company_key": key}, limit=1)
    if existing:
        return
    db.create("identified_company", data, actor="seed", source="seed")


def _ensure_crm(db: AuditedDatabase, room_id: str, data: dict[str, Any]) -> None:
    existing = db.find("crm_read_record", {"external_id": data["external_id"]}, limit=1)
    if existing:
        return
    db.create("crm_read_record", data, room_id=room_id, actor="seed", source="seed")


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Demo rows for the intent-alerting page.

    Three target accounts with four different alert states, because a demo where
    every alert is open teaches nothing about what the page does with an alert a
    rep has already worked. The states are: one open, two contacted, one dismissed,
    and one further signal suppressed because the same rep was told within the
    window.

    The prerequisites are provisioned here rather than borrowed. Reading them from
    WF-031 and WF-042 would make this feature's demo data depend on the alphabetical
    order of the seeder, and a demo that appears only when another branch's seed
    happens to run first is not a demo.
    """
    room_ids = context.get("room_ids") or []
    if not room_ids:
        return ""
    room_id = room_ids[0][0]
    now = context["now"]

    accounts = {
        "northwind-energy": {
            "company_key": "northwind-energy",
            "identified_from": "capture",
            "name": "Northwind Energy",
            "website": "https://northwind-energy.example",
            "address": "4 Shipley Lane, Manchester",
            "size": "1000+",
            "segment": "enterprise",
            "tags": ["target"],
            "countries": ["GB"],
            "contacts": [{"name": "Dana Okafor", "role": "VP Procurement"}],
            "page_views": 7,
            "paths": ["/overview", "/security", "/pricing", "/case-studies"],
            "first_seen_at": _ago(now, days=9),
            "last_visit_at": _ago(now, hours=5),
        },
        "tailwind-and-friends": {
            "company_key": "tailwind-and-friends",
            "identified_from": "capture",
            "name": "Tailwind and Friends",
            "website": "https://tailwind.example",
            "address": "",
            "size": "51-200",
            "segment": "mid-market",
            "tags": ["target"],
            "countries": ["NL"],
            "contacts": [{"name": "Ines Vogel", "role": "Head of IT"}],
            "page_views": 3,
            "paths": ["/pricing", "/security"],
            "first_seen_at": _ago(now, days=4),
            "last_visit_at": _ago(now, hours=30),
        },
        "meridian-foods": {
            "company_key": "meridian-foods",
            "identified_from": "capture",
            "name": "Meridian Foods",
            "website": "https://meridianfoods.example",
            "address": "",
            "size": "201-500",
            "segment": "mid-market",
            "tags": [],
            "countries": ["IE"],
            "contacts": [],
            "page_views": 1,
            "paths": ["/overview"],
            "first_seen_at": _ago(now, days=2),
            "last_visit_at": _ago(now, days=2),
        },
    }
    for data in accounts.values():
        _ensure_company(db, str(data["company_key"]), data)

    crm_rows = [
        {
            "system": "salesforce",
            "object": "account",
            "external_id": "wf133-acct-northwind",
            "owner_id": "005-dana",
            "fields": {
                "Name": "Northwind Energy",
                "Website": "https://northwind-energy.example",
                "Industry": "Energy",
            },
            "crm_owner": {"team": "enterprise-uk"},
        },
        {
            "system": "salesforce",
            "object": "deal",
            "external_id": "wf133-deal-northwind",
            "owner_id": "005-dana",
            "fields": {
                "Name": "Northwind platform rollout",
                "StageName": "Negotiation",
                "Amount": 48000,
                "AccountId": "wf133-acct-northwind",
            },
        },
        {
            "system": "salesforce",
            "object": "account",
            "external_id": "wf133-acct-tailwind",
            "owner_id": "005-sam",
            "fields": {
                "Name": "Tailwind and Friends Ltd",
                "Website": "https://tailwind.example",
                "Industry": "Logistics",
            },
            "crm_owner": {"team": "mid-market-eu"},
        },
        {
            "system": "salesforce",
            "object": "deal",
            "external_id": "wf133-deal-tailwind",
            "owner_id": "005-sam",
            "fields": {
                "Name": "Tailwind onboarding",
                "StageName": "Qualification",
                "Amount": 12000,
                "AccountId": "wf133-acct-tailwind",
            },
        },
    ]
    for data in crm_rows:
        _ensure_crm(db, room_id, data)

    identities = [
        {
            "system": "salesforce",
            "buyer_email": "dana.okafor@northwind-energy.example",
            "buyer_name": "Dana Okafor",
            "account_id": "wf133-acct-northwind",
            "contact_id": "wf133-con-northwind",
            "deal_id": "wf133-deal-northwind",
            "owner_id": "005-dana",
            "source": "room_mapping",
        },
        {
            "system": "salesforce",
            "buyer_email": "ines.vogel@tailwind.example",
            "buyer_name": "Ines Vogel",
            "account_id": "wf133-acct-tailwind",
            "contact_id": "wf133-con-tailwind",
            "deal_id": "wf133-deal-tailwind",
            "owner_id": "005-sam",
            "source": "room_mapping",
        },
    ]
    for data in identities:
        found = db.find("crm_read_identity", {"deal_id": data["deal_id"]}, limit=1)
        if not found:
            db.create("crm_read_identity", data, room_id=room_id, actor="seed", source="seed")

    engine = IntentRouter(RecordStore(db))
    created = _seed_configuration(engine, room_id)
    if not created:
        return "0 watchlists, 0 routing rules (already seeded)"

    raised = _seed_activity(engine, room_id, now)
    states = _tally(entry["state"] for entry in raised)
    channels = _tally(
        f"{channel}:{value}"
        for entry in raised
        for channel, value in (entry.get("channel_states") or {}).items()
    )
    parts = ", ".join(f"{count} {state}" for state, count in sorted(states.items()))
    channel_parts = ", ".join(f"{count} {key}" for key, count in sorted(channels.items()))
    return (
        f"3 target accounts, 1 watchlist, 2 routing rules, {len(raised)} alerts ({parts}); "
        f"dispatches: {channel_parts}"
    )


def _seed_configuration(engine: IntentRouter, room_id: str) -> bool:
    """The watchlist and the routing chain. Skipped when already present."""
    if engine.watchlists(room_id=room_id):
        return False
    engine.create_watchlist(
        {
            "name": "Strategic accounts",
            "tier": "strategic",
            "accounts": ["northwind-energy", "tailwind-and-friends", "meridian-foods"],
            "notify": ["dana.kelly@acme.example"],
            "note": "Reviewed every Monday. One alert per recipient per day.",
        },
        room_id=room_id,
        actor="seed",
        source="seed",
    )
    engine.create_rule(
        {
            "name": "Opportunity owner first",
            "kind": "crm_owner",
            "position": 0,
            "note": "The researched recipient. Tried before anything else.",
        },
        room_id=room_id,
        actor="seed",
        source="seed",
    )
    engine.create_rule(
        {
            "name": "Enterprise territory",
            "kind": "team",
            "match": {"team": "enterprise-uk"},
            "notify": ["dana.kelly@acme.example"],
            "position": 1,
            "stop": False,
        },
        room_id=room_id,
        actor="seed",
        source="seed",
    )
    return True


#: Four observations chosen to produce four different alert states between them.
_SEED_OBSERVATIONS: tuple[dict[str, Any], ...] = (
    {
        "company_key": "northwind-energy",
        "room_label": "the Northwind room",
        "pages": ["/overview", "/security", "/pricing", "/case-studies"],
        "dwell_seconds": 142,
        "total_dwell_seconds": 480,
        "revisits": 3,
        "downloads": 1,
        "demo_interactions": 1,
        "stakeholder": "Dana Okafor",
        "stakeholder_role": "VP Procurement",
        "hours": 5,
        "actions": (("acknowledged", "Calling her today."), ("contacted", "Left a voicemail.")),
    },
    {
        "company_key": "tailwind-and-friends",
        "room_label": "the Tailwind room",
        "pages": ["/pricing", "/security", "/pricing"],
        "dwell_seconds": 96,
        "total_dwell_seconds": 210,
        "revisits": 3,
        "downloads": 0,
        "demo_interactions": 0,
        "stakeholder": "",
        "stakeholder_role": "",
        "hours": 30,
        "actions": (("dismissed", "Read the pricing page last quarter too. Not real intent."),),
    },
    {
        # Below every threshold. Written to nothing, so the engagement view has a
        # watched account that has never alerted.
        "company_key": "meridian-foods",
        "room_label": "the Meridian room",
        "pages": ["/overview"],
        "dwell_seconds": 22,
        "total_dwell_seconds": 22,
        "revisits": 0,
        "downloads": 0,
        "demo_interactions": 0,
        "stakeholder": "",
        "stakeholder_role": "",
        "hours": 48,
        "actions": (),
    },
    {
        # The same company and the same recipient as the first observation, so this
        # one is suppressed rather than queued.
        "company_key": "northwind-energy",
        "room_label": "the Northwind room",
        "pages": ["/pricing"],
        "dwell_seconds": 104,
        "total_dwell_seconds": 104,
        "revisits": 0,
        "downloads": 0,
        "demo_interactions": 0,
        "stakeholder": "Dana Okafor",
        "stakeholder_role": "VP Procurement",
        "hours": 4,
        "actions": (),
    },
)


def _seed_activity(engine: IntentRouter, room_id: str, now: datetime) -> list[dict[str, Any]]:
    """Raise the four demo observations and leave the reps' actions behind."""
    alerts: list[dict[str, Any]] = []
    for entry in _SEED_OBSERVATIONS:
        payload = {key: value for key, value in entry.items() if key not in ("hours", "actions")}
        payload["room_id"] = room_id
        payload["last_seen_at"] = _ago(now, hours=float(entry["hours"]))
        result = engine.ingest(payload, room_id=room_id, actor="seed", source="seed", now=now)
        if not result.get("raised"):
            continue
        alert = result["alert"]
        for kind, note in entry["actions"]:
            engine.log_action(
                result["signal"]["id"],
                {"kind": kind, "note": note},
                actor="dana.kelly",
                source="seed",
                now=now,
            )
        alerts.append(engine.read_alert(alert["id"]))
    return alerts


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
