"""WF-014: expire or cap access to a room.

The rules live in ``dsr.access_controls``, beside this feature rather than inside
the plugin host, so they are importable and testable without a request and so the
folders in this package stay a set of route declarations. The module is named
``access_controls`` and not ``access``: ``backend/dsr/access.py`` already exists
on ``main`` and belongs to WF-015, which is live and merged. Two features cannot
own one module path - that is the exact collision the plugin host exists to end -
and WF-004 was renamed to ``roles.py`` for the same reason.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.access_controls import (
    ACCESS_STATUSES,
    PAGES,
    REFUSAL_STATUS,
    AccessControls,
    AccessWindowConflict,
    AccessWindowInvalid,
    AccessWindowRefusal,
    parse_instant,
    utcnow,
    vocabulary,
)
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-014-access-controls",
    "ticket": "WF-014",
    "name": "Expire or cap access to a room",
    "description": (
        "Bound a room's link two independent ways - by a number of days counted "
        "from going live, and by a number of openings - and show the buyer what "
        "the link now says. A live page can still be closed, which is the part "
        "this feature exists to get right."
    ),
}

#: Owned by this ticket and therefore unique. The `(method, path)` pairs below are
#: all concrete under it, so nothing here can shadow another feature's route or a
#: core one; the host refuses to mount a collision rather than let load order
#: decide.
router = APIRouter(prefix="/api/wf-014", tags=["WF-014"])


def _refusal(request: Request, exc: AccessWindowRefusal) -> JSONResponse:
    """Map each refusal to the status and code its next step implies.

    One handler for the family, dispatched on the subclass, because FastAPI takes
    handlers on the app only and a feature may map an error type no other feature
    has mapped.
    """
    status, code = REFUSAL_STATUS.get(type(exc), (422, "access_window_refused"))
    return JSONResponse(status_code=status, content={"error": code, "detail": str(exc)})


EXCEPTION_HANDLERS = {AccessWindowRefusal: _refusal}

__all__ = [
    "EXCEPTION_HANDLERS",
    "FEATURE",
    "router",
    "seed",
    "list_windows",
    "set_expiry",
    "set_view_limit",
    "buyer_view",
]


def controls(store: RecordStore) -> AccessControls:
    return AccessControls(store)


def _actor(request: Request) -> str:
    return str(request.headers.get("X-Actor") or "dana")


def _role(request: Request) -> str | None:
    return request.headers.get("X-Role")


def _at(request: Request, at: str | None) -> Any:
    """Resolve the instant a request is asking about.

    Every read here is a function of time, and "is this page open" is only
    meaningful *now*. The parameter exists so a caller - a test, or a seller
    asking what a window looked like - can resolve the window at a chosen instant
    instead of only at the present one.

    It accepts the value from the query string or the body, so the same name works
    whichever way a caller sends it, and it defaults to the present so no route can
    quietly freeze time.
    """
    raw = at
    if raw is None:
        raw = request.query_params.get("at")
    if not raw:
        return utcnow()
    moment = parse_instant(raw)
    if moment is None:
        raise AccessWindowInvalid(
            f"at must be an ISO-8601 instant in UTC, such as 2026-11-01T12:00:00Z; got {raw!r}"
        )
    return moment


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route() -> dict[str, Any]:
    """The rules this feature enforces, readable without a store.

    Sourced so a reviewer checks the claims against the quotes they came from
    rather than against prose, and so the page renders the rules instead of
    restating them. Includes the inferences and the two researched items this
    build deliberately does not implement.
    """
    return vocabulary()


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


@router.get("/pages")
def list_windows(
    request: Request,
    room_id: str | None = Query(default=None, description="Narrow to one room"),
    status: str | None = Query(
        default=None,
        description=(
            "Comma-separated access statuses: "
            + ", ".join(ACCESS_STATUSES)
            + ". Filters the derived badge, the way the vendor's "
            "GET /v1/pages?status= filters the dashboard."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every access window, with the derived badge each one wears.

    Filters on ``access_status`` rather than on the page's stored ``status``,
    because the stored field cannot answer the question a seller is asking: a page
    that expired keeps ``published``, and a page over its cap keeps ``published``
    too. Those are exactly the two pages a seller opens this screen to find.
    """
    wanted = None
    if status:
        wanted = [item.strip().lower() for item in status.split(",") if item.strip()]
        unknown = [item for item in wanted if item not in ACCESS_STATUSES]
        if unknown:
            raise AccessWindowInvalid(
                f"unknown access status {unknown[0]!r}; choose from {', '.join(ACCESS_STATUSES)}"
            )

    if room_id:
        controls(store).room(room_id)

    at = _at(request, None)
    windows = controls(store).windows(room_id=room_id, limit=limit, now=at)
    if wanted is not None:
        windows = [w for w in windows if w.status in wanted]
    return {
        "at": at.isoformat(),
        "room_id": room_id,
        "status": wanted,
        "count": len(windows),
        "windows": [w.to_dict() for w in windows],
    }


@router.get("/rooms/{room_id}/access")
def room_access(
    room_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every page in one room, with its window.

    The room is the unit a seller thinks in - "which of my rooms are closing?" -
    even though the window is stored per page.
    """
    engine = controls(store)
    engine.room(room_id)
    at = _at(request, None)
    windows = engine.windows(room_id=room_id, limit=1000, now=at)
    return {
        "room_id": room_id,
        "at": at.isoformat(),
        "count": len(windows),
        "closed": sum(1 for w in windows if not w.accessible),
        "windows": [w.to_dict() for w in windows],
    }


@router.get("/pages/{page_id}/access")
def page_access(
    page_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One page's window, with everything derived from it.

    ``available_actions`` comes from the same permission the write routes enforce,
    so the page cannot offer a button the route would refuse - the failure being
    a seller who is told they cannot change something on a screen that invited
    them to.
    """
    engine = controls(store)
    at = _at(request, None)
    window = engine.window(page_id, now=at)
    payload = window.to_dict()
    payload["at"] = at.isoformat()
    payload["available_actions"] = _actions(request, engine, page_id)
    return payload


def _actions(request: Request, engine: AccessControls, page_id: str) -> dict[str, bool]:
    """Which writes this caller could attempt, from the check the routes use."""
    try:
        engine.require_manage(page_id, role=_role(request), actor=_actor(request))
    except AccessWindowRefusal:
        return {
            "set_expiry": False,
            "clear_expiry": False,
            "set_view_limit": False,
            "clear_view_limit": False,
            "decline": False,
            "set_live": False,
        }
    return {
        "set_expiry": True,
        "clear_expiry": True,
        "set_view_limit": True,
        "clear_view_limit": True,
        "decline": True,
        "set_live": True,
    }


@router.get("/pages/{page_id}/buyer")
def buyer_view(
    page_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """What a buyer following this link gets, right now.

    The surface this workflow actually exists for. Takes no role and reads no
    header: someone following a link has not identified themselves, and the
    answer does not depend on who they are.

    Not an error response when the link is closed. A buyer who follows a dead
    link gets a page with a message on it, not a 4xx - which is what "the link
    will display an error message instead" describes. ``show_content`` is the
    branch the frontend takes; ``state`` is why.
    """
    at = _at(request, None)
    payload = controls(store).buyer_view(page_id, now=at)
    payload["at"] = at.isoformat()
    return payload


@router.get("/summary")
def summary(
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Counts per access status, and how many links are actually closed.

    ``closed`` is reported next to the statuses on purpose. A dashboard of badges
    alone would show one ``live`` and read as "everything is fine" while that one
    page is closed to every buyer - the specific misreading this workflow is
    about.
    """
    at = _at(request, None)
    windows = controls(store).windows(limit=1000, now=at)
    by_status = {name: 0 for name in ACCESS_STATUSES}
    for window in windows:
        by_status[window.status] = by_status.get(window.status, 0) + 1
    return {
        "at": at.isoformat(),
        "total": len(windows),
        "by_status": by_status,
        "closed": sum(1 for w in windows if not w.accessible),
        "open": sum(1 for w in windows if w.accessible),
        "with_expiry": sum(1 for w in windows if w.expiry_enabled),
        "with_view_limit": sum(1 for w in windows if w.view_limit_enabled),
    }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


@router.put("/pages/{page_id}/expiry")
def set_expiry(
    page_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Turn link expiry on or off, or change its length.

    Changeable at any time, and independent of the cap: nothing in this route
    reads or writes ``view_limit``. The clock is stamped now for a live page and
    left unset for a draft, because "The count of days starts when you publish the
    page. A draft page keeps the setting until you publish it."
    """
    body = payload or {}
    window = controls(store).set_expiry(
        page_id,
        enabled=body.get("enabled", True),
        days=body.get("days"),
        role=_role(request),
        actor=_actor(request),
        now=_at(request, body.get("at")),
    )
    return window.to_dict()


@router.delete("/pages/{page_id}/expiry")
def clear_expiry(
    page_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Remove the expiry setting. The view cap is left exactly as it was."""
    window = controls(store).clear_expiry(
        page_id,
        role=_role(request),
        actor=_actor(request),
    )
    return window.to_dict()


@router.put("/pages/{page_id}/view-limit")
def set_view_limit(
    page_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Turn the view cap on or off, or change its size.

    Independent of expiry, and counted against the page's lifetime openings
    rather than from the moment the cap was set. A cap raised above today's count
    re-opens a closed link without clearing anything, because the views really
    were views of that page.
    """
    body = payload or {}
    window = controls(store).set_view_limit(
        page_id,
        enabled=body.get("enabled", True),
        max_views=body.get("max_views"),
        role=_role(request),
        actor=_actor(request),
    )
    return window.to_dict()


@router.delete("/pages/{page_id}/view-limit")
def clear_view_limit(
    page_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Remove the cap.

    The only way a view-limited page re-opens: ``Set Live`` deliberately does not
    clear it, because the vendor documents that it does not.
    """
    window = controls(store).clear_view_limit(
        page_id,
        role=_role(request),
        actor=_actor(request),
    )
    return window.to_dict()


@router.post("/pages/{page_id}/views")
def record_view(
    page_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Count one opening of the page.

    The only route a buyer triggers, and the only one with no permission check -
    needing no role is the point. Refuses with 409 when the link is already
    closed, and records no row in that case, so a closed page cannot accrue views
    nobody made.
    """
    body = payload or {}
    return controls(store).record_view(
        page_id,
        viewer=body.get("viewer"),
        at=_at(request, body.get("at")),
    )


@router.post("/pages/{page_id}/decline")
def decline(
    page_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Close the link by hand.

    The second documented reason for a Declined badge, alongside expiry: "You've
    manually set the page as Declined." Declining an already-declined page is not
    a conflict - the caller's intent already holds.
    """
    window = controls(store).decline(
        page_id,
        role=_role(request),
        actor=_actor(request),
    )
    return window.to_dict()


@router.post("/pages/{page_id}/set-live")
def set_live(
    page_id: str,
    request: Request,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Revive a link: ``Share -> Set Live``.

    Clears the hand-declined flag and restarts the expiry clock, because setting a
    page live again is publishing it again and "The count of days starts when you
    publish the page". Does **not** clear the view cap - the vendor is explicit
    that reviving a view-limited page leaves the limit in place - so a page can
    come back from this route still closed, and the seller is told so in the
    response rather than discovering it on the buyer's next click.
    """
    window = controls(store).set_live(
        page_id,
        role=_role(request),
        actor=_actor(request),
        now=_at(request, None),
    )
    # The window is stamped at the revival instant, so the response is derived at
    # that same instant rather than a moment later.
    return window.to_dict()


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db, context):
    """Five pages, one per state the workflow can be in.

    A feature whose page is empty in the demo is a feature nobody can review, and
    the states worth showing are the ones hard to reach by clicking: a page
    expired on a date already past, a page whose cap is spent while its status
    still reads live, a page about to expire, a page that is open, and a draft
    holding a setting whose clock has not started.

    The view rows for the capped page go through ``record_view`` rather than being
    written by hand, so the cap is genuinely counted by ``count_where`` instead of
    faked with a stored number. That call refuses the view that meets the cap, so
    the refusal is caught rather than treated as a fault - a page one over its
    limit is what a real room looks like.
    """
    from dsr.access_controls import AccessControls
    from dsr.store import RecordStore

    store = RecordStore(db)
    engine = AccessControls(store)
    room_ids = [room_id for room_id, _account in (context.get("room_ids") or [])]
    now = context.get("now") or utcnow()
    if not room_ids:
        return None

    # The seeder is not a collaborator, and it writes as an instance
    # administrator. An archived room is read-only for a collaborator - earlier
    # features seed first and some of them archive a room to demonstrate their
    # own workflow - so writing this feature's windows as a collaborator made the
    # demo dataset depend on the order features happen to load in, and the
    # seeder reported this feature as failed when it landed on an archived room.
    # Choosing the identity here rather than filtering the rooms keeps the demo
    # rows present in either case, which is the point of a seed.
    SEED_ROLE = "instance_admin"

    def page(room_id: str, slug: str, title: str, *, published: bool) -> str:
        record = store.create(
            PAGES,
            {"slug": slug, "title": title, "blocks": []},
            room_id=room_id,
            actor="seed",
            source="seed",
        )
        if published:
            revision = store.create(
                "page_revision",
                {
                    "page_id": record["id"],
                    "number": 1,
                    "blocks": [],
                    "published_at": now.isoformat(),
                },
                room_id=room_id,
                actor="seed",
                source="seed",
            )
            store.update(
                record["id"],
                {"published_revision_id": revision["id"], "published_at": now.isoformat()},
                actor="seed",
                source="seed",
            )
        return record["id"]

    def window_at(row: int) -> str:
        return str(room_ids[row % len(room_ids)])

    # 1. Open, with a cap that has room left.
    open_page = page(window_at(0), "pricing", "Pricing and packaging", published=True)
    engine.set_view_limit(open_page, enabled=True, max_views=25, role=SEED_ROLE, actor="seed")

    # 2. About to expire: inside the seven-day warning horizon.
    expiring = page(window_at(1), "security-overview", "Security overview", published=True)
    engine.set_expiry(expiring, enabled=True, days=5, role=SEED_ROLE, actor="seed")

    # 3. Expired on a date already past: badged declined with nothing set by hand.
    expired = page(window_at(2), "procurement", "Procurement pack", published=True)
    engine.set_expiry(expired, enabled=True, days=3, role=SEED_ROLE, actor="seed")
    # Reopen the clock 40 days ago so a 3-day window closed on a date in the past.
    engine.set_live(expired, role=SEED_ROLE, actor="seed", now=now - timedelta(days=40))

    # 4. Cap spent, status still live: the case the whole workflow is about.
    #
    # Three views against a cap of two. The first two are admitted; the third
    # meets the cap and is refused, which is the rule working rather than a fault,
    # so the refusal is caught here. The page therefore ends closed with a count
    # sitting exactly on its limit - the state a real room reaches.
    capped = page(window_at(3), "implementation-plan", "Implementation plan", published=True)
    engine.set_view_limit(capped, enabled=True, max_views=2, role=SEED_ROLE, actor="seed")
    for index in range(3):
        try:
            engine.record_view(capped, viewer=f"buyer{index + 1}@northwind.example")
        except AccessWindowConflict:
            pass

    # 5. A draft that already carries a setting, to show the clock not running.
    draft = page(window_at(4), "case-studies", "Case studies", published=False)
    engine.set_expiry(draft, enabled=True, days=14, role=SEED_ROLE, actor="seed")

    states = []
    for record_id in (open_page, expiring, expired, capped, draft):
        window = engine.window(record_id, now=now)
        states.append(f"{window.status} ({window.buyer_state})")
    listed = ", ".join(states)
    return f"{len(states)} pages across the access states: {listed}"
