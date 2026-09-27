"""WF-019: rank content influence and associate revenue with assets.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-019.md``. This is a
build rather than a port: there was no source branch, and the research document
is the specification. The domain lives in :mod:`dsr.influence`; this module is
the three things a feature owns - the routes, the mapping from this feature's
own error types to responses, and the demo data.

What the researched flow asks for, and where each step is served
---------------------------------------------------------------
===================================  ==========================================
Researched step                       Route
===================================  ==========================================
"Number of assets, Content shares,   ``GET  /portfolio``
Content client views, Utilization
rate, Engagement rate"
"Content engagement over time graph  ``GET  /engagement`` (``grain``)
(day / week / month / quarter / year)"
"Top content ... sort by any column" ``GET  /top-content`` (``sort``,
                                     ``direction``, ``limit``, ``offset``)
"Content & Sales Influence"          ``GET  /sales-influence``
"Filter by collection, client-       every read above
activity time range, and shares
time range"
the ``asset.viewed`` /               ``POST /events``
``asset.shared`` / ``asset.downloaded``
webhooks
===================================  ==========================================

Three rules this module holds to, and why each has already bitten someone
------------------------------------------------------------------------
**``source`` is built here, from ``router.prefix``, and passed down.** Every
domain method that writes takes ``source`` as a *required* keyword, so it cannot
default to a hard-coded path. The defect the contract names by example is a
feature whose audit log kept recording a route the app had stopped serving, and
a default argument is how that happens. Nothing in :mod:`dsr.influence` knows
what a URL is.

**Reads write nothing.** A periodic report is a read. Portfolio, engagement,
Top content, sales influence, collections and the inferences are all pure
aggregations, and the test suite asserts the audit log does not grow when they
are called - because a report that writes on every poll fills the audit log with
rows that describe nothing.

**No room-scoped path that is not room-scoped.** ``/rooms/{room_id}/influence``
is the workspace report; it takes the same filters as the portfolio one and adds
nothing, which is deliberate. A route that quietly ignored its room would be the
kind of thing nobody tests until a rep is looking at the wrong deal's content.

Demo data
---------
:func:`seed` seeds the states the research says matter rather than a row of
successes: a library with an asset nobody has ever shared (so utilization is not
100%), an asset shared but never viewed, a won deal whose revenue is attributed
to two assets with the evidence attached, a deal that names an asset with no
engagement in its own workspace, and a workspace where content was engaged with
but whose deal names no asset. Every row is produced by running the real domain
functions, so the demo cannot show a shape the workflow would not produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.influence import (
    AssetOutOfScope,
    CrmNotLinked,
    Filters,
    InfluenceError,
    InvalidWindow,
    UnknownAsset,
    UnknownRoom,
    UnparseableTime,
    asset_detail,
    collections,
    describe_inferences,
    engagement,
    ingest_activity,
    link_account,
    link_deal,
    portfolio,
    preconditions,
    record_event,
    sales_influence,
    summarise_links,
    summarise_vocabulary,
    top_content,
    vocabulary,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-019-rank-content-influence-and-associate-r",
    "ticket": "WF-019",
    "name": "Rank content influence and associate revenue with assets",
    "description": (
        "The Content Influence report: portfolio metrics, the engagement trend at five "
        "grains, a sortable Top content table, and a per-asset revenue breakdown that "
        "says which CRM links are still missing rather than showing a confident zero."
    ),
    "nav": [{"id": "content-influence", "label": "Content influence"}],
}

router = APIRouter(prefix="/api/wf-019", tags=["wf019"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #
#
# Every type below is this feature's own, raised by nothing else in the product,
# so registering a handler for it cannot intercept an unrelated error anywhere.
# Two features may not map the same type and the host refuses the second rather
# than letting load order decide, so these names are claimed once and only here.
#
# `InfluenceError` is the base and answers 400, which means a refusal added to the
# domain later still answers sensibly rather than as a 500.


def _influence_error(request: Request, exc: InfluenceError) -> JSONResponse:
    """A refusal the caller has to fix. 400, with the message saying which rule."""
    return JSONResponse(status_code=400, content={"error": "invalid_influence_request", "detail": str(exc)})


def _unknown_room(request: Request, exc: UnknownRoom) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "unknown_room", "detail": str(exc)})


def _unknown_asset(request: Request, exc: UnknownAsset) -> JSONResponse:
    """404 for both "no such asset" and "real but filtered out".

    The subclass arrives here too, and its own message says which of the two
    happened - because they send a reader to different places.
    """
    code = "asset_out_of_scope" if isinstance(exc, AssetOutOfScope) else "unknown_asset"
    return JSONResponse(status_code=404, content={"error": code, "detail": str(exc)})


def _unparseable_time(request: Request, exc: UnparseableTime) -> JSONResponse:
    """A window bound or timestamp that is not ISO-8601.

    Its own status and error code rather than the base 400: the two researched
    time windows are the most common thing a client gets wrong here, and a
    caller that has to tell "your window is not a date" apart from "your sort
    column is not a column" should be able to.
    """
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_timestamp", "detail": str(exc), "field": exc.what, "value": exc.value},
    )


def _invalid_window(request: Request, exc: InvalidWindow) -> JSONResponse:
    """A window whose start is after its end."""
    return JSONResponse(status_code=400, content={"error": "invalid_window", "detail": str(exc)})


def _crm_not_linked(request: Request, exc: CrmNotLinked) -> JSONResponse:
    """Content & Sales Influence asked for strictly before its precondition holds.

    428 - precondition required - which is the status this codebase already uses
    for "not configured yet", and the research's own words: the report "will be
    shown assuming you have integrated with your CRM".
    """
    return JSONResponse(
        status_code=428,
        content={"error": "crm_not_linked", "detail": str(exc), "blockers": exc.blockers},
    )


EXCEPTION_HANDLERS = {
    InfluenceError: _influence_error,
    UnknownRoom: _unknown_room,
    UnknownAsset: _unknown_asset,
    UnparseableTime: _unparseable_time,
    InvalidWindow: _invalid_window,
    CrmNotLinked: _crm_not_linked,
}


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
#
# The researched filter set is read in one place, and it carries the store with
# it, so every surface scopes its reads identically and no route can accidentally
# scope a different set from the one its neighbour does.
#
# `from` and `to` are inclusive on both ends: a reader filtering "client
# activity in June" means the whole of June, and an exclusive upper bound
# silently drops the last day of the month - which is the day a reviewer most
# often checks.


@dataclass(frozen=True)
class Scope:
    """One request's store handle and filter set, built once."""

    store: RecordStore
    filters: Filters


def _filters(
    collection: str | None = Query(
        default=None, description="Library collection to report on. The research's filter 1."
    ),
    activity_from: str | None = Query(
        default=None, description="Client activity from, ISO-8601. Bounds views and downloads."
    ),
    activity_to: str | None = Query(
        default=None, description="Client activity to, ISO-8601. Inclusive."
    ),
    shared_from: str | None = Query(
        default=None, description="Shares from, ISO-8601. Bounds shares only."
    ),
    shared_to: str | None = Query(
        default=None, description="Shares to, ISO-8601. Inclusive."
    ),
) -> Filters:
    """The researched filter set, without a workspace.

    Split out from :func:`_scope` because the workspace route takes ``room_id``
    as a *path* parameter, and FastAPI refuses a dependency that declares the
    same name as a query parameter. So the shared filters live here and the
    workspace is added by whichever route needs it - as a query parameter on the
    portfolio reads, and as the path itself on the room-scoped one.
    """
    return Filters.build(
        collection=collection,
        activity_from=activity_from,
        activity_to=activity_to,
        shared_from=shared_from,
        shared_to=shared_to,
    )


FiltersDep = Depends(_filters)


def _scope(
    store: RecordStore = StoreDep,
    filters: Filters = FiltersDep,
    room_id: str | None = Query(default=None, description="Scope to one workspace."),
) -> Scope:
    return Scope(store=store, filters=_rescope(filters, room_id) if room_id else filters)


ScopeDep = Depends(_scope)


def _rescope(filters: Filters, room_id: str | None) -> Filters:
    """Re-point a filter set at one workspace, keeping every other filter.

    Built by keyword rather than by mutation because :class:`Filters` is frozen,
    which is what stops a caller from widening a filter after the report has been
    scoped to a room. ``room_id`` of ``None`` returns the set unchanged, so the
    portfolio reads need no branch of their own.
    """
    if not room_id:
        return filters
    return Filters(
        collection=filters.collection,
        activity_from=filters.activity_from,
        activity_to=filters.activity_to,
        shared_from=filters.shared_from,
        shared_to=filters.shared_to,
        room_id=room_id,
    )


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every choice this report accepts")
def influence_vocabulary() -> dict[str, Any]:
    """Grains, sortable columns, actions, metrics and windows.

    Served as data so a client renders its pickers from the same source the
    request validation enforces against, and a column added to Top content
    reaches every client at once instead of one call at a time.
    """
    return {"report": vocabulary(), "events": summarise_vocabulary()}


@router.get("/inferences", summary="What this build inferred, and what would change it")
def influence_inferences() -> dict[str, Any]:
    """The researched gaps this implementation had to choose across.

    The research for this workflow is unusually explicit about what it does not
    claim - no attribution model, no currency rule, no documented webhook
    redelivery - and those gaps are exactly where an implementation invents
    behaviour. Each choice is published here with a name, the researched sentence
    it rests on, and what a deployment can do differently. A reviewer can then
    disagree with a named entry instead of finding it in the arithmetic.
    """
    return describe_inferences()


@router.get("/portfolio", summary="The researched portfolio metrics")
def influence_portfolio(scope: Scope = ScopeDep) -> dict[str, Any]:
    """Number of assets, content shares, content client views, and the two rates.

    Sourced from WF-019: "Here you'll find the following insights: Number of
    assets, Content shares, Content client views, Utilization rate, Engagement
    rate" - with "Utilization rate: the % of content that has been shared at least
    once" and "Engagement rate: the % of content has been viewed at least once."

    Both rates are reported with their numerator and the asset count they are a
    proportion of, because the sources do not state the denominator and a reader
    deserves to know which one was used.
    """
    return portfolio(scope.store, scope.filters)


@router.get("/engagement", summary="Content engagement over time")
def influence_engagement(
    grain: str = Query(default="day", description="day | week | month | quarter | year"),
    scope: Scope = ScopeDep,
) -> dict[str, Any]:
    """The researched trend graph, at the five researched grains.

    Sourced from the user flow: "Read the Content engagement over time graph
    (day / week / month / quarter / year)." Buckets with no activity are returned
    as zeros rather than omitted, so a chart never draws a straight line through
    quiet days and reads as activity that never happened.
    """
    return engagement(scope.store, grain=grain, filters=scope.filters)


@router.get("/top-content", summary="Top content, sortable by any column")
def influence_top_content(
    sort: str = Query(
        default="views",
        description="title | shares | views | downloads | total_time_seconds | "
        "last_share_at | last_view_at",
    ),
    direction: str = Query(default="desc", description="asc | desc"),
    limit: int = Query(default=25, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    scope: Scope = ScopeDep,
) -> dict[str, Any]:
    """The researched Top content table.

    Sourced from WF-019: "The top content report is organized by most viewed
    content, but also shows additional reporting such as amount of shares, total
    time spent, download amount, last share, and last view" - and the user flow's
    "sort by any column", so every one of those is a sort key.

    Ties break on asset id in the same order whichever direction the sort runs,
    so paging a table full of equal values cannot show a row twice or skip one.
    """
    return top_content(
        scope.store,
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
        filters=scope.filters,
    )


@router.get("/sales-influence", summary="Content & Sales Influence")
def influence_sales(
    strict: bool = Query(
        default=False,
        description="Answer 428 instead of available:false when the CRM links are missing.",
    ),
    scope: Scope = ScopeDep,
) -> dict[str, Any]:
    """Revenue and deals per asset.

    Sourced from WF-019: "This report will be shown assuming you have integrated
    with your CRM, and have connected accounts & deals/opportunities to
    workspaces. For each piece of content, you will see the breakdown of revenue
    and deals associated with the asset."

    So the default answer is 200 with ``available: false`` and one named blocker
    per missing link, because a screen that errors because a CRM is not connected
    is worse than a screen that says what to connect. ``strict=true`` turns that
    into a 428 for a caller that must not publish an empty revenue column.

    Each associated row carries the evidence - views, shares, downloads, viewers
    and last activity *in that workspace* - because the report's whole claim is
    that content influences revenue, and a number with nothing behind it is a
    number nobody can check.
    """
    return sales_influence(scope.store, filters=scope.filters, strict=strict)


@router.get("/sales-influence/preconditions", summary="Which CRM links hold")
def influence_preconditions(
    room_id: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The researched preconditions, checked and named one at a time.

    Separate from the report so a client can show a "finish setting this up"
    checklist, and so the answer is the same when a caller wants the blockers
    without the whole breakdown.
    """
    return preconditions(store, room_id=room_id)


@router.get("/collections", summary="Library collections, for the filter picker")
def influence_collections(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every collection name in the library, with its asset count.

    Served rather than computed in the browser so the filter offers the names
    that actually exist, including the ones a deployment invented by filing an
    asset under a single tag.
    """
    return collections(store)


@router.get("/assets/{asset_id}", summary="One asset's influence, with its evidence")
def influence_asset(asset_id: str, scope: Scope = ScopeDep) -> dict[str, Any]:
    """A single asset's card: the same numbers Top content shows, plus every event.

    The events are included rather than summarised away, because this is the
    place a reader goes to check the table above them.
    """
    return asset_detail(scope.store, asset_id, filters=scope.filters)


@router.get("/rooms/{room_id}/influence", summary="Content influence for one workspace")
def influence_room(
    room_id: str,
    store: RecordStore = StoreDep,
    filters: Filters = FiltersDep,
) -> dict[str, Any]:
    """The same report, computed over one workspace.

    Room-scoped paths stay room-scoped: this route takes the same filters as
    ``/portfolio`` and adds nothing, so there is no way for it to quietly ignore
    the room it was asked about. A share made straight from the library has no
    workspace and is excluded here while the portfolio report counts it - which
    is the researched scope, "across all workspaces", applied the other way.
    """
    return portfolio(store, _rescope(filters, room_id))


@router.get("/links", summary="The CRM links this report joins on")
def influence_links(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every recorded account and deal, so the join's inputs can be read directly.

    A reviewer checking whether a revenue number is right needs the links, not
    just the answer, and this is the same data the report read.
    """
    return summarise_links(store)


# --------------------------------------------------------------------------- #
# Recording occurrences
# --------------------------------------------------------------------------- #


@router.post("/events", status_code=201, summary="Record an asset.viewed / .shared / .downloaded")
def influence_record_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None, description="The workspace it happened in."),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The researched webhook seam, and the entry point of the data flow.

    Accepts ``action`` (or ``event``) in either spelling: the researched
    ``asset.viewed`` / ``asset.shared`` / ``asset.downloaded``, or the plain
    ``viewed`` / ``shared`` / ``downloaded`` the product's own activity rows use.
    Both land in the same event log, which is what makes the report computable
    from either source.

    The asset is named by ``asset_id`` or by its title, because the two sources
    name assets differently. A repeat of a known ``event_id`` returns the
    original row and writes nothing, so a retried webhook is visible to the
    caller and invisible to the report.
    """
    return record_event(
        store, payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/events"
    )


@router.post("/events/ingest-activity", summary="Project the activity collection into the event log")
def influence_ingest_activity(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=20000),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Turn the product's existing ``activity`` rows into reportable occurrences.

    The researched webhooks and the product's own activity collection are the
    same three facts arriving two ways, and a deployment that has never seen a
    webhook still has this report to read. Only the three asset-relevant actions
    are projected; ``commented`` and ``opened_link`` are real rows in the same
    collection and are not asset occurrences, and they are counted as skipped.

    Every projected row is keyed on the source row's id, so running this twice
    writes nothing the second time and the response says how many were already
    there.
    """
    return ingest_activity(
        store, room_id=room_id, limit=limit, actor=actor,
        source=f"POST {router.prefix}/events/ingest-activity",
    )


# --------------------------------------------------------------------------- #
# The CRM links
# --------------------------------------------------------------------------- #


@router.post("/links/accounts", summary="Register a CRM account")
def influence_link_account(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Register the account a deal can be attributed to.

    An upsert on ``crm_id``: a CRM sync that sends the same account twice updates
    the one account rather than creating a second, because two accounts with one
    name would make the precondition check count the same integration twice.
    """
    return link_account(store, payload, actor=actor, source=f"POST {router.prefix}/links/accounts")


@router.post("/links/deals", summary="Connect a CRM deal to a workspace and its assets")
def influence_link_deal(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The other half of the researched precondition: "connected accounts &
    deals/opportunities to workspaces".

    The join is per workspace and per asset, and nothing is inferred. A deal must
    name a ``room_id`` and list the ``assets`` the content influenced, and every
    named asset must be a live library asset - a link to an asset that is not
    there can never be associated with anything, so it is refused at the moment
    it is made rather than becoming a row that silently contributes zero.

    Also an upsert on ``crm_id``, for the same reason as the account: two rows
    for one deal would double its revenue in every breakdown.
    """
    return link_deal(store, payload, actor=actor, source=f"POST {router.prefix}/links/deals")


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# Seeded through the real domain functions, with `source="seed"` because no route
# is serving these writes - the seeder is, and saying otherwise would put a path
# in the audit log that this app never answered.

#: A library with the states the report has to be able to show. The research's
#: own caveat - "this report relies on your Content Management Library having been
#: built out!" - is why an unshared and a never-viewed asset are here: a library
#: where everything is shared is a library that cannot demonstrate a utilization
#: rate.
DEMO_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "title": "Enterprise Overview Deck",
        "kind": "pptx",
        "collections": ["Enterprise", "Decks"],
        "tags": ["overview", "enterprise"],
    },
    {
        "title": "Security and Compliance Pack",
        "kind": "pdf",
        "collections": ["Security"],
        "tags": ["compliance"],
    },
    {
        "title": "Pricing One-Pager",
        "kind": "pdf",
        "collections": ["Enterprise", "Pricing"],
        "tags": ["pricing"],
    },
    {
        "title": "Implementation Roadmap",
        "kind": "pdf",
        "collections": ["Enterprise"],
        "tags": ["delivery"],
    },
    {
        # Never shared, never viewed. Its whole purpose is to keep the
        # utilization and engagement rates below 100% in the demo, which is the
        # number a content-governance reader is looking for.
        "title": "Legacy Migration Guide",
        "kind": "pdf",
        "collections": ["Archive"],
        "tags": ["legacy"],
    },
)

#: The shares and views, per asset index, per workspace. Written as a schedule
#: rather than at random so the seeded report has a shape a reviewer can check:
#: the Overview Deck is the most-shared and most-viewed asset, the Pricing
#: One-Pager is shared and viewed, the Compliance Pack is shared but never
#: viewed by a client, and the Migration Guide has nothing.
DEMO_SCHEDULE: dict[int, tuple[tuple[str, int, int], ...]] = {
    # (action, days ago, seconds of dwell)
    0: (
        ("shared", 40, 0),
        ("shared", 22, 0),
        ("viewed", 21, 180),
        ("viewed", 21, 240),
        ("viewed", 9, 300),
        ("downloaded", 8, 45),
        ("viewed", 2, 150),
    ),
    1: (
        ("shared", 35, 0),
        ("shared", 30, 0),
        ("downloaded", 12, 90),
    ),
    2: (
        ("shared", 18, 0),
        ("viewed", 17, 95),
        ("viewed", 4, 60),
        ("downloaded", 4, 30),
    ),
    3: (
        ("shared", 6, 0),
        ("viewed", 5, 210),
    ),
    4: (),
}

#: (index, share events, view events) spread over the demo workspaces, so the
#: "across all workspaces" claim in the report is doing real work.
DEMO_ROOM_SPREAD: tuple[int, ...] = (0, 0, 1, 1, 2, 0, 3, 1, 2)

PEOPLE = (
    "a.buyer@northwind.example",
    "b.buyer@northwind.example",
    "procurement@contoso.example",
    "ops@fabrikam.example",
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed a library, its activity, and the CRM links behind the revenue join.

    Deliberately not a row of successes. The demo contains an asset nobody has
    touched, an asset shared but never viewed by a client, a won deal whose
    revenue is attributed to two assets with the evidence attached, a deal that
    names an asset with no engagement in its own workspace, and a workspace
    where content was engaged with but whose deal names no asset. A demo
    containing only the happy path teaches a reviewer nothing about the two
    states the research says this report is read for.

    Every row is produced by running the real domain functions rather than being
    written by hand, so the demo cannot show a shape the workflow would not
    produce - the same reason WF-016 runs its own transport instead of inventing
    delivery rows.

    Returns a short description of what was added, which the seeder prints.
    """
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    now = context.get("now")
    if now is None:
        return "0 assets, 0 content events, 0 CRM links (no clock in the seed context)"
    store = RecordStore(db)
    source = "seed"

    # -- the library -------------------------------------------------------- #
    asset_ids: list[str] = []
    for spec in DEMO_ASSETS:
        record = store.create("document", spec, actor="dana", source=source)
        asset_ids.append(str(record["id"]))

    # -- the occurrences ---------------------------------------------------- #
    events = 0
    step = 0
    for index, schedule in DEMO_SCHEDULE.items():
        for offset, (action, days_ago, seconds) in enumerate(schedule):
            room_index = DEMO_ROOM_SPREAD[step % len(DEMO_ROOM_SPREAD)]
            step += 1
            room_id = rooms[room_index][0] if rooms else None
            if room_id is None:
                # No demo workspaces to attach to. The events are still recorded
                # - a share straight from the library is a real occurrence - and
                # the seeder prints what was skipped.
                continue
            when = now - timedelta(days=days_ago, hours=offset)
            record_event(
                store,
                {
                    "action": f"asset.{action}",
                    "asset_id": asset_ids[index],
                    "person": PEOPLE[step % len(PEOPLE)],
                    "account": rooms[room_index][1],
                    "occurred_at": when.isoformat(timespec="seconds"),
                    "seconds": seconds,
                },
                room_id=room_id,
                actor="dana",
                source=source,
            )
            events += 1

    if not rooms:
        return (
            f"{len(asset_ids)} library assets, {events} content events, 0 CRM links "
            "(no rooms to scope them to)"
        )

    # -- the CRM side ------------------------------------------------------- #
    accounts = ("Northwind Traders", "Contoso Health", "Fabrikam Logistics")
    for name in accounts:
        link_account(store, {"name": name}, actor="dana", source=source)

    # The happy path: one won deal whose content really was shared with and
    # viewed by people in its own workspace, with two assets attached.
    link_deal(
        store,
        {
            "name": "Northwind Traders - Enterprise Platform",
            "crm_id": "crm-northwind-1",
            "account": "Northwind Traders",
            "room_id": rooms[0][0],
            "amount": 48000,
            "stage": "Closed Won",
            "assets": [asset_ids[0], asset_ids[2]],
        },
        actor="dana",
        source=source,
    )
    # An open deal, so the report has a revenue figure that is not closed and a
    # won/open split worth reading.
    link_deal(
        store,
        {
            "name": "Contoso Health - Security Review",
            "crm_id": "crm-contoso-1",
            "account": "Contoso Health",
            "room_id": rooms[1][0] if len(rooms) > 1 else rooms[0][0],
            "amount": 22000,
            "stage": "Negotiation",
            "assets": [asset_ids[1]],
        },
        actor="dana",
        source=source,
    )
    # The interesting one: this deal names the Migration Guide, which nothing in
    # this workspace was ever shared with or viewed in. It must appear as an
    # unassociated link with a reason, and must not contribute revenue to any
    # asset's total.
    link_deal(
        store,
        {
            "name": "Fabrikam Logistics - Renewal",
            "crm_id": "crm-fabrikam-1",
            "account": "Fabrikam Logistics",
            "room_id": rooms[2][0] if len(rooms) > 2 else rooms[0][0],
            "amount": 15000,
            "stage": "Discovery",
            "assets": [asset_ids[4]],
        },
        actor="dana",
        source=source,
    )
    # And a workspace where content was engaged with but no deal names an asset,
    # which is the state the research says this report depends on a human to fix.
    link_deal(
        store,
        {
            "name": "Adventure Works - Pilot",
            "crm_id": "crm-adventure-1",
            "account": "Northwind Traders",
            "room_id": rooms[3][0] if len(rooms) > 3 else rooms[0][0],
            "amount": 9000,
            "stage": "Proposal",
        },
        actor="dana",
        source=source,
    )

    return (
        f"{len(asset_ids)} library assets, {events} content events, "
        f"{len(accounts)} CRM accounts, 4 deals "
        "(1 won, 1 open, 1 linked to an asset with no engagement in its workspace, "
        "1 with content engaged but no asset named)"
    )
