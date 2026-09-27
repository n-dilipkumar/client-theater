"""WF-018: read per-page dwell time and drop-off inside a PDF.

A build, not a port: this workflow has a finished research document and no code
at all, and the research document *is* the specification. What it specifies, in
the source product, is a seller opening **Content Management -> Library**,
opening one multi-page PDF, and scrolling to **Advanced Analytics -> PDF
Analytics** to read **Time spent per page** and **Drop off per page**.

===========================  ================================================
What the research says        Where this module serves it
===========================  ================================================
"time spent per page"         ``GET /assets/{asset_id}/pdf-analytics``
"drop off per page"           the same payload, ``drop_off_per_page``
"average watch time"          ``GET /assets/{asset_id}/video-analytics``
"Core Analytics bar charts"   ``GET /assets/{asset_id}/core-analytics``
asset snapshot + webhooks     ``POST /assets``, ``POST /assets/{id}/events``
Advanced Analytics, whole    ``GET /assets/{asset_id}``
a deal's own library          ``GET /rooms/{room_id}/assets``
"no documented endpoint"      ``GET /inferences``
===========================  ================================================

What the research deliberately does not say is the mechanics: there is "no
documented per-page analytics endpoint" and "no documented Dock endpoint for
per-page timing". Those judgement calls are written down in
:mod:`dsr.pdf_analytics.inferences` and served at ``/inferences``, so a reviewer
can disagree with a named entry instead of hunting through a diff.

Five decisions in here are worth stating at the top, because a reviewer will
want to check them:

**The prefix is ``/api/wf-018``**, per the build brief, so this feature cannot
collide with a workflow that reached for a topical path like ``/api/analytics``.

**``source=`` comes from the route, always.** Every write below passes
``f"{METHOD} {router.prefix}/..."`` built from the real path parameter. A
hardcoded string in a domain function can drift from the route that serves the
request, and an audit row naming a path the app no longer serves is worse than
no audit row. One test asserts every source this feature records matches a route
the host actually mounted.

**``EXCEPTION_HANDLERS`` claims one type, :class:`AnalyticsError`.** FastAPI
only accepts exception handlers on the app, so the export is how a feature
describes its own errors without editing ``api.py``. One type covers the whole
hierarchy and reads the status off the exception; a built-in is deliberately not
claimed, because registering ``ValueError`` globally would let this feature
intercept errors anywhere in the product.

**Room-scoped means room-scoped.** ``/rooms/{room_id}/assets`` is a real
question - how did *this* buyer read it - and the ``room_id`` query filter on
each analytics route narrows the same researched metrics to one workspace. With
no filter the numbers are library-wide, which is what the source's Library view
is.

**Reads never write.** "Analytics are computed continuously; the *action* on them
is manual" means there is no rollup to keep in step and no cache to invalidate,
so every GET here is a pure read of the telemetry. Two tests assert it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.pdf_analytics import AnalyticsBook, AnalyticsError, describe_inferences
from dsr.pdf_analytics.vocabulary import (
    ASSET_SNAPSHOT_FIELDS,
    ASSET_TYPES,
    AUDIENCES,
    GRAINS,
    INTERNAL_ONLY_METRICS,
    MIN_PAGES_FOR_PDF_ANALYTICS,
    PDF_UNAVAILABLE_REASONS,
    VIDEO_UNAVAILABLE_REASONS,
    WEBHOOK_EVENT_TYPES,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-018-read-per-page-dwell-time-and-drop-off-",
    "ticket": "WF-018",
    "name": "Read per-page dwell time and drop-off inside a PDF",
    "description": (
        "Per-page average dwell and a drop-off curve for every multi-page PDF in the "
        "library, average watch time for self-hosted video, and the Core Analytics counts "
        "- buyers only, with Shares the one internal metric."
    ),
    "nav": [{"id": "pdf-analytics", "label": "PDF analytics"}],
}

router = APIRouter(prefix="/api/wf-018", tags=["wf-018"])


def get_book(store: RecordStore = StoreDep) -> AnalyticsBook:
    """An :class:`AnalyticsBook` over the process-wide audited store.

    Built per request rather than stored on ``app.state``: the book holds
    nothing but the store handle, and hanging it on the app is the shared-file
    edit this host exists to remove. It also leaves the seam a test can
    override.
    """
    return AnalyticsBook(store)


BookDep = Depends(get_book)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _analytics_error(request: Request, exc: AnalyticsError) -> JSONResponse:
    """A refusal this layer made, as the status the exception carries.

    One registration for the whole hierarchy. ``AnalyticsError`` is defined in
    this workflow's own package, so a global handler for it cannot intercept an
    unrelated error anywhere else in the product - which is the reason the
    builtins (``ValueError``, ``PermissionError``) are not claimed here, unlike
    in the WF-006 port.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


EXCEPTION_HANDLERS = {AnalyticsError: _analytics_error}


# --------------------------------------------------------------------------- #
# What is sourced, and what is inferred
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The exact vocabulary this workflow accepts.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a new event type reaches every client at
    once. The internal-metrics list is the researched exception, published
    rather than buried: "Dock's analytics only show engagement from external
    users ... The one exception is 'Shares'".
    """
    return {
        "asset_types": list(ASSET_TYPES),
        "asset_snapshot_fields": list(ASSET_SNAPSHOT_FIELDS),
        "webhook_event_types": list(WEBHOOK_EVENT_TYPES),
        "audiences": list(AUDIENCES),
        "internal_only_metrics": list(INTERNAL_ONLY_METRICS),
        "grains": list(GRAINS),
        "min_pages_for_pdf_analytics": MIN_PAGES_FOR_PDF_ANALYTICS,
        "pdf_unavailable_reasons": list(PDF_UNAVAILABLE_REASONS),
        "video_unavailable_reasons": list(VIDEO_UNAVAILABLE_REASONS),
    }


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each.

    The research for WF-018 states its own limits twice: "No documented
    per-page analytics endpoint" and "there is no documented Dock endpoint for
    per-page timing". The parts of this feature that are therefore judgement
    calls are collected in :mod:`dsr.pdf_analytics.inferences` and served here.

    It is a read with no side effect, so it needs no store. Publishing it is the
    difference between "we inferred this, see the comment" and "we inferred this,
    here it is, here is what would change it".
    """
    return describe_inferences()


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #


@router.get("/assets")
def list_assets(
    type: str | None = Query(default=None, description="pdf | video"),
    room_id: str | None = Query(default=None, description="Scope to one room's library"),
    q: str | None = Query(default=None, description="Case-insensitive substring of the name"),
    limit: int = Query(default=200, ge=1, le=1000),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """The Library list: every asset, with the analytics panels it can show.

    Each row carries ``pdfAnalyticsAvailable`` / ``videoAnalyticsAvailable`` so a
    client can dim a panel before asking for it, and so the reason a panel is
    missing is visible in the list rather than only on a failed request.
    """
    records = book.list_assets(type=type, room_id=room_id, q=q, limit=limit)
    return {
        "count": len(records),
        "assets": [
            {
                "id": record["id"],
                "room_id": record["room_id"],
                "updated_at": record["updated_at"],
                **record["data"],
            }
            for record in records
        ],
    }


@router.post("/assets", status_code=201)
def register_asset(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Register a library asset from the researched snapshot, or update one.

    The body is the researched asset object: ``name``, ``type``, ``shareUrl``,
    ``isInternal``, ``tags``, ``downloadEnabled``, ``trackingEnabled``, plus a
    ``pageCount`` for a PDF and a ``selfHosted`` flag for a video. Upsert keyed on
    ``externalId``, so the same asset arriving from ``GET /v1/assets`` and then
    from an ``asset.viewed`` payload is one row with one set of telemetry.

    A field this workflow does not name is stored verbatim. No migration, no
    typed column, no new required field - the schema-flexibility rule is what
    lets a team add its own without coordinating with anyone.
    """
    return book.register_asset(payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/assets")


@router.get("/rooms/{room_id}/assets")
def room_assets(
    room_id: str,
    grain: str = Query(default="day", description="day | week | month"),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """The library assets shared into one room, with their engagement in it.

    The room-scoped question, and a different one from the library view: "how did
    *this* buyer read it" rather than "how does this deck perform". The figures
    are the same researched metrics over that room's telemetry only, so a room
    number and a library number can never be mistaken for one another.
    """
    return book.room_assets(room_id, grain=grain)


# --------------------------------------------------------------------------- #
# The asset detail page
# --------------------------------------------------------------------------- #


@router.get("/assets/{asset_id}")
def asset_detail(
    asset_id: str,
    grain: str = Query(default="day"),
    room_id: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """One asset and its whole **Advanced Analytics** block.

    The researched flow is "open the asset, scroll to Advanced Analytics, read
    the PDF or Video panel", so one call answers the page. A panel that does not
    apply is reported as unavailable with its reason rather than raised: on the
    detail page "not applicable" is information, while on the dedicated route it
    is a wrong question.
    """
    return book.asset_detail(asset_id, grain=grain, room_id=room_id)


@router.get("/assets/{asset_id}/pdf-analytics")
def pdf_analytics(
    asset_id: str,
    room_id: str | None = Query(default=None, description="Scope to one workspace's readers"),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """**PDF Analytics**: time spent per page, and the drop-off curve.

    "For multi-page PDFs, we're able to show two additional metrics: Time spent
    per page: the average amount of time that's spent per page. / Drop off per
    page: understand when someone stops looking at your content."

    422 for a single-page PDF or a video, with a named ``reason``. An empty
    curve would read as "nobody read it", which is a different and much more
    damaging thing to tell a seller.
    """
    return book.pdf_analytics(asset_id, room_id=room_id)


@router.get("/assets/{asset_id}/video-analytics")
def video_analytics(
    asset_id: str,
    room_id: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """**Video Analytics**: the average watch time.

    "For self-hosted videos, we're able to show the average watch time of the
    video." 422 for anything else, for the same reason as the PDF block.
    """
    return book.video_analytics(asset_id, room_id=room_id)


@router.get("/assets/{asset_id}/core-analytics")
def core_analytics(
    asset_id: str,
    grain: str = Query(default="day", description="day | week | month"),
    room_id: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """**Core Analytics**: the bar charts, and the flat counts under them.

    Views and downloads count buyers. Shares counts the internal team - the one
    exception the research states. The audience rule is applied per metric
    rather than as a filter over the rows, because a share is internal *by
    definition* and filtering it out upstream would make the exception
    unrepresentable.
    """
    return book.core_analytics(asset_id, grain=grain, room_id=room_id)


# --------------------------------------------------------------------------- #
# The ingest the research says has no documented endpoint
# --------------------------------------------------------------------------- #


@router.post("/assets/{asset_id}/events", status_code=201)
def record_event(
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Record one ``asset.viewed`` / ``asset.downloaded`` / ``asset.shared``.

    The body is the researched webhook payload: the event name, the viewer, and
    the embedded asset snapshot, which is stored verbatim on the event row. That
    stored snapshot is the researched extensibility path made concrete - "a third
    party can build its own per-page scoring by consuming asset.viewed events and
    joining to its own viewer telemetry" - because it is the join key.

    An event naming an asset this product has never seen registers the asset from
    that snapshot, because the payload embeds it precisely so the receiver need
    not make a second call. A known asset is left alone.
    """
    return book.record_event(
        asset_id, payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/assets/{asset_id}/events"
    )


@router.post("/assets/{asset_id}/timings", status_code=201)
def record_timings(
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Record one viewer's per-page timing for one open of one PDF.

    The researched data flow is exactly this shape: "Dock's viewer emits per-page
    timing" while a buyer has one document open. So the envelope is per request
    (``viewer``, ``session_id``, ``room_id``, ``isInternal``) and the rows are
    pages, which also makes the batch a single audited unit - half a session is
    not a session.

    Refused with 422 when the asset has ``trackingEnabled: false``. Stored-and-
    ignored was the alternative and it leaves a hole in the curve that is
    indistinguishable from "readers skipped this page", which is the exact
    distinction this workflow exists to make.
    """
    return book.record_timings(
        asset_id, payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/assets/{asset_id}/timings"
    )


@router.post("/assets/{asset_id}/watch", status_code=201)
def record_watch(
    asset_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    book: AnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Record one completed watch of a self-hosted video.

    One row per watch, so the average is over watches rather than over
    seconds-of-video - which is what "average watch time" means when a reader
    can pause and come back.
    """
    return book.record_watch(
        asset_id, payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/assets/{asset_id}/watch"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

# The demo deliberately contains the states a reviewer needs to see and the happy
# path alone would not teach:
#
# * a 24-page deck that most readers finish, with a slow, real curve;
# * a 12-page deck that dies at page 5, which is the whole point of the feature;
# * a single-page PDF, so the "PDF Analytics needs a multi-page document" refusal
#   has somewhere to be seen;
# * a self-hosted video with watch times, for the adjacent Video Analytics block;
# * a video hosted elsewhere, for the other Video Analytics refusal;
# * an untracked PDF, so "timings refused" is a visible state;
# * an internal-only deck, so the external-only rule is visible: the team previews
#   it and shares it four times, and Core Analytics still reports zero buyer views.

#: Reader profiles, as ``(viewer, pages reached)``. The healthy deck has readers
#: finishing, coasting into the appendix, and bailing early; the roadmap dies at
#: the wall. The fourth tuple element, where present, makes that reader leave a
#: gap long enough to be a *second* reading, so the demo exercises the
#: session-gap rule rather than asking a reviewer to trust it.
_READERS: dict[str, tuple[tuple[str, int], ...]] = {
    "Enterprise Security & Compliance Overview": (
        ("a.buyer@northwind.example", 24),
        ("procurement@northwind.example", 24),
        # No session id, and a 40-minute pause after page 10: one human, two
        # readings. The curve is unchanged and the session count proves why.
        ("security@northwind.example", 19),
        ("ops@fabrikam.example", 7),
        ("cfo@fabrikam.example", 2),
    ),
    "Platform Roadmap FY27": (
        ("a.buyer@northwind.example", 12),
        ("procurement@northwind.example", 5),
        ("security@northwind.example", 4),
        ("ops@fabrikam.example", 4),
        ("cfo@fabrikam.example", 1),
    ),
    "Commercial Terms One-Pager": (
        ("procurement@northwind.example", 1),
        ("a.buyer@northwind.example", 1),
    ),
}

#: The reader who stops for lunch, and the page they stop after.
IDLE_READER = "security@northwind.example"
IDLE_AFTER_PAGE = 10
#: Comfortably past SESSION_GAP_SECONDS, so the split is unambiguous.
IDLE_SECONDS = 2_400

_WATCHES: tuple[tuple[str, float, str], ...] = (
    ("a.buyer@northwind.example", 214.0, "sso-walkthrough-northwind-1"),
    ("procurement@northwind.example", 96.0, "sso-walkthrough-northwind-2"),
    ("ops@fabrikam.example", 31.0, "sso-walkthrough-fabrikam-1"),
    ("ops@fabrikam.example", 402.0, "sso-walkthrough-fabrikam-2"),
)

#: ``(asset name, event, viewer, is_internal, days ago)``. The last four are the
#: internal-only deck, which is where the researched exception is visible.
_EVENTS: tuple[tuple[str, str, str, bool, int], ...] = (
    ("Enterprise Security & Compliance Overview", "asset.viewed", "a.buyer@northwind.example", False, 21),
    ("Enterprise Security & Compliance Overview", "asset.viewed", "a.buyer@northwind.example", False, 9),
    ("Enterprise Security & Compliance Overview", "asset.viewed", "procurement@northwind.example", False, 6),
    ("Enterprise Security & Compliance Overview", "asset.downloaded", "security@northwind.example", False, 4),
    ("Platform Roadmap FY27", "asset.viewed", "a.buyer@northwind.example", False, 12),
    ("Platform Roadmap FY27", "asset.shared", "dana", True, 12),
    ("Commercial Terms One-Pager", "asset.viewed", "procurement@northwind.example", False, 3),
    ("Partner Referral Terms (internal)", "asset.viewed", "dana", True, 30),
    ("Partner Referral Terms (internal)", "asset.viewed", "sam", True, 27),
    ("Partner Referral Terms (internal)", "asset.shared", "dana", True, 28),
    ("Partner Referral Terms (internal)", "asset.shared", "dana", True, 21),
    ("Partner Referral Terms (internal)", "asset.shared", "sam", True, 14),
    ("Partner Referral Terms (internal)", "asset.shared", "dana", True, 2),
)


def _dwell_seconds(asset_name: str, page: int) -> float:
    """A readable, deterministic dwell per page.

    Scripted rather than random because the point of the demo is that a reviewer
    can *see* the shape: a long cover, a meaty middle, a dry appendix, and a
    cliff. A random draw would hide every one of those behind noise.
    """
    if asset_name == "Platform Roadmap FY27":
        # Four pages of architecture diagrams, then a wall of roadmap jargon.
        return 55.0 if page == 1 else (34.0 if page <= 4 else 9.0)
    if asset_name == "Enterprise Security & Compliance Overview":
        if page == 1:
            return 62.0
        if page <= 6:
            return 28.0
        if page <= 18:
            return 41.0
        return 6.0  # the appendix nobody reads
    if page == 1:
        return 48.0
    return 12.0


def _asset(index: int, name: str, kind: str, **overrides: Any) -> dict[str, Any]:
    """Build one researched asset snapshot, plus this build's extra fields.

    The source's asset id is derived from ``index``, not from ``hash(name)``:
    Python randomises string hashing per process, so a hash would give the demo
    a different asset id on every run and make "the same rows" unfalsifiable.
    """
    asset: dict[str, Any] = {
        "name": name,
        "type": kind,
        "externalId": f"asset_demo_{index:02d}",
        "shareUrl": f"https://share.example/assets/asset_demo_{index:02d}",
        "isInternal": bool(overrides.get("isInternal", False)),
        "tags": list(overrides.get("tags") or ["demo"]),
        "downloadEnabled": bool(overrides.get("downloadEnabled", True)),
        "trackingEnabled": bool(overrides.get("trackingEnabled", True)),
    }
    if kind == "pdf":
        asset["pageCount"] = int(overrides["pageCount"])
    else:
        asset["selfHosted"] = bool(overrides.get("selfHosted", True))
        asset["playbackUrl"] = str(overrides["playbackUrl"])
    return asset


def _read(
    book: AnalyticsBook,
    asset_id: str,
    asset_name: str,
    viewer: str,
    pages_reached: int,
    *,
    room_id: str,
    now: datetime,
) -> int:
    """Emit one reader's per-page timing, through the real ingest.

    The clock advances as it goes rather than every page sharing one instant, so
    the session-gap rule is exercised by the demo itself: the reader who stops
    for lunch becomes two readings, which is the behaviour a reviewer should be
    able to see rather than take on trust.
    """
    idle = viewer == IDLE_READER
    elapsed = 0.0
    batch: list[dict[str, Any]] = []
    for page in range(1, pages_reached + 1):
        seconds = _dwell_seconds(asset_name, page)
        batch.append(
            {
                "page": page,
                "seconds": seconds,
                "occurred_at": (
                    now - timedelta(days=3) + timedelta(seconds=elapsed)
                ).isoformat(timespec="seconds"),
            }
        )
        elapsed += seconds + 3
        if idle and page == IDLE_AFTER_PAGE:
            elapsed += IDLE_SECONDS

    book.record_timings(
        asset_id,
        {
            "viewer": viewer,
            # No session id for the idling reader, so the gap rule is what
            # separates the two readings. Everyone else gets one explicitly.
            "session_id": None if idle else f"{asset_id}-{viewer}",
            "room_id": room_id,
            "isInternal": False,
            "timings": batch,
        },
        source="seed",
    )
    return len(batch)


def seed(db: AuditedDatabase, context: Mapping[str, Any]) -> str:
    """Seed the library assets and the telemetry a reviewer needs to read.

    Telemetry is written through the real :meth:`AnalyticsBook.record_timings`
    and :meth:`~AnalyticsBook.record_event` rather than by hand, so the demo
    cannot show a shape the workflow would not produce - and so the seeder
    proves the ingest works, not only the reads.
    """
    book = AnalyticsBook(RecordStore(db))
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    now = context["now"]
    if not rooms:
        return "0 content assets (no rooms to scope them to)"

    primary = rooms[0][0]
    secondary = rooms[1][0] if len(rooms) > 1 else primary

    specs: tuple[tuple[dict[str, Any], str], ...] = (
        (
            _asset(
                1,
                "Enterprise Security & Compliance Overview",
                "pdf",
                pageCount=24,
                tags=["security", "compliance", "approved"],
            ),
            primary,
        ),
        (
            _asset(2, "Platform Roadmap FY27", "pdf", pageCount=12, tags=["product", "confidential"]),
            primary,
        ),
        (
            _asset(3, "Commercial Terms One-Pager", "pdf", pageCount=1, tags=["commercial"]),
            primary,
        ),
        (
            _asset(
                4,
                "M&A Diligence Data Room Index",
                "pdf",
                pageCount=18,
                tags=["legal"],
                trackingEnabled=False,
            ),
            secondary,
        ),
        (
            _asset(
                5,
                "Partner Referral Terms (internal)",
                "pdf",
                pageCount=9,
                isInternal=True,
                downloadEnabled=False,
                tags=["internal", "legal"],
            ),
            secondary,
        ),
        (
            _asset(
                6,
                "SSO Onboarding Walkthrough",
                "video",
                playbackUrl="https://cdn.example/sso-walkthrough.mp4",
                tags=["onboarding"],
            ),
            primary,
        ),
        (
            _asset(
                7,
                "Analyst Day Keynote (external player)",
                "video",
                selfHosted=False,
                playbackUrl="https://player.example/embed/analyst-day",
                tags=["event"],
            ),
            secondary,
        ),
    )

    assets: dict[str, str] = {}
    for snapshot, room_id in specs:
        record = book.register_asset(snapshot, room_id=room_id, actor="dana", source="seed")
        assets[str(snapshot["name"])] = record["id"]

    readings = 0
    for name, readers in _READERS.items():
        asset_id = assets.get(name)
        if not asset_id:
            continue
        for viewer, pages_reached in readers:
            readings += _read(
                book, asset_id, name, viewer, pages_reached, room_id=primary, now=now
            )

    watches = 0
    video_id = assets.get("SSO Onboarding Walkthrough")
    if video_id:
        for index, (viewer, seconds, session) in enumerate(_WATCHES):
            book.record_watch(
                video_id,
                {
                    "viewer": viewer,
                    "seconds": seconds,
                    "session_id": session,
                    "room_id": primary,
                    "occurred_at": (now - timedelta(days=4 - index)).isoformat(timespec="seconds"),
                },
                source="seed",
            )
            watches += 1

    events = 0
    for name, event, viewer, internal, days_ago in _EVENTS:
        asset_id = assets.get(name)
        if not asset_id:
            continue
        book.record_event(
            asset_id,
            {
                "event": event,
                "viewer": viewer,
                "isInternal": internal,
                "room_id": secondary if internal else primary,
                "occurred_at": (now - timedelta(days=days_ago)).isoformat(timespec="seconds"),
            },
            source="seed",
        )
        events += 1

    return (
        f"{len(specs)} content assets, {readings} page timings, "
        f"{watches} watches, {events} asset events "
        "(1 single-page PDF, 1 untracked PDF, 1 internal-only PDF, "
        "1 externally hosted video, 1 reader who stopped mid-deck)"
    )
