"""WF-104: drip a multi-channel nurture sequence with channel fallback.

The researched workflow, in full. A seller opens ``Outbound > Series``, clicks **+ New Series**,
drops a **rule block** to set who enters, drops a **content block** with a per-block **Goal** and
**Scheduling**, sets the block's **send window** ("Wait up to 30d") so the block retries while the
buyer is offline, adds a fallback **email** block on the *If not online after 30d* branch, adds a
rule block on *When delivered -> Clicked link* with ``Try to match for 3d``, wires the branches to
converge on a shared next block, and clicks **Set series live**.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-104`` and every route is room-scoped.** A series belongs to a room, and
the room is what a seller is looking at when they ask how their nurture is doing.

**``source`` comes from the route.** Every write passes a string built from ``router.prefix``, so
the audit row names the route that served it. ``source`` is a required keyword on every writing
method of :class:`~dsr.nurture_series.engine.NurtureSeriesEngine`, and
``test_wf104_http.py`` asserts that every source it records names a path the host mounted.

**The poll is a route, not a thread.** The research says "Series checks every 30 minutes whether a
customer matches each rule, message, wait, or tag block." This product runs no worker; a thread
needs a clock no test can move, it would write audit rows naming no route, and under pytest-xdist
it would race across workers. ``POST /rooms/{room_id}/evaluate`` is therefore the sweep, and its
response reports a ``due`` count so the gap that leaves is visible rather than silent.

**One handler for the whole error hierarchy.**
:class:`~dsr.nurture_series.errors.SeriesRefused` is the base of every refusal and each carries
its own published ``code`` and ``status``, so one handler answers 400 for a bad filter, 404 for a
missing series, and 409 for a run that is already waiting. Every type is this feature's own,
which is what makes mapping it safe.

**The audience is caller-supplied.** The domain module filters the contacts the caller hands it
and stores no contact rows, so it is decoupled from the CRM that owns them (WF-039). Chosen by
Jev, audit ``jev-20261013T150622-7908-82240``, at confidence 0.86.

**Nothing here claims a message left this product.** Every dispatch row carries
``sent_by_this_product: False`` and ``channel_send_is_recorded: True``, the standard
:mod:`dsr.conversation_chase` and :mod:`dsr.automation_engagement` follow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.nurture_series import inferences as inference_register, vocabulary as vocab
from dsr.nurture_series.engine import NurtureSeriesEngine
from dsr.nurture_series.errors import SeriesRefused
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-104-nurture-series",
    "ticket": "WF-104",
    "name": "Drip a multi-channel nurture sequence with channel fallback",
    "description": (
        "Build a nurture series of rule, content, wait and tag blocks, and drip content to a "
        "buyer across in-app, email, mobile push and SMS. A block's send window retries while "
        "the buyer is offline; if the buyer still has not come online after the window, the "
        "offline branch falls back to email. A rule block on 'When delivered -> Clicked link' "
        "waits up to three days for the buyer to match, and the divergent branches converge on "
        "a shared next block. A series is a draft until it is set live, and a draft fires "
        "nothing."
    ),
}

router = APIRouter(prefix="/api/wf-104", tags=["wf104"])

PREFIX = router.prefix


def get_engine(store: RecordStore = StoreDep) -> NurtureSeriesEngine:
    """A :class:`NurtureSeriesEngine` over the process-wide audited store.

    Built per request rather than held on ``app.state``, because putting it there is the edit to
    the shared ``dsr/api.py`` this feature host exists to make unnecessary. The engine holds a
    store handle and a clock and nothing else, so a test can build one directly and move the
    clock by hand.
    """

    return NurtureSeriesEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _series_refused(request: Request, exc: SeriesRefused) -> JSONResponse:
    """A domain refusal, answered with the status and published code it carries.

    One handler for the hierarchy: a bad filter (400), a missing series (404) and a run that is
    already waiting (409) are all this package's errors, and only the first is about a value the
    caller sent.
    """

    return JSONResponse(status_code=exc.status, content=exc.to_dict())


#: Subclasses are covered by the base, so one entry maps the whole hierarchy. Every type is
#: raised only by this feature, which is what makes mapping it safe.
EXCEPTION_HANDLERS = {SeriesRefused: _series_refused}


# --------------------------------------------------------------------------- #
# Audit sources
# --------------------------------------------------------------------------- #
#
# Every one built from ``router.prefix``, so the audit row names a path the host actually
# mounted. ``test_wf104_http.py`` asserts each of these against the live registry.

VOCABULARY_SOURCE = f"GET {PREFIX}/vocabulary"
INFERENCES_SOURCE = f"GET {PREFIX}/inferences"
SERIES_CREATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/series"
SERIES_PATCH_SOURCE = f"PATCH {PREFIX}/rooms/{{room_id}}/series/{{series_id}}"
SERIES_DELETE_SOURCE = f"DELETE {PREFIX}/rooms/{{room_id}}/series/{{series_id}}"
GO_LIVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/series/{{series_id}}/go-live"
EVALUATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/evaluate"
ADVANCE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/runs/{{run_id}}/advance"
RESOLVE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/runs/{{run_id}}/resolve"
EVENT_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/runs/{{run_id}}/events"

#: The seeder writes through the same sources, so a seeded row's audit names a route the app
#: serves rather than a string that exists only in demo data.
SEED_SOURCES = (
    SERIES_CREATE_SOURCE,
    GO_LIVE_SOURCE,
    EVALUATE_SOURCE,
    ADVANCE_SOURCE,
    RESOLVE_SOURCE,
    EVENT_SOURCE,
)


# --------------------------------------------------------------------------- #
# The published vocabulary and the judgement calls
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every researched term and every refusal code")
def vocabulary() -> dict[str, Any]:
    """The terms, the labels, the filter fields and the refusal codes, as data."""

    return vocab.published_vocabulary()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inferences_route() -> dict[str, Any]:
    """The decisions the specification left open, and the alternative each rejected."""

    return inference_register.inferences_report()


@router.get("/decisions/{decision_id}", summary="One recorded decision")
def read_decision(decision_id: str) -> dict[str, Any]:
    entry = inference_register.decision_by_id(decision_id)
    if entry is None:
        raise SeriesRefused(
            f"no recorded decision {decision_id!r}; this workflow recorded "
            f"{inference_register.inferences_report()['count']}",
            code="decision_not_found",
            status=404,
            errors={"decision_id": "unknown"},
        )
    return entry


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/series", summary="A room's series")
def list_series(
    room_id: str,
    state: str | None = Query(default=None, description="draft or live"),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    rows = engine.list_series(room_id, state=state)
    return {
        "room_id": room_id,
        "series": rows,
        "count": len(rows),
        "live": sum(1 for row in rows if row["state"] == vocab.STATE_LIVE),
    }


@router.post("/rooms/{room_id}/series", status_code=201, summary="Start a series")
def create_series(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Step 1 and step 3: start a series and describe its blocks.

    The blocks are validated on the way in: the four block kinds, the four channels, the seven
    content types, the filter fields and the two rule wait modes. A series is a draft until it is
    set live.
    """

    return engine.create_series(room_id, payload, source=SERIES_CREATE_SOURCE, actor="seller")


@router.get("/rooms/{room_id}/series/{series_id}", summary="One series")
def read_series(
    room_id: str, series_id: str, engine: NurtureSeriesEngine = EngineDep
) -> dict[str, Any]:
    return engine.series_view(series_id)


@router.patch("/rooms/{room_id}/series/{series_id}", summary="Edit a series, or set it live")
def patch_series(
    room_id: str,
    series_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Edit the name, the blocks or the template. Every field is revalidated on the way in."""

    return engine.update_series(series_id, payload, source=SERIES_PATCH_SOURCE, actor="seller")


@router.delete("/rooms/{room_id}/series/{series_id}", summary="Remove a series")
def delete_series(
    room_id: str, series_id: str, engine: NurtureSeriesEngine = EngineDep
) -> dict[str, Any]:
    """Soft-delete a series. Its runs survive, because a run records what actually happened."""

    return engine.delete_series(series_id, source=SERIES_DELETE_SOURCE, actor="seller")


@router.post("/rooms/{room_id}/series/{series_id}/go-live", summary="Set a series live")
def go_live(
    room_id: str, series_id: str, engine: NurtureSeriesEngine = EngineDep
) -> dict[str, Any]:
    """Step 8: "Click Set series live". A draft fires nothing."""

    return engine.go_live(series_id, source=GO_LIVE_SOURCE, actor="seller")


@router.post("/rooms/{room_id}/series/{series_id}/preview", summary="The audience preview")
def preview(
    room_id: str,
    series_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Step 2: "set the entry filters (Who will enter, audience preview count)".

    Read-only. The filters are the same evaluator the entry gate uses, so the preview cannot
    drift from what ``evaluate`` will do.
    """

    return engine.preview(room_id, series_id, payload)


@router.get(
    "/rooms/{room_id}/series/{series_id}/export",
    summary="Export the series for the EU Data Act",
)
def export_series(
    room_id: str, series_id: str, engine: NurtureSeriesEngine = EngineDep
) -> dict[str, Any]:
    """The researched export, exposing ``targeting``, ``snapshot``, ``attributes`` and
    ``embedded_rules``."""

    return engine.export_series(series_id)


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/evaluate",
    summary="Run the 30-minute poll: gate entry and report what is due",
)
def evaluate(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """One poll over the room's live series and the audience the caller supplies.

    Nothing is due until somebody calls this. That is the cost of the explicit-route decision
    and it is reported rather than hidden: the response carries ``due``, the count the next call
    would act on.

    Skips are returned, not raised. "Not eligible" and "already entered" are states of this
    room, not faults in the caller's request.
    """

    return engine.evaluate(room_id, payload, source=EVALUATE_SOURCE, actor="scheduler")


@router.get("/rooms/{room_id}/summary", summary="What the series have done")
def room_summary(room_id: str, engine: NurtureSeriesEngine = EngineDep) -> dict[str, Any]:
    """Per-state counts, the fallback count and how many runs are due."""

    return engine.summary(room_id)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/runs", summary="A room's runs")
def list_runs(
    room_id: str,
    series_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    rows = engine.list_runs(room_id, series_id=series_id, state=state)
    by_state = {run_state: 0 for run_state in vocab.RUN_STATES}
    for row in rows:
        if row["state"] in by_state:
            by_state[row["state"]] += 1
    return {
        "room_id": room_id,
        "runs": rows,
        "count": len(rows),
        "by_state": by_state,
        "state_labels": dict(vocab.RUN_STATE_LABELS),
    }


@router.get("/rooms/{room_id}/runs/{run_id}", summary="One run")
def read_run(room_id: str, run_id: str, engine: NurtureSeriesEngine = EngineDep) -> dict[str, Any]:
    """One run with its cursor, its dispatches, its engagement and its history."""

    return engine.run_view(run_id)


@router.post("/rooms/{room_id}/runs/{run_id}/advance", summary="Walk the run one hold")
def advance(
    room_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Walk the run until it holds, exits, finishes, or holds on a retry window.

    A content block dispatches to its channel. A block that cannot deliver and has a fallback
    holds the run while it retries, and a Wait or a rule match window starts its timer.
    """

    return engine.advance(run_id, payload, source=ADVANCE_SOURCE, actor="scheduler")


@router.post("/rooms/{room_id}/runs/{run_id}/resolve", summary="Finish a hold")
def resolve(
    room_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Finish a wait, a rule match window, or a send-window retry.

    A rule match window resolves as soon as the buyer matches, and exits the path if the window
    elapses without a match. A send-window retry delivers if the buyer has come online, and
    falls back if the window elapses with them still offline.
    """

    return engine.resolve(run_id, payload, source=RESOLVE_SOURCE, actor="scheduler")


@router.post("/rooms/{room_id}/runs/{run_id}/events", summary="Record an engagement event")
def record_event(
    room_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: NurtureSeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Record an open, a click or a reply against the buyer the run belongs to."""

    return engine.record_event(run_id, payload, source=EVENT_SOURCE, actor="buyer")


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """One live series, four buyers and the states that are not all successes.

    The rows are produced by running the real :class:`NurtureSeriesEngine`, so the demo cannot
    show a shape this workflow would not produce. Every instant derives from ``context["now"]``,
    so nothing here carries a fixed date.

    It is deliberately mixed, because a demo of only green teaches a reviewer nothing:

    * **one buyer delivered, clicked and tagged**, so the whole content / wait / click / tag
      walk is a row rather than a claim;
    * **one buyer offline throughout**, so the send window retries and the fallback email fires
      once the window elapses - the researched *If not online after 30d* branch;
    * **one buyer who never clicks**, so the *Try to match for 3d* rule exits the path;
    * **one buyer excluded by the entry rule**, so the audience preview's exclusion is on the
      record.

    Every character in the returned string is encodable by cp1252. The seeder prints it on a
    Windows console, and a single RIGHTWARDS ARROW in one recovered feature broke the whole seed.
    """

    store = RecordStore(db)
    clock = context.get("now")
    if not isinstance(clock, datetime):
        clock = datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    def at(minutes: float) -> datetime:
        return clock + timedelta(minutes=minutes)

    def engine_at(minutes: float) -> NurtureSeriesEngine:
        moment = at(minutes)
        return NurtureSeriesEngine(store, now=lambda: moment)

    given = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:1]
    created = 0
    if not room_ids:
        record = store.create(
            "room", {"name": "Northwind Traders - nurture", "account": "Northwind"}, actor="dana"
        )
        room_ids.append(record["id"])
        created += 1
    room_id = room_ids[0]

    builder = engine_at(0)
    series = builder.create_series(
        room_id,
        {
            "name": "Enterprise evaluation nurture",
            "template": "from_scratch",
            "blocks": [
                {
                    "kind": "rule",
                    "id": "who_enters",
                    "filters": [{"field": "plan", "op": "eq", "value": "enterprise"}],
                    "match": "all",
                },
                {
                    "kind": "content",
                    "id": "intro_post",
                    "channel": "in_app",
                    "content_type": "post",
                    "body": "Welcome - here is the 2-minute product tour.",
                    "goal": "Get a reply on the tour",
                    "scheduling": {"timezone": "UTC"},
                    "send_window_seconds": 30 * DAY,
                    "fallback_channel": "email",
                    "fallback_enabled": True,
                },
                {"kind": "wait", "id": "give_it_a_day", "duration_seconds": DAY},
                {
                    "kind": "rule",
                    "id": "clicked_link",
                    "condition": "clicked_link",
                    "wait_mode": "for",
                    "match_within_seconds": 3 * DAY,
                },
                {"kind": "tag", "id": "engaged_tag", "tag": "nurtured"},
            ],
        },
        source=SERIES_CREATE_SOURCE,
        actor="dana",
    )
    builder.go_live(series["id"], source=GO_LIVE_SOURCE, actor="dana")

    audience = [
        {
            "contact_id": "buyer_online",
            "attributes": {
                "email": "a.buyer@northwind.example",
                "plan": "enterprise",
                "lead_score": 82,
            },
            "company": {"name": "Northwind Traders", "industry": "logistics", "employees": 400},
            "online": True,
            "channels": ["in_app", "email"],
        },
        {
            "contact_id": "buyer_offline",
            "attributes": {
                "email": "ops@northwind.example",
                "plan": "enterprise",
                "lead_score": 61,
            },
            "company": {"name": "Northwind Traders", "industry": "logistics", "employees": 400},
            "online": False,
            "channels": ["in_app", "email"],
        },
        {
            "contact_id": "buyer_noclick",
            "attributes": {
                "email": "proc@northwind.example",
                "plan": "enterprise",
                "lead_score": 44,
            },
            "company": {"name": "Northwind Traders", "industry": "logistics", "employees": 400},
            "online": True,
            "channels": ["in_app", "email"],
        },
        {
            "contact_id": "buyer_starter",
            "attributes": {"email": "trial@other.example", "plan": "starter", "lead_score": 12},
            "company": {"name": "Other Co", "industry": "retail", "employees": 6},
            "online": True,
            "channels": ["email"],
        },
    ]

    sweep = builder.evaluate(
        room_id, {"audience": audience}, source=EVALUATE_SOURCE, actor="scheduler"
    )
    runs = {row["contact_id"]: row["id"] for row in sweep["fired"]}

    # The buyer who is online: delivered, wait, then clicks inside the 3-day window and is tagged.
    online_run = runs["buyer_online"]
    engine_at(0).advance(online_run, {}, source=ADVANCE_SOURCE, actor="scheduler")
    engine_at(1 * DAY).resolve(online_run, {}, source=RESOLVE_SOURCE, actor="scheduler")
    engine_at(1 * DAY + 60).record_event(
        online_run, {"event": "click"}, source=EVENT_SOURCE, actor="buyer"
    )
    engine_at(1 * DAY + 60).resolve(online_run, {}, source=RESOLVE_SOURCE, actor="scheduler")

    # The buyer who is offline: the send window retries, then the fallback email fires, then the
    # match window elapses with no click and the run leaves the path.
    offline_run = runs["buyer_offline"]
    engine_at(0).advance(offline_run, {}, source=ADVANCE_SOURCE, actor="scheduler")
    engine_at(30 * DAY).resolve(offline_run, {}, source=RESOLVE_SOURCE, actor="scheduler")
    engine_at(31 * DAY).resolve(offline_run, {}, source=RESOLVE_SOURCE, actor="scheduler")
    engine_at(34 * DAY).resolve(offline_run, {}, source=RESOLVE_SOURCE, actor="scheduler")

    # The buyer who never clicks: delivered, then the match window elapses and the path is left.
    noclick_run = runs["buyer_noclick"]
    engine_at(0).advance(noclick_run, {}, source=ADVANCE_SOURCE, actor="scheduler")
    engine_at(1 * DAY).resolve(noclick_run, {}, source=RESOLVE_SOURCE, actor="scheduler")
    engine_at(4 * DAY).resolve(noclick_run, {}, source=RESOLVE_SOURCE, actor="scheduler")

    summary = builder.summary(room_id)
    excluded = sum(1 for row in sweep["skipped"] if row["reason"] == "entry_filters_unmatched")
    finished = summary["runs_by_state"].get(vocab.RUN_FINISHED, 0)
    exited = summary["runs_by_state"].get(vocab.RUN_EXITED, 0)
    clicks = summary["engagement_by_event"].get("click", 0)

    return (
        f"{summary['series_by_state'].get(vocab.STATE_LIVE, 0)} live series of "
        f"{summary['series']}: {len(audience)} audience contacts, {sweep['fired_count']} entered, "
        f"{excluded} excluded by the entry rule; "
        f"{summary['dispatches']} dispatch attempts and {summary['fallbacks']} fallback to email; "
        f"{finished} finished and tagged, {exited} left the path after the match window with no "
        f"click; {clicks} engagement event (click)"
        + (f"; {created} room created for these states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
    )
