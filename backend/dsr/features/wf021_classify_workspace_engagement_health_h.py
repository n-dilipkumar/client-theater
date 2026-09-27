"""WF-021: classify workspace engagement health as Hot / Warm / Cooling / Cold.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-021.md``. The
domain logic is in :mod:`dsr.trend_health`; this module is the three things a
workflow needs in order to exist in this product: the HTTP surface under a prefix
of its own, the mapping from its domain errors to responses, and the demo data.

What the research specifies, and where each piece of it landed
---------------------------------------------------------------

*The four buckets and the three windows.* ``dsr.trend_health.windows`` quotes
Dock's definition and implements it as a total ladder. Nothing here decides a
bucket; the routes read the value the ladder returns.

*The automation.* "The classification recomputes continuously from activity; no
user action" and "a workspace decays from Hot -> Warm -> Cooling -> Cold without
any new activity." There is no refresh endpoint and no cached column, because a
stored value needs a job to age it. ``GET /rooms/{room_id}/trend`` takes an
``as_of`` on every read, so the decay is a question a client can ask rather than
a claim this module has to keep true.

*The user flow.* "Sort/filter on Trend plus ``Last Client View`` to isolate Hot
rooms and Cold rooms" is ``GET /dashboard``, whose filters are buckets, owner and
team, and whose sort keys are the researched columns - Trend, owner, workspace
creation date, recent client activity - plus the name a rep is looking at. The
per-room health badge the research notes as a Liferay precedent is
``GET /rooms/{room_id}/trend``.

*The data sources.* ``POST /events`` is the seam: the five researched
``workspace.*`` event shapes, the researched camelCase ``occurredAt``, and the
room scope the envelope provides. A team that already receives Dock webhooks can
forward them here unchanged.

Choices the research left open
------------------------------

* **"tons" and "a decent amount" carry no number.** The floors implementing them
  are the ``volume-floor`` entry in :mod:`dsr.trend_health.inferences`, they are
  a stored record, and ``PATCH /rules`` is how a team replaces them.
* **The bucket ladder is total.** A workspace whose recent activity is under
  every floor reads Cooling or Cold rather than nothing, because the research's
  four states are a partition and a hole in a partition is a support ticket.
* **Nothing is stored per workspace.** Also an inference, and the reason there
  is no "recompute" route to call and no state to get wrong.

The two things this module is careful about, because both have gone wrong in this
codebase before:

**``source`` is built from ``router.prefix`` and required by the domain layer.**
Every write below names the route that served it, so an audit row can be traced
to a request. A domain method with a hardcoded string as its ``source`` is a
defect, and ``source`` having no default is what stops it regressing.

**Dependencies come from ``dsr.deps``.** This module never imports ``dsr.api``;
a feature that reaches for the app reintroduces exactly the coupling the plugin
host exists to remove.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.trend_health import (
    DASHBOARD_SORTS,
    TrendError,
    TrendHealth,
    UnknownRoom,
    describe as describe_inferences,
    vocabulary,
)
from dsr.trend_health.vocabulary import (
    CLIENT_VIEW_EVENT,
    ENGAGEMENT_COLLECTION,
    TREND_VALUES,
)

FEATURE = {
    "id": "wf-021-classify-workspace-engagement-health-h",
    "ticket": "WF-021",
    "name": "Classify workspace engagement health",
    "description": (
        "A Hot / Warm / Cooling / Cold Trend value per workspace, derived from "
        "workspace activity on the researched 7 / 14 / 30-day windows, with the "
        "arithmetic and the decay that follows if nothing else happens."
    ),
    "nav": [{"id": "trend", "label": "Trend"}],
}

router = APIRouter(prefix="/api/wf-021", tags=["wf-021"])


def get_trend_health(store: RecordStore = StoreDep) -> TrendHealth:
    """A :class:`TrendHealth` over the process-wide audited store.

    Built per request rather than held on ``app.state``: the class holds nothing
    but the store handle, and ``app.state`` is a shared file this feature must
    not edit. It also leaves the whole workflow unit-testable against a temporary
    database without the app running.
    """
    return TrendHealth(store)


TrendDep = Depends(get_trend_health)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _trend_error(request: Request, exc: TrendError) -> JSONResponse:
    """A refusal from this workflow, as a response.

    One registered handler for the whole hierarchy, branching on the type rather
    than registering five: :class:`TrendError` is a type this workflow owns, so a
    global registration for it cannot intercept an exception raised anywhere else
    in the product, and a class hierarchy that grows a sixth refusal next month
    does not need a sixth registration.

    An unknown room is 404 because that is what it is. Everything else is 400:
    a well-formed request asking for something this layer will not do - an event
    shape outside the researched vocabulary, a timestamp that cannot be read, a
    rules patch that would produce a configuration the ladder cannot use.
    """
    status = 404 if isinstance(exc, UnknownRoom) else 400
    return JSONResponse(
        status_code=status,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {TrendError: _trend_error}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The Trend values, the event shapes, the labels")
def trend_vocabulary() -> dict[str, Any]:
    """Every published name, as data.

    Served so a client renders its column headers, its bucket filters and its
    badge labels from the same source the classifier validates against. A name
    added here reaches every client at once, and no client hard-codes a list that
    can drift.
    """
    return vocabulary()


@router.get("/inferences", summary="What the research left to this build")
def trend_inferences() -> dict[str, Any]:
    """Every judgement call in the workflow, named, bounded and changeable.

    The research quotes its four buckets and its three windows and then declines
    to number "tons" or "a decent amount of", says nothing about internal views,
    clock skew, or which side of a window edge an event falls on. This is the
    list of everything that follows from those silences, so a reviewer can
    disagree with a named entry instead of hunting through a diff. It is a read
    with no side effect, so it needs no store.
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


@router.get("/rules", summary="The classification rules in force")
def read_rules(trend: TrendHealth = TrendDep) -> dict[str, Any]:
    """The windows, the volume floors, and where the values came from.

    ``source`` distinguishes the researched defaults from a stored override, so a
    client can say which model it is reading rather than presenting a tuned
    threshold as though the source published it.
    """
    return trend.rules_view()


@router.patch("/rules", summary="Retune the classification")
def update_rules(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    trend: TrendHealth = TrendDep,
) -> dict[str, Any]:
    """Store a rules override. A partial patch, and the write is audited.

    Retuning the Hot floor merges key by key rather than replacing the record, so
    a change to one threshold cannot silently reset another. Nothing here is a
    migration or a column: the rules are a record, which is how a team ships a
    different health model without coordinating with anyone.
    """
    return trend.save_rules(payload, actor=actor, source=f"PATCH {router.prefix}/rules")


# --------------------------------------------------------------------------- #
# Portfolio: the Trend column
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="How the portfolio is distributed")
def trend_summary(
    room_id: str | None = Query(default=None, description="One room; omitted means every room"),
    as_of: str | None = Query(default=None, description="ISO instant to evaluate against"),
    trend: TrendHealth = TrendDep,
) -> dict[str, Any]:
    """Counts per bucket, the engagement behind them, and the rules in force.

    The stat row a rep reads before deciding which rooms to open. Scoped to one
    room when ``room_id`` is given, so a room's own page does not show the whole
    portfolio's distribution.
    """
    return trend.summary(room_id=room_id, as_of=as_of)


@router.get("/dashboard", summary="The Trend column, filterable and sortable")
def trend_dashboard(
    trend_filter: str | None = Query(
        default=None,
        alias="trend",
        description="One or more of hot, warm, cooling, cold - comma separated",
    ),
    owner: str | None = Query(default=None),
    team: str | None = Query(default=None),
    room_id: str | None = Query(default=None, description="One room; omitted means every room"),
    sort: str = Query(default="trend", description=f"One of {', '.join(DASHBOARD_SORTS)}"),
    order: str = Query(default="asc", description="asc | desc"),
    as_of: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    trend: TrendHealth = TrendDep,
) -> dict[str, Any]:
    """One row per workspace: the Trend value, the counts behind it, and when it
    next changes on its own.

    The user flow this serves is "sort/filter on Trend plus Last Client View to
    isolate Hot rooms and Cold rooms, then act", so the bucket filter accepts a
    comma-separated list and one call can isolate both ends of the pipeline.
    Sorting by Trend is hottest first; a room with no client view sorts last in
    both directions, because a missing value is the coldest thing in the column
    rather than the most recent one.
    """
    return trend.dashboard(
        room_id=room_id,
        trend=trend_filter,
        owner=owner,
        team=team,
        sort=sort,
        order=order,
        as_of=as_of,
        limit=limit,
    )


@router.get("/rooms/{room_id}/trend", summary="One room's health badge")
def room_trend(
    room_id: str,
    as_of: str | None = Query(default=None, description="ISO instant to evaluate against"),
    trend: TrendHealth = TrendDep,
) -> dict[str, Any]:
    """The per-room health badge, its arithmetic, and its decay.

    The Liferay DSR "Room Trend" widget the research cites as a precedent for a
    per-room health badge, with the Dock windows behind it and the sourced
    sentence for the value it returned. ``decay`` is the researched automation
    made visible: the value at each remaining window edge if no new activity
    arrives, which is what makes "this room goes Cold in nine days" a thing a rep
    can be told before it happens rather than after.
    """
    return trend.classify_room(room_id, as_of=as_of)


# --------------------------------------------------------------------------- #
# Engagement events: the webhook seam
# --------------------------------------------------------------------------- #


@router.get("/events", summary="Recorded engagement events")
def list_events(
    room_id: str | None = Query(default=None),
    type: str | None = Query(default=None, description="One researched event shape"),
    audience: str | None = Query(default=None, description="external | internal"),
    client_view: bool | None = Query(default=None, description="Only client views, or only the rest"),
    since: str | None = Query(default=None, description="ISO instant, inclusive"),
    until: str | None = Query(default=None, description="ISO instant, inclusive"),
    limit: int = Query(default=100, ge=1, le=1000),
    trend: TrendHealth = TrendDep,
) -> dict[str, Any]:
    """The engagement events behind the buckets, newest first.

    Written and read through the audited store, so every row here is also a row in
    the audit log. The stored ``client_view`` flag is what makes the researched
    "Last Client View" column queryable through the generic records API as well as
    through this one.
    """
    records = trend.list_events(
        room_id=room_id,
        event_type=type,
        audience=audience,
        client_view=client_view,
        since=since,
        until=until,
        limit=limit,
    )
    published = vocabulary()
    return {
        "count": len(records),
        "events": [
            record["data"] | {"id": record["id"], "room_id": record["room_id"]}
            for record in records
        ],
        "accepted_types": list(published["engagement_event_types"])
        + [f"{published['order_form_prefix']}*"],
        "client_view_event": CLIENT_VIEW_EVENT,
    }


@router.post("/events", status_code=201, summary="Record one engagement event")
def record_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None, description="Overrides the room named in the body"),
    actor: str | None = Query(default=None),
    trend: TrendHealth = TrendDep,
) -> dict[str, Any]:
    """Record a workspace activity event from the researched vocabulary.

    Accepts the Dock webhook body unchanged: ``type`` names the event,
    ``occurredAt`` is the researched camelCase timestamp, and ``workspaceId``
    names the room. ``room_id`` on the query string wins when both are present.

    The event is stored before anything reads it, so the bucket moves as soon as
    the event lands - which is the whole of the researched automation from this
    side. Anything in the body this workflow does not own is stored verbatim and
    is immediately queryable with ``?where=``, so a team adding a field to an
    engagement event ships a record rather than a migration.
    """
    record = trend.record_event(
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/events",
        now=datetime.now(timezone.utc),
    )
    return {
        "recorded": True,
        "event": record["data"] | {"id": record["id"], "room_id": record["room_id"]},
        "client_view_event": CLIENT_VIEW_EVENT,
        "trend_values": list(TREND_VALUES),
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

# The demo covers the first four demo rooms, in the core seeder's order, one in each
# of the four researched states - and the states worth reviewing are not all healthy:
#
#   rooms[0]  Hot     a genuinely busy week: eight buyer events in under four days,
#                      two of them openings, one of them a completed order form.
#   rooms[1]  Warm    a real fortnight of engagement that stopped a week ago. The
#                      researched "Warm" case, and the one a rep is meant to notice
#                      while it is still Warm.
#   rooms[2]  Cooling last buyer engagement twenty days ago - and an internal view
#                      two days ago that must NOT lift it, because the metric is
#                      about external engagement. A rep previewing their own room
#                      is the single most likely way this column gets wrong, so the
#                      demo contains it.
#   rooms[3]  Cold    no engagement recorded at all, and a last client view of null.
#                      The extreme of "no engagement within the last month", and the
#                      row that exercises the UI's empty rendering.
#
# Keyed by position rather than by account name: the seeder is handed ids, not room
# records, so a name lookup would mean reading the room collection to find them. If
# the core dataset grows a room before these, the plan's positions move with it,
# which is why the states are asserted by classification in the tests rather than
# assumed here.
DEMO_PLAN: tuple[dict[str, Any], ...] = (
    {
        "room": 0,
        "state": "hot",
        "summary": "busy week",
        "events": [
            (0.2, "workspace.viewed", "external"),
            (0.4, "workspace.page.viewed", "external"),
            (0.9, "workspace.file.viewed", "external"),
            (1.4, "workspace.link.clicked", "external"),
            (1.8, "workspace.page.viewed", "external"),
            (2.2, "workspace.viewed", "external"),
            (2.9, "workspace.file.viewed", "external"),
            (3.6, "workspace.order_form.completed", "external"),
        ],
    },
    {
        "room": 1,
        "state": "warm",
        "summary": "a fortnight of engagement that stopped a week ago",
        "events": [
            (8.2, "workspace.page.viewed", "external"),
            (9.1, "workspace.file.viewed", "external"),
            (10.4, "workspace.viewed", "external"),
            (12.7, "workspace.link.clicked", "external"),
        ],
    },
    {
        "room": 2,
        "state": "cooling",
        "summary": "no buyer engagement for 20 days, plus an internal view that must not count",
        "events": [
            (20.4, "workspace.page.viewed", "external"),
            (22.1, "workspace.file.viewed", "external"),
            (2.0, "workspace.viewed", "internal"),
        ],
    },
    {
        "room": 3,
        "state": "cold",
        "summary": "no engagement recorded at all",
        "events": [],
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the four researched states, and the trap that keeps the column honest.

    The rows are written through the real :class:`TrendHealth` over the audited
    database rather than by hand, so the demo cannot show a shape the workflow
    would not produce - and so every seeded event is audited like any other.

    A demo of only healthy rooms would teach a reviewer nothing, so the plan
    above includes a Cooling room with a recent *internal* view and a Cold room
    with no events at all. The internal view is the interesting one: it is the
    mistake this column makes most easily, and the demo is where someone can see
    that it does not make it.

    Returns a short description, which the seeder prints. A database with no demo
    rooms still gets nothing, and says so rather than inventing rooms to hang
    events on.
    """
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    now = context["now"]
    health = TrendHealth(RecordStore(db))

    if not rooms:
        return "0 engagement events (no rooms to scope them to)"

    seeded: list[str] = []
    for plan in DEMO_PLAN:
        index = int(plan["room"])
        if index >= len(rooms):
            continue
        room_id, account = rooms[index]
        for days_ago, event_type, audience in plan["events"]:
            record = health.record_event(
                {
                    "type": event_type,
                    "occurred_at": (now - timedelta(days=days_ago)).isoformat(timespec="seconds"),
                    "audience": audience,
                    "person": f"buyer@{account.split()[0].lower()}.example",
                },
                room_id=room_id,
                actor="system",
                source="seed",
                now=now,
            )
            seeded.append(record["id"])

    return (
        f"{len(seeded)} engagement events across {len(DEMO_PLAN)} rooms: "
        + ", ".join(f"{plan['state']} ({plan['summary']})" for plan in DEMO_PLAN)
        + f". Collection {ENGAGEMENT_COLLECTION}."
    )
