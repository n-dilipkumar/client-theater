"""WF-020: extract DSR viewing sessions (dwell time + geography) for BI.

The HTTP surface and the demo data for the researched workflow. The rules live in
:mod:`dsr.features.wf020_reporting_domain`, which keeps them testable without an
HTTP client; this module is the route table, the error mapping, and the seed hook
- the three things that used to be edits to shared files.

What this feature is
--------------------
A landing zone and a BI read surface for the Seismic Reporting v2
``digitalSalesRoomViewingSessions`` extraction, plus the machine-readable contract
an external ETL job is generated from.

The researched user flow has the pull running "from an ETL job" and the landing
happening in "a data warehouse / lake", so this service does not call
``api.seismic.com``. It publishes the request a job must make
(``GET /contract``), receives the rows that job pulled (``POST /extract``), merges
them on the session key, and answers the questions the flow says the warehouse is
for - dwell time, geography, internal-versus-external, and per-user engagement.
The full reasoning is in the domain module's docstring, under "Design
inferences" 1 and 2.

Every researched rule that survives into a response is visible in one of four
places: ``/contract`` publishes the documented fields, parameters, headers and
verbatim evidence; ``/window`` publishes the SLA-paged incremental window the next
job should pull; the run records carry the lineage of which window produced which
rows; and the ``/summary``, ``/dwell``, ``/geography`` and ``/viewers`` rollups
publish the computations the flow says happen downstream in BI.

Routes
------
============================== ====== ==============================================
Path                           Method What it is
============================== ====== ==============================================
``/contract``                  GET    the extraction contract, with its evidence
``/window``                    GET    the window the next extraction should pull
``/sweep``                     POST   the scheduled-job entry point (409 if early)
``/extract``                   POST   land a batch of viewing-session rows
``/runs``                      GET    the extraction runs
``/runs/{run_id}``             GET    one run
``/rooms``                     GET    the landed room inventory
``/rooms``                     POST   land a batch of room inventory rows
``/rooms/{room_id}/sessions``  GET    sessions for one core room
``/rooms/{room_id}/dwell``     GET    dwell for one core room
``/sessions``                  GET    the landed sessions
``/export``                    GET    the same rows as JSON or CSV (``Accept``)
``/summary``                   GET    headline numbers, sessions apart from visitors
``/dwell``                     GET    dwell by room or by viewer
``/geography``                 GET    country / state / city rollup
``/viewers``                   GET    per-user engagement
============================== ====== ==============================================

Notes on the hard rules
----------------------
* Every write takes its ``source`` from this module, built from ``router.prefix``,
  and ``backend/tests/test_wf020.py`` asserts that every source recorded names a
  route the app actually serves. A domain function that hard-coded a path here
  would be the defect the port brief names by name.
* Nothing is imported from ``dsr.api``; the dependency comes from ``dsr.deps``.
* No collection is a migration and no field is a typed column. Every attribute of
  a landed session is arbitrary JSON in ``records.data``, which is why a team can
  add one without coordinating with anyone - and why an undocumented field in an
  API row lands under ``extra`` instead of being dropped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from dsr.deps import StoreDep
from dsr.features import wf020_reporting_domain as reporting
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-020-extract-dsr-viewing-sessions-dwell-tim",
    "ticket": "WF-020",
    "name": "Extract DSR viewing sessions for BI",
    "description": (
        "Land tab-level DSR viewing sessions from the Reporting v2 extraction, "
        "merge them on the incremental modifiedAt watermark, and answer the "
        "dwell-time, geography, internal-versus-external and per-user questions "
        "the warehouse is built for."
    ),
    "nav": [{"id": "viewing-sessions", "label": "Viewing sessions"}],
}

router = APIRouter(prefix="/api/wf-020", tags=["wf-020"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# These are this feature's own types, mapped to explicit status codes rather than
# left to fall through to the core ``AuditError`` handler, which picks 409-vs-400
# by searching a message for the words "conflict" or "exists". That makes a status
# code a function of prose: rewrite a message and the HTTP contract changes under
# a client that branched on it. FastAPI only accepts handlers on the app object,
# so they are exported and the host attaches them.


def _window_error(request: Request, exc: reporting.WindowError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": "invalid_window", "detail": str(exc)})


def _payload_error(request: Request, exc: reporting.PayloadError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": "invalid_payload", "detail": str(exc)})


def _sweep_not_due(request: Request, exc: reporting.SweepNotDue) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "sweep_not_due", "detail": str(exc)})


def _room_unknown(request: Request, exc: reporting.RoomUnknown) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    reporting.WindowError: _window_error,
    reporting.PayloadError: _payload_error,
    reporting.SweepNotDue: _sweep_not_due,
    reporting.RoomUnknown: _room_unknown,
}

#: The two resources the Reporting v2 extraction covers.
RESOURCES = ("viewing_sessions", "digital_sales_rooms")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hours(raw: float | None) -> float:
    return float(reporting.REFRESH_SLA_HOURS if raw is None else raw)


def _resource(raw: str) -> str:
    if raw not in RESOURCES:
        raise reporting.PayloadError(f"unknown resource {raw!r}; expected one of {list(RESOURCES)}")
    return raw


def _bound(store: RecordStore, room_id: str | None) -> list[str] | None:
    """Seismic room ids in scope: every room, or the ones bound to a core room.

    The binding is read at request time rather than copied onto the session, so
    binding a Seismic room to a core room *after* its sessions landed makes those
    sessions visible immediately, with no re-extraction.
    """
    if room_id is None:
        return None
    return reporting.bound_dsr_rooms(store, room_id)


def _sessions(
    store: RecordStore,
    *,
    room_id: str | None,
    limit: int,
    include_sensitive: bool,
) -> list[dict[str, Any]]:
    return reporting.sessions_for(
        store,
        room_ids=_bound(store, room_id),
        limit=limit,
        include_sensitive=include_sensitive,
    )


def _run_or_404(store: RecordStore, run_id: str) -> dict[str, Any]:
    run = store.get(run_id)
    if run is None or run.get("collection") != reporting.RUNS:
        raise HTTPException(status_code=404, detail=f"extraction run {run_id} not found")
    return run


def _high_frequency(store: RecordStore, now: datetime) -> bool:
    """True when an extraction landed less than one SLA ago.

    Reported, never refused. The research says the APIs are "not designed to be
    used in high-frequency, interactive use cases", which is a statement about how
    fresh the data is, not a prohibition, so the annotation is what stops a caller
    reading a number as live.
    """
    last = reporting.latest_landed(store, resource="viewing_sessions")
    landed = (last or {}).get("data", {}).get("landed_at")
    if not landed:
        return False
    moment = reporting.parse_instant(landed, field="landed_at", require_offset=False)
    return (
        moment is not None and (now - moment).total_seconds() < reporting.REFRESH_SLA_HOURS * 3600
    )


# --------------------------------------------------------------------------- #
# The extraction contract
# --------------------------------------------------------------------------- #


@router.get("/contract", summary="The Reporting v2 extraction contract")
def extraction_contract() -> dict[str, Any]:
    """The machine-readable contract an ETL job is generated from.

    Published rather than embedded because it is a projection of the researched
    sources: the endpoints, the bearer header, the ``Accept`` values, the
    documented query parameters and their semantics, the documented response
    fields, the camelCase-to-snake_case mapping used on the way in, the 24 hour
    refresh SLA, the tab-level row granularity, the room join, and the star shape.

    The ``evidence`` block quotes the research verbatim, so a reviewer can check
    the contract against its sources without opening a source file.
    """
    return reporting.contract()


# --------------------------------------------------------------------------- #
# The incremental window and the nightly sweep
# --------------------------------------------------------------------------- #


@router.get("/window", summary="The window the next extraction should pull")
def next_window(
    resource: str = Query(default="viewing_sessions"),
    hours: float | None = Query(
        default=None,
        gt=0,
        description="Page span in hours. Defaults to the documented 24h refresh SLA.",
    ),
    limit: int | None = Query(
        default=None, ge=1, description="The documented `limit` page size to send with the pull."
    ),
    fmt: str = Query(
        default=reporting.ACCEPT_JSON,
        description="Accept value the job should send. One of the two documented ones.",
    ),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The next ``modifiedAt`` page, derived from the runs rather than stored twice.

    A first extraction leaves the lower bound open, because the endpoints are
    built to allow "large portions of data" to be extracted; the upper bound is
    always closed at the current instant, so the watermark can advance from the
    very first run instead of waiting for a second one to notice.
    """
    return reporting.next_window(
        store,
        now=_now(),
        resource=_resource(resource),
        hours=_hours(hours),
        limit=limit,
        fmt=reporting.negotiate_format(None, fmt),
    )


@router.get("/runs", summary="Extraction runs")
def list_runs(
    resource: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every run, newest first, with its window, counters and watermark."""
    runs = reporting.list_runs(store, resource=resource, limit=limit)
    return {"count": len(runs), "runs": runs}


@router.get("/runs/{run_id}", summary="One extraction run")
def get_run(run_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    return _run_or_404(store, run_id)


@router.post("/sweep", summary="The scheduled nightly sweep")
def sweep(
    resource: str = Query(default="viewing_sessions"),
    hours: float | None = Query(default=None, gt=0),
    limit: int | None = Query(default=None, ge=1),
    fmt: str = Query(default=reporting.ACCEPT_JSON),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Open the next extraction, or refuse while the SLA has not elapsed.

    The research makes the sweep the intended job: the data is "updated no less
    than every 24 hours" and the APIs are "not designed to be used in
    high-frequency, interactive use cases". So the frequency here is the SLA rather
    than whatever a caller feels like, and calling this twice inside one SLA
    window is a 409 carrying the remaining hours rather than a second window - two
    overlapping pages of the same incremental filter would double-count the day.

    The run it opens stays ``requested`` until the rows land, which is what makes a
    job that failed between the pull and the landing visible in ``/runs`` instead
    of being silently retried forever.
    """
    now = _now()
    plan = reporting.next_window(
        store,
        now=now,
        resource=_resource(resource),
        hours=_hours(hours),
        limit=limit,
        fmt=reporting.negotiate_format(None, fmt),
    )
    state = plan["sweep"]
    if not state["due"]:
        raise reporting.SweepNotDue(
            f"{state['detail']}; {state['due_in_hours']:g}h remaining (reason: {state['reason']})"
        )
    run = reporting.open_run(
        store,
        window=reporting.build_window(
            {"kind": "modified", "start": plan["window"]["start"], "end": plan["window"]["end"]},
            resource=plan["resource"],
        ),
        resource=plan["resource"],
        run_kind="sweep",
        fmt=plan["format"],
        limit=limit,
        started=now,
        actor=actor,
        source=f"POST {router.prefix}/sweep",
    )
    return {"due": True, "run": run, "window": plan["window"], "query": plan["query"]}


# --------------------------------------------------------------------------- #
# Landing
# --------------------------------------------------------------------------- #


@router.post("/extract", status_code=201, summary="Land a batch of viewing sessions")
def extract(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Land the rows an ETL job pulled, merging on the session key.

    The window may be given either way round: as the API's own parameter names
    (``modifiedAtStartTime`` and friends) or as ``{"kind", "start", "end"}``,
    because the job that pulls already speaks the first vocabulary and a caller
    reading ``/window`` gets the second. Omitting it entirely means "everything",
    which is what a first backfill is.

    Every extraction records a run, whether or not one was opened for it. That is
    the lineage - which window produced which rows - and it is also what advances
    the watermark, so an extraction that recorded nothing would be invisible to
    the next sweep and would be pulled a second time. Pass ``run_id`` to land into
    a run ``POST /sweep`` opened; the window on the request must then match the
    one the run was opened for, because a lineage that claims one window while the
    rows say another is the one state a warehouse cannot recover from.

    Rows are never dropped for being odd. A reversed timestamp, a duration that
    disagrees with the timestamps, a missing city, an unknown room: each is landed
    and flagged, and the run's counters say how many. Only a row that is not an
    object at all is rejected. A warehouse that silently drops rows is worse than
    one that says which rows it doubts.
    """
    now = _now()
    window = reporting.build_window(payload, resource="viewing_sessions")
    rows = reporting.require_rows(payload.get("rows", payload.get("sessions")), what="session")
    fmt = reporting.negotiate_format(None, payload.get("format"))
    run_id = str(payload.get("run_id") or "").strip() or None
    # Read before this request writes anything, or the run this request is about
    # to land would count as its own predecessor and every extraction would
    # report itself as high frequency.
    high_frequency = _high_frequency(store, now)

    run = None
    if run_id:
        run = _run_or_404(store, run_id)
        opened = reporting.window_from_run(run)
        if opened.as_query() != window.as_query():
            raise reporting.WindowError(
                f"run {run_id} was opened for {opened.as_query()} but this batch "
                f"declares {window.as_query()}; land it into its own run"
            )
        window = opened
    else:
        run = reporting.open_run(
            store,
            window=window,
            resource="viewing_sessions",
            run_kind="extract",
            fmt=fmt,
            limit=None,
            started=now,
            actor=actor,
            source=f"POST {router.prefix}/extract",
        )

    counters = reporting.land_sessions(
        store,
        window,
        rows,
        redact_ip=bool(payload.get("redact_ip")),
        actor=actor,
        source=f"POST {router.prefix}/extract",
    )
    reporting.close_run(
        store,
        run["id"],
        counters,
        finished=now,
        actor=actor,
        source=f"POST {router.prefix}/extract",
    )

    return {
        "window": window.to_dict(),
        "format": fmt,
        "counters": counters,
        "run": store.get(run["id"]),
        "watermark": reporting.watermark(store, resource="viewing_sessions"),
        "high_frequency": high_frequency,
        "sla_hours": reporting.REFRESH_SLA_HOURS,
    }


@router.get("/rooms", summary="The landed room inventory")
def list_rooms(
    limit: int = Query(default=200, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The ``digitalSalesRooms`` rows landed so far, with their core-room binding.

    A session joins to a room on ``digital_sales_room_id``. Binding that id to a
    core room is what makes the room-scoped paths answer, and it is a record
    rather than a column, so an unbound room is simply not visible from a core
    room - visible, not an error.
    """
    rows = [
        {**(record.get("data") or {}), "id": record["id"], "updated_at": record.get("updated_at")}
        for record in store.list(
            reporting.ROOMS, limit=limit, order_by="created_at", descending=False
        )
    ]
    return {
        "count": len(rows),
        "rooms": rows,
        "bound": sum(1 for row in rows if row.get("bound_room_id")),
        "note": (
            "A session joins to a room on digital_sales_room_id. Binding that id to a "
            "core room is what makes the room-scoped paths answer."
        ),
    }


@router.post("/rooms", status_code=201, summary="Land a batch of room inventory rows")
def land_rooms(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Land the ``digitalSalesRooms`` rows, merging on the room key.

    A separate route from ``/extract`` because it is a separate pull from a
    separate endpoint: a failed inventory pull then shows on its own counters
    rather than hiding inside a session run. A row may carry ``bound_room_id`` to
    bind it to a core room as it lands.
    """
    window = reporting.build_window(payload, resource="digital_sales_rooms")
    rows = reporting.require_rows(payload.get("rows", payload.get("rooms")), what="room inventory")
    counters = reporting.land_rooms(
        store, window, rows, actor=actor, source=f"POST {router.prefix}/rooms"
    )
    return {
        "window": window.to_dict(),
        "counters": counters,
        "watermark": reporting.watermark(store, resource="digital_sales_rooms"),
    }


# --------------------------------------------------------------------------- #
# Reads: the BI surface
# --------------------------------------------------------------------------- #


@router.get("/sessions", summary="The landed viewing sessions")
def list_sessions(
    room_id: str | None = Query(default=None, description="A core room, through its binding."),
    limit: int = Query(default=200, ge=1, le=1000),
    include_sensitive: bool = Query(
        default=False,
        description=(
            "Include the IP address, coordinates and engagement email. Off by default: "
            "the sources document no consent, anonymisation or retention policy for "
            "these, so a list endpoint does not volunteer them."
        ),
    ),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The landed rows, newest first, with the room join resolved on read.

    Resolving the join here rather than at landing time is deliberate: a room
    renamed in the inventory must not leave a stale label frozen inside a session
    row that landed last month.
    """
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=include_sensitive)
    return {
        "count": len(rows),
        "sessions": rows,
        "room_id": room_id,
        "bound_dsr_rooms": _bound(store, room_id) if room_id else None,
        "sensitive_fields_included": include_sensitive,
    }


@router.get("/export", summary="Export the landed sessions as JSON or CSV")
def export(
    request: Request,
    room_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=1000),
    format: str | None = Query(  # noqa: A002 - the parameter is named in the contract
        default=None, description="`csv` or `json`. The Accept header is the documented route."
    ),
    include_sensitive: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> Any:
    """The flat-file load the ``Accept: text/csv`` alternative buys.

    The header is the documented mechanism; ``?format=`` exists because a browser
    link cannot set one, which is the only reason it is here. The CSV columns are
    the documented field names, in the order ``/contract`` publishes them, so a
    lake load and the published dictionary agree.
    """
    resolved = reporting.negotiate_format(request.headers.get("accept"), format)
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=include_sensitive)
    if resolved == reporting.ACCEPT_CSV:
        return PlainTextResponse(
            reporting.to_csv(rows),
            media_type=reporting.ACCEPT_CSV,
            headers={"Content-Disposition": 'attachment; filename="dsr-viewing-sessions.csv"'},
        )
    return {"count": len(rows), "format": resolved, "sessions": rows}


@router.get("/summary", summary="Headline extraction numbers")
def summary(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Sessions and visitors, kept apart, plus the internal/external split.

    "Each row represents a single session in a DSR link from a single user in a
    single browser tab", so ``sessions`` is a row count and ``visitors`` is a
    distinct-viewer count. They are almost never equal, and reporting the first as
    the second is the mistake this vocabulary exists to prevent.
    """
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=False)
    return {
        "room_id": room_id,
        "summary": reporting.summarise(rows),
        "watermark": reporting.watermark(store, resource="viewing_sessions"),
    }


@router.get("/dwell", summary="Dwell time by room or by viewer")
def dwell(
    group_by: str = Query(default="room", pattern="^(room|viewer)$"),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Dwell totals, grouped by room or by viewer.

    A total is tab-seconds, because a row is one tab session; ``unit`` and
    ``grain`` say so in the response, so the number cannot be quietly relabelled
    "visit time" further downstream.
    """
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=False)
    return {
        "group_by": group_by,
        "unit": "seconds",
        "grain": "one row is one session in one browser tab, so a total is tab-seconds",
        "room_id": room_id,
        "count": len(rows),
        "dwell": reporting.dwell_rollup(rows, group_by=group_by),
    }


@router.get("/geography", summary="Where the sessions came from")
def geography(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Country / state / city rollup with a centroid.

    The centroid is the mean of whatever coordinates arrived. No geocoding service
    is invented here because none is researched, and ``rows_with_coordinates`` says
    how much of the rollup the centroid actually covers.
    """
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=False)
    return {
        "room_id": room_id,
        "count": len(rows),
        "countries": len({row.get("country") for row in rows if row.get("country")}),
        "geography": reporting.geography_rollup(rows),
        "withheld": list(reporting.SENSITIVE_SESSION_FIELDS),
    }


@router.get("/viewers", summary="Per-user engagement")
def viewers(
    room_id: str | None = Query(default=None),
    internal: bool | None = Query(
        default=None, description="Filter to internal (seller-side) or external (buyer) viewers."
    ),
    limit: int = Query(default=1000, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Per-viewer sessions, dwell, rooms and first/last seen.

    Internal-versus-external is a dimension rather than a filter in the data: a
    seller previewing a room and a buyer reading it are the same shape of event.
    Collapsing them is how a dashboard reports a team's own time spent as buyer
    engagement, so both flags are carried per viewer and the split is on the
    summary too.

    This is the one read surface that includes the engagement email, because a
    per-user engagement table without an identity is not usable. The aggregate
    endpoints withhold it.
    """
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=True)
    if internal is not None:
        rows = [row for row in rows if (row.get("is_engagement_user_internal") is True) is internal]
    rollup = reporting.viewer_rollup(rows)
    return {
        "room_id": room_id,
        "internal_filter": internal,
        "count": len(rollup),
        "viewers": rollup,
    }


@router.get("/rooms/{room_id}/sessions", summary="Sessions for one core room")
def room_sessions(
    room_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    include_sensitive: bool = Query(default=False),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Room-scoped sessions, resolved through the inventory binding."""
    bound = _bound(store, room_id)
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=include_sensitive)
    return {
        "room_id": room_id,
        "bound": bool(bound),
        "bound_dsr_rooms": bound,
        "count": len(rows),
        "sessions": rows,
    }


@router.get("/rooms/{room_id}/dwell", summary="Dwell for one core room")
def room_dwell(
    room_id: str,
    limit: int = Query(default=1000, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Room-scoped dwell, with the same unit caveat the global rollup carries."""
    rows = _sessions(store, room_id=room_id, limit=limit, include_sensitive=False)
    return {
        "room_id": room_id,
        "bound_dsr_rooms": _bound(store, room_id),
        "unit": "seconds",
        "grain": "one row is one session in one browser tab, so a total is tab-seconds",
        "summary": reporting.summarise(rows),
        "by_viewer": reporting.dwell_rollup(rows, group_by="viewer"),
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# Seeded states, not just the happy path. An ETL demo containing only clean rows
# teaches a reviewer nothing, so this seeds:
#
# * a first unbounded backfill and a nightly page after it, so the watermark and
#   the "pages behind" arithmetic are both visible;
# * a **run that was opened and never landed** - the state a job that failed
#   between the pull and the landing leaves behind, and the one a sweep must
#   refuse to open a second window on top of;
# * a **re-landed row the vendor corrected** (one update) beside a **re-landed row
#   that came back identical** (one unchanged), so the merge is visible doing both
#   of the things it does;
# * a 1-second session, which is the value in the API's own documented example;
# * an internal (seller) view beside the buyer views, so the internal/external
#   split is not vacuous;
# * an **unattributed** session with no userId and no email, and a session whose
#   identity falls back to the email alone;
# * a session for a room that is **not in the inventory**, so the join is visibly
#   unresolved rather than silently absent;
# * a session whose reported duration **disagrees** with its own timestamps, and a
#   session with no geography at all, and one with a naive timestamp;
# * a room whose ``userModifiedAt`` differs from ``modifiedAt`` - the template
#   refresh the API reports as a user edit;
# * two rooms with **no sessions at all**, because a room nobody opened is as much
#   a fact as one that was;
# * a row carrying an **undocumented field**, which lands under ``extra`` and shows
#   that a field nobody declared still reaches the warehouse.
#
# The seeder's ``rng`` keeps the output reproducible.

#: A stable, obviously fake Seismic room identifier prefix for the demo.
_DSR_ROOM_PREFIX = "dsr-room"

#: (email, userId, internal, city, state, country, latitude, longitude)
#: The fourth viewer has no ``userId`` on purpose: identity then falls back to the
#: engagement email, which is the fallback the researched field list implies.
_VIEWERS = (
    ("a.buyer@northwind.example", "u-nw-01", False, "Sydney", "NSW", "AU", -33.8688, 151.2093),
    ("b.buyer@northwind.example", "u-nw-02", False, "Seattle", "WA", "US", 47.6062, -122.3321),
    ("procurement@contoso.example", "u-ch-01", False, "Dublin", "Leinster", "IE", 53.3498, -6.2603),
    ("ops@fabrikam.example", None, False, "Berlin", "Berlin", "DE", 52.52, 13.405),
    ("dana@seller.example", "u-in-01", True, "Sydney", "NSW", "AU", -33.8688, 151.2093),
)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _viewer_row(index: int) -> dict[str, Any]:
    email, user_id, internal, city, state_name, country, lat, lon = _VIEWERS[index]
    row: dict[str, Any] = {
        "engagementUserEmail": email,
        "isEngagementUserInternal": internal,
        "city": city,
        "state": state_name,
        "country": country,
        "ipAddress": "203.0.113.7",
        "geolocationLatitude": lat,
        "geoLocationLongitude": lon,
    }
    if user_id:
        row["userId"] = user_id
    return row


def seed(db, context: dict[str, Any]) -> str:
    """Seed the room inventory, the extraction runs, and the sessions they land."""
    room_ids: list[tuple[str, str]] = context["room_ids"]
    now: datetime = context["now"]
    rng = context["rng"]

    if not room_ids:
        return None

    # The seeder hands the hook an AuditedDatabase, not a store. RecordStore is a
    # facade over exactly that object, so wrapping it here writes the demo rows
    # through the same audited path as every other write - no second connection to
    # the same file, which would break the single-writer design.
    store = RecordStore(db)

    # -- the room inventory ---------------------------------------------------- #
    dsr_room_ids: list[str] = []
    for index, (room_id, _account) in enumerate(room_ids):
        dsr_room_id = f"{_DSR_ROOM_PREFIX}-{index + 1:02d}"
        dsr_room_ids.append(dsr_room_id)
        created = now - timedelta(days=120 - index * 20)
        # The last room's template version moved and a user touched it, so
        # modifiedAt and userModifiedAt disagree and the flag fires.
        user_modified = created + timedelta(days=9) if index == len(room_ids) - 1 else None
        store.create(
            reporting.ROOMS,
            {
                "room_key": dsr_room_id,
                "digital_sales_room_id": dsr_room_id,
                "name": f"Seismic DSR {index + 1:02d}",
                "digital_sales_room_template_id": f"tmpl-{100 + index}",
                "digital_sales_room_template_version_id": f"tmpl-{100 + index}-v{rng.randint(1, 4)}",
                "created_by": f"u-in-{index + 1:02d}",
                "created_by_username": ("dana", "sam")[index % 2],
                "created_at": _iso(created),
                "modified_at": _iso(user_modified or created + timedelta(days=2)),
                "user_modified_at": _iso(user_modified) if user_modified else None,
                "quality_flags": ["user_modified"] if user_modified else [],
                "has_quality_flags": bool(user_modified),
                # Only the first two are bound. An unbound room is deliberate: the
                # binding is what makes a room-scoped path answer, and a demo where
                # every room is bound cannot show what an unbound one looks like.
                **({"bound_room_id": room_id} if index < 2 else {}),
            },
            actor="etl",
            source="seed",
        )

    # A room the reporting API knows about and nobody has opened, and a second one
    # the sessions reference but the inventory has not caught up with. A warehouse
    # demo that only contains engaged rooms cannot answer "which rooms are quiet".
    for suffix, name in (
        (f"{_DSR_ROOM_PREFIX}-{len(room_ids) + 1:02d}", "Seismic DSR (never opened)"),
        (f"{_DSR_ROOM_PREFIX}-99", "Seismic DSR (no sessions landed)"),
    ):
        store.create(
            reporting.ROOMS,
            {
                "room_key": suffix,
                "digital_sales_room_id": suffix,
                "name": name,
                "digital_sales_room_template_id": "tmpl-200",
                "digital_sales_room_template_version_id": "tmpl-200-v1",
                "created_by": "u-in-09",
                "created_by_username": "sam",
                "created_at": _iso(now - timedelta(days=30)),
                "modified_at": _iso(now - timedelta(days=29)),
                "user_modified_at": None,
                "quality_flags": [],
                "has_quality_flags": False,
            },
            actor="etl",
            source="seed",
        )

    # -- the sessions ---------------------------------------------------------- #
    # Written as tabs, which is the documented grain: one buyer opening a room
    # produces several rows, and a buyer who left a tab open produces one very
    # long row. Both are here, because "single browser tab" is the whole reason a
    # session count is not a visit count.
    landed_at = now - timedelta(hours=6)
    sessions: list[dict[str, Any]] = []

    def room_at(index: int) -> str:
        """The nth demo room, wrapping.

        The seeder supplies four rooms, but a feature whose demo data indexes
        ``room_ids[2]`` breaks on a smaller dataset, and that is the kind of
        failure a reviewer hits the first time they seed a one-room database.
        """
        return dsr_room_ids[index % len(dsr_room_ids)]

    def add(
        viewer: int,
        room: int,
        *,
        started: datetime,
        seconds: int,
        ended: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "digitalSalesRoomId": room_at(room),
            "roomDurationSeconds": seconds,
            "sessionStartedAt": _iso(started),
            "sessionEndedAt": _iso(
                ended if ended is not None else started + timedelta(seconds=seconds)
            ),
            **_viewer_row(viewer),
            "ipAddress": f"203.0.113.{rng.randint(2, 250)}",
        }
        if extra:
            row.update(extra)
        sessions.append(row)
        return row

    # Room 1: a buyer who worked through it in several tabs.
    for step in range(3):
        add(
            0,
            0,
            started=landed_at - timedelta(hours=5, minutes=step * 11),
            seconds=rng.randint(240, 900),
        )
    # Room 1: a one-second session - the value in the API's own example - and a
    # long one, a tab left open.
    add(1, 0, started=landed_at - timedelta(hours=4), seconds=1)
    add(1, 0, started=landed_at - timedelta(hours=3), seconds=4210)
    # Room 2: procurement, several short sessions the same morning.
    for step in range(4):
        add(
            2,
            1,
            started=landed_at - timedelta(hours=2, minutes=step * 7),
            seconds=rng.randint(30, 240),
        )
    # Room 2: the seller's own preview. Internal dwell is not buyer engagement, and
    # the demo has to show the split being real rather than all-external.
    add(4, 1, started=landed_at - timedelta(hours=1, minutes=30), seconds=95)
    # Room 3: a buyer with no userId, so identity falls back to the email.
    add(3, 2, started=landed_at - timedelta(hours=9), seconds=610)

    # An unattributed session: no userId, no email, nothing to attribute it to. It
    # is still landed, because it is a real viewing session by somebody.
    sessions.append(
        {
            "digitalSalesRoomId": room_at(0),
            "roomDurationSeconds": 75,
            "sessionStartedAt": _iso(landed_at - timedelta(hours=8)),
            "sessionEndedAt": _iso(landed_at - timedelta(hours=8) + timedelta(seconds=75)),
            "country": "SG",
        }
    )
    # A session for a room the inventory has not caught up with. Landed and flagged
    # rather than dropped, because the inventory pull may simply not have run yet
    # and dropping the row would lose that engagement permanently.
    sessions.append(
        {
            "digitalSalesRoomId": f"{_DSR_ROOM_PREFIX}-98",
            "roomDurationSeconds": 300,
            "engagementUserEmail": "lead@adventure.example",
            "isEngagementUserInternal": False,
            "sessionStartedAt": _iso(landed_at - timedelta(hours=7)),
            "sessionEndedAt": _iso(landed_at - timedelta(hours=7) + timedelta(seconds=300)),
            "city": "Austin",
            "state": "TX",
            "country": "US",
            "geolocationLatitude": 30.2672,
            "geoLocationLongitude": -97.7431,
        }
    )
    # A session whose reported duration disagrees with its own timestamps by more
    # than the tolerance: 40s reported, 8 minutes elapsed. Kept and flagged, never
    # corrected - the ETL must not decide which of the two the vendor meant.
    mismatched = {
        "digitalSalesRoomId": room_at(1),
        "roomDurationSeconds": 40,
        "engagementUserEmail": "procurement@contoso.example",
        "userId": "u-ch-01",
        "isEngagementUserInternal": False,
        "sessionStartedAt": _iso(landed_at - timedelta(hours=6)),
        "sessionEndedAt": _iso(landed_at - timedelta(hours=6) + timedelta(minutes=8)),
        "city": "Dublin",
        "state": "Leinster",
        "country": "IE",
    }
    sessions.append(mismatched)
    # A session with no geography at all, and one with a naive timestamp, which is
    # read as UTC and flagged rather than dropped.
    naive_start = (landed_at - timedelta(hours=10)).replace(tzinfo=None)
    sessions.append(
        {
            "digitalSalesRoomId": room_at(0),
            "roomDurationSeconds": 12,
            "engagementUserEmail": "a.buyer@northwind.example",
            "userId": "u-nw-01",
            "isEngagementUserInternal": False,
            "sessionStartedAt": naive_start.isoformat(),
            "sessionEndedAt": (naive_start + timedelta(seconds=12)).isoformat(),
        }
    )
    # A row carrying a field the dictionary does not name. It lands under `extra`
    # and is still queryable, which is the point: a field the vendor adds tomorrow
    # reaches the lake without a code change here.
    undocumented = {
        "digitalSalesRoomId": room_at(0),
        "roomDurationSeconds": 132,
        "engagementUserEmail": "b.buyer@northwind.example",
        "userId": "u-nw-02",
        "isEngagementUserInternal": False,
        "sessionStartedAt": _iso(landed_at - timedelta(hours=11)),
        "sessionEndedAt": _iso(landed_at - timedelta(hours=11) + timedelta(seconds=132)),
        "country": "US",
        "city": "Portland",
        "state": "OR",
        "documentId": "doc-unreleased-0042",
    }
    sessions.append(undocumented)

    # -- the runs and the landings --------------------------------------------- #
    # The first run is the unbounded backfill: the lower bound is open because the
    # endpoints are built to allow "large portions of data", and the upper bound is
    # closed so the watermark can advance from the very first run.
    first_end = now - timedelta(hours=26)
    backfill = reporting.land_sessions(
        store,
        reporting.Window(kind="modified", start=None, end=first_end, resource="viewing_sessions"),
        sessions,
        actor="etl",
        source="seed",
    )
    store.create(
        reporting.RUNS,
        {
            "run_kind": "extract",
            "state": "landed",
            "resource": "viewing_sessions",
            "format": reporting.ACCEPT_JSON,
            "limit": None,
            "window": reporting.Window(
                kind="modified", start=None, end=first_end, resource="viewing_sessions"
            ).to_dict(),
            "sla_hours": reporting.REFRESH_SLA_HOURS,
            "requested_at": _iso(first_end),
            "landed_at": _iso(first_end),
            "watermark_before": None,
            "watermark_after": _iso(first_end),
            "counters": backfill,
            "note": "the first extraction: unbounded below, so it backfills",
        },
        actor="etl",
        source="seed",
    )

    # The nightly page re-lands two rows: one the vendor corrected, and one that
    # came back byte-identical. Both are the merge doing what it says, and the
    # counters say which was which.
    corrected = {**mismatched, "roomDurationSeconds": 481}
    nightly = reporting.land_sessions(
        store,
        reporting.Window(
            kind="modified",
            start=first_end,
            end=now - timedelta(hours=1),
            resource="viewing_sessions",
        ),
        [corrected, undocumented],
        actor="etl",
        source="seed",
    )
    store.create(
        reporting.RUNS,
        {
            "run_kind": "extract",
            "state": "landed",
            "resource": "viewing_sessions",
            "format": reporting.ACCEPT_JSON,
            "limit": 1000,
            "window": reporting.Window(
                kind="modified",
                start=first_end,
                end=now - timedelta(hours=1),
                resource="viewing_sessions",
            ).to_dict(),
            "sla_hours": reporting.REFRESH_SLA_HOURS,
            "requested_at": _iso(landed_at),
            "landed_at": _iso(landed_at),
            "watermark_before": _iso(first_end),
            "watermark_after": _iso(landed_at),
            "counters": nightly,
            "note": "the nightly page: one row corrected, one unchanged",
        },
        actor="etl",
        source="seed",
    )

    # A run that was opened and never landed: the state a job that failed between
    # the pull and the landing leaves behind, and the one a sweep refuses to open
    # a second window on top of.
    pending_start = landed_at
    store.create(
        reporting.RUNS,
        {
            "run_kind": "sweep",
            "state": "requested",
            "resource": "viewing_sessions",
            "format": reporting.ACCEPT_JSON,
            "limit": 1000,
            "window": reporting.Window(
                kind="modified",
                start=pending_start,
                end=pending_start + timedelta(hours=reporting.REFRESH_SLA_HOURS),
                resource="viewing_sessions",
            ).to_dict(),
            "sla_hours": reporting.REFRESH_SLA_HOURS,
            "requested_at": _iso(now - timedelta(minutes=40)),
            "landed_at": None,
            "watermark_before": _iso(landed_at),
            "watermark_after": None,
            "counters": {},
            "note": "opened by the nightly job; the landing has not arrived",
        },
        actor="etl",
        source="seed",
    )

    return (
        f"{len(dsr_room_ids) + 2} inventory rooms, {len(sessions)} viewing sessions "
        f"(backfill {backfill['landed']} landed, nightly {nightly['landed']} re-landed "
        f"of which {nightly['unchanged']} unchanged), 3 extraction runs, 1 never landed"
    )
