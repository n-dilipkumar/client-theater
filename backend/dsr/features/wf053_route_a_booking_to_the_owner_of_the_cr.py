"""WF-053: route a booking to the owner of the CRM record.

The researched workflow, in full. An admin creates an **Ownership** scheduling
link; a prospect lands on it, or a backend calls the Edge API; Chili Piper resolves
the owner of the guest's matching CRM record - the lead, contact, or account owner
- **at booking time**; availability is read from that owner's connected calendar;
the prospect books and the owner gets the meeting. The researched alternative is a
**CRM Ownership** routing rule in a Concierge router that checks whether a rep from
a team owns the Lead, Contact *or* Account and routes to that rep.

The domain logic is in :mod:`dsr.ownership_routing`, which this module does not own
and which no other feature could have written into its own path. What lives here is
the three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-053``, and the room is where a routing decision lives.**
The researched link is a workspace asset a prospect meets before any room exists, so
links, CRM records, reps and routing sessions are account-level. A decision is taken
for *a* room's prospect though, so the two calls that produce one are room-scoped:
``/rooms/{room_id}/check`` and ``/rooms/{room_id}/init-simple``, with the second call
booking against the ``routingId`` the first returned.

**``source=`` comes from the route.** Every write below passes a string built from
``router.prefix``, so the audit row and the route table cannot drift. A hardcoded
string inside a domain method is a defect, and the same class of bug has shipped in
this codebase before: a feature's audit log kept naming a path the app had stopped
serving. ``source`` is a *required* keyword on every writing method of
:class:`~dsr.ownership_routing.engine.OwnershipEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.**
:class:`~dsr.ownership_routing.errors.OwnershipError` is the base of every refusal
in the package, and each subclass carries its own ``status`` and ``code``, so one
handler answers 400 for a missing ``guestEmail`` and 409 for a routing session
that has already been booked - which are not the same kind of problem. It is a
domain type, so registering it globally cannot intercept anything unrelated
elsewhere in the product. ``RecordNotFound`` is deliberately *not* claimed: the
core app already maps it to 404, and two handlers for one type is a collision the
host refuses.

**The two researched calls stay two calls.** ``init-simple`` answers with
``startTimes`` and a ``routingId``; ``schedule-simple`` takes a ``startTime`` that
was in that list. Merging them would lose the property that makes the flow safe.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.ownership_routing import (
    FORBIDDEN_ON_OWNERSHIP_PATH,
    RECORD_COLLECTION,
    REP_COLLECTION,
    OwnershipEngine,
    OwnershipError,
    inferences as ownership_inferences,
    rules as ownership_rules,
    vocabulary as ownership_vocab,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-053-route-a-booking-to-the-owner-of-the-cr",
    "ticket": "WF-053",
    "name": "Route a booking to the owner of the CRM record",
    "description": (
        "An Ownership scheduling link resolves the lead, contact, or account owner of the guest's "
        "CRM record at booking time, reads that owner's connected calendar, and hands the prospect "
        "the owner's slots. The researched CRM Ownership alternative routes through a chain of "
        "team and value rules that has to end in a catch-all, and the nodes the research forbids "
        "on an ownership path are refused where they are added."
    ),
    "nav": [{"id": "ownership-routing", "label": "Ownership routing"}],
}

router = APIRouter(prefix="/api/wf-053", tags=["wf053"])


def get_engine(store: RecordStore = StoreDep) -> OwnershipEngine:
    """An :class:`OwnershipEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, the CRM seam and a clock, and an ``app.state`` entry is
    exactly the edit to the shared ``dsr/api.py`` that the feature host exists to
    make unnecessary. Building it here also leaves the clock and the CRM seam
    plain constructor arguments, which is what lets a test drive the whole workflow
    with rows of its own and a clock it controls.
    """
    return OwnershipEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _ownership_error(request: Request, exc: OwnershipError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``OwnershipError`` is the base of every
    refusal in :mod:`dsr.ownership_routing` - a link type this workflow does not
    route, a missing ``guestEmail``, a chain with no catch-all, an owner with no
    connected calendar, a session already booked - and all of them are the caller's
    to fix. The status rides on the exception rather than being decided here,
    because a malformed request and a conflict with state that already exists are
    different problems and answering both 400 would hide the second.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {OwnershipError: _ownership_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the five link types, the three CRM objects, the Edge
    payloads, the rule kinds, and the nodes refused on an Ownership path.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a key added in one place reaches every
    client at once.
    """
    return {
        **ownership_vocab.published_vocabulary(),
        "node_guardrail": {
            "forbidden": list(FORBIDDEN_ON_OWNERSHIP_PATH),
            "quote": ownership_vocab.FORBIDDEN_QUOTE,
            "preview_only": True,
        },
    }


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research for WF-053 is precise about the link type, the required
    ``guestEmail`` and the forbidden nodes, and silent about the resolution order,
    the slot arithmetic, and what a second schedule call does. Those gaps are
    product behaviour rather than comments, so they are collected here for a
    reviewer to disagree with by name. A read with no side effect, so it needs no
    store.
    """
    return ownership_inferences.describe()


@router.get("/nodes/guardrail", summary="Which nodes an Ownership path refuses")
def node_guardrail(
    nodes: str | None = Query(
        default=None, description="comma-separated node names to check against the guardrail"
    ),
) -> dict[str, Any]:
    """The forbidden nodes, the researched sentence, and an advisory check.

    The saving path refuses these nodes; this endpoint reports them without
    refusing, so a page can show the problem on a draft before anybody tries to
    save it. Read-only and therefore needing no store.
    """
    names = [entry.strip() for entry in (nodes or "").split(",") if entry.strip()]
    return {
        "ownership_path": {
            "forbidden": list(FORBIDDEN_ON_OWNERSHIP_PATH),
            "quote": ownership_vocab.FORBIDDEN_QUOTE,
        },
        "other_path": {"forbidden": [], "quote": ownership_vocab.FORBIDDEN_QUOTE},
        "warnings": ownership_rules.nodes_warnings(names, ownership_path=True),
    }


@router.get("/catalog", summary="The CRM records, reps and teams the resolution reads")
def catalog(engine: OwnershipEngine = EngineDep) -> dict[str, Any]:
    """Everything a reviewer needs to understand why a prospect was routed where.

    The extensibility note says "Teams + Distributions can hold many owners", so
    team membership is a product surface rather than an internal detail: a reader
    needs to see which reps are on which team to know why a CRM Ownership rule did
    or did not match.
    """
    return engine.catalog()


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Links, routes, bookings, and decisions by outcome and object type.

    The summary is computed over exactly the rows the same filters would return,
    so a room-scoped total above an unscoped list cannot be misread as a
    product-wide one.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


@router.get("/links", summary="List Ownership scheduling links")
def list_links(
    room_id: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """The declared links, newest first.

    The full envelope per link, because an administrator configuring one needs the
    record id to patch it, the resolved interval, and the chain it will run.
    """
    records = engine.links(room_id=room_id, limit=limit)
    if enabled is not None:
        records = [
            record for record in records if bool(record["data"].get("enabled", True)) is enabled
        ]
    return {"count": len(records), "links": records}


@router.post("/links", status_code=201, summary="Declare an Ownership scheduling link")
def create_link(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Declare an Ownership scheduling link.

    "Admin creates an **Ownership** scheduling link (link type ``Ownership``)."
    Only Ownership is accepted: the other four researched types route by something
    this workflow does not consult, and declaring one here would make the link's own
    type a lie.

    Validated in full before the row is created, so a bad type, a malformed
    interval, a chain with no catch-all, or a forbidden node cannot leave a
    half-configured link behind that later looks usable.
    """
    return engine.create_link(
        payload, room_id=payload.get("room_id"), actor=actor, source=f"POST {router.prefix}/links"
    )


@router.get("/links/{link_id}", summary="Read one link")
def read_link(link_id: str, engine: OwnershipEngine = EngineDep) -> dict[str, Any]:
    """One link, its chain, and its capped rolling history of decisions."""
    record = engine.get_link(link_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"scheduling link {link_id} not found")
    return record


@router.patch("/links/{link_id}", summary="Patch a link")
def update_link(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Patch a link, re-validating the whole merged result.

    Re-validated rather than patched, because every field this workflow checks
    interacts: changing ``type`` to Round Robin has to fail the same way a fresh
    declaration would, and changing ``rules`` to a chain with no catch-all must not
    leave the link holding one.
    """
    return engine.update_link(
        link_id, payload, actor=actor, source=f"PATCH {router.prefix}/links/{link_id}"
    )


@router.delete("/links/{link_id}", status_code=204, summary="Delete a link")
def delete_link(
    link_id: str,
    actor: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> Response:
    """Soft-delete a link. 204.

    Its routes and bookings stay auditable, which is the point: the history of who
    a prospect was routed to outlives the link that routed them.
    """
    if engine.get_link(link_id) is None:
        raise HTTPException(status_code=404, detail=f"scheduling link {link_id} not found")
    engine.delete_link(link_id, actor=actor, source=f"DELETE {router.prefix}/links/{link_id}")
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The CRM rows the resolution reads
# --------------------------------------------------------------------------- #


@router.get("/records", summary="List the CRM records owners are resolved from")
def list_records(
    room_id: str | None = Query(default=None),
    object_type: str | None = Query(default=None, description="lead | contact | account"),
    owner_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """The Lead / Contact / Account rows an Ownership link resolves against.

    These stand in for the CRM's own tables. A rep reads them to understand *why*
    a prospect reached them, and a team populates them from its own import.
    """
    records = engine.crm.records(
        room_id=room_id, object_type=object_type, owner_id=owner_id, limit=limit
    )
    return {"count": len(records), "records": records, "collection": RECORD_COLLECTION}


@router.get("/reps", summary="List the reps an owner id resolves to")
def list_reps(
    limit: int = Query(default=200, ge=1, le=1000),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Every rep with a connected calendar, which is what availability needs.

    A rep with no calendar is listed too, because "this prospect resolves to
    someone who cannot take meetings" is exactly what a rep needs to see before
    they fix it.
    """
    reps = engine.crm.reps(limit=limit)
    return {"count": len(reps), "reps": reps, "collection": REP_COLLECTION}


# --------------------------------------------------------------------------- #
# The researched API calls
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/check", summary="Who would this guest reach?")
def check(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Run steps 2 to 4 for a guest and report what *would* happen.

    Writes nothing at all - not a routing session, not a decision record, not the
    link's history. It is the read-only half of ``init-simple``, for a form that
    wants to say "this reaches Sam" before the prospect commits.

    The answer is identical to what ``init-simple`` would produce, because both
    call the same resolve, the same chain evaluation and the same calendar
    arithmetic; only the consequences differ.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    link_id = str(payload.get("link_id") or "")
    if not link_id:
        raise HTTPException(
            status_code=400, detail="link_id is required; which link did the guest land on?"
        )
    return engine.check(link_id, payload, room_id=room_id)


@router.post(
    "/rooms/{room_id}/init-simple",
    status_code=201,
    summary="Resolve the owner and offer their slots",
)
def init_simple(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Steps 2 to 4: resolve the owner, read their calendar, answer with slots.

    The researched init call sends ``{link, guestEmail, interval}`` and answers
    with "Available ``startTimes`` plus a ``routingId`` for the second call". The
    ``guestEmail`` is required on this link type - "it is required so Chili Piper
    can resolve the owner from your CRM" - so a call without one is refused rather
    than answered with nobody.

    Writes the routing session, the decision record and the link's history entry
    in one transaction, so a decision can never exist without the route that
    produced it.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    link_id = str(payload.get("link_id") or "")
    if not link_id:
        raise HTTPException(
            status_code=400, detail="link_id is required; which link did the guest land on?"
        )
    return engine.init_simple(
        link_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/init-simple",
    )


@router.post(
    "/rooms/{room_id}/schedule-simple", status_code=201, summary="Book one of the offered slots"
)
def schedule_simple(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Step 4's second half: book a slot this room's prospect was offered.

    The researched schedule-simple payload is ``{startTime, guestEmail}`` and it
    names the ``routingId`` in its path. Three refusals guard it: a session that
    has already been booked, a ``startTime`` the session never offered, and a
    ``guestEmail`` that is not the one the session was opened for.

    Booking, the session's transition, and the link's history entry land in one
    transaction, because a booking that exists beside an open session is the state
    that would let one slot be taken twice.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    route_id = str(payload.get("routing_id") or payload.get("routingId") or "")
    if not route_id:
        raise HTTPException(
            status_code=400,
            detail="routing_id is required; the researched schedule-simple path carries the routeId",
        )
    return engine.book(
        route_id,
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/schedule-simple",
    )


# --------------------------------------------------------------------------- #
# Routes and bookings
# --------------------------------------------------------------------------- #


@router.get("/routes", summary="List routing sessions")
def list_routes(
    room_id: str | None = Query(default=None),
    link_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="open | booked"),
    owner_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Every routing session, newest first, with the slots it offered.

    ``state`` separates the sessions still waiting for a booking from the ones
    whose slot is gone, which is the distinction a rep reads first.
    """
    records = engine.routes(
        room_id=room_id, link_id=link_id, state=state, owner_id=owner_id, limit=limit
    )
    return {"count": len(records), "routes": records}


@router.get("/routes/{route_id}", summary="Read one routing session")
def read_route(route_id: str, engine: OwnershipEngine = EngineDep) -> dict[str, Any]:
    """One routing session, its offered slots, and whether it is still open."""
    record = engine.get_route(route_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"routing session {route_id} not found")
    return record


@router.get("/rooms/{room_id}/bookings", summary="A room's bookings")
def room_bookings(
    room_id: str,
    state: str | None = Query(default=None, description="confirmed | cancelled"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """The bookings taken for one room.

    Room-scoped because a booking is taken for a room's prospect, so a rep reads
    their own bookings from their own room and not from a product-wide feed.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    records = engine.bookings(room_id=room_id, state=state, limit=limit)
    return {"room_id": room_id, "count": len(records), "bookings": records}


@router.get("/bookings/{booking_id}", summary="Read one booking")
def read_booking(booking_id: str, engine: OwnershipEngine = EngineDep) -> dict[str, Any]:
    """One booking, or 404 if it does not exist."""
    record = engine.get_booking(booking_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} not found")
    return record


@router.post(
    "/rooms/{room_id}/bookings/{booking_id}/cancel", summary="Cancel a booking and release its slot"
)
def cancel_booking(
    room_id: str,
    booking_id: str,
    actor: str | None = Query(default=None),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Cancel a booking, releasing the slot back into the owner's availability.

    Room-scoped so a cancellation is served under the room whose prospect it
    cancels, and checked here rather than trusted from the body.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    record = engine.get_booking(booking_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"booking {booking_id} not found")
    return engine.cancel_booking(
        booking_id,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/bookings/{{booking_id}}/cancel",
    )


@router.get("/decisions", summary="List routing decisions")
def list_decisions(
    room_id: str | None = Query(default=None),
    link_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None, description="resolved | catch_all"),
    owner_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: OwnershipEngine = EngineDep,
) -> dict[str, Any]:
    """Every routing decision, newest first.

    A decision records which rule matched and why, which CRM object the owner came
    from, and how many slots were offered - which is the answer to "why did this
    prospect reach me", asked often enough to deserve its own records rather than a
    re-derivation.
    """
    records = engine.decisions(
        room_id=room_id, link_id=link_id, outcome=outcome, owner_id=owner_id, limit=limit
    )
    return {"count": len(records), "decisions": records}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The reps the demo routes to, including the two states that make the demo worth
#: reading: a rep whose calendar is not connected, and a record whose owner is not
#: in this workspace. A demo of only clean resolutions would teach nothing about the
#: two failures this workflow exists to name.
DEMO_REPS: tuple[dict[str, Any], ...] = (
    {
        "owner_id": "005-nadia",
        "name": "Nadia A. Farouk",
        "email": "nadia.farouk@soluspring.example",
        "team": "Enterprise",
        "calendar": {"provider": "google", "connected": True, "buffer_minutes": 15},
    },
    {
        "owner_id": "005-rui",
        "name": "Rui Silva",
        "email": "rui.silva@soluspring.example",
        "team": "Enterprise",
        "calendar": {"provider": "outlook", "connected": True},
    },
    {
        "owner_id": "005-priya",
        "name": "Priya Raman",
        "email": "priya.raman@soluspring.example",
        "team": "Commercial",
        "calendar": {"provider": "google", "connected": False},
    },
    {
        "owner_id": "005-desk",
        "name": "SoluSpring Deal Desk",
        "email": "desk@soluspring.example",
        "team": "Commercial",
        "calendar": {"provider": "outlook", "connected": True},
    },
)

#: The CRM rows the demo resolves against. ``object_type`` and ``owner_id`` are the
#: two fields the lookup actually reads; the rest is what a rep reads to understand
#: a decision. The lead with no owner, and the record naming a rep this workspace
#: does not have, are the interesting states.
DEMO_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "object_type": "lead",
        "name": "Marguerite Oyelaran",
        "email": "m.oyelaran@northwind.example",
        "owner_id": "005-nadia",
        "company": "Northwind Traders",
        "room_index": 0,
    },
    {
        "object_type": "contact",
        "name": "Devendra Rao",
        "email": "Devendra.Rao@contoso.example",
        "owner_id": "005-rui",
        "account_id": "acct-contoso",
        "room_index": 1,
    },
    {
        "object_type": "account",
        "name": "Fabrikam Logistics",
        "domain": "fabrikam.example",
        "owner_id": "005-rui",
        "room_index": 2,
    },
    {
        # Lead with no owner of its own: the lookup skips it and falls through to
        # the Contact the same address also appears on.
        "object_type": "lead",
        "name": "Devendra Rao",
        "email": "devendra.rao@contoso.example",
        "owner_id": "",
        "company": "Contoso Health",
        "room_index": 1,
    },
    {
        # Names an owner id no rep in this workspace answers to. On a link with a
        # catch-all this is a *routing* outcome rather than a refusal - the prospect
        # reaches the deal desk and the decision records the dead owner id - so the
        # second demo link below is where the honest refusal is demonstrated.
        "object_type": "contact",
        "name": "Ines Baptista",
        "email": "ines.baptista@adventure.example",
        "owner_id": "005-former-employee",
        "room_index": 3,
    },
    {
        "object_type": "lead",
        "name": "Tomás Iglesias",
        "email": "t.iglesias@fabrikam.example",
        "owner_id": "005-priya",
        "company": "Fabrikam Logistics",
        "room_index": 2,
    },
)

#: The Lead-to-Account setting, declared the way the research frames it: the
#: workspace's own existing setting, read rather than configured here.
DEMO_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "default",
        "lead_to_account_matching": True,
        "l2a_note": "read from the workspace setting",
    },
)

#: Two links: the plain Ownership link, and one carrying the researched CRM
#: Ownership rule chain that ends in a catch-all.
DEMO_LINKS: tuple[dict[str, Any], ...] = (
    {
        "name": "Ownership — book with your rep",
        "type": "Ownership",
        "linkId": "own_demo_direct",
        "resolution_order": ["lead", "contact", "account"],
        "rules": [{"kind": "catch_all", "name": "Anything not matched", "owner_id": "005-desk"}],
    },
    {
        "name": "Ownership — Enterprise routed by team",
        "type": "Ownership",
        "linkId": "own_demo_team",
        "resolution_order": ["lead", "contact", "account"],
        "rules": [
            {
                "kind": "crm_ownership",
                "name": "Owner is on the Enterprise team",
                "team": "Enterprise",
            },
            {"kind": "catch_all", "name": "Deal desk", "owner_id": "005-desk"},
        ],
    },
    {
        # Deliberately misconfigured: the catch-all names a rep this workspace has
        # not been given. It saves because the catch-all *is* declared, and it is
        # worth having in the demo because it is the one configuration that is legal
        # to declare and impossible to route to - the failure only shows up when a
        # prospect actually falls through to it.
        "name": "Ownership — catch-all points at a rep who has not joined",
        "type": "Ownership",
        "linkId": "own_demo_unprovisioned",
        "rules": [
            {
                "kind": "catch_all",
                "name": "Unprovisioned desk",
                "owner_id": "005-desk-unprovisioned",
            }
        ],
    },
)

#: The guests the demo runs, one per state worth showing: a lead owner, an account
#: owner, a catch-all fall-through, and two refusals.
DEMO_GUESTS: tuple[dict[str, Any], ...] = (
    {
        "link": "own_demo_direct",
        "email": "m.oyelaran@northwind.example",
        "room_index": 0,
        "book": True,
    },
    {"link": "own_demo_direct", "email": "ops@fabrikam.example", "room_index": 2, "book": False},
    {
        "link": "own_demo_team",
        "email": "devendra.rao@contoso.example",
        "room_index": 1,
        "book": True,
    },
    {
        "link": "own_demo_team",
        "email": "unknown.guest@newco.example",
        "room_index": 0,
        "book": False,
    },
    # Resolves to a rep whose calendar is not connected.
    {
        "link": "own_demo_direct",
        "email": "t.iglesias@fabrikam.example",
        "room_index": 2,
        "book": False,
    },
    # Names an owner id no rep answers to, and the link's catch-all catches them.
    {
        "link": "own_demo_direct",
        "email": "ines.baptista@adventure.example",
        "room_index": 3,
        "book": False,
    },
    # Falls through to a catch-all that names a rep this workspace does not have:
    # the one state that is legal to declare and impossible to route to.
    {
        "link": "own_demo_unprovisioned",
        "email": "stranger@newco.example",
        "room_index": 0,
        "book": False,
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the reps, CRM records, settings, links, routes and bookings.

    The routes come from running the real :class:`OwnershipEngine` over the real
    rules, so the demo cannot show a shape the workflow would not produce, and
    seeding opens no socket because the CRM here is the audited store.

    Deliberately mixed. A demo of only clean resolutions would teach nothing about
    the three states this workflow exists for: the catch-all fall-through, the owner
    with no connected calendar, and the record naming a rep this workspace does not
    have. All three are run here and expected, so the demo asserts the behaviour
    rather than a reader having to take the seed's word for it.

    Returns a description the seeder prints, which says how many of each outcome
    landed so a reviewer can see at a glance that the demo is mixed.
    """
    store = RecordStore(db)
    engine = OwnershipEngine(store, clock=lambda: context["now"])
    source = "seed"
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        return "0 routes (no rooms to scope them to)"

    def room_at(index: int) -> str:
        return rooms[index % len(rooms)][0]

    for rep in DEMO_REPS:
        store.create(REP_COLLECTION, dict(rep), actor="dana", source=source)
    for setting in DEMO_SETTINGS:
        store.create("crm_owner_setting", dict(setting), actor="dana", source=source)

    by_owner: dict[str, str] = {}
    for spec in DEMO_RECORDS:
        body = dict(spec)
        index = body.pop("room_index", None)
        record = store.create(
            RECORD_COLLECTION,
            body,
            room_id=room_at(index) if index is not None else None,
            actor="dana",
            source=source,
        )
        by_owner.setdefault(str(body.get("email") or ""), record["id"])

    links: dict[str, str] = {}
    for spec in DEMO_LINKS:
        payload = dict(spec)
        payload["interval"] = _seed_interval(context["now"])
        created = engine.create_link(payload, actor="dana", source=source)
        links[str(created["data"]["linkId"])] = created["id"]

    outcomes: list[str] = []
    bookings = 0
    refusals: list[str] = []
    for guest in DEMO_GUESTS:
        room_id = room_at(guest["room_index"])
        try:
            opened = engine.init_simple(
                links[guest["link"]],
                {"guestEmail": guest["email"]},
                room_id=room_id,
                actor="dana",
                source=source,
            )
            outcomes.append(str(opened["outcome"]))
            if guest.get("book") and opened["start_times"]:
                engine.book(
                    str(opened["routing_id"]),
                    {"startTime": opened["start_times"][0], "guestEmail": guest["email"]},
                    room_id=room_id,
                    actor="dana",
                    source=source,
                )
                bookings += 1
        except OwnershipError as exc:
            refusals.append(exc.code)

    tally: dict[str, int] = {}
    for outcome in outcomes:
        tally[outcome] = tally.get(outcome, 0) + 1
    return (
        f"{len(DEMO_REPS)} reps, {len(DEMO_RECORDS)} CRM records, {len(links)} links, "
        f"{len(outcomes)} routes, {bookings} bookings; outcomes: "
        + ", ".join(f"{count} {name}" for name, count in sorted(tally.items()))
        + (f"; refused: {', '.join(sorted(set(refusals)))}" if refusals else "")
    )


def _seed_interval(now) -> dict[str, Any]:
    """A window that starts on the next working morning and runs a week.

    Built from the seeder's clock so the demo's slots are in the future whenever
    the seed runs, rather than being a fixed week that goes stale the first time
    somebody seeds after it.
    """
    from datetime import timedelta

    start = now + timedelta(days=1)
    # Roll forward to Monday so the default Monday-Friday working days have
    # something to offer, and to midnight so the first slot is a clean grid step.
    start += timedelta(days=(7 - start.weekday()) % 7)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(days=7)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
        "max_days": 14,
    }
