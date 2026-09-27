"""WF-023: relate buyer engagement to CRM pipeline and close rate.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-023.md``, and designed in
``WF-023-design.md`` beside it. This is a **build**, not a port: the workflow had a
finished research document and no code, so the research document is the specification
and the rules it fixes are landed rather than re-litigated.

This module is the three things the feature host expects and nothing else: the ``FEATURE``
block, a prefixed ``router``, and ``EXCEPTION_HANDLERS`` for this workflow's own domain
errors. The arithmetic and the vocabulary live in :mod:`dsr.salesimpact`, which this
module never duplicates.

What the research decided, and where it landed
----------------------------------------------
* "The Sales Impact report pulls in any workspace designated as a 'Sales' type that has a
  CRM opportunity" - a two-part conjunction, enforced once in
  :func:`~dsr.salesimpact.rollup.population` and reported per room with the reason it
  failed, so a workspace missing from the numbers is explainable.
* The eight tiles - the researched names and order, in ``/report``.
* "Close rate - How many workspaces with deals/opportunities that have been closed won,
  divided by the total (closed won + closed lost)" - the denominator is closed won plus
  closed lost, with open deals in neither arm, and ``null`` rather than ``0%`` when
  nothing has closed.
* "Deals Created Over Time" and "Deals By Owner" - the two panel charts.
* "Buyer Engagement: Views, actions, and average buyers per workspace, Most engaged
  buyers", plus views over time.
* "Filter by date range, CRM stage, owners, teams" - one filter parser shared by every
  aggregating read, echoed back on every response.
* "Metric tiles with drill-in" - ``/deals`` and ``/buyers``, filtered by the same
  vocabulary the tiles use.
* "CRM integration must be on and deals attached or the report is incomplete - unless you
  are requiring reps attach a deal to each space, it's possible this report is missing
  data" - ``/coverage`` names the workspaces responsible, and the report answers anyway
  with ``complete: false`` rather than refusing, because a refusal would hide the very
  rooms a reader needs in order to fix it.

Three things the build brief is explicit about, and how they are honoured here
---------------------------------------------------------------------------
**The prefix is ``/api/wf-023``** and room-scoped paths stay room-scoped
(``/report/rooms/{room_id}``). A ticket-derived prefix cannot collide with a
feature-shaped one by construction, and the host refuses a colliding ``(method, path)``
and reports it rather than shadowing.

**Every read and write goes through ``StoreDep`` / ``RecordStore``.** No SQLite connection
is opened anywhere in this feature; the engine is built per request from the store the
host already resolved, which is also what leaves the tests a seam via
``app.dependency_overrides``.

**``source=`` is passed from the HTTP layer, built from ``router.prefix``.** Every write
route below names the route that actually served it. The branch history of this codebase
is full of features whose audit log named a route the app had stopped serving, so
``source`` is a required keyword-only argument on every domain write rather than a
defaulted one, and a test asserts that every recorded source matches a route the host
actually mounted.

Demo data
---------
:func:`seed` seeds the states the research says matter, not just the happy path: a Sales
workspace with a won deal, one with a lost deal, one mid-negotiation, a Sales workspace
with **no deal attached** (the researched "possible missing data" case), a workspace that
is **not typed Sales** despite having a deal (which the type gate must exclude), a deal in
a currency that is not the report's, a deal with no amount, a deal whose close date
precedes its created date, and a deal attached to no workspace. Every one of those is a
case the tiles, the coverage panel or a ``data_warning`` has something to say about, and
demo data containing only success teaches a reviewer nothing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.salesimpact import DealConflict, InvalidDeal, InvalidFilter, SalesImpact, UnknownWorkspace
from dsr.salesimpact.filters import ReportFilter, parse_filters
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-023-relate-buyer-engagement-to-crm-pipelin",
    "ticket": "WF-023",
    "name": "Relate buyer engagement to CRM pipeline and close rate",
    "description": (
        "The Sales Impact report: a pipeline-weighted rollup over Sales-type workspaces "
        "with a CRM deal attached, joined to the buyer engagement inside them. Close "
        "rate, revenue, days to close, the two deal panels, the engagement panel, and a "
        "coverage panel naming the workspaces that are missing from the numbers."
    ),
    "nav": [{"id": "sales-impact", "label": "Sales impact"}],
}

router = APIRouter(prefix="/api/wf-023", tags=["WF-023"])


def get_sales_impact(store: RecordStore = StoreDep) -> SalesImpact:
    """A :class:`~dsr.salesimpact.SalesImpact` over the process-wide audited store.

    Per request rather than built in the lifespan and hung on ``app.state``: the engine
    holds nothing but the store handle, and ``app.state`` is the shared file this feature
    must not edit. Building it from a dependency also leaves the transport-less store as an
    override seam for the suite.
    """
    return SalesImpact(store)


ImpactDep = Depends(get_sales_impact)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _invalid_filter(request: Request, exc: InvalidFilter) -> JSONResponse:
    """A report that cannot exist as asked for. 422."""
    return JSONResponse(status_code=422, content={"error": "invalid_filter", "detail": str(exc)})


def _invalid_deal(request: Request, exc: InvalidDeal) -> JSONResponse:
    """A deal payload with nothing to key it by. 422."""
    return JSONResponse(status_code=422, content={"error": "invalid_deal", "detail": str(exc)})


def _deal_conflict(request: Request, exc: DealConflict) -> JSONResponse:
    """A CRM id already attached. 409, and it names the record to patch instead.

    409 rather than 400: the request is well formed and conflicts with current state, the
    same reading the core app takes for an audit conflict. The body carries
    ``existing_id`` so a client that re-posted by mistake can correct itself in one call
    rather than guessing which row it duplicated.
    """
    return JSONResponse(
        status_code=409,
        content={
            "error": "deal_conflict",
            "detail": str(exc),
            "existing_id": exc.existing_id,
            "existing_room_id": exc.existing_room_id,
        },
    )


def _unknown_workspace(request: Request, exc: UnknownWorkspace) -> JSONResponse:
    """An id that does not resolve to a live record. 404.

    Used for a missing room and for a missing deal, so a client has one shape to handle
    across a read, a patch and a delete. ``RecordNotFound`` is deliberately **not** claimed:
    the core app already maps it to 404, and two handlers for one type is a collision the
    host refuses to mount - which would take this whole feature offline.
    """
    return JSONResponse(status_code=404, content={"error": "unknown_workspace", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    InvalidFilter: _invalid_filter,
    InvalidDeal: _invalid_deal,
    DealConflict: _deal_conflict,
    UnknownWorkspace: _unknown_workspace,
}


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def _filters(
    date_from: str | None,
    date_to: str | None,
    stage: str | None,
    owner: str | None,
    team: str | None,
    bucket: str | None = None,
    limit: int | None = None,
) -> ReportFilter:
    """The one filter parser, shared by every aggregating read.

    Built here rather than repeated per route so the vocabulary a caller may send, and the
    ``filters`` block that comes back on every response, cannot drift apart between the
    tiles and the drill-in behind them. The query parameter is ``from``, which is a Python
    keyword, so FastAPI needs an alias on the way in.
    """
    return parse_filters(
        {
            "from": date_from,
            "to": date_to,
            "stage": stage,
            "owner": owner,
            "team": team,
            "bucket": bucket,
            "limit": limit,
        },
        limit=limit,
    )


@router.get("/report", summary="The Sales Impact report")
def report(
    date_from: str | None = Query(default=None, alias="from", description="ISO-8601, inclusive"),
    date_to: str | None = Query(default=None, alias="to", description="ISO-8601, inclusive"),
    stage: str | None = Query(default=None, description="A stage string, a class, or a comma-separated list"),
    owner: str | None = Query(default=None, description="Comma-separated list"),
    team: str | None = Query(default=None, description="Comma-separated list"),
    bucket: str | None = Query(default=None, description="day | week | month"),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """The whole researched report: tiles, funnel, both panels, engagement, coverage.

    A read that writes nothing. Every figure is computed on the spot from the records a
    team already keeps, because a stored metric row would be a cache that goes stale the
    moment a CRM syncs a new stage or amount - and the research says explicitly that
    "Deal stage/amount sync keeps the rollup fresh".

    The applied filter comes back under ``filters`` so a tile is never read as a
    whole-report total.
    """
    return impact.report(_filters(date_from, date_to, stage, owner, team, bucket))


@router.get("/report/rooms/{room_id}", summary="One workspace's contribution")
def room_report(
    room_id: str,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    stage: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    team: str | None = Query(default=None),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """One workspace's deals, its tiles, its buyers, and whether it is in the report.

    A workspace that exists but is out of scope is a **200 with a reason** -
    ``not_sales`` or ``no_deal`` - never a 404. The research makes typing a workspace
    ``Sales`` the user's first step, so the most likely reason to open this is "why is mine
    not in the numbers", and a 404 would answer "no such room", which is a different and
    wrong thing. Only an id that resolves to nothing is a 404.
    """
    return impact.room(room_id, _filters(date_from, date_to, stage, owner, team))


# --------------------------------------------------------------------------- #
# The deals - the drill-in behind the tiles, and the writes
# --------------------------------------------------------------------------- #


@router.get("/deals", summary="Filtered deals behind the tiles")
def list_deals(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    stage: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    team: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """The in-scope deals behind the tiles, filtered. The researched tile drill-in.

    Only **in-scope** deals appear: a deal attached to a workspace that is not Sales-typed,
    or that has no opportunity, contributes to no tile, so it has no place in the list
    behind one either. ``totals`` is computed over the whole filtered population and never
    over the returned page, so ``limit`` cannot change a number a reader would quote.
    """
    return impact.list_deals(_filters(date_from, date_to, stage, owner, team, limit=limit))


@router.post("/deals", status_code=201, summary="Attach a CRM deal to a workspace")
def create_deal(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None, description="The workspace to attach the deal to"),
    actor: str | None = Query(default=None),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """Register a CRM opportunity and attach it to a workspace.

    The workspace is named by the ``room_id`` query parameter, or by ``room_id`` /
    ``workspace_id`` in the body - the latter is the researched spelling. The attachment
    itself is always the record envelope's ``room_id``, so one mechanism governs the join
    and two spellings cannot disagree about it.

    ``crm_deal_id`` identifies the deal inside the CRM, and re-posting one that is already
    attached is a **409** rather than a second row: a duplicate would count the same money
    twice in every tile, and the conflict returns the existing record id so the client can
    ``PATCH`` it instead.

    Only ``crm_deal_id`` or ``name`` is required. Every other field is optional, every
    unrecognised key is stored verbatim, and nothing here is a schema.
    """
    return impact.register_deal(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/deals"
    )


@router.get("/deals/{deal_id}", summary="One deal")
def read_deal(deal_id: str, impact: SalesImpact = ImpactDep) -> dict[str, Any]:
    """One deal: the stored payload verbatim, plus the derived read projection.

    The derived half is never persisted. ``stage_class``, ``owner_source`` and
    ``days_to_close`` are recomputed on the way out, because the research says the stage
    changes by sync and a stored derived copy would be a cache that goes stale against it.
    """
    return impact.read_deal(deal_id)


@router.patch("/deals/{deal_id}", summary="The researched stage/amount sync")
def update_deal(
    deal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """A shallow merge patch over a deal. This is the "deal stage/amount sync" path.

    Only the keys sent change, and a key sent as JSON ``null`` is stored as ``null``,
    which is how a field is cleared. The three fields a sync actually moves - stage, amount
    and close date - all go through this one route, so a CRM push and a person correcting
    a typo produce the same audit row shape.
    """
    return impact.patch_deal(deal_id, payload, actor=actor, source=f"PATCH {router.prefix}/deals/{deal_id}")


@router.delete("/deals/{deal_id}", summary="Detach a deal")
def delete_deal(
    deal_id: str,
    actor: str | None = Query(default=None),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """Soft-delete a deal, so the detachment and its audit row survive.

    Soft rather than hard, because "this deal was detached on the 3rd" is exactly what a
    report about pipeline completeness has to be able to answer, and a hard delete would
    erase the question along with the answer.
    """
    return impact.detach_deal(deal_id, actor=actor, source=f"DELETE {router.prefix}/deals/{deal_id}")


# --------------------------------------------------------------------------- #
# Engagement
# --------------------------------------------------------------------------- #


@router.get("/buyers", summary="Most engaged buyers")
def list_buyers(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    owner: str | None = Query(default=None),
    team: str | None = Query(default=None),
    bucket: str | None = Query(default=None, description="day | week | month"),
    limit: int = Query(default=100, ge=1, le=1000),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """The researched "Most Engaged Buyers" ranking, filtered.

    Ranked by actions, then views, then the buyer's email - three keys rather than one, so
    two calls over the same data produce the same order and a reader comparing two reports
    is not looking at an arbitrary reshuffle. A buyer is identified by lowercased email,
    which is how an uninvited contact appears in the researched product.
    """
    return impact.buyers(_filters(date_from, date_to, None, owner, team, bucket, limit=limit))


@router.get("/engagement", summary="Buyer views, actions and views over time")
def engagement(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    owner: str | None = Query(default=None),
    team: str | None = Query(default=None),
    bucket: str | None = Query(default=None, description="day | week | month"),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """Buyer views, buyer actions, average buyers per workspace, and the views series.

    ``buyer_actions`` includes ``buyer_views``, because the researched gloss of a client
    action is interacting with a space and clicking into a page is a view. Both are
    counted only in in-scope workspaces, so the engagement half answers the same question
    as the revenue half beside it.

    The series is zero-filled inside the requested range: a day with no views is drawn as
    a zero, not omitted, because a gap in a bar chart reads as data that is missing rather
    than activity that did not happen.
    """
    return impact.engagement(_filters(date_from, date_to, None, owner, team, bucket))


# --------------------------------------------------------------------------- #
# Coverage - the researched incompleteness, made nameable
# --------------------------------------------------------------------------- #


@router.get("/coverage", summary="What is missing from the report, and why")
def coverage(impact: SalesImpact = ImpactDep) -> dict[str, Any]:
    """The report's completeness, and the workspaces responsible for it.

    The research's own sentence about this report is that it can be *missing data* without
    anyone noticing, unless reps are required to attach a deal to every space. A tile
    cannot say which rooms caused that, so this names them: every Sales-typed workspace
    with no deal, every workspace that is not Sales-typed, and every deal attached to
    nothing. ``complete`` is true only when the CRM integration is on and every
    Sales-typed workspace carries a deal.
    """
    return impact.coverage()


@router.get("/integration", summary="The CRM integration state")
def read_integration(impact: SalesImpact = ImpactDep) -> dict[str, Any]:
    """The integration state the report's completeness depends on.

    Returns the built-in defaults with ``configured: false`` when nothing has been set, so
    the page renders before anyone has turned anything on. A missing configuration record
    is the normal first state of a fresh database, not an error.
    """
    return impact.config()


@router.patch("/integration", summary="Turn the CRM integration on or off")
def update_integration(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    impact: SalesImpact = ImpactDep,
) -> dict[str, Any]:
    """A shallow merge patch over the single configuration record.

    Creating it on first write is what lets this be the only endpoint a caller has to know
    about, and that create is itself audited - so the first call with
    ``{"connected": true}`` produces an ``insert`` audit row and later calls produce
    ``update`` rows.

    ``provider`` is free text. The research names Salesforce and HubSpot as having native
    integrations but defines no enum, so none is enforced and any string is accepted.
    """
    return impact.set_config(payload, actor=actor, source=f"PATCH {router.prefix}/integration")


# --------------------------------------------------------------------------- #
# Vocabulary and the inferred half
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The published vocabulary and API constraints")
def vocabulary(impact: SalesImpact = ImpactDep) -> dict[str, Any]:
    """The stage classes, the won and lost stage sets, the field synonyms, and the
    documented API constraints - the 429 rate limit and the ``properties`` selection.

    Served as data so a client renders its pickers from the same source the classifier
    enforces against, and a stage set changed here reaches every client at once.

    The constraints are the researched ones and nothing here implements them: this feature
    opens no socket. They are here so a client building a real extractor knows what the
    source documents, without this module pretending to know a schema it does not have.
    """
    return impact.vocabulary()


@router.get("/inferences", summary="Every decision the research does not make")
def inferences(impact: SalesImpact = ImpactDep) -> dict[str, Any]:
    """The named registry of decisions the research leaves open, with the sourced quotes
    they are contrasted against.

    The research fixes the report's vocabulary and quotes exactly one formula. It does not
    define "total pipeline touched", it does not say how a days-to-close average is
    measured, it never mentions currency, and it does not say what a caller should see
    when the data is incomplete. Those are this build's decisions.

    They are served here rather than left in comments because a judgement call buried in a
    function body is one nobody re-reads, and a wrong one becomes product behaviour
    without anyone noticing. Each entry names what was chosen, quotes what the research
    does and does not say, and points at the constant or function that would have to change
    to argue with it.
    """
    return impact.inferences()


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: Reps and teams in the demo. Two owners and two teams, so *Deals By Owner* and the team
#: filter both have something to separate, and one owner carries no deals at all so the
#: panel does not look uniformly populated.
DEMO_OWNERS = ("dana", "sam")
DEMO_TEAMS = ("enterprise", "midmarket")

#: The core seeder's four demo workspaces, addressed by the account name it gave them.
#: The type gate is exercised on real rooms rather than on rooms invented for the purpose,
#: so the demo's report covers the same engagement events the rest of the product shows.
_NORTHWIND = "Northwind Traders"
_CONTOSO = "Contoso Health"
_FABRIKAM = "Fabrikam Logistics"
_ADVENTURE = "Adventure Works"


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the CRM mirror, the integration, and the buyer activity behind the report.

    The states seeded are the ones the research says matter, and deliberately not only the
    successful ones:

    ``Northwind`` (typed ``Sales``, deal attached, two buyers, most engagement)
        The happy path, and the room the rest of the demo's 140 core engagement events
        belong to, so the report's engagement half is over real activity.
    ``Fabrikam`` (typed ``Sales``, three deals: won, lost, mid-negotiation)
        The full funnel, across two teams, which is what makes the close rate and the
        days-to-close average non-trivial. Its won deal's close date precedes its created
        date, which is the CRM data error a report must exclude rather than average in.
    ``Contoso`` (typed ``Sales``, **no deal attached**)
        The researched "possible missing data" case, word for word: unless reps are
        required to attach a deal to every space, this room is silently absent from the
        report. The coverage panel has to name it.
    ``Adventure Works`` (**not typed Sales**, and a deal attached to it anyway)
        The type gate's case. The deal is real and the amount is real, and neither may
        reach a single tile - which is only provable by seeding it and looking.

    Plus three deals that are attached to nothing and would break a naive sum: one in a
    second currency, one with no amount, and one with a stage this build cannot classify.

    Returns a short description, which the seeder prints.
    """
    store = RecordStore(db)
    impact = SalesImpact(store)
    rooms = context.get("room_ids") or []
    now = context.get("now")
    rng = context.get("rng")

    # The integration is on, because the research's precondition is that it is, and a demo
    # whose report is permanently flagged incomplete never shows the good state.
    impact.set_config(
        {"connected": True, "provider": "salesforce", "engagement": "activity"},
        actor="dana",
        source="seed",
    )

    by_account: dict[str, str] = {}
    for room_id, account in rooms:
        by_account[str(account)] = str(room_id)

    sales_rooms: list[str] = []
    for account, workspace_type in (
        (_NORTHWIND, "Sales"),
        (_FABRIKAM, "sales"),
        (_CONTOSO, "Sales"),
    ):
        room_id = by_account.get(account)
        if room_id:
            sales_rooms.append(room_id)
            _type_room(db, room_id, workspace_type, source="seed")

    # Adventure Works keeps whatever the core seeder gave it, which is no `type` at all.
    # Adding one would prove the gate excludes a room; leaving it out proves the gate
    # excludes a room for the reason the research names, which is a room nobody typed.
    adventure = by_account.get(_ADVENTURE)

    if not sales_rooms:
        return "CRM integration on, 0 deals (no demo rooms to attach them to)"

    northwind, fabrikam, contoso = (sales_rooms + [sales_rooms[0]] * 3)[:3]
    deals: list[tuple[str, str | None, dict[str, Any]]] = []

    if northwind == sales_rooms[0]:
        deals += [
            (
                "006NW-1",
                northwind,
                {
                    "crm_deal_id": "006NW-1",
                    "name": "Northwind — Enterprise platform",
                    "account": _NORTHWIND,
                    "stage": "Negotiation",
                    "amount": 48000,
                    "currency": "USD",
                    "owner": "dana",
                    "team": "enterprise",
                    "created_date": _shift(now, -74),
                },
            ),
            (
                "006NW-2",
                northwind,
                {
                    "crm_deal_id": "006NW-2",
                    "name": "Northwind — Analytics add-on",
                    "account": _NORTHWIND,
                    "stage": "Closed Won",
                    "amount": 12500,
                    "currency": "USD",
                    "owner": "dana",
                    "team": "enterprise",
                    "created_date": _shift(now, -190),
                    "closed_at": _shift(now, -96),
                },
            ),
        ]

    if fabrikam == sales_rooms[1]:
        deals += [
            (
                "006FK-1",
                fabrikam,
                {
                    "crm_deal_id": "006FK-1",
                    "name": "Fabrikam renewal FY27",
                    "account": _FABRIKAM,
                    "stage": "Negotiation",
                    "amount": 90000,
                    "currency": "USD",
                    "owner": "sam",
                    "team": "midmarket",
                    "created_date": _shift(now, -40),
                },
            ),
            (
                "006FK-2",
                fabrikam,
                {
                    "crm_deal_id": "006FK-2",
                    "name": "Fabrikam — pilot expansion",
                    "account": _FABRIKAM,
                    "stage": "Closed Won",
                    "amount": 30000,
                    "currency": "USD",
                    "owner": "sam",
                    "team": "midmarket",
                    "created_date": _shift(now, -120),
                    # A close date before the created date: a real CRM data error, and the
                    # case days-to-close must exclude rather than average into a negative.
                    "closed_at": _shift(now, -150),
                },
            ),
            (
                "006FK-3",
                fabrikam,
                {
                    "crm_deal_id": "006FK-3",
                    "name": "Fabrikam — security review",
                    "account": _FABRIKAM,
                    "stage": "Closed Lost",
                    "amount": 45000,
                    "currency": "USD",
                    "team": "midmarket",
                    "created_date": _shift(now, -160),
                    "closed_at": _shift(now, -88),
                },
            ),
        ]

    if contoso == sales_rooms[2]:
        # No deal on purpose. This is the researched "possible missing data" room, and it
        # is the one the coverage panel exists to name.
        pass

    if adventure:
        deals.append(
            (
                "006AW-1",
                adventure,
                {
                    "crm_deal_id": "006AW-1",
                    "name": "Adventure Works — pilot (not a Sales workspace)",
                    "account": _ADVENTURE,
                    "stage": "Closed Won",
                    "amount": 21000,
                    "currency": "USD",
                    "owner": "sam",
                    "team": "midmarket",
                    "created_date": _shift(now, -210),
                    "closed_at": _shift(now, -150),
                },
            )
        )

    # Three deals that are attached to nothing, each for a different reason. All three
    # would break a report that summed whatever it found.
    deals += [
        (
            "006EU-1",
            None,
            {
                "crm_deal_id": "006EU-1",
                "name": "Litware — EU rollout",
                "account": "Litware",
                "stage": "Negotiation",
                "amount": 61000,
                "currency": "EUR",
                "owner": "dana",
                "team": "enterprise",
                "created_date": _shift(now, -25),
            },
        ),
        (
            "006XX-1",
            None,
            {
                "crm_deal_id": "006XX-1",
                "name": "Proseware — pilot with no quoted amount",
                "account": "Proseware",
                "stage": "Closed Lost",
                "owner": "sam",
                "team": "midmarket",
                "created_date": _shift(now, -64),
                "closed_at": _shift(now, -30),
            },
        ),
        (
            "006ZZ-1",
            None,
            {
                "crm_deal_id": "006ZZ-1",
                "name": "Wingtip — stage this report cannot classify",
                "account": "Wingtip",
                "stage": "Escalated to the board",
                "amount": 38000,
                "currency": "USD",
                "owner": "dana",
                "created_date": _shift(now, -12),
            },
        ),
    ]

    created = 0
    for _crm_id, room_id, payload in deals:
        impact.register_deal(payload, room_id=room_id, actor="dana", source="seed")
        created += 1

    events = _seed_engagement(db, sales_rooms, now=now, rng=rng)

    return (
        f"CRM integration on (salesforce), {len(sales_rooms)} Sales-typed workspaces, "
        f"{created} CRM deals (1 in a second currency, 1 with no amount, 1 with an "
        f"unclassifiable stage, 1 attached to a non-Sales workspace, 1 closed before it "
        f"was created, 1 Sales workspace deliberately left with no deal), "
        f"{events} buyer events"
    )


def _type_room(db: AuditedDatabase, room_id: str, workspace_type: str, *, source: str) -> None:
    """Set a workspace's type, the researched first step, without touching a shared file.

    ``type`` is an ordinary schema-flexible field on the room record, so this is a
    ``PATCH`` of one field rather than a migration or a new column. Audited, because
    changing a workspace's type is exactly the kind of change that decides what a
    leadership report will say next quarter and must be traceable.
    """
    db.update(room_id, {"type": workspace_type}, actor="dana", source=source)


def _seed_engagement(
    db: AuditedDatabase, sales_rooms: Sequence[str], *, now: Any, rng: Any
) -> int:
    """Add buyer activity to the Sales-typed rooms, in the product's own shape.

    Written into the collection the core seeder already fills, in the same record shape, so
    the report's engagement half is computed over the same buyer activity the rest of the
    product shows rather than over a private copy that only this feature can see. The
    collection name is configurable, so a team with their own webhook-derived store
    re-points it and this demo's approach stops mattering.

    Two of the buyers deliberately have views and no other actions, and one has actions
    and no views, so the difference between the two researched counters is visible rather
    than assumed.
    """
    if not sales_rooms:
        return 0
    buyers = (
        ("a.buyer@northwind.example", "viewed", 14),
        ("b.buyer@northwind.example", "viewed", 9),
        ("c.buyer@northwind.example", "downloaded", 6),
        ("ops@fabrikam.example", "viewed", 11),
        ("ops@fabrikam.example", "commented", 4),
        ("procurement@contoso.example", "opened_link", 3),
    )
    written = 0
    for index, (person, action, count) in enumerate(buyers):
        room_id = sales_rooms[index % len(sales_rooms)]
        for step in range(count):
            day_offset = -((step * 5) + index * 11 + (rng.randint(0, 3) if rng else 0))
            db.create(
                "activity",
                {
                    "person": person,
                    "action": action,
                    "target": "Enterprise Overview Deck",
                    "seconds_on_page": 30 + (step * 11) if rng is None else 30 + rng.randint(5, 240),
                    "device": "desktop",
                    "occurred_at": _shift(now, day_offset),
                    "source_note": "seeded for the Sales Impact report's engagement half",
                },
                room_id=room_id,
                actor="system",
                source="seed",
            )
            written += 1
    return written


def _shift(now: Any, days: int) -> str:
    """An ISO date ``days`` away from ``now``, as a string.

    Written rather than reused from the store so the demo's dates are plain calendar days
    a reader can check, rather than timestamps at whatever time of day the seed happened to
    run.
    """
    from datetime import date, timedelta

    if now is None:
        return (date.today() + timedelta(days=days)).isoformat()
    return (now.date() + timedelta(days=days)).isoformat()
