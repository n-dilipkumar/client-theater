"""WF-024: roll up client engagement and multi-threading portfolio-wide.

The HTTP surface and the demo data for the **Reports -> Client Engagement**
report, plus the two reports the same research document names in the same family
(Team Usage, Implementations). The domain is :mod:`dsr.client_engagement`; this
module is the three things a feature must own: the routes, the mapping from
domain errors to responses, and its own demo rows.

What the research decided, and what this module therefore serves
---------------------------------------------------------------
* **The report spans all workspaces automatically.** "The Client Engagement
  report analyzes external activity across ALL workspaces in your Dock
  instance." So ``GET /report`` has no workspace selector. The one
  workspace-scoped route is ``GET /rooms/{room_id}/engagement``, which is the
  same rollup narrowed to a single workspace, and it keeps the room in the path
  so a room-scoped read stays room-scoped.
* **Client activity only.** Every client metric filters on the external/internal
  distinction the research names as a data source, and the response carries a
  ``coverage`` block saying how many events were excluded as internal, so the
  distinction is visible rather than a filter the reader has to trust.
* **Click a tile to expand the account list.** Every tile carries the
  ``accounts_href`` it expands to, built from the tile's own sort key. The
  expansion is served as data because the research specifies the behaviour
  ("Click a metric tile to expand the full list of accounts and their
  engagement") and not a URL.
* **Click into the cell to expand upon who these individuals are.**
  ``GET /accounts/{account_key}`` is that expansion, and every row of the list
  carries its own ``clients`` so a table can show them inline.
* **Sort by any column.** ``sort`` accepts any column of the list; an unknown
  one is refused with 422 rather than ignored, because a silently dropped sort
  is a report that looks sorted by something it is not.
* **Filter by date range, owners, teams.** ``from``/to``, ``owner``, ``team``,
  and ``GET /filters`` publishes the owners, teams, accounts and date bounds
  actually in scope so a client builds its pickers from the server rather than
  from a guess.

Three reports, and why
----------------------
The research document names three reports in one family and cites five sources,
one of which is the Implementations Report's own help article. Jev chose to ship
all three, with Client Engagement primary, at confidence 0.99 (audit
``jev-20260927T062104-16408-64900``; the first question was ``uncertain`` until
the source weighting and marginal cost were supplied). Team Usage and
Implementations are reads over the same rollup, so neither invents a widget: the
Implementations Report's six widgets are quoted verbatim in the research, and
Team Usage is one sentence, which is why everything Team Usage reports beyond a
count is marked as an inference in :mod:`dsr.client_engagement`.

What this module deliberately does not build
--------------------------------------------
**Webhook delivery.** The research names "the full ``workspace.*`` webhook
stream" and quotes Dock's promise that "Webhooks allow you to push all the
workspace activity data out of Dock for use in other applications" - as
extensibility, for a third party to consume, and it is workflow WF-023's subject
rather than this report's. This feature is the consumer-shaped end of that: the
report is computed on read from the events the product already stores, so a team
that *does* push the stream into a warehouse is doing the same aggregation
somewhere else, and nothing here needs a network call. ``POST /events`` is the
ingest seam instead, so a webhook consumer has one place to land events.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr import client_engagement as domain
from dsr.client_engagement import EngagementReportError, UnknownAccount, UnknownWorkspace
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-024-roll-up-client-engagement-and-multi-th",
    "ticket": "WF-024",
    "name": "Roll up client engagement and multi-threading portfolio-wide",
    "description": (
        "The Client Engagement report over every workspace: total client views and "
        "actions, average unique clients per workspace, client views over time and most "
        "engaged clients, expandable to a sortable account list and the individuals behind "
        "each number, with the Team Usage and Implementations reports from the same family."
    ),
    "nav": [{"id": "client-engagement", "label": "Client engagement"}],
}

router = APIRouter(prefix="/api/wf-024", tags=["wf024"])


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _report_error(request: Request, exc: EngagementReportError) -> JSONResponse:
    """A well-formed request asking for something this report will not do. 422.

    One handler for the whole hierarchy: a bad date range and an unknown sort
    column are both the caller's to fix, and both are the same shape of problem.
    ``EngagementReportError`` is this feature's own type, so registering it
    globally cannot intercept an exception raised anywhere else in the product.
    """
    return JSONResponse(
        status_code=422, content={"error": "invalid_report_request", "detail": str(exc)}
    )


def _unknown_workspace(request: Request, exc: UnknownWorkspace) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_workspace", "detail": f"workspace {exc} not found"},
    )


def _unknown_account(request: Request, exc: UnknownAccount) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_account", "detail": f"account {exc} not in scope"},
    )


EXCEPTION_HANDLERS = {
    EngagementReportError: _report_error,
    UnknownWorkspace: _unknown_workspace,
    UnknownAccount: _unknown_account,
}


# --------------------------------------------------------------------------- #
# Shared query parameters
# --------------------------------------------------------------------------- #


def _filters(
    date_from: str | None,
    date_to: str | None,
    owner: list[str] | None,
    team: list[str] | None,
) -> domain.Filters:
    """The three sourced filters, resolved once for every read route."""
    return domain.resolve_filters(date_from=date_from, date_to=date_to, owners=owner, teams=team)


def _with_expansion(payload: dict[str, Any]) -> dict[str, Any]:
    """Give every tile the URL it expands to.

    Built from ``router.prefix`` and from the tile's own sort key, so a tile that
    claims to expand to the account list cannot point somewhere this app does not
    serve. The domain stays free of URL knowledge, for the same reason every write
    gets its ``source`` from the HTTP layer.
    """
    for tile in payload.get("tiles", []):
        tile["accounts_href"] = f"{router.prefix}/accounts?sort={tile['expand']}&descending=true"
    return payload


# --------------------------------------------------------------------------- #
# Client Engagement
# --------------------------------------------------------------------------- #


@router.get("/report", summary="Client Engagement report across all workspaces")
def client_engagement_report(
    date_from: str | None = Query(default=None, description="ISO date or timestamp, inclusive"),
    date_to: str | None = Query(default=None, description="ISO date or timestamp, inclusive"),
    owner: list[str] | None = Query(default=None, description="Repeatable, or comma-separated"),
    team: list[str] | None = Query(default=None, description="Repeatable, or comma-separated"),
    grain: str = Query(default="day", description="Chart grain for Client views over time"),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The five tiles, over every workspace in the instance.

    Sourced: "The Client Engagement report analyzes external activity across ALL
    workspaces in your Dock instance. Here you'll find the following insights:
    Total client views, Total client actions, Client views over time, Most engaged
    clients."

    There is no workspace parameter, deliberately. The research is explicit that
    the report spans all of them, and a portfolio report with a workspace
    selector is the WF-006 Analytics view wearing this report's name.
    """
    return _with_expansion(
        domain.client_engagement(
            store,
            filters=_filters(date_from, date_to, owner, team),
            grain=grain,
            as_of=as_of,
        )
    )


@router.get("/accounts", summary="Every account and its engagement, sortable by any column")
def account_engagement_list(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    owner: list[str] | None = Query(default=None),
    team: list[str] | None = Query(default=None),
    sort: str | None = Query(default=None, description="Any column of the list"),
    descending: bool = Query(default=True),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The expanded tile: the full list of accounts and their engagement.

    Sourced: "Click a metric tile to expand the full list of accounts and their
    engagement; sort by any column."

    Every row carries its ``clients``, because the research also says clicking
    into the actions cell expands upon who those individuals are; the dedicated
    route below is the same data for a client that would rather fetch on demand.
    """
    return domain.account_list(
        store,
        filters=_filters(date_from, date_to, owner, team),
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
        as_of=as_of,
    )


@router.get("/accounts/{account_key}", summary="The individuals behind one account's numbers")
def account_engagement_detail(
    account_key: str,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    owner: list[str] | None = Query(default=None),
    team: list[str] | None = Query(default=None),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One account: its workspaces, its people, its champion, its thread depth.

    Sourced: "Total client actions counts how many times a client has interacted
    with a space. ... Click into the cell to expand upon who these individuals
    are!"

    404 for a key that is not in scope. An account that exists but is filtered
    out answers 404 rather than an empty success, so a client cannot mistake
    "you filtered it away" for "this account has no engagement".
    """
    return domain.account_detail(
        store, account_key, filters=_filters(date_from, date_to, owner, team), as_of=as_of
    )


@router.get(
    "/rooms/{room_id}/engagement",
    summary="The same report narrowed to one workspace",
)
def workspace_engagement(
    room_id: str,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    grain: str = Query(default="day"),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """One workspace's client engagement, from the same rollup.

    The portfolio report has no workspace selector, so this is where a
    workspace-scoped read lives, and the workspace stays in the path. Owner and
    team filters are deliberately not accepted here: a single workspace's report
    is scoped by its own id, and an owner filter here could only ever empty it.
    """
    return domain.workspace_engagement(
        store,
        room_id,
        filters=domain.resolve_filters(date_from=date_from, date_to=date_to),
        grain=grain,
        as_of=as_of,
    )


@router.get("/filters", summary="What this report can be filtered and sorted by")
def report_filters(
    as_of: str | None = Query(default=None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """The owners, teams, accounts, date bounds, grains and sort keys in scope.

    Sourced: "You can filter the report down by date range, owners, and/or
    teams." A team no record carries cannot be filtered on, so the values that
    exist are published here rather than left for a client to enumerate by hand.
    """
    return domain.available_filters(store, as_of=as_of)


# --------------------------------------------------------------------------- #
# The other two reports in the same family
# --------------------------------------------------------------------------- #


@router.get("/reports/team-usage", summary="Team Usage: how actively the team is using the room")
def team_usage_report(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    owner: list[str] | None = Query(default=None),
    team: list[str] | None = Query(default=None),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """The internal mirror of the Client Engagement report.

    Sourced as one sentence: "Team Usage: How actively is your team using Dock?"
    No widget vocabulary exists for it, so every column beyond a count is a
    documented inference in :mod:`dsr.client_engagement`.
    """
    return domain.team_usage(store, filters=_filters(date_from, date_to, owner, team), as_of=as_of)


@router.get(
    "/reports/implementations", summary="Implementations: how long customer implementations take"
)
def implementations_report(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    owner: list[str] | None = Query(default=None),
    team: list[str] | None = Query(default=None),
    as_of: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """All six sourced widgets, over the accounts in scope.

    Sourced: "Total/Active/Completed implementations, Time to completion average,
    % completed on time, Implementations by owner, Customer Views/Actions, Most
    Engaged Customers", for "at-risk delivery tracking".
    """
    return domain.implementations(
        store, filters=_filters(date_from, date_to, owner, team), as_of=as_of
    )


# --------------------------------------------------------------------------- #
# Configuration and ingest
# --------------------------------------------------------------------------- #


@router.get("/config", summary="The effective configuration: defaults plus stored overrides")
def read_config(store: RecordStore = StoreDep) -> dict[str, Any]:
    """Every rule this report applies that the research did not fix.

    The external/internal vocabulary, the view taxonomy, the multi-thread floor
    and the chart window are inferences. They are served as data so a reviewer
    can disagree with a named entry instead of hunting for it in a docstring, and
    so a team can change one without a code change.
    """
    return {"config": domain.load_config(store)}


@router.patch("/config", summary="Override the report's inferred rules")
def update_config(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Merge a partial config patch. The write is audited like any other.

    A deep merge, so patching one threshold cannot drop its siblings. The
    ``source`` is built from ``router.prefix`` here, in the HTTP layer, because
    only the HTTP layer knows its own path.
    """
    record = domain.save_config(store, payload, actor=actor, source=f"PATCH {router.prefix}/config")
    return {"updated": True, "record": record, "config": domain.load_config(store)}


@router.post("/events", status_code=201, summary="Record one client interaction")
def record_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Ingest one interaction: the entry point of the data flow.

    Sourced: "underlying event capture is continuous", and the same activity
    reaches the product through the activity log or through "the full
    ``workspace.*`` webhook stream". A webhook consumer lands events here.

    The event is counted as a client interaction unless the payload says who it
    is; the response reports which side of the report it landed on, so a caller
    is never guessing whether its event reached the totals.
    """
    return domain.record_event(
        store, payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/events"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The people in the demo, and which side of the report each belongs to.
#:
#: The core dataset seeds client activity only, so a report that separates
#: external from internal would look correct in a demo and never have been
#: exercised. These identities are the interesting states: three buyers on one
#: account (multi-threaded), one buyer holding a whole account by herself
#: (single-threaded, the coverage risk the report exists to surface), a rep whose
#: own page views must not reach the client tiles, and an anonymous interaction
#: that can be counted as nobody's.
#:
#: ``Initech`` is deliberately an account name the core dataset does not use. The
#: core seeder scatters its people across rooms at random - it picks a room and
#: then a person independently - so every account it names ends up with all five
#: of its people, and the single-threaded case would be invisible in the demo
#: however correctly the report computed it. This feature owns its own rows, so
#: it seeds a clean instance of the state it needs to be reviewable.
DEMO_CLIENTS = {
    "Northwind Traders": [
        "a.buyer@northwind.example",
        "b.buyer@northwind.example",
        "c.buyer@northwind.example",
    ],
    "Contoso Health": ["procurement@contoso.example"],
    "Fabrikam Logistics": ["ops@fabrikam.example", "finance@fabrikam.example"],
    "Initech": ["one.person@initech.example"],
    "Adventure Works": [],
}

#: The reps who appear in the demo. ``kai`` owns no workspace: an internal person
#: whose activity belongs to no owner is the case the Team Usage report has to
#: name rather than quietly drop.
DEMO_REPS = ("dana", "sam", "kai")

#: The subset who own workspaces. Deliberately not all of :data:`DEMO_REPS`, so
#: the demo contains a rep with activity and no portfolio of their own.
DEMO_OWNERS = DEMO_REPS[:2]

#: One account nobody has opened. A portfolio report whose average excludes it is
#: a portfolio report that cannot show an unworked account, which is the whole
#: reason the denominator is every workspace in scope.
DEMO_QUIET_ACCOUNT = "Quiet Holdings — Pilot"

CLIENT_ACTIONS = ("viewed", "downloaded", "commented", "completed_section", "opened_link")
INTERNAL_ACTIONS = ("viewed", "opened_link", "shared", "published")

DEMO_IMPLEMENTATIONS = (
    {
        "name": "Northwind Traders — rollout",
        "account": "Northwind Traders",
        "owner": "dana",
        "status": "completed",
        "started_days_ago": 90,
        "completed_days_ago": 54,
        # The due date is 40 days back and the work landed 54 days back, so it
        # was delivered fourteen days early. The on-time percentage is then a
        # real number rather than a degenerate zero, and both branches of the
        # widget are exercised.
        "due_days_ago": 40,
    },
    {
        "name": "Contoso Health — security review",
        "account": "Contoso Health",
        "owner": "sam",
        "status": "active",
        "started_days_ago": 40,
        # Due in three days and the account has one engaged buyer: the
        # at-risk row a delivery lead reads first.
        "due_days_ago": -3,
    },
    {
        "name": "Fabrikam Logistics — data migration",
        "account": "Fabrikam Logistics",
        "owner": "dana",
        "status": "active",
        "started_days_ago": 75,
        # Overdue and still active: the other at-risk shape.
        "due_days_ago": 6,
    },
    {
        "name": "Adventure Works — enablement",
        "account": "Adventure Works",
        "owner": "sam",
        # Completed fifteen days after its due date: the other branch of the
        # "% completed on time" widget, and an at-risk row for a closed deal.
        "status": "completed",
        "started_days_ago": 120,
        "completed_days_ago": 30,
        "due_days_ago": 45,
    },
    {
        "name": "Initech — single-threaded rollout",
        "account": "Initech",
        "owner": "dana",
        "status": "active",
        "started_days_ago": 30,
        "due_days_ago": -10,
    },
    {
        "name": "Northwind Traders — data warehouse",
        # Active with no due date and no owner. Both are the shapes the report
        # has to surface: a delivery nobody has committed to, and a row with
        # nobody against it.
        "account": "Northwind Traders",
        "status": "active",
        "started_days_ago": 20,
    },
    {
        "name": "Quiet Holdings — discovery",
        "account": "Quiet Holdings — Pilot",
        "status": "onboarding",
        "started_days_ago": 5,
        "due_days_ago": -25,
    },
    {
        "name": "Contoso Health — data import",
        # No owner at all. The by-owner report has to show an unassigned bucket
        # rather than quietly dropping the row, because an implementation with
        # nobody against it is the thing a delivery lead needs to see.
        "account": "Contoso Health",
        "owner": "",
        "status": "in_progress",
        "started_days_ago": 12,
        "due_days_ago": -2,
    },
)


def seed(db, context: dict[str, Any]) -> str:
    """Seed the external/internal split, an unworked account, and delivery rows.

    The demo exists to make the report's rules reviewable, so it seeds the states
    that change an answer rather than a spread of success:

    * an account multi-threaded three ways (Northwind), an account resting on one
      person (Initech, on a name the core dataset does not touch, because the
      core seeder scatters its people across rooms at random and would otherwise
      make every account look multi-threaded), and an account nobody has opened
      (Quiet Holdings, plus the workspace that makes it a real workspace with no
      client activity);
    * internal rep activity, which must be excluded from every client tile and
      counted by the Team Usage report instead;
    * an anonymous interaction, which cannot be attributed to a client and must
      land in ``coverage`` rather than in a total;
    * an implementation that is overdue, one due in days, one completed late, one
      completed on time, and one with no owner and no committed date.

    Written through the same :mod:`dsr.client_engagement` domain the HTTP routes
    use, so the demo cannot show a shape the report would not produce. ``db`` is
    an ``AuditedDatabase``; the store wrapper is built here rather than imported
    as a dependency, because a seeder is handed the database, not the app.
    """
    from dsr.store import RecordStore

    store = RecordStore(db)
    now = context["now"]
    rng = context["rng"]
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])

    domain.load_config(store)
    domain.save_config(
        store,
        {"audience": {"internal_people": list(DEMO_REPS)}},
        actor="dana",
        source="seed",
    )
    domain.load_config(store)

    if not rooms:
        return "0 workspaces, 0 events (no demo rooms to attach activity to)"

    # One workspace per demo account, so an account's rollup has somewhere to
    # come from. The core dataset already made four; the quiet one is new.
    created_rooms: list[tuple[str, str]] = list(rooms)
    if len(rooms) < len(DEMO_CLIENTS) + 1:
        for index, account in enumerate(list(DEMO_CLIENTS) + [DEMO_QUIET_ACCOUNT]):
            if any(entry[1] == account for entry in created_rooms):
                continue
            record = db.create(
                "room",
                {
                    "name": f"{account} — Engagement Review",
                    "account": account,
                    "stage": "evaluation",
                    "owner": DEMO_OWNERS[index % len(DEMO_OWNERS)],
                    "team": "Enterprise" if index % 2 == 0 else "Mid-Market",
                },
                actor="dana",
                source="seed",
            )
            created_rooms.append((record["id"], account))

    events = 0
    for _index, (room_id, account) in enumerate(created_rooms):
        buyers = DEMO_CLIENTS.get(account, [])
        if not account or account == DEMO_QUIET_ACCOUNT:
            # The unworked account: a workspace with no client activity at all,
            # which is the row the average-forever-denominator exists for.
            continue
        for person in buyers:
            # The first buyer on an account is its champion by volume; the rest
            # exist to make the thread count differ from one.
            sessions = rng.randint(2, 4) if person == buyers[0] else rng.randint(1, 2)
            for _ in range(sessions):
                entered = now - timedelta(days=rng.randint(0, 60), minutes=rng.randint(0, 600))
                for step in range(rng.randint(1, 4)):
                    db.create(
                        "activity",
                        {
                            "person": person,
                            "account": account,
                            "action": rng.choice(CLIENT_ACTIONS),
                            "target": rng.choice(
                                [
                                    "Enterprise Overview Deck",
                                    "Security & Compliance Pack",
                                    "Pricing One-Pager",
                                ]
                            ),
                            "seconds_on_page": rng.randint(20, 900),
                            "device": rng.choice(["desktop", "mobile", "tablet"]),
                            "occurred_at": (
                                entered + timedelta(minutes=step * rng.randint(2, 9))
                            ).isoformat(timespec="seconds"),
                        },
                        room_id=room_id,
                        actor="system",
                        source="seed",
                    )
                    events += 1

    # Internal rep activity. A rep's own page views look exactly like a client's
    # unless something says otherwise, which is the whole point of the
    # distinction: these rows must never reach a client tile.
    for index, (room_id, account) in enumerate(created_rooms):
        rep = DEMO_REPS[index % len(DEMO_REPS)]
        for _ in range(rng.randint(3, 7)):
            db.create(
                "activity",
                {
                    "person": rep,
                    "user_type": "internal",
                    "account": account,
                    "action": rng.choice(INTERNAL_ACTIONS),
                    "occurred_at": (
                        now - timedelta(days=rng.randint(0, 20), minutes=rng.randint(0, 600))
                    ).isoformat(timespec="seconds"),
                },
                room_id=room_id,
                actor=rep,
                source="seed",
            )
            events += 1

    # An anonymous interaction: no person, so no audience can be resolved. It must
    # be counted in coverage and kept out of every client total.
    for room_id, account in created_rooms[:1]:
        db.create(
            "activity",
            {
                "account": account,
                "action": "viewed",
                "target": "Pricing One-Pager",
                "occurred_at": (now - timedelta(days=2)).isoformat(timespec="seconds"),
            },
            room_id=room_id,
            actor="system",
            source="seed",
        )
        events += 1

    implementations = 0
    for spec in DEMO_IMPLEMENTATIONS:
        data: dict[str, Any] = {
            "name": spec["name"],
            "account": spec["account"],
            "status": spec["status"],
        }
        if spec.get("owner"):
            data["owner"] = spec["owner"]
        if "started_days_ago" in spec:
            data["started_at"] = (now - timedelta(days=spec["started_days_ago"])).isoformat(
                timespec="seconds"
            )
        if "completed_days_ago" in spec:
            data["completed_at"] = (now - timedelta(days=spec["completed_days_ago"])).isoformat(
                timespec="seconds"
            )
        if "due_days_ago" in spec:
            data["due_at"] = (now - timedelta(days=spec["due_days_ago"])).isoformat(
                timespec="seconds"
            )
        db.create("implementation", data, actor="dana", source="seed")
        implementations += 1

    at_risk = 0
    on_time = 0
    dated_completions = 0
    for spec in DEMO_IMPLEMENTATIONS:
        due = spec.get("due_days_ago")
        done = spec.get("completed_days_ago")
        # due_days_ago larger than completed_days_ago means the deadline passed
        # before the work landed, i.e. late.
        if spec["status"] in ("active", "onboarding", "in_progress") and (due is None or due > 0):
            at_risk += 1
        if done is not None and due is not None:
            dated_completions += 1
            if done >= due:
                on_time += 1
            else:
                at_risk += 1
    unassigned = sum(1 for spec in DEMO_IMPLEMENTATIONS if not spec.get("owner"))
    return (
        f"{events} activity events ({len(DEMO_REPS)} reps, "
        f"{len(DEMO_CLIENTS)} accounts incl. one with no client activity, 1 anonymous), "
        f"{implementations} implementations ({at_risk} at risk, {unassigned} unassigned, "
        f"{on_time}/{dated_completions} completed on time), "
        f"internal identities configured: {', '.join(DEMO_REPS)}"
    )
