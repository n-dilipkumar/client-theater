"""WF-127: interactive demo / sandbox analytics with stakeholder discovery.

The rules live in :mod:`dsr.demo_analytics`, which knows nothing about HTTP.
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

Source: ``docs/research/digital-sales-room-workflows/wf/WF-127.md``, quoted in
full on issue #152.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.demo_analytics import DemoAnalyticsBook, DemoAnalyticsError
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-127-demo-analytics",
    "ticket": "WF-127",
    "name": "Interactive demo / sandbox analytics with stakeholder discovery",
    "description": (
        "A product recording becomes a branching interactive demo. Its per-viewer event "
        "stream produces activity heatmaps and a six-level analytics roll-up, and a buyer's "
        "internal re-share surfaces the newly discovered stakeholders. The metric vocabulary "
        "is the research's; every value it does not give is a stated inference."
    ),
    "nav": [{"id": "demo-analytics", "label": "Interactive demos"}],
}

router = APIRouter(prefix="/api/wf-127", tags=["WF-127"])


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_book(store: RecordStore = StoreDep) -> DemoAnalyticsBook:
    """The book, built on the shared audited store, per request."""
    return DemoAnalyticsBook(store)


BookDep = Depends(get_book)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _demo_error(request: Request, exc: DemoAnalyticsError) -> JSONResponse:
    """A refusal this workflow made, answered with the status it carries.

    One handler for the whole hierarchy. ``DemoAnalyticsError`` is defined in
    :mod:`dsr.demo_analytics.errors`, so a handler for it cannot intercept an
    unrelated error anywhere else - which is why the builtins are not claimed.
    """
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


EXCEPTION_HANDLERS = {DemoAnalyticsError: _demo_error}


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """Every value this workflow accepts, and which half is sourced."""
    return book.vocabulary()


@router.get("/inferences")
def inferences(book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """Every decision the research does not make, and how to change each one."""
    return book.inferences()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Counts for the scope: demos by state, demos sent, views, discovery."""
    return book.summary(room_id)


# --------------------------------------------------------------------------- #
# The demos
# --------------------------------------------------------------------------- #


@router.get("/demos")
def list_demos(
    room_id: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Every demo in scope, newest first."""
    demos = book.list_demos(room_id)
    return {"count": len(demos), "room_id": room_id, "demos": demos}


@router.post("/demos", status_code=201)
def create_demo(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str = Query(..., description="The room this demo belongs to"),
    actor: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Build one interactive demo from a recording or from a click path."""
    return book.create_demo(room_id, payload, actor=actor, source=_source("POST", "/demos"))


@router.get("/demos/{demo_id}")
def read_demo(demo_id: str, book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """One demo, its click-path tree and its state."""
    return book.read_demo(demo_id)


@router.patch("/demos/{demo_id}")
def update_demo(
    demo_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Change a demo's fields, then re-validate its whole click path."""
    return book.update_demo(
        demo_id, payload, actor=actor, source=_source("PATCH", "/demos/{demo_id}")
    )


@router.post("/demos/{demo_id}/publish")
def publish_demo(
    demo_id: str,
    actor: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Publish a demo so it can be shared and watched."""
    return book.publish_demo(
        demo_id, actor=actor, source=_source("POST", "/demos/{demo_id}/publish")
    )


@router.post("/demos/{demo_id}/archive")
def archive_demo(
    demo_id: str,
    actor: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Withdraw a demo. It keeps its activity and accepts no more."""
    return book.archive_demo(
        demo_id, actor=actor, source=_source("POST", "/demos/{demo_id}/archive")
    )


# --------------------------------------------------------------------------- #
# Distribution and the event stream
# --------------------------------------------------------------------------- #


@router.post("/demos/{demo_id}/shares", status_code=201)
def record_share(
    demo_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Record an initial send or a forward re-share, and the stakeholders it names."""
    return book.record_share(
        demo_id, payload, actor=actor, source=_source("POST", "/demos/{demo_id}/shares")
    )


@router.post("/demos/{demo_id}/events", status_code=201)
def record_event(
    demo_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Record one viewer event: a view, a section selection, or a download.

    The body may carry ``shared_from``, the upstream share reference. That field
    is what makes a downstream viewer a discovered stakeholder, so the discovery
    is inferred from the re-share rather than declared by the viewer.
    """
    return book.record_event(
        demo_id, payload, actor=actor, source=_source("POST", "/demos/{demo_id}/events")
    )


# --------------------------------------------------------------------------- #
# The analytics
# --------------------------------------------------------------------------- #


@router.get("/demos/{demo_id}/analytics")
def analytics(demo_id: str, book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """The whole analytics block: metrics, heatmap, sections, stakeholders."""
    return book.analytics(demo_id)


@router.get("/demos/{demo_id}/heatmap")
def heatmap(demo_id: str, book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """Buyer activity by weekday and hour slot, from each event's timestamp."""
    return book.heatmap(demo_id)


@router.get("/demos/{demo_id}/sections")
def sections(demo_id: str, book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """The features/sections selected, one row per click-path node."""
    return book.sections(demo_id)


@router.get("/demos/{demo_id}/stakeholders")
def stakeholders(demo_id: str, book: DemoAnalyticsBook = BookDep) -> dict[str, Any]:
    """Every stakeholder behind the demo, with the engagement the events imply."""
    rows = book.stakeholder_views(demo_id)
    return {"demo_id": demo_id, "count": len(rows), "stakeholders": rows}


@router.get("/demos/{demo_id}/rollup")
def rollup(
    demo_id: str,
    level: str = Query(default="top_level"),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """One of the six researched drill-down levels over this demo's room.

    The level drills *up* from one demo: "all individual demos" and "team" only
    mean something across the demos a demo shares a room with, so the roll-up
    scope is the room the demo belongs to.
    """
    demo = book.read_demo(demo_id)
    return book.rollup(level, room_id=demo["room_id"])


@router.get("/stakeholders")
def list_stakeholders(
    room_id: str | None = Query(default=None),
    book: DemoAnalyticsBook = BookDep,
) -> dict[str, Any]:
    """Every stakeholder in scope, newest first, across every demo."""
    rows = book.list_stakeholders(room_id=room_id)
    return {"room_id": room_id, "count": len(rows), "stakeholders": rows}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The recorded demo, registered through this workflow's own build, so the
#: click-path tree is the one a real build would produce rather than a stub.
DEMO_TITLE = "Northwind onboarding sandbox"
DEMO_RECORDING = "rec-2026-04-28-northwind"

#: ``(recipient, days_ago, hour)``. Three initial recipients: one engages fully
#: and returns, one forwards the demo on, and one never opens it - so view_rate
#: is below one and the discovery is a real discovery rather than a certainty.
INITIAL_RECIPIENTS: tuple[tuple[str, int, int], ...] = (
    ("buyer@northwind.example", 3, 9),
    ("procurement@fabrikam.example", 3, 10),
    ("never@contoso.example", 3, 11),
)

#: The downstream viewer the forward re-share discovers.
FORWARD_RECIPIENT = "cfo@northwind.example"

#: The internal preview, which is recorded and excluded from every buyer metric.
INTERNAL_PREVIEW = "dana@northwind.example"


def _at(now: datetime, *, days_ago: int, hour: int, minute: int = 0) -> str:
    """A timestamp a fixed number of days before the seed clock, at a set hour."""
    base = now - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed two demos, a distributed send, a forward re-share and an archive.

    The demo deliberately contains the states the happy path alone would not
    teach, because a demo of only success teaches nothing:

    * a published demo with a five-node branching click path;
    * three initial recipients, of whom one engages, selects sections, downloads
      a document and returns, one forwards the demo on, and one never opens it -
      so ``view_rate`` is below one;
    * a forward re-share that discovers a downstream stakeholder, so
      ``discovery_rate`` is a measured number rather than a certainty;
    * an **internal** preview, which is recorded and excluded from every buyer
      metric and from the heatmap;
    * an archived demo, which refuses a new event - the non-success state the
      seeder prints.

    Every write goes through the audited store, so the demo cannot show a shape
    the workflow would not produce.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return "0 interactive demos (no rooms to scope them to)"
    room_id = rooms[0][0]
    now = context["now"]
    book = DemoAnalyticsBook(RecordStore(db))

    demo = book.create_demo(
        room_id,
        {
            "title": DEMO_TITLE,
            "demo_type": "sandbox",
            "recording_ref": DEMO_RECORDING,
            "team": "Enterprise",
            "owner": "dana",
        },
        actor="dana",
        source="seed",
    )
    book.publish_demo(demo["id"], actor="dana", source="seed")

    recipients = [address for address, _days, _hour in INITIAL_RECIPIENTS]
    book.record_share(
        demo["id"],
        {"kind": "initial", "recipients": recipients, "channel": "link"},
        actor="dana",
        source="seed",
    )

    # The engaged buyer: a view, two sections, a download, then a return.
    book.record_event(
        demo["id"],
        {
            "viewer": "buyer@northwind.example",
            "event": "view",
            "occurred_at": _at(now, days_ago=3, hour=9, minute=5),
        },
        actor="system",
        source="seed",
    )
    book.record_event(
        demo["id"],
        {
            "viewer": "buyer@northwind.example",
            "event": "section_selected",
            "section": "dashboard",
            "occurred_at": _at(now, days_ago=3, hour=9, minute=7),
        },
        actor="system",
        source="seed",
    )
    book.record_event(
        demo["id"],
        {
            "viewer": "buyer@northwind.example",
            "event": "section_selected",
            "section": "reports",
            "occurred_at": _at(now, days_ago=3, hour=9, minute=9),
        },
        actor="system",
        source="seed",
    )
    book.record_event(
        demo["id"],
        {
            "viewer": "buyer@northwind.example",
            "event": "doc_downloaded",
            "section": "reports",
            "occurred_at": _at(now, days_ago=3, hour=9, minute=11),
        },
        actor="system",
        source="seed",
    )
    book.record_event(
        demo["id"],
        {
            "viewer": "buyer@northwind.example",
            "event": "view",
            "occurred_at": _at(now, days_ago=2, hour=10, minute=0),
        },
        actor="system",
        source="seed",
    )

    # An internal preview: recorded, and excluded from every buyer metric.
    book.record_event(
        demo["id"],
        {
            "viewer": INTERNAL_PREVIEW,
            "event": "view",
            "is_internal": True,
            "occurred_at": _at(now, days_ago=3, hour=8, minute=0),
        },
        actor="system",
        source="seed",
    )

    # The buyer forwards the demo internally, which discovers a stakeholder.
    book.record_share(
        demo["id"],
        {
            "kind": "forward",
            "sender": "buyer@northwind.example",
            "recipients": [FORWARD_RECIPIENT],
            "channel": "email",
        },
        actor="system",
        source="seed",
    )
    book.record_event(
        demo["id"],
        {
            "viewer": FORWARD_RECIPIENT,
            "event": "view",
            "shared_from": "buyer@northwind.example",
            "occurred_at": _at(now, days_ago=2, hour=11, minute=0),
        },
        actor="system",
        source="seed",
    )
    book.record_event(
        demo["id"],
        {
            "viewer": FORWARD_RECIPIENT,
            "event": "section_selected",
            "section": "overview",
            "shared_from": "buyer@northwind.example",
            "occurred_at": _at(now, days_ago=2, hour=11, minute=2),
        },
        actor="system",
        source="seed",
    )

    # A second demo that was withdrawn. An event on it is refused, which is the
    # non-success state this seeder exists to print.
    retired = book.create_demo(
        room_id,
        {"title": "Legacy pricing tour", "demo_type": "tour", "owner": "dana"},
        actor="dana",
        source="seed",
    )
    book.publish_demo(retired["id"], actor="dana", source="seed")
    book.archive_demo(retired["id"], actor="dana", source="seed")
    refused = 0
    try:
        book.record_event(
            retired["id"],
            {
                "viewer": "buyer@northwind.example",
                "event": "view",
                "occurred_at": _at(now, days_ago=1, hour=9),
            },
            actor="system",
            source="seed",
        )
    except DemoAnalyticsError:
        refused += 1

    analytics = book.analytics(demo["id"])
    block = analytics["metrics"]
    published = sum(1 for row in book.list_demos(room_id) if row["state"] == "published")
    archived = sum(1 for row in book.list_demos(room_id) if row["state"] == "archived")
    never_opened = len(recipients) - block["viewers"]
    return (
        f"{len(book.list_demos(room_id))} interactive demos ({published} published, "
        f"{archived} archived) over a {demo['node_count']}-node branching click path; "
        f"{block['demos_sent']} demos sent, {block['views']} buyer views, "
        f"{block['stakeholders_discovered']} stakeholder discovered "
        f"(discovery rate {block['discovery_rate'] * 100:.0f}%, "
        f"view rate {block['view_rate'] * 100:.0f}%); "
        f"{block['features_selected']} sections selected, {block['docs_downloaded']} doc downloaded; "
        f"{never_opened} recipient never opened, 1 internal preview excluded, "
        f"{refused} archived demo refused an event"
    )


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
