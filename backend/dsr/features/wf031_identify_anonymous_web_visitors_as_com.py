"""WF-031: identify anonymous web visitors as companies and filter by pages visited.

The domain logic is in :mod:`dsr.visitor_identification`, which this module does
not own and which no other feature could have written into its own path. What
lives here is the three things a workflow has to take out of shared files: the
HTTP surface, the mapping from domain errors to responses, and the demo data.

Why the surface is account-scoped and not room-scoped
----------------------------------------------------

The research has no room in it anywhere. A tracking snippet is installed on a
website, the Pages list belongs to the account whose dashboard holds it, and an
identified company is a property of the portal. Scoping a capture to a room would
mean the same site had to be installed once per room, and that traffic from a
buyer's own browser would be attributed to whichever room the visitor happened to
be reading. The reading is recorded as ``room_scope`` in
:mod:`dsr.visitor_identification.inferences`.

Why the capture endpoint is the only write a tracking snippet needs
-------------------------------------------------------------------

The data flow names one inbound event - "Anonymous website request -> tracking
script reads IP address, country, network and other public parameters -> matched
against the company database -> a company-level record is created" - and the
"other public parameters" are not enumerated. :func:`capture` therefore closes
its vocabulary at the three parameters the research names and refuses anything
else, which is what keeps the privacy stance a property of the code rather than
of every caller's snippet configuration.

``source=`` comes from the route
--------------------------------

Every write below passes ``f"{router.prefix}..."`` so the audit row names the
route that actually served it. A hardcoded string inside a domain method is a
defect, and the same class of bug has shipped in this codebase before: a
feature's audit log kept naming a path the app had stopped serving.

No network call is made
-----------------------

The research states plainly that "no public REST reference was reachable in the
sources read". The surfaces it documents are the dashboard, install guides, CRM
connectors, Zapier, Slack, Teams and webhooks. So this is a room-side design, not
a copy of a vendor API: the company database this workflow matches against is
the store, and the Pages list is served from here. An outbound call inside an
audited route would make the audit row depend on a third party.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.visitor_identification.company import keyed
from dsr.visitor_identification.engine import VisitorEngine
from dsr.visitor_identification.errors import VisitorIdentificationError

FEATURE = {
    "id": "wf-031-identify-anonymous-web-visitors-as-com",
    "ticket": "WF-031",
    "name": "Identify anonymous web visitors as companies and filter by pages visited",
    "description": (
        "Resolve an anonymous website request to a company record and never to a person, define "
        "the intent pages by path without the domain, and filter the ranked in-market lead list by "
        "the pages a company read. The match conditions are Exact, Contains and Starts with."
    ),
    "nav": [{"id": "identified-companies", "label": "Identified companies"}],
}

router = APIRouter(prefix="/api/wf-031", tags=["wf031"])


def get_engine(store: RecordStore = StoreDep) -> VisitorEngine:
    """A :class:`VisitorEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return VisitorEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _visitor_error(request: Request, exc: VisitorIdentificationError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``VisitorIdentificationError`` is the base
    of every refusal in :mod:`dsr.visitor_identification` - a page definition
    carrying a domain, a capture naming a person, a condition outside the three,
    a client id with no snippet installed - and all of them are the caller's to
    fix.

    ``RecordNotFound`` is deliberately *not* claimed: the core app already maps it
    to 404, and two handlers for one type is a collision the host refuses.
    """
    content: dict[str, Any] = {"error": exc.code, "detail": str(exc), "status": exc.status}
    return JSONResponse(status_code=exc.status, content=content)


EXCEPTION_HANDLERS = {VisitorIdentificationError: _visitor_error}


# --------------------------------------------------------------------------- #
# What this workflow publishes about itself
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """Every published value, served as data.

    The three match conditions with the vendor's own labels, the three public
    capture parameters, the five researched company fields, the two keys a
    contact may carry, the lead-list filter set, the ranking, and the two
    downstream surfaces this workflow hands a company list to without building
    them. A page renders its pickers from this rather than from a list compiled
    into the component.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the five company fields, the three match conditions, the
    three public parameters and the rule that a page definition carries a path and
    not a URL. It does not say what identifies a company, how the lead list ranks,
    or where this workflow ends. Those edges are collected here - named,
    traceable, and served - rather than left as comments in function bodies.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Step 1: the tracking snippet
# --------------------------------------------------------------------------- #


@router.get("/installations")
def list_installations(engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """The tracking snippets installed on this portal, with the site each serves."""
    return engine.installations()


@router.post("/installations", status_code=201)
def install(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """ "Install the Albacross tracking snippet on the site and note the Client ID."

    A capture is refused without a client id, so this is what makes a capture
    addressable. Installing the same id twice is not a failure: it answers 200
    with ``created: false`` and re-points the site, because re-running the
    install guide is how a seller arrives here twice.
    """
    return engine.install(payload, actor="system", source=f"POST {router.prefix}/installations")


# --------------------------------------------------------------------------- #
# The capture
# --------------------------------------------------------------------------- #


@router.post("/captures", status_code=201)
def capture(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """One anonymous website request, as the tracking script sends it.

    "We check the IP address, the country, the network, and other publicly
    available parameters to stay GDPR compliant." Those three are the whole
    vocabulary, plus the path requested and the client id. Anything else is
    refused, and a person-level key is refused by name: "Albacross focuses on
    exclusively company-level identification rather than tracking individual
    users".

    The response says whether the company was created for this request, which
    intent pages the path satisfied, and the five researched company fields.
    """
    return engine.capture(
        payload, actor="tracking-snippet", source=f"POST {router.prefix}/captures"
    )


# --------------------------------------------------------------------------- #
# Steps 3 and 4: the Pages list
# --------------------------------------------------------------------------- #


@router.get("/pages")
def list_pages(
    client_id: str | None = Query(default=None, description="the Pages list to read"),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """The intent pages, with the path and the match condition each one uses.

    "Give the page a name and input the URL. You can now select this page when
    using the Pages filter." The Pages list belongs to the account whose dashboard
    holds it, so ``client_id`` selects one account's list and no value reads them
    all.
    """
    return engine.pages(client_id or "")


@router.post("/pages", status_code=201)
def define_page(
    payload: dict[str, Any] = Body(default_factory=dict),
    client_id: str | None = Query(default=None, description="the Pages list to add to"),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """Define a page that signals intent: a name, a path, and a match condition.

    "When you type in the web page URL do not include the domain", so
    ``https://acme.example/pricing`` is refused and ``/pricing`` is accepted. The
    condition is one of Exact, Contains or Starts with.
    """
    return engine.define_page(
        payload, client_id=client_id or "", actor="system", source=f"POST {router.prefix}/pages"
    )


@router.patch("/pages/{page_id}")
def amend_page(
    page_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    client_id: str | None = Query(default=None),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """Change a page definition, re-checked the way a new one is checked.

    Step four is a choice a seller may revisit, so the condition and the path are
    both editable. A match is computed when the list is read, so an edit changes
    the filter immediately and nothing has to be swept.
    """
    return engine.amend_page(
        page_id,
        payload,
        client_id=client_id or "",
        actor="system",
        source=f"PATCH {router.prefix}/pages/{{page_id}}",
    )


@router.delete("/pages/{page_id}")
def drop_page(page_id: str, engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """Remove a page from the Pages list."""
    return engine.drop_page(
        page_id, actor="system", source=f"DELETE {router.prefix}/pages/{{page_id}}"
    )


# --------------------------------------------------------------------------- #
# The ICP the lead list can be filtered by
# --------------------------------------------------------------------------- #


@router.get("/icps")
def list_profiles(engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """The saved ideal customer profiles, with the sizes and countries each covers."""
    return engine.profiles()


@router.post("/icps", status_code=201)
def save_profile(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """Save the ideal customer profile the lead list filters by.

    "combine with Segment filters, tags, and the ICP". The research names the ICP
    as a filter and not as a predicate, so the criteria it accepts are the two
    company facts this workflow already holds: the size, which is one of the five
    named fields, and the country, which is one of the three named capture
    parameters. At least one is required.
    """
    return engine.save_profile(payload, actor="system", source=f"POST {router.prefix}/icps")


@router.delete("/icps/{icp_id}")
def drop_profile(icp_id: str, engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """Remove a saved profile. A lead-list filter naming it then refuses."""
    return engine.drop_profile(
        icp_id, actor="system", source=f"DELETE {router.prefix}/icps/{{icp_id}}"
    )


# --------------------------------------------------------------------------- #
# Step 5: the lead list
# --------------------------------------------------------------------------- #


@router.get("/companies")
def lead_list(
    page: list[str] | None = Query(default=None, description="the Pages filter"),
    segment: str | None = Query(default=None),
    tag: list[str] | None = Query(default=None),
    icp: str | None = Query(default=None, description="a saved ideal customer profile id"),
    country: str | None = Query(default=None),
    size: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """The ranked in-market list, filtered by the Pages filter and its companions.

    "In the lead list, apply the **Pages** filter to isolate companies that visited
    those pages; combine with Segment filters, tags, and the ICP."

    Naming two pages means either of them. Naming two tags means both of them,
    because a tag list is a description of a company and a page list is a choice of
    pages to look for. The response echoes the filter set it applied, so the page
    never has to guess what the server read.
    """
    return engine.lead_list(
        {
            "page": page or [],
            "segment": segment,
            "tag": tag or [],
            "icp": icp,
            "country": country,
            "size": size,
            "limit": limit,
        }
    )


@router.post("/companies", status_code=201)
def add_company(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """Add a company by hand, for a site that has not been seen yet.

    "matched against the company database" implies the database has entries of its
    own. A hand-added company has no network behind it, so the key is the
    seller's to choose, and a key already in the table is refused rather than
    overwritten.
    """
    return engine.create_company(payload, actor="system", source=f"POST {router.prefix}/companies")


@router.get("/companies/{company_key}")
def read_company(company_key: str, engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """Step 6: one company, with the five researched fields.

    "The insights provided include the company's name, website, address, size, and
    a list of employees or contacts associated with the company."

    The five are present and possibly empty, because a company identified a
    moment ago has none of them and that is a normal state. The pages it has
    satisfied are evaluated here rather than stored, so a page defined after the
    visit still shows.
    """
    return engine.company(company_key)


@router.patch("/companies/{company_key}")
def amend_company(
    company_key: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """Set the company's name, website, address, size and contacts.

    Also the segment and the tags, because the lead-list filter needs them and the
    research names both. Everything else on the record is derived from captures
    and is not writable here. A field that is present but blank is refused: the
    drill-down has five columns and a blank one has no way to say "not known yet".
    """
    return engine.update_company(
        company_key,
        payload,
        actor=actor or "system",
        source=f"PATCH {router.prefix}/companies/{{company_key}}",
    )


@router.get("/companies/{company_key}/visits")
def read_visits(
    company_key: str,
    limit: int = Query(default=50, ge=1, le=200),
    engine: VisitorEngine = EngineDep,
) -> dict[str, Any]:
    """The page-visit events recorded for this company, newest first.

    "page-visit events recorded per URL path" - one row per request, grouped by
    path in ``top_paths`` so the table the research describes is one query. Every
    row is a company-level fact: a path, a country, and the public parameters the
    request arrived with.
    """
    return engine.visits(company_key, limit=limit)


@router.get("/companies/{company_key}/pages")
def read_company_pages(company_key: str, engine: VisitorEngine = EngineDep) -> dict[str, Any]:
    """Which intent pages this company has satisfied, and under which condition."""
    return engine.matched_pages(company_key)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The tracking snippets the demo installs. Two clients, because the Pages list
#: belongs to an account and a page defined under one must not filter the other.
DEMO_CLIENTS: tuple[dict[str, Any], ...] = (
    {"client_id": "cli-acme", "site": "https://acme.example"},
    {"client_id": "cli-northwind", "site": "https://northwind.example"},
)

#: The intent pages the demo defines. Every one of the three researched conditions
#: is represented, so the Pages filter has a row for each and the lead list has
#: three different answers to give.
DEMO_PAGES: tuple[dict[str, Any], ...] = (
    {
        "name": "Read the conversion article",
        "path": "/newsroom/converting-the-unconverted-article",
        "condition": "Starts with",
    },
    {
        "name": "Asked for a price",
        "path": "/pricing",
        "condition": "Exact",
    },
    {
        "name": "Read anything under pricing",
        "path": "/pricing",
        "condition": "Contains",
    },
    {
        "name": "Read the security pack",
        "path": "/security",
        "condition": "Starts with",
    },
    {
        "name": "The careers page",
        "path": "/careers",
        "condition": "Contains",
    },
)

#: The saved ideal customer profile the demo filters by.
DEMO_PROFILE = {
    "name": "Mid-market and larger",
    "sizes": ["201-500", "501-1000", "1000+"],
    "countries": [],
}

#: One hand-added company. "matched against the company database" implies the
#: database has entries of its own, and this is the row that makes "a company the
#: seller already knew about" a state rather than a claim.
DEMO_MANUAL_COMPANY = {
    "company_key": "tailwind-and-friends",
    "name": "Tailwind and Friends",
    "website": "https://tailwindandfriends.example",
    "address": "18 Harbour Road, Wellington",
    "size": "51-200",
    "contacts": [
        {"name": "Ivy Nakamura", "role": "Head of Revenue Operations"},
        {"name": "Sam Okafor", "role": "Procurement Lead"},
    ],
    "segment": "mid-market",
    "tags": ["in-market", "tail-lights"],
}

#: The company detail the demo fills in after the captures have created the
#: companies. Written against the network rather than the company key, because the
#: key is derived from the network and the whole point of the table is that a
#: capture made the row. Three of the four fill every researched field; the fourth
#: is left mostly blank on purpose, so the drill-down shows what an unidentified
#: company looks like.
DEMO_COMPANY_DETAIL: tuple[dict[str, Any], ...] = (
    {
        "network": "203.0.113.0/24",
        "name": "Northwind Traders",
        "website": "https://northwind.example",
        "address": "4 Shipley Lane, Manchester",
        "size": "1000+",
        "segment": "enterprise",
        "tags": ["in-market", "tail-lights"],
        "contacts": [
            {"name": "Dana Kelly", "role": "Chief Revenue Officer"},
            {"name": "Bo Nkemelu", "role": "Solutions Architect"},
        ],
    },
    {
        "network": "198.51.100.0/24",
        "name": "Fabrikam Logistics",
        "website": "https://fabrikam.example",
        "address": "9 Dock Road, Rotterdam",
        "size": "501-1000",
        "segment": "mid-market",
        "tags": ["in-market"],
        "contacts": [{"name": "Lukas Weber", "role": "IT Director"}],
    },
    {
        "network": "192.0.2.128/25",
        "name": "Talent Insight Partners",
        "website": "https://talentinsight.example",
        "address": "7 Fenchurch Avenue, London",
        "size": "51-200",
        "segment": "agency",
        "tags": ["agency"],
        "contacts": [],
    },
)

#: The captured traffic. Written as visits rather than generated, so a reviewer can
#: read exactly which state each row is here for - including the states that are
#: *not* successes.
DEMO_CAPTURES: tuple[dict[str, Any], ...] = (
    # Northwind read the article and then asked for a price, which is the whole
    # workflow in two rows: two companies out of three have visited an intent page.
    {
        "client_id": "cli-acme",
        "network": "203.0.113.0/24",
        "ip_address": "203.0.113.11",
        "country": "GB",
        "path": "/newsroom/converting-the-unconverted-article",
        "days_ago": 9,
    },
    {
        "client_id": "cli-acme",
        "network": "203.0.113.0/24",
        "ip_address": "203.0.113.12",
        "country": "GB",
        "path": "/pricing",
        "days_ago": 2,
    },
    # A second address on the same network, so the "narrowest evidence first"
    # lookup is a row rather than a claim: this address was never on file and the
    # network resolves it to the same company.
    {
        "client_id": "cli-acme",
        "network": "203.0.113.0/24",
        "ip_address": "203.0.113.13",
        "country": "GB",
        "path": "/security/compliance-pack",
        "days_ago": 1,
    },
    # Fabrikam read the pricing *tree*, never the pricing page itself. That is the
    # whole point of the three conditions: Exact on /pricing matches Northwind and
    # not Fabrikam, Contains on the same path matches both. The two pages are
    # separate rows precisely so the lead list can give two different answers.
    {
        "client_id": "cli-acme",
        "network": "198.51.100.0/24",
        "ip_address": "198.51.100.31",
        "country": "NL",
        "path": "/pricing/enterprise",
        "days_ago": 5,
    },
    {
        "client_id": "cli-acme",
        "network": "198.51.100.0/24",
        "ip_address": "198.51.100.32",
        "country": "BE",
        "path": "/pricing/enterprise/resellers",
        "days_ago": 4,
    },
    # A request carrying a query string, so path normalisation is a row: the page
    # was defined as the bare path and the visit still matched it.
    {
        "client_id": "cli-acme",
        "network": "198.51.100.0/24",
        "ip_address": "198.51.100.32",
        "country": "BE",
        "path": "/newsroom/converting-the-unconverted-article?utm_source=linkedin",
        "days_ago": 3,
    },
    # Adventure Works: real traffic, stored, and matching nothing. Without this row
    # the Pages filter has nothing to exclude and "isolate companies that visited
    # those pages" is untestable from a table that only has matches.
    {
        "client_id": "cli-acme",
        "network": "192.0.2.0/24",
        "ip_address": "192.0.2.7",
        "country": "BR",
        "path": "/about",
        "days_ago": 7,
    },
    # A recruiting agency reading the seller's own careers page. It is in the lead
    # list with no intent page, which is the state the Pages filter is what removes.
    {
        "client_id": "cli-acme",
        "network": "192.0.2.128/25",
        "ip_address": "192.0.2.140",
        "country": "GB",
        "path": "/careers/sales",
        "days_ago": 2,
    },
    # The other account. Its own page and its own traffic, so the lead list shows
    # that a page defined under one client does not filter the other.
    {
        "client_id": "cli-northwind",
        "network": "203.0.113.128/25",
        "ip_address": "203.0.113.140",
        "country": "IE",
        "path": "/pricing",
        "days_ago": 1,
    },
)


def _seed_now(context: dict[str, Any]) -> datetime:
    value = context.get("now")
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """The tracking installs, the Pages list, the captured traffic, and the misses.

    The rows are produced by running the real :class:`VisitorEngine`, so the demo
    cannot show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only matches teaches a
    reviewer nothing. What is here on purpose:

    * **Two clients**, so the Pages list has an account boundary and the lead list
      shows that a page defined under one client does not filter the other.
    * **All three match conditions**, so ``Exact``, ``Contains`` and
      ``Starts with`` each have a row and the lead list has three different
      answers to give for the same traffic.
    * **A second address on a known network**, so the "narrowest evidence first"
      company lookup is a row rather than a claim.
    * **A visit carrying a query string**, so path normalisation is a row and the
      page defined as the bare path still matched.
    * **A company that matched nothing** - Adventure Works, which only ever read
      ``/about`` - so the Pages filter has something to isolate out.
    * **A recruiting agency reading the seller's own careers page**, so the lead
      list contains a company a human would not call in-market and the Pages
      filter is what removes it.
    * **A hand-added company** with every researched field filled in and no network
      behind it, because "matched against the company database" implies the
      database has entries of its own.
    * **A size outside the saved ICP**, so the ICP filter has a negative case that
      is not an empty list.
    """
    store = RecordStore(db)
    context.get("rng") or random.Random("wf031")
    base = _seed_now(context)
    actor = "dana"
    source = "seed"
    engine = VisitorEngine(store, now=lambda: base)

    def at(days_ago: float) -> str:
        return (base - timedelta(days=days_ago)).isoformat()

    for client in DEMO_CLIENTS:
        engine.install(client, actor=actor, source=source)

    for page in DEMO_PAGES:
        engine.define_page(
            {**page, "client_id": "cli-acme"}, client_id="cli-acme", actor=actor, source=source
        )
    # The second account defines its own page, and it is the same path under a
    # different condition, so the two lists are not interchangeable.
    engine.define_page(
        {
            "name": "Asked for a price",
            "path": "/pricing",
            "condition": "Exact",
            "client_id": "cli-northwind",
        },
        client_id="cli-northwind",
        actor=actor,
        source=source,
    )

    engine.save_profile(DEMO_PROFILE, actor=actor, source=source)

    for entry in DEMO_CAPTURES:
        engine.capture(
            {
                "client_id": entry["client_id"],
                "path": entry["path"],
                "network": entry["network"],
                "ip_address": entry["ip_address"],
                "country": entry["country"],
                "captured_at": at(entry["days_ago"]),
            },
            actor="tracking-snippet",
            source=source,
        )

    # The hand-added company is keyed by name rather than by a network, so it is
    # written through an upsert here: a second run over the same database must
    # leave the demo in the same shape rather than fail, because a seed that
    # throws is a feature the seeder reports as broken.
    manual = {k: v for k, v in DEMO_MANUAL_COMPANY.items() if k != "company_key"}
    existing = store.find(
        "identified_company", {"company_key": DEMO_MANUAL_COMPANY["company_key"]}, limit=1
    )
    if existing:
        engine.update_company(
            DEMO_MANUAL_COMPANY["company_key"], manual, actor=actor, source=source
        )
    else:
        engine.create_company(DEMO_MANUAL_COMPANY, actor=actor, source=source)

    # The captured companies are found by the network their capture arrived from,
    # which is the same lookup the capture itself uses. A network absent from the
    # detail table stays a bare row, so the drill-down has an unidentified company
    # to show next to a filled-in one.
    for entry in DEMO_COMPANY_DETAIL:
        record = store.find(
            "identified_company",
            {f"known_networks.{keyed(entry['network'])}": entry["network"].lower()},
            limit=1,
        )
        assert record, f"the demo capture for {entry['network']} created no company"
        engine.update_company(
            record[0]["data"]["company_key"],
            {key: value for key, value in entry.items() if key != "network"},
            actor=actor,
            source=source,
        )

    return (
        "2 tracking installs, 6 intent pages, 1 ideal customer profile, "
        "9 captured visits, 6 identified companies"
    )
