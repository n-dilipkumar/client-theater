"""WF-075: review who engaged, where and for how long.

A build from a researched specification, not a port. There was no source branch. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-075.md``, quoted in
full in issue 150. The rules live in :mod:`dsr.security_governance` and are not restated
here. This module is the three things a feature contributes and the three things it
must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only
  and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

This workflow reads view events, and it says so
-----------------------------------------------

The specification's data flow says "Every view writes a ``View`` row" and "updates a
``Visitor`` row". This ticket is not that producer. It presents them, and the two write
routes below are the only writes it owns: they exist so a demo and a test have rows to
read, and each one records ``source`` as the ingest path rather than as a route, because
a row claiming a route served it would be the lie the audit-source rule exists to prevent.

Every other route here reads, takes no source, and writes no audit row. A route that
recorded a row on every read would fill this product's own guarantee - the audit log -
with entries describing no change having been made.

The wording this workflow is not allowed to use
-----------------------------------------------

The specification marks the geolocation provider as an inference and says so itself:
"viewer IP -> geolocation provider ``[inferred - the API returns location.country/city
but names no vendor]``". So no vendor name appears anywhere in this feature, and
:data:`GEOGRAPHY_SOURCE` says where the numbers came from in every response that
carries them.

``verified`` means proven
-------------------------

The user flow reads the flag "to confirm the identity was actually proven (not merely
typed in)". So a visitor row reports one of three words rather than a boolean, and
``unknown`` means no proof was recorded. A boolean that defaulted to true on a typed
address would answer the question the page asks wrongly on most rows.

The status codes here are the researched ones
---------------------------------------------

There are none, because the specification documents no errors for this workflow. So
every status follows the product's own conventions: 400 for a bound or a filter this
workflow will not accept, 404 for a visitor or a view that does not exist. The
distinction is recorded rather than invented: an unparseable ``since`` is a bad
request, because the caller sent something this build cannot read.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.security_governance import (
    engagement as vocab,
    engagement_inferences as inferences,
    engagement_rules as rules,
)
from dsr.security_governance.engagement_engine import EngagementEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-075-review-who-engaged-where-and-for-how-long",
    "ticket": "WF-075",
    "name": "Review who engaged, where and for how long",
    "description": (
        "Read the viewer's list, the room's aggregate with time bounds, and one view's "
        "page-by-page dwell with the viewer's location and client. Every timestamp is "
        "Unix milliseconds and the aggregate is cached, because polling is the "
        "documented integration path until webhooks ship."
    ),
    "nav": [{"id": "wf-075-engagement-review", "label": "Engagement review"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-075", tags=["WF-075"])

#: Where the location numbers came from, in the specification's own words. It marks
#: the geolocation provider as an inference and names no vendor, so this feature names
#: none either. Every response carrying a country or a city carries this beside it.
GEOGRAPHY_SOURCE = (
    "viewer IP to geolocation provider [inferred - the API returns location.country/city "
    "but names no vendor]"
)


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a
    domain function hardcoding a URL string, which leaves the audit log naming a route
    the app stopped serving. ``tests/test_wf075_http.py`` asserts every source this
    router can record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> EngagementEngine:
    """An :class:`EngagementEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store and two clocks, so building it
    here leaves both overridable in a test instead of hanging a long-lived object off
    ``app.state``, which is a shared file this feature may not edit.
    """

    return EngagementEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All three types are declared in dsr.security_governance.engagement_rules and raised
# by nothing else in the product. That is what makes it safe to map them here: the host
# refuses a second feature registering a handler for the same type, and a handler for
# ValueError would intercept that exception across the whole product.


def _engagement_error(request: Request, exc: rules.EngagementError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_engagement_query",
            "detail": str(exc),
            "errors": exc.errors,
            vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
        },
    )


def _visitor_not_found(request: Request, exc: rules.VisitorNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such visitor."},
    )


def _view_not_found(request: Request, exc: rules.ViewNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such view."},
    )


EXCEPTION_HANDLERS = {
    rules.EngagementError: _engagement_error,
    rules.VisitorNotFound: _visitor_not_found,
    rules.ViewNotFound: _view_not_found,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    dataroom_id: str | None = Query(None), engine: EngagementEngine = EngineDep
) -> dict[str, Any]:
    """The board's headline numbers. Reads only.

    Carries the two counts apart: ``visitors`` is the persistent rows and ``views`` is
    the events. They are separate numbers because the specification says they are - "a
    viewer who hits two links in the same dataroom shows up once here, but twice in
    ``papermark views list``" - and a board that derived one from the other would answer
    a question the rep did not ask.
    """
    return engine.summary(dataroom_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the board cannot drift from the
    rules that compute it: the two record shapes the evidence quotes verbatim, the view
    and download types, the three verification states and the page size all come from
    the same tables the engine reads.
    """
    return {
        "view_analytics_fields": list(vocab.VIEW_ANALYTICS_FIELDS),
        "visitor_fields": list(vocab.VISITOR_FIELDS),
        "view_fields": list(vocab.VIEW_FIELDS),
        "location_fields": list(vocab.LOCATION_FIELDS),
        "client_fields": list(vocab.CLIENT_FIELDS),
        "page_duration_fields": list(vocab.PAGE_DURATION_FIELDS),
        "view_types": list(vocab.VIEW_TYPES),
        "download_types": list(vocab.DOWNLOAD_TYPES),
        "no_download": vocab.NO_DOWNLOAD,
        "verification_states": list(vocab.VERIFICATION_STATES),
        "default_verified": vocab.DEFAULT_VERIFIED,
        "page_size": {"default": vocab.DEFAULT_PAGE_SIZE, "max": vocab.MAX_PAGE_SIZE},
        "cache_seconds": vocab.DEFAULT_CACHE_SECONDS,
        "collections": list(vocab.ALL_COLLECTIONS),
        vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
        "geography_source": GEOGRAPHY_SOURCE,
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification says an implementer "must derive it and record the derivation,
    not assume it". This route is that record, served rather than buried in a docstring
    so a reviewer reads the decision instead of the code.
    """
    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/decisions/{inference_id}")
def read_decision(inference_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""
    decision = inferences.describe_one(inference_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# The viewers list
# --------------------------------------------------------------------------- #


@router.get("/visitors")
def list_visitors(
    email: str | None = Query(None),
    dataroom_id: str | None = Query(None),
    engine: EngagementEngine = EngineDep,
) -> dict[str, Any]:
    """One row per buyer email, with First Seen and Last Seen.

    The specification's first user-flow step. ``email`` filters to a single address,
    which is the other half of that step: "or filters to a single address".
    """
    rows = engine.visitors(email, dataroom_id)
    return {
        "count": len(rows),
        "visitors": rows,
        "verification_states": list(vocab.VERIFICATION_STATES),
        vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
    }


@router.get("/visitors/{visitor_id}")
def read_visitor(visitor_id: str, engine: EngagementEngine = EngineDep) -> dict[str, Any]:
    """One visitor, with the proof state. ``GET /v1/visitors/{id}``.

    Carries ``verification`` as one of three words rather than a boolean, because the
    specification asks the page to "confirm the identity was actually proven (not merely
    typed in)" and a boolean cannot say that for a row where no proof was recorded.
    """
    return engine.read_visitor(visitor_id)


@router.get("/visitors/{visitor_id}/views")
def list_visitor_views(
    visitor_id: str, limit: int | None = Query(None), engine: EngagementEngine = EngineDep
) -> dict[str, Any]:
    """That visitor's view history, newest first. ``GET /v1/visitors/{id}/views``.

    Read-only, so no audit row: drilling into one buyer's history changes nothing.
    """
    rows = engine.visitor_views(visitor_id, limit)
    return {
        "visitor_id": visitor_id,
        "count": len(rows),
        "views": rows,
        vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
    }


# --------------------------------------------------------------------------- #
# The aggregate
# --------------------------------------------------------------------------- #
#
# ``since`` and ``until`` are declared ``str | None`` rather than ``int | None``, and
# that is deliberate. A query parameter is text on the wire, and the specification
# spells the bounds as command-line values - ``stats --since --until`` in Unix ms -
# which arrive as strings. Declaring them ``int`` would move the validation into
# FastAPI, which answers an unparseable bound with a 422 whose message names the
# parameter and says nothing about the unit. Declaring them text hands the value to
# ``rules.coerce_ms``, the one conversion function in the workflow, so an unparseable
# bound is a 400 that names the field and states the unit the caller should use.
#
# The route signatures are the only place this matters, and every one of them takes
# the same pair.


@router.get("/analytics/datarooms/{dataroom_id}")
def dataroom_stats(
    dataroom_id: str,
    since: str | None = Query(None),
    until: str | None = Query(None),
    engine: EngagementEngine = EngineDep,
) -> dict[str, Any]:
    """The room's aggregate, with ``since`` and ``until`` in Unix ms.

    ``GET /v1/analytics/datarooms/{id}``. The cached path the specification describes:
    "cheap (cached aggregates)" and "Cache the response if you're polling".

    Every response carries ``cached`` and ``computed_at``, so a poller can tell a fresh
    answer from a cached one. A poller cannot poll honestly against a response that does
    not say which it got.
    """
    return engine.stats(dataroom_id, since=since, until=until)


@router.get("/analytics/links/{link_id}")
def link_stats(
    link_id: str,
    since: str | None = Query(None),
    until: str | None = Query(None),
    engine: EngagementEngine = EngineDep,
) -> dict[str, Any]:
    """One link's aggregate. ``GET /v1/analytics/links/{id}``.

    Shares the engine's cache key shape, so a link's board and a room's board cannot
    read each other's numbers.
    """
    rows = engine.link_views(link_id, since=since, until=until)
    bounds = rules.window(since, until)
    return {
        "link_id": link_id,
        vocab.TOTAL_VIEWS_FIELD: len(rows),
        vocab.UNIQUE_VISITORS_FIELD: len(rules.unique_visitors(rows)),
        vocab.TIME_SPENT_SECONDS_FIELD: sum(rules.total_duration(row) for row in rows),
        vocab.PER_PAGE_FIELD: engine.per_page(rows),
        "since": bounds["since"],
        "until": bounds["until"],
        vocab.COMPUTED_AT: engine.computed_at_ms(),
        vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
        vocab.CACHED_FIELD: False,
        "rate_limit_note": (
            "Analytics carry a tighter per-minute rate limit than the rest of the "
            "surface. Poll this endpoint rather than recomputing it per viewer."
        ),
    }


@router.get("/analytics/views/{view_id}")
def read_view(view_id: str, engine: EngagementEngine = EngineDep) -> dict[str, Any]:
    """One view's full breakdown. ``GET /v1/analytics/views/{id}``.

    The specification's third user-flow step: page dwell times plus the viewer's
    country and city and browser, OS and device.

    The response carries ``geography_source`` because the specification marks the
    geolocation provider as an inference. Naming a vendor would be a claim the research
    does not support.
    """
    payload = engine.read_view(view_id)
    payload["geography_source"] = GEOGRAPHY_SOURCE
    return payload


# --------------------------------------------------------------------------- #
# The per-link view list
# --------------------------------------------------------------------------- #


@router.get("/links/{link_id}/views")
def list_link_views(
    link_id: str,
    limit: int | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    engine: EngagementEngine = EngineDep,
) -> dict[str, Any]:
    """Every view of one link, newest first. ``GET /v1/links/{id}/views``.

    The specification's fourth user-flow step. Anonymous views stay in this list, and
    that is the requirement rather than an accident: views "never tied to a ``Visitor``
    record are still reachable per-link via ``GET /v1/links/{id}/views``".
    """
    rows = engine.link_views(link_id, limit, since=since, until=until)
    return {
        "link_id": link_id,
        "count": len(rows),
        "views": rows,
        "anonymous": sum(1 for row in rows if row.get("anonymous")),
        vocab.TIME_UNIT_FIELD: vocab.TIME_UNIT_VALUE,
    }


# --------------------------------------------------------------------------- #
# The write path this workflow owns
# --------------------------------------------------------------------------- #


@router.post("/visitors", status_code=201)
def create_visitor(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: EngagementEngine = EngineDep,
) -> dict[str, Any]:
    """Record one persistent visitor row.

    This workflow is a read path over view events, but a viewers list with nothing in
    it cannot be reviewed, so the row is written through the engine and lands in the
    audit log the way every other write in this product does.

    ``verified`` is optional on purpose. A row written without it reads as ``unknown``,
    which is a distinct and truthful answer from ``unverified``: it says no proof was
    recorded rather than that proof was refused.
    """
    body = dict(payload or {})
    return engine.ingest_visitor(
        body.get("email") or "",
        dataroom_id=body.get("dataroom_id") or room_id,
        verified=body.get("verified"),
        invited_at=body.get("invited_at"),
        total_views=body.get("total_views"),
        last_viewed_at=body.get("last_viewed_at"),
        room_id=room_id,
    )


@router.post("/links/{link_id}/views", status_code=201)
def create_view(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    engine: EngagementEngine = EngineDep,
) -> dict[str, Any]:
    """Record one view event against one link.

    ``viewer_email`` is optional and that is the specification's rule, not a
    convenience: anonymous views are real, reachable view events. The write lands in the
    audit log through the engine, in the same transaction as the row.
    """
    body = dict(payload or {})
    return engine.ingest_view(
        link_id,
        viewer_email=body.get("viewer_email"),
        view_type=body.get("view_type"),
        viewed_at=body.get("viewed_at"),
        downloaded_at=body.get("downloaded_at"),
        download_type=body.get("download_type"),
        page_durations=body.get("page_durations"),
        location=body.get("location"),
        client=body.get("client"),
        document_id=body.get("document_id"),
        room_id=room_id,
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification's user flow describes, not just the happy path.

    Every row is produced by calling the real :class:`EngagementEngine`, so the demo
    cannot show a shape, a count or an audit row the HTTP routes would not produce.

    The states seeded, and why each is here:

    * a **verified** visitor and an **unverified** one, so the flag the specification
      asks a rep to read has both answers on the board;
    * a visitor written **without** the flag at all, so the third state - no proof
      recorded - is visible rather than merely documented;
    * a buyer who hit **two links in the same room**, which is the sentence that forces
      the two counts apart: one visitor row, two view rows;
    * an **anonymous view**, because the specification requires anonymous views to stay
      reachable and a demo that showed only addressed views would not demonstrate it;
    * a view that **downloaded** a file and one that downloaded nothing, so the two
      download answers do not render the same;
    * views spread **across a window**, so the ``--since`` / ``--until`` bounds have
      something to separate.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf075.py``: the seeder prints it to a Windows console, and one
    RIGHTWARDS ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    engine = EngagementEngine(store, now=lambda: now)

    base_ms = int(now.timestamp() * 1000)
    hour = 3600000

    link_one = "seed-wf075-link-a"
    link_two = "seed-wf075-link-b"

    # 1. A proven buyer. Verified, so the flag has a true on the board.
    engine.ingest_visitor(
        "buyer@northwind.example",
        dataroom_id=room_id,
        verified=True,
        invited_at=base_ms - 72 * hour,
        total_views=2,
        last_viewed_at=base_ms - 2 * hour,
        room_id=room_id,
    )

    # 2. A buyer whose address was typed and never proven.
    engine.ingest_visitor(
        "buyer@halcyon.example",
        dataroom_id=room_id,
        verified=False,
        invited_at=base_ms - 48 * hour,
        total_views=1,
        last_viewed_at=base_ms - 26 * hour,
        room_id=room_id,
    )

    # 3. A visitor written with no verified field at all, so the third state is a state
    #    the board renders rather than a state only the vocabulary describes.
    engine.ingest_visitor(
        "buyer@vantage.example",
        dataroom_id=room_id,
        invited_at=base_ms - 30 * hour,
        room_id=room_id,
    )

    # 4. The buyer who hit two links in the same room: one visitor, two views.
    for link_id, moment, pages in (
        (
            link_one,
            base_ms - 5 * hour,
            [(1, 90), (2, 140), (3, 60)],
        ),
        (link_two, base_ms - 2 * hour, [(1, 45), (2, 210)]),
    ):
        engine.ingest_view(
            link_id,
            viewer_email="buyer@northwind.example",
            view_type=vocab.VIEW_TYPE_LINK,
            viewed_at=moment,
            page_durations=[
                {"page_number": number, "duration_seconds": seconds} for number, seconds in pages
            ],
            location={"country": "United Kingdom", "city": "London"},
            client={"browser": "Chrome", "os": "macOS", "device": "laptop"},
            room_id=room_id,
        )

    # 5. A view that downloaded a file, so the download answer is not always
    #    not_downloaded.
    engine.ingest_view(
        link_one,
        viewer_email="buyer@halcyon.example",
        view_type=vocab.VIEW_TYPE_DOCUMENT,
        viewed_at=base_ms - 26 * hour,
        downloaded_at=base_ms - 26 * hour + 60000,
        download_type=vocab.DOWNLOAD_TYPE_PDF,
        page_durations=[{"page_number": 1, "duration_seconds": 75}],
        location={"country": "United States", "city": "Chicago"},
        client={"browser": "Safari", "os": "macOS", "device": "desktop"},
        room_id=room_id,
    )

    # 6. An anonymous view. The specification requires it to stay reachable per link,
    #    and it is the reason the board reports anonymous_views separately.
    engine.ingest_view(
        link_one,
        view_type=vocab.VIEW_TYPE_LINK,
        viewed_at=base_ms - 90 * 60000,
        page_durations=[{"page_number": 1, "duration_seconds": 33}],
        location={"country": "Germany", "city": "Berlin"},
        client={"browser": "Edge", "os": "Windows", "device": "desktop"},
        room_id=room_id,
    )

    # Counts are read back from the engine rather than written out here, so the line
    # the seeder prints cannot describe a state the seed did not produce.
    board = engine.summary(room_id)
    link_rows = engine.link_views(link_one)

    return (
        f"{board['visitors']} visitors ({board['verified']} verified, "
        f"{board['unverified']} unverified, {board['unknown_verification']} with no proof "
        f"recorded); {board['views']} view events across 2 links, of which "
        f"{board['anonymous_views']} anonymous and still reachable per link; "
        f"{board['unique_viewers']} distinct viewer address(es); {board['downloads']} "
        f"download(s); {board['time_spent_seconds']}s of recorded dwell; "
        f"{len(link_rows)} view(s) on the first link; times in Unix milliseconds"
    )
