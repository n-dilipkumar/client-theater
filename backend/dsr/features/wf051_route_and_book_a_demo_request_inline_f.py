"""WF-051: route and book a demo request inline from a web form.

A Concierge Router is a declared flow of nodes. A prospect submits a webform, the
router qualifies and routes them automatically, and the same screen shows the
right seller's calendar so the prospect books a time without an email thread.

This module is the whole of the backend registration. The host discovers it, so
nothing in ``dsr/api.py`` learns this feature's name. Every dependency comes from
``dsr.deps`` rather than ``dsr.api``, which keeps the dependency direction one-way
and lets this module be imported on its own.

The two researched calls are modelled as routes named after the vendor's
operations, so a client written against the Edge API sample finds them where the
sample says they are. Neither is actually issued: the endpoints are served as data
at ``GET /api/wf-051/vocabulary`` so a client renders the mapping rather than
compiling it.

See ``docs/design/WF-051-concierge-router.md`` for the researched rules this
enforces and for the decisions the research left open.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.concierge_router import ConciergeRouterEngine
from dsr.concierge_router.errors import RouterError
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-051-route-and-book-a-demo-request-inline-f",
    "ticket": "WF-051",
    "name": "Route and book a demo request inline",
    "description": (
        "Declare a Concierge Router as a flow of nodes, route a webform submission "
        "against Data Fields and live CRM records, and commit a slot in the same "
        "step so a buyer does not wait on an email thread."
    ),
    "nav": [{"id": "concierge-router", "label": "Concierge router"}],
}

router = APIRouter(prefix="/api/wf-051", tags=["wf051"])


def get_engine(store: RecordStore = StoreDep) -> ConciergeRouterEngine:
    """One engine per request, over the store the app is holding.

    Built here rather than cached on ``app.state`` so an engine can never hold a
    store from a previous test's database.
    """
    return ConciergeRouterEngine(store)


EngineDep = Depends(get_engine)


def _source(method: str, suffix: str) -> str:
    """The audit source for a write: the route that served it, method included.

    Never a literal. The branch history is full of features whose audit log kept
    naming a path the app had stopped serving, and this project has shipped that
    defect before. Building it from ``router.prefix`` means a renamed route
    cannot leave a stale string behind.

    The method is part of the string because the core writes do the same
    (``f"POST /api/records/{collection}"``) and because a source naming only a
    path could not tell the ``GET`` that retires an expired session from the
    ``POST`` that commits a slot.
    """
    return f"{method} {router.prefix}{suffix}"


def _router_error(request: Request, exc: RouterError) -> JSONResponse:
    """Answer a domain refusal with the status it carries.

    ``status`` rides on the exception rather than being decided here, because
    these refusals are not all the same kind of thing: a router declared with no
    Catch All is a 400 because the declaration could never have worked, while a
    second commit against a booked session is a 409 because the request was well
    formed and what it conflicts with is the state of the system.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "code": exc.code, "status": exc.status, "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {RouterError: _router_error}


# --------------------------------------------------------------------------- #
# The vocabulary the domain enforces against
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every researched term, as data")
def vocabulary_route(engine: ConciergeRouterEngine = EngineDep) -> dict[str, Any]:
    return engine.vocabulary()


@router.get("/inferences", summary="Every decision the research left open")
def inferences_route(engine: ConciergeRouterEngine = EngineDep) -> dict[str, Any]:
    return engine.inferences()


@router.get("/summary", summary="Counts by publish state, booking state and outcome")
def summary_route(engine: ConciergeRouterEngine = EngineDep) -> dict[str, Any]:
    return engine.summary()


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


@router.get("/routers", summary="Every declared router")
def list_routers(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    routers = engine.routers(room_id=room_id, limit=limit)
    return {"count": len(routers), "routers": routers}


@router.post("/routers", status_code=201, summary="Declare a router")
def create_router(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a router, refusing any shape the research says could not work.

    The refusals are the researched absolutes, checked here rather than when a
    prospect arrives: the ``Trigger`` must be the first node, and the chain must
    end in a ``Catch All``.
    """
    return engine.create_router(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/routers")
    )


@router.get("/routers/{router_id}", summary="One router with its declared nodes")
def get_router(router_id: str, engine: ConciergeRouterEngine = EngineDep) -> dict[str, Any]:
    return engine.require_router(router_id)


@router.post("/routers/{router_id}/publish", summary="Publish or switch off a router")
def publish_router(
    router_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    return engine.publish_router(
        router_id,
        payload,
        room_id=room_id,
        actor=actor,
        # The literal route template, not the interpolated id. A source names a
        # route the app serves, and the test that checks this matches it
        # segment-wise against the registry, where the segment is spelled
        # ``{router_id}``. Interpolating the concrete id produces a source that
        # names nothing the registry can resolve.
        source=_source("POST", "/routers/{router_id}/publish"),
    )


# --------------------------------------------------------------------------- #
# The sellers whose calendars are offered
# --------------------------------------------------------------------------- #


@router.get("/sellers", summary="Every registered seller")
def list_sellers(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    sellers = engine.sellers(room_id=room_id, limit=limit)
    return {"count": len(sellers), "sellers": sellers}


@router.post("/sellers", status_code=201, summary="Register a seller")
def create_seller(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    return engine.register_seller(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/sellers")
    )


@router.get("/rooms/{room_id}/sellers", summary="The sellers in one room")
def room_sellers(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    sellers = engine.sellers(room_id=room_id, limit=limit)
    return {"room_id": room_id, "count": len(sellers), "sellers": sellers}


# --------------------------------------------------------------------------- #
# The first researched call: route and qualify
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/route", status_code=201, summary="Route and qualify a prospect")
def route_request(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    """Open a routing session, modelled on the researched ``/rest`` call.

    Returns the researched field names, so a client written against the vendor's
    Edge API sample works unchanged: ``routeId``, ``routingLink``,
    ``schedulingAllowed`` and ``assignment{userId,type}``.
    """
    return engine.route(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/rooms/{room_id}/route")
    )


@router.get("/rooms/{room_id}/route/{route_id}", summary="Read one routing session")
def get_session(
    room_id: str,
    route_id: str,
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    return engine.session(route_id, source=_source("GET", "/rooms/{room_id}/route/{route_id}"))


@router.get(
    "/rooms/{room_id}/route/{route_id}/slots",
    summary="The slot list the Display Calendar modal would render",
)
def get_slots(
    room_id: str,
    route_id: str,
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    return engine.slots(route_id, source=_source("GET", "/rooms/{room_id}/route/{route_id}/slots"))


# --------------------------------------------------------------------------- #
# The second researched call: commit the chosen slot
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/route/{route_id}/schedule-simple",
    status_code=201,
    summary="Commit the chosen slot",
)
def schedule_simple(
    room_id: str,
    route_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    """Commit a slot, modelled on the researched ``schedule-simple`` call.

    Returns a ``meetingId``. A session commits once: a second call is a 409,
    because it would put two prospects in one slot on one seller's calendar.
    """
    return engine.schedule_simple(
        route_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/route/{route_id}/schedule-simple"),
    )


@router.post(
    "/rooms/{room_id}/route/{route_id}/expire",
    summary="Run the Display Calendar node's Time Elapsed timer now",
)
def expire_session(
    room_id: str,
    route_id: str,
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    """Move a pending session to ``not_scheduled`` and run the follow-up path.

    Nothing polls for this. The route exists so an operator can retire a prospect
    who went quiet, and so a test can reach the researched outcome without waiting
    an hour for the timer.
    """
    return engine.expire(
        route_id,
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/route/{route_id}/expire"),
    )


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/bookings", summary="Every booking in a room")
def list_bookings(
    room_id: str,
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    bookings = engine.bookings(
        room_id=room_id, limit=limit, source=_source("GET", "/rooms/{room_id}/bookings")
    )
    if state:
        bookings = [booking for booking in bookings if booking["state"] == state]
    return {"room_id": room_id, "count": len(bookings), "bookings": bookings}


@router.get("/rooms/{room_id}/bookings/{booking_id}", summary="One booking")
def get_booking(
    room_id: str,
    booking_id: str,
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    return engine.session(
        booking_id, source=_source("GET", "/rooms/{room_id}/bookings/{booking_id}")
    )


@router.delete("/rooms/{room_id}/bookings/{booking_id}", summary="Cancel a booking")
def cancel_booking(
    room_id: str,
    booking_id: str,
    actor: str | None = Query(default=None),
    engine: ConciergeRouterEngine = EngineDep,
) -> dict[str, Any]:
    """Cancel a booking, which runs the researched Not Scheduled path.

    The seller's slot is freed, so another prospect who saw it free can take it.
    """
    return engine.cancel(
        booking_id,
        room_id=room_id,
        actor=actor,
        source=_source("DELETE", "/rooms/{room_id}/bookings/{booking_id}"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not only the happy path.

    Four routers and six sessions, deliberately including the states a reviewer
    needs to see: a draft that was never published, a router switched off, a
    prospect the timer retired, a booking that was cancelled, a rule that matched
    and then deliberately offered no calendar, and a pending session still waiting
    for a slot. Demo data containing only success teaches a reviewer nothing.

    Every string here is plain ASCII. The seeder prints this on a Windows console,
    and one RIGHTWARDS ARROW in a recovered feature broke the whole seed run.
    """
    room_ids: list[tuple[str, str]] = context.get("room_ids") or []
    if not room_ids:
        return "0 routers (no rooms to scope them to)"
    room_id, account = room_ids[0]

    now = context["now"]
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _create(collection: str, payload: dict[str, Any], actor: str = "dana") -> dict[str, Any]:
        return db.create(collection, payload, room_id=room_id, actor=actor, source="seed")

    sellers: list[dict[str, Any]] = []
    for name, email, team, position in [
        ("Dana Ivers", f"dana@{account}", "field-sales", 0),
        ("Priya Raman", f"priya@{account}", "field-sales", 1),
        ("Sam Ortiz", f"sam@{account}", "sdr", 0),
    ]:
        sellers.append(
            _create(
                "concierge_seller",
                {
                    "name": name,
                    "email": email,
                    "team": team,
                    "calendar_connected": True,
                    "busy": [
                        {"start": f"{stamp[:10]}T12:00:00Z", "end": f"{stamp[:10]}T13:00:00Z"}
                    ],
                    "working_hours": {"start_hour": 9, "end_hour": 17, "weekdays": [0, 1, 2, 3, 4]},
                    "round_robin_position": position,
                },
                actor=name.split()[0].lower(),
            )
        )

    def _calendar(
        assignment: dict[str, Any], types: list[str], minutes: int = 60
    ) -> dict[str, Any]:
        return {
            "type": "display_calendar",
            "assignment": assignment,
            "meeting_types": types,
            "timer_minutes": minutes,
        }

    trigger = {
        "type": "trigger",
        "actions": ["webform_is_submitted"],
        "field_map": {
            "work_email": "email",
            "company": "company",
            "employee_count": "employee_count",
        },
    }

    declared = _create(
        "concierge_router",
        {
            "slug": "enterprise-demo",
            "name": "Enterprise demo",
            "nodes": [
                trigger,
                {
                    "type": "routing_rule",
                    "name": "Named account owner",
                    "kind": "crm_ownership",
                    "conditions": [
                        {
                            "field": "owner_team",
                            "source": "crm_object",
                            "operator": "equals",
                            "value": "field-sales",
                        }
                    ],
                    "calendar": _calendar({"type": "owner"}, ["demo", "deep dive"]),
                },
                {
                    "type": "display_calendar",
                    "name": "Round-robin desk",
                    "assignment": {"type": "round_robin", "policy": "sdr-pool"},
                    "meeting_types": ["intro"],
                    "timer_minutes": 45,
                },
                {"type": "catch_all", "name": "Deal desk"},
                {
                    "type": "redirect_to",
                    "name": "Thank you",
                    "url": f"https://{account}/thanks",
                    "timer_minutes": 5,
                },
            ],
            "field_map": {
                "work_email": "email",
                "company": "company",
                "employee_count": "employee_count",
            },
            "catch_all": "Deal desk",
            "publish_state": "published",
            "deployment": ["web_form", "router_link"],
            "enabled": True,
            "routing_link_base": f"https://{account}.chilipiper.com",
        },
    )

    _create(
        "concierge_router",
        {
            "slug": "webinar-signup",
            "name": "Webinar signup",
            "nodes": [
                trigger,
                {"type": "catch_all", "name": "SDR queue"},
                _calendar({"type": "round_robin", "policy": "sdr-pool"}, ["intro"]),
            ],
            "field_map": {"work_email": "email"},
            "catch_all": "SDR queue",
            "publish_state": "draft",
            "deployment": [],
            "enabled": True,
            "routing_link_base": "",
        },
        actor="sam",
    )

    _create(
        "concierge_router",
        {
            "slug": "paused-partner",
            "name": "Partner intake, paused",
            "nodes": [
                trigger,
                {"type": "catch_all", "name": "Partners"},
                _calendar({"type": "individual_user", "user_id": sellers[0]["id"]}, ["intro"]),
            ],
            "field_map": {"work_email": "email"},
            "catch_all": "Partners",
            "publish_state": "published",
            "deployment": ["in_app_button"],
            "enabled": False,
            "routing_link_base": "",
        },
        actor="sam",
    )

    def _session(**overrides: Any) -> dict[str, Any]:
        base = {
            "router_id": declared["id"],
            "router_slug": "enterprise-demo",
            "rule": "Named account owner",
            "routing_outcome": "rule_matched",
            "state": "booked",
            "scheduling_allowed": True,
            "guest": {"email": f"buyer@{account}", "company": "Northwind"},
            "data_fields": {"email": f"buyer@{account}", "company": "Northwind"},
            "assignment": {"type": "user", "user_id": sellers[0]["id"], "policy": ""},
            "seller_id": sellers[0]["id"],
            "seller_name": "Dana Ivers",
            "meeting_types": ["demo"],
            "slots": [
                {
                    "start": f"{stamp[:10]}T09:00:00Z",
                    "end": f"{stamp[:10]}T09:30:00Z",
                    "duration_minutes": 30,
                    "meeting_type": "demo",
                }
            ],
            "routing_link": "/concierge-router/enterprise-demo/routing/buyer",
            "routing_link_base": f"https://{account}.chilipiper.com",
            "timer_minutes": 60,
            "timer_expires_at": f"{stamp[:10]}T12:00:00Z",
            "meeting_id": f"mtg_{declared['id'][:12]}",
            "slot": {
                "start": f"{stamp[:10]}T09:00:00Z",
                "end": f"{stamp[:10]}T09:30:00Z",
            },
            "meeting_type": "demo",
            "booked_at": stamp,
            "post_booking_nodes": ["redirect_to"],
            "redirect_url": f"https://{account}/thanks",
        }
        base.update(overrides)
        return _create("concierge_booking", base)

    # A booked meeting, the happy path.
    _session()

    # A prospect the Time Elapsed timer retired: the researched outcome, and the
    # state a reviewer most needs to see because it is the one the research does
    # not spell out.
    _session(
        guest={"email": f"quiet@{account}", "company": "Contoso"},
        state="not_scheduled",
        meeting_id="",
        slot={},
        booked_at="",
        notification={
            "channel": "email",
            "recipient": "Priya Raman",
            "reason": "the Display Calendar node's Time Elapsed timer expired",
            "at": stamp,
            "sent": False,
        },
        reassigned_to={"id": sellers[1]["id"], "name": "Priya Raman"},
    )

    # A pending session still waiting for a slot, which is the state a prospect
    # actually sits in while they choose a time.
    _session(
        guest={"email": f"waiting@{account}", "company": "Fabrikam"},
        state="pending",
        meeting_id="",
        slot={},
        booked_at="",
        meeting_types=["demo", "deep dive"],
        rule="Deal desk",
        routing_outcome="catch_all_matched",
    )

    # A rule that matched and then deliberately offered no calendar.
    _session(
        guest={"email": f"nocalendar@{account}", "company": "Tailspin"},
        state="not_offered",
        scheduling_allowed=False,
        routing_outcome="rule_declined",
        meeting_id="",
        slot={},
        booked_at="",
        seller_id="",
        seller_name="",
        meeting_types=[],
        slots=[],
        assignment={"type": "owner", "user_id": "", "policy": ""},
    )

    # A booking that was cancelled, which freed the seller's slot again.
    _session(
        guest={"email": f"cancelled@{account}", "company": "Adventure Works"},
        state="not_scheduled",
        meeting_id="",
        slot={},
        booked_at="",
        cancelled_at=stamp,
        notification={
            "channel": "email",
            "recipient": "Dana Ivers",
            "reason": "the booking was cancelled",
            "at": stamp,
            "sent": False,
        },
    )

    return (
        "3 routers (1 published, 1 draft, 1 switched off), 3 sellers, "
        "6 bookings (1 booked, 1 pending, 1 not scheduled by the timer, "
        "1 not offered, 1 cancelled, 1 not scheduled by a cancellation)"
    )
