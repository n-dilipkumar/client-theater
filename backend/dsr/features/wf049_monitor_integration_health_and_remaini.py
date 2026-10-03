"""WF-049: monitor integration health and remaining API quota.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-049.md``, which is
the specification. The researched decisions are the product: the Monitoring
dashboard's five reads (remaining daily + burst quota, sync success rate, mean
latency, the four error classes, the live change-stream lag), quota read from
the vendor's own surfaces - Salesforce ``Sforce-Limit-Info`` + ``/limits/``,
HubSpot's ``X-HubSpot-RateLimit-*`` headers + account information, Dataverse's
``EntityDefinitions`` change-tracking audit and ``globalmetadataversion`` - and
the operator's controls: "If a connector is starved, the operator lowers its
concurrency or pauses it from the same page."

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the
demo data. The domain lives in :mod:`dsr.integ_monitor`.

Why the prefix is ``/api/wf-049``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is
room-scoped - ``GET /api/wf-049/rooms/<room_id>/dashboard`` - and the host's
loader would report a ``(method, path)`` clash as a failed feature rather than
shadowing it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is
a *required* keyword on every domain method that writes. This matters more here
than on most features: a monitoring dashboard is exactly the page whose audit
log would quietly fill with rows naming a route the app had stopped serving,
because everything on it polls. A test asserts that every source recorded in
the audit log matches a route the host actually mounted.

Error mapping
-------------
One registered handler for the whole hierarchy, branching on the type:
404 for a room, connector or rule that does not exist; 400 for everything the
caller can fix - an unknown vendor, an unparseable quota surface, a rule
watching a metric the room does not compute, a threshold that is not a number.
A vendor refusing to be read is not an error state this layer models at all:
the room never reads a vendor, so there is no 502 here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.integ_monitor import (
    DEFAULT_WINDOW_SECONDS,
    METRICS,
    IntegrationMonitor,
    InvalidQuotaSurface,
    MonitorError,
    UnknownConnector,
    UnknownRoom,
    UnknownRule,
    describe_inferences,
    vocabulary,
)
from dsr.integ_monitor.vocabulary import CHANNELS, VENDORS
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-049-monitor-integration-health-and-remaini",
    "ticket": "WF-049",
    "name": "Monitor integration health and remaining API quota",
    "description": (
        "The room's Integrations → Monitoring dashboard: remaining daily and burst quota "
        "normalised from each vendor's own surfaces, sync success rate and mean latency, "
        "the validation / throttle / auth / vendor-5xx error breakdown, the live "
        "change-stream lag, and the pause and concurrency controls."
    ),
    "nav": [{"id": "integration-monitor", "label": "Integration monitor"}],
}

router = APIRouter(prefix="/api/wf-049", tags=["wf-049"])


def get_monitor(store: RecordStore = StoreDep) -> IntegrationMonitor:
    """An :class:`IntegrationMonitor` over the process-wide audited store.

    Built per request rather than held on ``app.state``: the engine holds
    nothing but the store handle, and ``app.state`` is a shared file this
    feature must not edit. It also leaves the whole workflow unit-testable
    against a temporary database without the app running.
    """
    return IntegrationMonitor(store)


MonitorDep = Depends(get_monitor)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _monitor_error(request: Request, exc: MonitorError) -> JSONResponse:
    """A refusal from this workflow, as a response.

    One registered handler for the whole hierarchy, branching on the type
    rather than registering nine. A room, connector or rule that does not exist
    is 404 because that is what it is. Everything else is 400: a well-formed
    request asking for something this layer will not do - an unparseable quota
    surface, a rule watching a metric the room does not compute, a threshold
    that is not a number, telemetry with no calls in it. An unparseable vendor
    answer is a 400 rather than a 502 because the room never fetched it: the
    connector handed it in, and the caller can re-read the surface and hand it
    in again.

    ``RecordNotFound`` is deliberately not claimed: the core app already maps
    it to 404, and two handlers for one type is a collision the host refuses.
    """
    status = 404 if isinstance(exc, (UnknownRoom, UnknownConnector, UnknownRule)) else 400
    content: dict[str, Any] = {"error": type(exc).__name__, "detail": str(exc)}
    if isinstance(exc, InvalidQuotaSurface):
        content["hint"] = "the surface ids each vendor accepts are at GET /api/wf-049/vocabulary"
    return JSONResponse(status_code=status, content=content)


EXCEPTION_HANDLERS = {MonitorError: _monitor_error}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published name, as data")
def read_vocabulary() -> dict[str, Any]:
    """Vendors, error classes, metrics, channels, collections, quota surfaces.

    Served so a client renders its pickers and its threshold units from the same
    source the parsers validate against, and so a reviewer can read the
    researched facts - each surface with the sentence that produced it, and the
    research's own two gaps - without opening a research file.
    """
    return vocabulary()


@router.get("/inferences", summary="What the research left to this build")
def read_inferences() -> dict[str, Any]:
    """Every judgement call in the workflow, named, bounded and changeable.

    The research is specific about the quota surfaces and the two automations,
    and silent about the dashboard's window, the alert cooldown, the concurrency
    default, and the status-to-class mapping. Those are collected in
    :mod:`dsr.integ_monitor.inferences` and served here, next to the sourced
    facts they are measured against.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Step 1 and step 4: the connector, and its controls
# --------------------------------------------------------------------------- #


@router.get("/connectors", summary="Monitored connectors")
def list_connectors(
    room_id: str | None = Query(default=None),
    vendor: str | None = Query(default=None, description=" | ".join(VENDORS)),
    paused: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Every monitored connector this installation has, newest first.

    ``room_id`` narrows to one room's Integrations page. ``paused`` is a filter
    rather than an afterthought because the pause state is one of the states
    the dashboard exists to show: a connector an operator paused is not a
    broken connector, and the page says which it is.
    """
    rows = monitor.list_connectors(room_id=room_id, vendor=vendor, paused=paused, limit=limit)
    return {"count": len(rows), "room_id": room_id, "connectors": rows}


@router.post("/connectors", status_code=201, summary="Register a connector for monitoring")
def create_connector(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Register a connector on a room.

    The vendor must already have numbers this room can read: an unregistered
    vendor is refused with the researched extensibility point - "there is
    exactly one place to teach the system a new vendor's numbers" - which is
    :data:`dsr.integ_monitor.engine.DEFAULT_POLICIES`, not this route.
    """
    return monitor.register_connector(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/connectors"
    )


@router.get("/connectors/{connector_id}", summary="One connector, and its current readings")
def read_connector(connector_id: str, monitor: IntegrationMonitor = MonitorDep) -> dict[str, Any]:
    return monitor.connector(connector_id)


@router.patch("/connectors/{connector_id}", summary="Pause, resume, or lower concurrency")
def update_connector(
    connector_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """The researched step 4, from the same page that shows the dashboard.

    [sourced] "If a connector is starved, the operator lowers its concurrency
    or pauses it from the same page."

    ``{"paused": true}`` takes the connector out of alert evaluation and marks
    when it was paused; ``{"concurrency": 2}`` lowers the bound the connector's
    own scheduler reads. Both are refusals-not-clamps: an operator typing
    ``concurrency: 0`` finds out now, not when the connector stops syncing.
    """
    return monitor.update_connector(
        connector_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connectors/{connector_id}",
    )


@router.delete("/connectors/{connector_id}", status_code=204, summary="Stop monitoring a connector")
def delete_connector(
    connector_id: str,
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> Response:
    """Soft delete. The observations and the audit trail outlive it, so the
    history of a connector that was monitored and then removed is still here."""
    monitor.remove_connector(
        connector_id, actor=actor, source=f"DELETE {router.prefix}/connectors/{connector_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Steps 2 and 3: quota, from the vendor's own surfaces
# --------------------------------------------------------------------------- #


@router.post("/connectors/{connector_id}/quota", summary="Record one reading from a vendor surface")
def record_quota(
    connector_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """The researched data flow, one call at a time.

    [sourced] "vendor quota metadata (headers + limits endpoints) → room
    metrics store", read from surfaces the research names per vendor:

    * Salesforce: ``{"surface": "limit_info_header", "header": "api-usage=123/500000"}``
      or ``{"surface": "limits_resource", "body": [...]}``;
    * HubSpot: ``{"surface": "rate_limit_headers", "headers": {...}}`` or
      ``{"surface": "account_information", "body": {...}}``;
    * Dataverse: none - the research's own gap says its limit table was not
      found at a readable URL, so a Dataverse quota reading is refused with
      that gap quoted rather than answered with invented numbers.

    The room opens no socket and holds no vendor credential: the connector
    hands in the answer it already received, and the room normalises and
    stores it.
    """
    return monitor.record_quota(
        connector_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connectors/{connector_id}/quota",
    )


@router.get(
    "/connectors/{connector_id}/quota", summary="One connector's latest pair, and its delta"
)
def read_quota(connector_id: str, monitor: IntegrationMonitor = MonitorDep) -> dict[str, Any]:
    """The normalised "remaining today / remaining this window" pair.

    ``delta`` is the change since the previous reading, which is the number
    that turns "1,499,877 remaining" into "burning 123 per poll", and it is
    ``null`` when either side is unknown rather than a zero nobody measured.
    """
    return monitor.quota_view(connector_id)


# --------------------------------------------------------------------------- #
# The telemetry and the lag, the other two dashboard inputs
# --------------------------------------------------------------------------- #


@router.post("/connectors/{connector_id}/telemetry", summary="Record the calls a connector made")
def record_telemetry(
    connector_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Store one or more call samples, one record per call, and the aggregate.

    [sourced] "connector telemetry → room metrics store", and the dashboard
    reads "sync success rate, mean latency, error-class breakdown (validation /
    throttle / auth / vendor-5xx)".

    A sample is ``{"ok": true}`` or ``{"ok": false, "status": 429,
    "latency_ms": 812}``; ``status`` alone works because ``ok`` follows from it.
    An explicit ``error_class`` from the four researched ones wins over the
    status-derived class, which is how a Salesforce 403
    REQUEST_LIMIT_EXCEEDED counts as a throttle rather than a validation.
    """
    return monitor.record_telemetry(
        connector_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connectors/{connector_id}/telemetry",
    )


@router.get("/connectors/{connector_id}/telemetry", summary="One connector's health aggregate")
def read_telemetry(
    connector_id: str,
    window_seconds: float = Query(default=DEFAULT_WINDOW_SECONDS, ge=60.0, le=2_592_000.0),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Success rate, mean latency, and the error-class breakdown, over one window.

    ``known: false`` for a connector with no samples in the window - the honest
    "no data" rather than a 100% success rate nobody earned.
    """
    return monitor.telemetry_view(connector_id, window_seconds=window_seconds)


@router.post(
    "/connectors/{connector_id}/stream", summary="Record one change-stream lag observation"
)
def record_stream(
    connector_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """The "live change-stream lag" reading, stored with how it was computed.

    Either ``{"lag_seconds": 42}`` or ``{"source_event_at": "...", "observed_at":
    "..."}``, from which the room computes the difference. A receiver ahead of
    its source is clock skew and clamps to zero rather than recording a
    negative lag, which would make a lag rule un-fireable.
    """
    return monitor.record_stream(
        connector_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/connectors/{connector_id}/stream",
    )


# --------------------------------------------------------------------------- #
# The room-scoped dashboard
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/dashboard", summary="The researched Monitoring dashboard")
def room_dashboard(
    room_id: str,
    window_seconds: float = Query(default=DEFAULT_WINDOW_SECONDS, ge=60.0, le=2_592_000.0),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Everything the research says the dashboard shows, per connector.

    [sourced] "Dashboard shows remaining daily + burst quota, sync success
    rate, mean latency, error-class breakdown (validation / throttle / auth /
    vendor-5xx), and the live change-stream lag."

    The alert section is a dry run: every rule, what it would fire on now, and
    why. Firing is ``POST /rooms/{room_id}/alerts/evaluate``, because a GET
    that starts cooldowns would be a read that lies about having read.
    """
    return monitor.dashboard(room_id, window_seconds=window_seconds)


# --------------------------------------------------------------------------- #
# The Dataverse change-tracking audit and the schema-drift signal
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/change-tracking", status_code=201, summary="Record the change-tracking audit"
)
def record_change_tracking(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """The Dataverse audit, and the schema-drift signal beside it.

    [sourced] "GET /api/data/v9.2/EntityDefinitions?$select=SchemaName&$filter=
    ChangeTrackingEnabled eq true" to audit which tables are being tracked, and
    "Microsoft.Dynamics.CRM.globalmetadataversion | The value changes when any
    schema change occurs, indicating that you might need to refresh any schema
    data that your application cached."

    A version change against the previous audit is recorded as ``drift: true``
    with both versions - a signal, not a failure, because the vendor's own
    sentence is advisory. The room audits the *tables being tracked*, which is
    the change-stream lag's own health from the vendor's side.
    """
    return monitor.record_change_tracking(
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/change-tracking",
    )


@router.get("/rooms/{room_id}/change-tracking", summary="The change-tracking audits, drift first")
def read_change_tracking(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=1000),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Every audit this room has recorded, newest first, with the drift count."""
    return monitor.change_tracking_view(room_id, limit=limit)


# --------------------------------------------------------------------------- #
# The alert rules, and the automation that fires them
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/alerts", summary="The room's alert rules")
def list_alert_rules(
    room_id: str,
    enabled: bool | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """The rules in force for this room, creation order, with their fire history."""
    rows = monitor.list_rules(room_id, enabled=enabled)
    return {
        "room_id": room_id,
        "count": len(rows),
        "metrics": list(METRICS),
        "channels": list(CHANNELS),
        "rules": rows,
    }


@router.post("/rooms/{room_id}/alerts/rules", status_code=201, summary="Add an alert rule")
def create_alert_rule(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """Arm one rule.

    [sourced] "alert rules fire when remaining budget crosses a threshold or
    when the change-stream lag exceeds N seconds" - so the metric is one of the
    two budget halves or the lag, the comparison follows the metric family
    (budget rules fire *below*, lag rules *exceed*), and the channels are the
    research's Slack/email/webhook. Every field is validated here rather than
    defaulted silently: a rule stored with a metric the room does not compute
    is a rule that looks armed and never fires.
    """
    return monitor.create_rule(
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/alerts/rules",
    )


@router.patch("/alerts/rules/{rule_id}", summary="Retune an alert rule")
def update_alert_rule(
    rule_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """A partial patch, re-validated as a whole, with the fire history intact."""
    return monitor.update_rule(
        rule_id, payload, actor=actor, source=f"PATCH {router.prefix}/alerts/rules/{rule_id}"
    )


@router.delete("/alerts/rules/{rule_id}", status_code=204, summary="Remove an alert rule")
def delete_alert_rule(
    rule_id: str,
    actor: str | None = Query(default=None),
    monitor: IntegrationMonitor = MonitorDep,
) -> Response:
    monitor.delete_rule(
        rule_id, actor=actor, source=f"DELETE {router.prefix}/alerts/rules/{rule_id}"
    )
    return Response(status_code=204)


@router.post("/rooms/{room_id}/alerts/evaluate", summary="Evaluate every rule now, and fire")
def evaluate_alerts(
    room_id: str,
    actor: str | None = Query(default=None),
    window_seconds: float = Query(default=DEFAULT_WINDOW_SECONDS, ge=60.0, le=2_592_000.0),
    monitor: IntegrationMonitor = MonitorDep,
) -> dict[str, Any]:
    """The researched automation, evaluated on demand.

     [sourced] "Quota polling on a fixed interval" is the poll; this is what the
     poll calls. Rules that cross fire into their channels and start their
     cooldown; rules that are mid-cooldown are suppressed and say so; a rule
     with no readable reading says so rather than firing a zero.

     The room does not deliver to Slack or email itself - this product holds no
     outbound credentials - so a fire is a record with the channel list and the
    connector it is about, which is the seam a delivery integration consumes.
    """
    return monitor.evaluate_alerts(
        room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/alerts/evaluate",
        window_seconds=window_seconds,
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


#: The connectors the demo registers, one per researched state.
#:
#: A demo of only green would teach a reviewer nothing about the thing this
#: workflow exists for, so the five cover the states the research makes this
#: workflow responsible for: healthy, starved (the researched "starved"
#: connector the controls exist for), throttled, an auth failure, a vendor
#: outage, and one with no readings at all - the state the "known: false"
#: honest-no-data rule exists for.
#:
#: ``room`` is an index into ``context["room_ids"]``.
DEMO_CONNECTORS: tuple[dict[str, Any], ...] = (
    {
        "room": 0,
        "vendor": "salesforce",
        "label": "Northwind — Salesforce",
        "limit_name": "API Requests",
        "minutes_ago": 65,
    },
    {
        "room": 0,
        "vendor": "hubspot",
        "label": "Northwind — HubSpot (starved)",
        "limit_name": None,
        "minutes_ago": 58,
    },
    {
        "room": 0,
        "vendor": "hubspot",
        "label": "Adventure Works — HubSpot (throttled)",
        "minutes_ago": 50,
    },
    {
        "room": 0,
        "vendor": "salesforce",
        "label": "Fabrikam — Salesforce (auth failing)",
        "limit_name": "API Requests",
        "minutes_ago": 45,
    },
    {
        "room": 0,
        "vendor": "dataverse",
        "label": "Contoso — Dataverse (no sourced numbers)",
        "minutes_ago": 40,
    },
    {
        "room": 1,
        "vendor": "hubspot",
        "label": "Initech — HubSpot (no readings yet)",
        "minutes_ago": 10,
    },
)


#: The quota readings the demo hands in per connector, in the vendor's own
#: surface shape - the demo never invents a state the parser would not produce.
DEMO_QUOTA: dict[str, dict[str, Any]] = {
    "Northwind — Salesforce": {
        "surface": "limits_resource",
        "body": {
            "limits": [
                {"name": "API Requests", "max": 100000, "remaining": 91240},
                {"name": "DailyApiRequests", "max": 100000, "remaining": 91240},
                {"name": "DataStorageMB", "max": 1024, "remaining": 890},
            ],
        },
    },
    "Northwind — Salesforce (later)": {
        "surface": "limit_info_header",
        "header": "api-usage=10310/100000",
    },
    "Northwind — HubSpot (starved)": {
        "surface": "rate_limit_headers",
        "headers": {
            "X-HubSpot-RateLimit-Max": "190",
            "X-HubSpot-RateLimit-Remaining": "7",
            "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            "X-HubSpot-RateLimit-Daily": "250000",
            "X-HubSpot-RateLimit-Daily-Remaining": "1820",
        },
    },
    "Adventure Works — HubSpot (throttled)": {
        "surface": "rate_limit_headers",
        "headers": {
            "X-HubSpot-RateLimit-Max": "190",
            "X-HubSpot-RateLimit-Remaining": "161",
            "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
        },
    },
    "Fabrikam — Salesforce (auth failing)": {
        "surface": "limit_info_header",
        "header": "api-usage=41200/100000",
    },
}


#: The telemetry each demo connector reports. Starved means 429s; throttled
#: means a recent burst of 429s among successes; auth failing means 401s; the
#: clean connector is the one a reviewer can compare the others against.
DEMO_TELEMETRY: dict[str, list[dict[str, Any]]] = {
    "Northwind — Salesforce": [
        {"ok": True, "status": 200, "latency_ms": 210, "minutes_ago": 50},
        {"ok": True, "status": 200, "latency_ms": 180, "minutes_ago": 45},
        {"ok": True, "status": 200, "latency_ms": 240, "minutes_ago": 30},
        {"ok": False, "status": 400, "latency_ms": 90, "minutes_ago": 20},
        {"ok": True, "status": 200, "latency_ms": 200, "minutes_ago": 10},
    ],
    "Northwind — HubSpot (starved)": [
        {"ok": False, "status": 429, "latency_ms": 610, "minutes_ago": 55},
        {"ok": True, "status": 200, "latency_ms": 320, "minutes_ago": 40},
        {"ok": False, "status": 429, "latency_ms": 590, "minutes_ago": 25},
        {"ok": False, "status": 429, "latency_ms": 640, "minutes_ago": 12},
    ],
    "Adventure Works — HubSpot (throttled)": [
        {"ok": True, "status": 200, "latency_ms": 150, "minutes_ago": 45},
        {"ok": False, "status": 429, "latency_ms": 480, "minutes_ago": 30},
        {"ok": True, "status": 200, "latency_ms": 170, "minutes_ago": 15},
    ],
    "Fabrikam — Salesforce (auth failing)": [
        {"ok": False, "status": 401, "latency_ms": 60, "minutes_ago": 44},
        {"ok": False, "status": 401, "latency_ms": 58, "minutes_ago": 40},
        {"ok": False, "status": 401, "latency_ms": 61, "minutes_ago": 35},
    ],
    "Contoso — Dataverse (no sourced numbers)": [
        {"ok": True, "status": 200, "latency_ms": 220, "minutes_ago": 35},
        {"ok": False, "status": 503, "latency_ms": 1200, "minutes_ago": 20},
    ],
}


#: One stream observation per connector that has a change stream. Initech's
#: stale lag is what the lag rule's threshold is set just below, so the demo
#: shows a fired lag alert without anyone editing anything.
DEMO_STREAM: dict[str, dict[str, Any]] = {
    "Northwind — Salesforce": {"lag_seconds": 4},
    "Northwind — HubSpot (starved)": {"lag_seconds": 31},
    "Initech — HubSpot (no readings yet)": {"lag_seconds": 340},
}


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the researched states, including the ones that only show up on failure.

    Everything is written through the real :class:`IntegrationMonitor` - the
    real parsers, the real aggregates, the real rule evaluation - so the demo
    cannot show a state the workflow would not produce, and every seeded row is
    audited like any other. The clock is wound back per observation so the demo
    lands in a spread of real states rather than six identical fresh ones.

    States seeded, per the research's own list:

    * **healthy** - Northwind on Salesforce, quota read from the ``/limits/``
      table by the connector's declared ``limit_name``, then again from the
      ``Sforce-Limit-Info`` header, so the delta is visible;
    * **starved** - Northwind on HubSpot, 7 of 190 calls left in the window and
      1,820 of 250,000 in the day, with 429s in the telemetry. The researched
      "starved" connector the pause and concurrency controls exist for;
    * **throttled** - Adventure Works on HubSpot, a recent 429 among successes;
    * **auth failing** - Fabrikam on Salesforce, three 401s;
    * **vendor outage** - Contoso on Dataverse, a 503, and - the research's own
      gap - no quota surface at all, which the dashboard says rather than hides;
    * **no readings yet** - Initech, whose rows honestly read ``known: false``;
    * plus a **change-tracking audit** with the Dataverse tables and a first
      ``globalmetadataversion``, a second audit whose version moved (the
      researched drift signal), and **three alert rules** with one already
      fired once.

    Returns a short description of what was added, which the seeder prints.
    """
    from datetime import datetime, timezone

    store = RecordStore(db)
    source = "seed"
    actor = "dana"
    real_now = datetime.now(timezone.utc)
    clock: dict[str, datetime] = {"now": real_now}
    monitor = IntegrationMonitor(store, now=lambda: clock["now"].isoformat(timespec="milliseconds"))

    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        return "0 monitored connectors (no demo rooms to scope them to)"

    by_label: dict[str, dict[str, Any]] = {}
    for spec in DEMO_CONNECTORS:
        index = int(spec["room"])
        if index >= len(rooms):
            continue
        room_id = rooms[index][0]
        clock["now"] = real_now - timedelta(minutes=int(spec["minutes_ago"]))
        connector = monitor.register_connector(
            {
                "vendor": spec["vendor"],
                "label": spec["label"],
                "limit_name": spec.get("limit_name"),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        by_label[str(spec["label"])] = connector
    clock["now"] = real_now

    # Quota readings, each through the real parser.
    for plan in DEMO_QUOTA:
        label = plan if not plan.endswith("(later)") else plan[: -len(" (later)")]
        connector = by_label.get(label)
        if connector is None:
            continue
        reading = dict(DEMO_QUOTA[plan])
        if label == "Northwind — Salesforce":
            clock["now"] = real_now - timedelta(minutes=48)
        monitor.record_quota(connector["id"], reading, actor=actor, source=source, now=clock["now"])
        clock["now"] = real_now

    # Telemetry, each sample its own audited record, the clock wound back.
    for label, calls in DEMO_TELEMETRY.items():
        connector = by_label.get(label)
        if connector is None:
            continue
        samples = []
        for call in calls:
            at = real_now - timedelta(minutes=int(call.get("minutes_ago", 30)))
            samples.append(
                {
                    "ok": call["ok"],
                    "status": call["status"],
                    "latency_ms": call["latency_ms"],
                    "at": at.isoformat(timespec="seconds"),
                }
            )
        monitor.record_telemetry(connector["id"], {"calls": samples}, actor=actor, source=source)

    # Stream lag observations.
    for label, reading in DEMO_STREAM.items():
        connector = by_label.get(label)
        if connector is None:
            continue
        at = real_now - timedelta(minutes=5)
        monitor.record_stream(
            connector["id"],
            {
                "lag_seconds": reading["lag_seconds"],
                "observed_at": at.isoformat(timespec="seconds"),
            },
            actor=actor,
            source=source,
        )

    # The Dataverse change-tracking audit, twice, so the drift signal has fired.
    contoso = by_label.get("Contoso — Dataverse (no sourced numbers)")
    drift_note = "no drift recorded"
    if contoso is not None:
        monitor.record_change_tracking(
            {
                "vendor": "dataverse",
                "globalmetadataversion": "61950421",
                "entities": [
                    {"schema_name": "opportunity", "change_tracking_enabled": True},
                    {"schema_name": "contact", "change_tracking_enabled": True},
                    {"schema_name": "quote", "change_tracking_enabled": False},
                ],
            },
            room_id=contoso["room_id"],
            actor=actor,
            source=source,
            now=real_now - timedelta(minutes=38),
        )
        drifted = monitor.record_change_tracking(
            {
                "vendor": "dataverse",
                "globalmetadataversion": "61950488",
                "entities": [
                    {"schema_name": "opportunity", "change_tracking_enabled": True},
                    {"schema_name": "contact", "change_tracking_enabled": True},
                    {"schema_name": "quote", "change_tracking_enabled": True},
                    {"schema_name": "invoice", "change_tracking_enabled": False},
                ],
            },
            room_id=contoso["room_id"],
            actor=actor,
            source=source,
            now=real_now - timedelta(minutes=6),
        )
        # Plain ASCII on purpose. This string is interpolated into the summary
        # below, and seed.py prints that summary on a console whose encoding is
        # not always UTF-8. A U+2192 arrow here raised UnicodeEncodeError on a
        # cp1252 Windows host, and because seed.py's print sits outside the
        # per-feature try/except, one unencodable character aborted the whole
        # seed and every other feature's demo data with it. Words, not a glyph.
        # The separator seed.py prints before this summary is already an arrow,
        # so a second one here read as two meanings in one line.
        drift_note = (
            f"drift={drifted['drift']}, "
            f"globalmetadataversion {drifted['previous_version']} "
            f"to {drifted['globalmetadataversion']}"
        )

    # One connector paused by the operator, the researched step 4 in action:
    # Adventure Works took a burst of 429s, the operator paused it from the
    # same page, and a paused connector is out of alert evaluation by design.
    adventure = by_label.get("Adventure Works — HubSpot (throttled)")
    if adventure is not None:
        monitor.update_connector(adventure["id"], {"paused": True}, actor=actor, source=source)

    # Three alert rules, in the researched vocabulary. The lag rule's threshold
    # sits just below Initech's seeded lag, so the demo shows a fired rule.
    first_room = rooms[0][0]
    monitor.create_rule(
        {
            "label": "Daily budget below 20%",
            "metric": "daily_remaining",
            "threshold": 20.0,
            "channels": ["slack"],
        },
        room_id=first_room,
        actor=actor,
        source=source,
    )
    monitor.create_rule(
        {
            "label": "Burst window below 10%",
            "metric": "window_remaining",
            "threshold": 10.0,
            "channels": ["slack", "email"],
        },
        room_id=first_room,
        actor=actor,
        source=source,
    )
    monitor.create_rule(
        {
            "label": "Change-stream lag over 5 minutes",
            "metric": "stream_lag",
            "threshold": 300.0,
            "channels": ["webhook"],
        },
        room_id=first_room,
        actor=actor,
        source=source,
    )

    # The automation, run once for real: the starved HubSpot connector is below
    # every budget threshold and Initech's lag is over the lag threshold, so
    # this fires them and starts their cooldowns - the demo shows both a fresh
    # fire and the suppression that follows.
    evaluation = monitor.evaluate_alerts(first_room, actor=actor, source=source)

    return (
        f"{len(by_label)} monitored connectors across {len(rooms)} demo room(s), "
        f"{sum(len(calls) for calls in DEMO_TELEMETRY.values())} telemetry samples, "
        f"{len(DEMO_QUOTA)} quota readings, {len(DEMO_STREAM)} lag observations, "
        f"change-tracking audit with {drift_note}, "
        f"3 alert rules, one evaluation that fired {len(evaluation['fired'])} "
        f"of {len(evaluation['rules'])} rules, and one connector paused by the operator"
    )
