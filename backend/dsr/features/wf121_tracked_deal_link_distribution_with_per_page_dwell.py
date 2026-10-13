"""WF-121: tracked deal-link distribution with per-page dwell.

The rules live in :mod:`dsr.tracked_deal_link`, which knows nothing about HTTP.
This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary and the inferences, served from one place so the page cannot
  drift from the rules that validate it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

**What this feature reuses, and why that is the whole design.** The brief for
this ticket says the workflow "restate[s] the capability WF-006 and WF-018
already provide", and to reuse their vocabulary rather than re-declare it. So:

* per-page dwell, drop-off and the revisit split are
  :mod:`dsr.pdf_analytics`'s, called from :mod:`dsr.tracked_deal_link.rules`;
* the engagement record handed to the CRM carries
  :mod:`dsr.crm_engagement`'s canonical field names, and the write-back is that
  workflow's two-stage ``EngagementSync``: the room's row first, the vendor
  create enqueued;
* the CRM object is WF-042's ``deal``, and the opportunity id travels on the
  link because WF-042 is what provisions a room's CRM deal;
* the gate - password, expiry, email verification - stays WF-069's. Its expiry
  boundary and email normalisation are imported, not re-implemented.

``fire_queue`` defaults to false everywhere, so recording a view never opens a
socket. A deployment drains the queue through
``POST /api/wf-121/rooms/{room_id}/push``.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-121.md``, quoted in
full on issue #122.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.pdf_analytics import AnalyticsBook
from dsr.store import RecordStore
from dsr.tracked_deal_link import TrackedLinkBook, TrackedLinkError

FEATURE = {
    "id": "wf-121-tracked-deal-link",
    "ticket": "WF-121",
    "name": "Tracked deal-link distribution with per-page dwell",
    "description": (
        "One tracked link a rep shares, the per-page dwell and revisits it records, "
        "the rep alerts it raises, and the engagement record it writes back to the "
        "room's CRM opportunity. Per-page dwell is WF-018's; the CRM write-back is "
        "WF-037's; the gate stays WF-069's."
    ),
    "nav": [{"id": "tracked-deal-link", "label": "Tracked deal link"}],
}

router = APIRouter(prefix="/api/wf-121", tags=["WF-121"])


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> TrackedLinkBook:
    """The book, built on the shared audited store.

    Per request rather than hung on ``app.state``, which is the shared-file edit
    this host exists to remove. It also leaves the engagement sync a seam a test
    can replace without a socket.
    """
    return TrackedLinkBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _tracked_link_error(request: Request, exc: TrackedLinkError) -> JSONResponse:
    """A refusal this workflow made, answered with the status it carries.

    One handler for the whole hierarchy. ``TrackedLinkError`` is defined in
    :mod:`dsr.tracked_deal_link.errors`, so a global handler for it cannot
    intercept an unrelated error anywhere else - which is why the builtins are
    not claimed here.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


EXCEPTION_HANDLERS = {TrackedLinkError: _tracked_link_error}


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """Every value this workflow accepts, and which workflow it is borrowed from.

    Served as data so the page renders its pickers from the same source the rules
    enforce against, and so the shared half - the revisit gap, the engagement
    field names - is visibly WF-018's and WF-037's rather than a private copy.
    """
    return book.vocabulary()


@router.get("/inferences")
def inferences(book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """Every decision the research does not make, and how to change each one."""
    return book.inferences()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Counts for the scope: links by state, views, viewers, deliveries, alerts."""
    return book.summary(room_id)


# --------------------------------------------------------------------------- #
# The links
# --------------------------------------------------------------------------- #


@router.get("/links")
def list_links(
    room_id: str | None = Query(default=None),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Every live tracked link, newest first."""
    links = book.list_links(room_id)
    return {"count": len(links), "room_id": room_id, "links": links}


@router.post("/links", status_code=201)
def create_link(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str = Query(..., description="The room this link belongs to"),
    actor: str | None = Query(default=None),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Create one tracked link over one WF-018 content asset.

    The body names the asset, the recipients and the alert knobs. The share URL
    is built from this router's own prefix once the store has minted the id, so a
    link can never be handed out pointing at a path the host does not serve.
    """
    link = book.create_link(room_id, payload, actor=actor, source=_source("POST", "/links"))
    if not link.get("share_url"):
        link = book.attach_share_url(
            link["id"],
            f"{router.prefix}/links/{link['id']}",
            actor=actor,
            source=_source("POST", "/links"),
        )
    return link


@router.get("/links/{link_id}")
def read_link(link_id: str, book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """One link, its state on this request, and its share URL."""
    return book.read_link(link_id)


@router.patch("/links/{link_id}")
def update_link(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Change a link's recipients, alert knobs or expiry, then re-validate it."""
    return book.update_link(
        link_id,
        payload,
        actor=actor,
        source=_source("PATCH", "/links/{link_id}"),
    )


@router.delete("/links/{link_id}")
def revoke_link(
    link_id: str,
    actor: str | None = Query(default=None),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Revoke a link. A soft delete, so its views and alerts survive."""
    return book.revoke_link(link_id, actor=actor, source=_source("DELETE", "/links/{link_id}"))


@router.get("/links/{link_id}/deliveries")
def list_deliveries(link_id: str, book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """Who the one link was shared with."""
    deliveries = book.list_deliveries(link_id)
    return {"link_id": link_id, "count": len(deliveries), "deliveries": deliveries}


@router.post("/links/{link_id}/deliveries", status_code=201)
def distribute_link(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Record the recipients the one link was shared with."""
    return book.distribute(
        link_id,
        payload,
        actor=actor,
        source=_source("POST", "/links/{link_id}/deliveries"),
    )


# --------------------------------------------------------------------------- #
# The viewer telemetry
# --------------------------------------------------------------------------- #


@router.post("/links/{link_id}/views", status_code=201)
def record_view(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    write_back: bool = Query(
        default=True, description="Hand the engagement record to the CRM write-back"
    ),
    fire_queue: bool = Query(
        default=False, description="Send the CRM create now, rather than leaving it queued"
    ),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Record one viewer's open of one link, page by page.

    The body is the researched view payload: the viewer, the session, and a list
    of ``{page, seconds}`` timings, in the shape WF-018's viewer emits. A payload
    carrying a document body is refused by name, which is the researched privacy
    boundary made a rule.

    ``fire_queue`` defaults to false so recording a view never opens a socket.
    The engagement row and its queue row are written either way; the queue drains
    on ``/rooms/{room_id}/push``.
    """
    return book.record_view(
        link_id,
        payload,
        actor=actor,
        source=_source("POST", "/links/{link_id}/views"),
        write_back=write_back,
        fire_queue=fire_queue,
    )


@router.get("/links/{link_id}/analytics")
def link_analytics(link_id: str, book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """The link's whole analytics block: page dwell, drop-off, revisits, viewers.

    Buyers only, which is WF-018's sourced rule for every metric it computes.
    """
    return book.link_analytics(link_id)


@router.get("/links/{link_id}/viewers")
def list_viewers(link_id: str, book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """One row per buyer behind the link, with its revisit count."""
    return book.list_viewers(link_id)


@router.get("/links/{link_id}/viewers/{viewer}/views")
def viewer_views(link_id: str, viewer: str, book: TrackedLinkBook = BookDep) -> dict[str, Any]:
    """Every page one buyer read, with the revisits the timings group into."""
    return book.list_records_for_viewer(link_id, viewer)


@router.get("/alerts")
def list_alerts(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """The rep alerts the tracked views raised, newest first."""
    alerts = book.alerts(room_id, limit=limit)
    return {"room_id": room_id, "count": len(alerts), "alerts": alerts}


@router.post("/rooms/{room_id}/push")
def push_engagement(
    room_id: str,
    actor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    book: TrackedLinkBook = BookDep,
) -> dict[str, Any]:
    """Drain the queued CRM writes for this room, through WF-037's worker."""
    return book.push(
        room_id, actor=actor, source=_source("POST", "/rooms/{room_id}/push"), limit=limit
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The demo deck. Registered through WF-018's own ingest, so the link points at
#: a real library asset rather than at a row this feature invented.
DEMO_ASSET = {
    "externalId": "wf121-demo-security-overview",
    "name": "Enterprise Security & Compliance Overview",
    "type": "pdf",
    "pageCount": 24,
    "tags": ["security", "compliance", "approved"],
    "downloadEnabled": False,
    "isInternal": False,
    "trackingEnabled": True,
}

#: ``(viewer, is_internal, days_ago, pages, seconds_per_page)``. Two reviewers are
#: real buyers with different reading shapes, one returns for a second revisit,
#: and one is an internal preview that must not alert and must not count.
DEMO_READERS: tuple[tuple[str, bool, int, int, float], ...] = (
    ("a.buyer@northwind.example", False, 0, 24, 41.0),
    ("procurement@northwind.example", False, 0, 5, 33.0),
    ("cfo@fabrikam.example", False, 0, 2, 18.0),
    ("security@northwind.example", False, 3, 12, 27.0),
    ("dana@northwind.example", True, 0, 4, 12.0),
)


def _demo_link(book: TrackedLinkBook, room_id: str, now: Any) -> dict[str, Any]:
    return book.create_link(
        room_id,
        {
            "title": "Enterprise Security & Compliance Overview",
            "asset_id": DEMO_ASSET["externalId"],
            "recipients": [
                "a.buyer@northwind.example",
                "procurement@northwind.example",
                "cfo@fabrikam.example",
                "security@northwind.example",
            ],
            "lifetime_days": 30,
            "alert_on_page": 3,
            "notify_on_return": True,
            "crm_system": "salesforce",
            "crm_deal_id": "006-nw-5001",
        },
        actor="dana",
        source="seed",
    )


def _record_reading(
    book: TrackedLinkBook,
    link_id: str,
    *,
    viewer: str,
    is_internal: bool,
    started: Any,
    pages: int,
    seconds: float,
    session: str,
) -> dict[str, Any]:
    """One reading, emitted page by page through the real ingest.

    The clock advances as it goes, so a reading is a sequence of page events
    rather than one instant - which is what the researched viewer emits and what
    the revisit split is measured against.
    """
    elapsed = 0.0
    timings: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        timings.append(
            {
                "page": page,
                "seconds": seconds,
                "occurred_at": (started + timedelta(seconds=elapsed)).isoformat(timespec="seconds"),
            }
        )
        elapsed += seconds + 3
    return book.record_view(
        link_id,
        {
            "viewer": viewer,
            "session_id": session,
            "is_internal": is_internal,
            "timings": timings,
        },
        actor="system",
        source="seed",
        write_back=not is_internal,
        fire_queue=False,
    )


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed one tracked link and the readings a reviewer needs to read.

    The demo deliberately contains the states the happy path alone would not
    teach, because a demo of only success teaches nothing:

    * one deck registered through WF-018's own ingest, so the link points at a
      real library asset;
    * a buyer who reads the whole deck and **returns** the next day, which shows
      a second revisit and the ``return_visit`` alert;
    * a buyer who stops on page five, which is the drop-off the feature exists to
      show;
    * a buyer who looks at the cover only, so the drop-off curve has a shallow
      reading;
    * an **internal** preview, which is recorded and raises no alert and is
      excluded from the buyer metrics - the rule WF-018 states for every metric
      it computes.

    Every reading is written through the real ingest, so the demo cannot show a
    shape the workflow would not produce, and no socket is opened: the CRM
    writes are enqueued, never fired.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return "0 tracked links (no rooms to scope them to)"
    room_id = rooms[0][0]
    now = context["now"]

    library = AnalyticsBook(RecordStore(db))
    library.register_asset(DEMO_ASSET, room_id=room_id, actor="dana", source="seed")

    book = TrackedLinkBook(RecordStore(db))
    link = _demo_link(book, room_id, now)
    share_url = link.get("share_url") or f"https://share.example/links/{link['id']}"
    link = book.attach_share_url(link["id"], share_url, actor="dana", source="seed")
    book.distribute(
        link["id"],
        {"recipients": link["recipients"], "share_url": share_url},
        actor="dana",
        source="seed",
    )

    readings = 0
    revisits = 0
    for index, (viewer, is_internal, days_ago, pages, seconds) in enumerate(DEMO_READERS):
        started = now - timedelta(days=days_ago) - timedelta(hours=index)
        result = _record_reading(
            book,
            link["id"],
            viewer=viewer,
            is_internal=is_internal,
            started=started,
            pages=pages,
            seconds=seconds,
            session=f"wf121-demo-{index}-1",
        )
        readings += result["readings"]
        revisits = max(revisits, result["revisit"])

    # The second revisit: the same buyer returns the next day and finishes the
    # deck. Separated by more than WF-018's session gap, so it is a new reading.
    returned = _record_reading(
        book,
        link["id"],
        viewer="a.buyer@northwind.example",
        is_internal=False,
        started=now - timedelta(hours=2),
        pages=24,
        seconds=30.0,
        session="wf121-demo-0-2",
    )
    readings += returned["readings"]
    revisits = max(revisits, returned["revisit"])

    return (
        f"1 tracked link distributed to {len(link['recipients'])} recipients, "
        f"{readings} page views across {len(DEMO_READERS) + 1} readings and "
        f"{revisits} revisits (1 buyer read the whole deck and returned, 1 stopped on "
        "page 5, 1 read the cover only, 1 internal preview that raises no alert), "
        "and engagement records queued for the CRM opportunity"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
