"""WF-033: auto-add and continuously track in-market companies from intent signals.

The domain logic is in :mod:`dsr.market_intent`, which this module does not own
and which no other feature could have written into its own path. What lives here
is the three things a workflow has to take out of shared files: the HTTP surface,
the mapping from domain errors to responses, and the demo data.

Two properties of the specification shaped the routes
-----------------------------------------------------

**Nothing here is room-scoped, and that is a reading rather than an oversight.**
The research has no room concept. A target market, an intent criterion, a
research topic, an exclusion list and a saved view are all seller settings, the
stock auto-add categories run over the whole account, and "auto-add will only
add companies that enter your saved views" is a statement about a view, not about
a room. Scoping them to a room would make the same view name registerable twice
and would imply a view sees only one room's buyers, which the research never
says. The reading is recorded as ``views_are_portal_scoped`` in
:mod:`dsr.market_intent.inferences`, and the prefix is
``/api/wf-033/...`` rather than ``/api/wf-033/rooms/{room_id}/...``.

**No outbound CRM call is made.** The research is explicit that its page is
UI-first and that "no public buyer-intent REST endpoint was documented in the
page read", and the four CRM primitives it cites are recorded as
:data:`dsr.market_intent.engine.CRM_PLAN` on every added company rather than
called. A network call inside a route that has to be audited and deterministic
would make the audit row depend on a third party.

``source=`` comes from the route
--------------------------------
Every write below passes ``f"{router.prefix}..."`` so the audit row names the
route that actually served it. A hardcoded string inside a domain method is a
defect, and the same class of bug has shipped in this codebase before: a
feature's audit log kept naming a path the app had stopped serving.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.market_intent import names as collection_names
from dsr.market_intent import views as view_rules
from dsr.market_intent.engine import MarketIntentEngine
from dsr.market_intent.errors import MarketIntentError
from dsr.market_intent.vocabulary import AUTOMATION_ADD, AUTOMATION_TRACK
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-033-auto-add-and-continuously-track-in-mar",
    "ticket": "WF-033",
    "name": "Auto-add and continuously track in-market companies from intent signals",
    "description": (
        "Hold the buyer-intent table of in-market companies, save a named view over it, and "
        "auto-add and continuously track the companies that enter that view. Activity rolls up "
        "into a root domain, time frames are last-visit based and capped at 90 midnight-UTC days, "
        "and the first charge in a billing period is the only one."
    ),
    "nav": [{"id": "intent-companies", "label": "Intent companies"}],
}

router = APIRouter(prefix="/api/wf-033", tags=["wf033"])


def get_engine(store: RecordStore = StoreDep) -> MarketIntentEngine:
    """A :class:`MarketIntentEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return MarketIntentEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _market_intent_error(request: Request, exc: MarketIntentError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``MarketIntentError`` is the base of
    every refusal in :mod:`dsr.market_intent` - a criterion with no page, a
    time frame older than ninety days, a view name already taken, a lifecycle
    stage moved backwards, a feature that needs credits this portal does not
    have - and all of them are the caller's to fix. The status rides on the
    exception rather than being decided here, because a time frame the research
    caps at 90 days and a view name that collides with an existing one are both
    this package's errors and only one of them conflicts with state that already
    exists.

    ``RecordNotFound`` is deliberately *not* claimed: the core app already maps
    it to 404, and two handlers for one type is a collision the host refuses.
    """
    content: dict[str, Any] = {"error": exc.code, "detail": str(exc), "status": exc.status}
    capability = getattr(exc, "capability", "")
    if capability:
        content["capability"] = capability
    return JSONResponse(status_code=exc.status, content=content)


EXCEPTION_HANDLERS = {MarketIntentError: _market_intent_error}


# --------------------------------------------------------------------------- #
# Vocabulary, inferences, gating
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The five path operators with the vendor's own labels, the three sort keys,
    the traffic sources, the news signal types, the two automation toggles, the
    four stock auto-add categories with their definitions, the credit costs, and
    the four Buyer Intent card fields. A client renders its pickers from this
    rather than from a list compiled into the page.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the filter vocabulary, the four stock categories, the
    90-day ceiling, the 10-credit charge and the record-source value. It does not
    say how a root domain is found, which midnight the ninety days count from,
    or when a billing period starts, so the edges are collected here - named,
    traceable, and served - rather than left as comments in function bodies.
    """
    return engine.inferences()


@router.get("/settings")
def read_settings(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The portal's gating state: HubSpot Credits, and who may enrich."""
    return engine.settings()


@router.patch("/settings")
def patch_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Switch HubSpot Credits on, or grant the Data enrichment permission.

    "To add and enrich companies from buyer intent, Super Admin must assign users
    with Data enrichment permissions" - modelled as a granted-actor list rather
    than as a role tier, because the research says an administrator *assigns* the
    permission to a user and the user doing the work is an assignee.
    """
    return engine.update_settings(payload, actor=actor, source=f"PATCH {router.prefix}/settings")


@router.get("/capabilities")
def capabilities(
    actor: str | None = Query(default=None, description="the calling user"),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """What this caller may do, so a page hides what would be refused.

    A page that renders a control the API will refuse is a page that teaches a
    seller the product is broken, so the gate is published rather than discovered.
    """
    return engine.capabilities(actor)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@router.get("/criteria")
def list_criteria(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The intent criteria, each with the custom property it derives."""
    listed = [criterion.to_dict() for criterion in engine.criteria()]
    return {"count": len(listed), "criteria": listed}


@router.post("/criteria", status_code=201)
def create_criterion(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Define an intent criterion: a name, and the pages that mean it.

    "intent criteria per page" is why a criterion with no page filter is
    refused. ``derived_property`` is the extensibility hook: name it "Showing
    SMB Intent" and the property follows the criteria on every run, including
    back to false.
    """
    return engine.add_criterion(payload, actor="system", source=f"POST {router.prefix}/criteria")


@router.delete("/criteria/{criterion_id}")
def withdraw_criterion(
    criterion_id: str,
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Withdraw a criterion, so it stops qualifying anything.

    The row is kept rather than deleted because the derived property it wrote
    onto company records has to be re-derived to ``false`` rather than orphaned.
    """
    return engine.withdraw_criterion(
        criterion_id, actor="system", source=f"DELETE {router.prefix}/criteria/{criterion_id}"
    )


@router.get("/topics")
def list_topics(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The research topics the Research tab matches against."""
    listed = engine.topics()
    return {"count": len(listed), "topics": listed}


@router.post("/topics", status_code=201)
def create_topic(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Set up a research topic, and the terms that match it."""
    return engine.add_topic(payload, actor="system", source=f"POST {router.prefix}/topics")


@router.delete("/topics/{topic_id}")
def withdraw_topic(topic_id: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """Withdraw a research topic."""
    return engine.withdraw_topic(
        topic_id, actor="system", source=f"DELETE {router.prefix}/topics/{topic_id}"
    )


@router.get("/markets")
def list_markets(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The target markets a company can be "in"."""
    listed = engine.markets()
    return {"count": len(listed), "markets": listed}


@router.post("/markets", status_code=201)
def create_market(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Define a target market by country, by industry, or by both.

    Refused if it names neither: a market every company is in makes the "In my
    target markets" filter a control that does nothing.
    """
    return engine.add_market(payload, actor="system", source=f"POST {router.prefix}/markets")


@router.get("/exclusions")
def list_exclusions(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The domains excluded from the table and from every automation.

    Credit-gated, and the 402 says which capability was refused so a page can
    point at the specific control rather than at the whole feature.
    """
    listed = engine.exclusions()
    return {"count": len(listed), "exclusions": listed}


@router.post("/exclusions", status_code=201)
def create_exclusion(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Exclude a domain, and every host rolled up into it.

    Credit-gated. The domain is reduced through the root-domain model first, so
    excluding ``www.south.example.com`` excludes ``south.example.com`` - a filter
    keyed on a subdomain would stop excluding anything the moment the company
    used a different one.
    """
    return engine.exclude(payload, actor=actor, source=f"POST {router.prefix}/exclusions")


@router.delete("/exclusions/{domain}")
def remove_exclusion(
    domain: str,
    actor: str | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Stop excluding a domain. Credit-gated."""
    return engine.unexclude(
        domain, actor=actor, source=f"DELETE {router.prefix}/exclusions/{{domain}}"
    )


# --------------------------------------------------------------------------- #
# Observations
# --------------------------------------------------------------------------- #


@router.post("/visits", status_code=201)
def record_visit(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """One tracked page view, from the tracking code.

    "the HubSpot tracking code ... collects visitor data, such as website
    activity data, IP addresses, and other online identifiers. This data is used
    ... for website visits to be matched to companies." The matching happens here,
    by IP address first and then by a known contact, and the response says which
    route it took - or that the visitor is still anonymous.

    Nothing derived is stored: the Intent tag is computed on read against the
    current criteria, so a criterion added today tags a visit from last week and
    a withdrawn one stops.
    """
    return engine.record_visit(payload, actor="system", source=f"POST {router.prefix}/visits")


@router.post("/research", status_code=201)
def record_research(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """One research observation: a topic match, or a company news signal.

    "broader intent signals beyond your website, such as companies researching
    topics across the web or company news like funding, executive hires,
    layoffs, product launches, and mergers."
    """
    return engine.record_research(payload, actor="system", source=f"POST {router.prefix}/research")


@router.get("/research")
def research_tab(
    days: int | None = Query(default=None, description="last-visit time frame, at most 90 days"),
    visitor_intent: bool | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """The Research tab: who is researching a topic, and who is in the news.

    The two are counted separately so a company that raised money is not
    reported as a company reading about cloud security, even though both are
    "broader intent signals" and both set research intent.
    """
    filters = view_rules.FilterSet.parse(
        {"days": days, "visitor_intent": bool(visitor_intent)}
    )
    return engine.research_tab(filters)


# --------------------------------------------------------------------------- #
# The Visitors tab
# --------------------------------------------------------------------------- #


@router.get("/companies")
def list_companies(
    days: int | None = Query(default=None, description="last-visit time frame, at most 90 days"),
    visitor_intent: bool | None = Query(default=None, description="Showing visitor intent"),
    traffic_source: list[str] | None = Query(default=None),
    country: list[str] | None = Query(default=None),
    path: list[str] | None = Query(
        default=None, description="operator:value, e.g. starts_with:/pricing or eq:/pricing@shop.example.com"
    ),
    in_target_markets: bool | None = Query(default=None),
    segment: str | None = Query(default=None, description="Filter by segment; needs HubSpot Credits"),
    lifecycle_stage: list[str] | None = Query(default=None),
    deal_stage: list[str] | None = Query(default=None),
    owner: list[str] | None = Query(default=None),
    sort: str = Query(default="page_views"),
    direction: str = Query(default="desc"),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """The buyer-intent table: every company, filtered and sorted.

    The whole left panel in one query string, in the research's own order, and
    the two credit-gated pieces (the segment filter and the exclusion list) say
    so with a 402 rather than silently ignoring themselves.

    ``path`` is ``operator:value`` or ``operator:value@domain`` - the five
    researched operators plus Domain - because a query parameter cannot carry a
    list of objects and a view's stored filter set is the same shape.
    """
    filters = view_rules.FilterSet.parse(
        {
            "days": days,
            "visitor_intent": bool(visitor_intent),
            "traffic_sources": traffic_source or [],
            "countries": country or [],
            "page_filters": [_parse_path_clause(entry) for entry in (path or [])],
            "in_target_markets": bool(in_target_markets),
            "segment": segment,
            "lifecycle_stages": lifecycle_stage or [],
            "deal_stages": deal_stage or [],
            "owners": owner or [],
            "sort": sort,
            "direction": direction,
        }
    )
    return engine.companies(filters)


def _parse_path_clause(entry: str) -> dict[str, str]:
    """``operator:value[@domain]`` into a page filter object.

    Split on the first colon so a path containing a colon still parses, and
    split the domain off the end so a path containing an ``@`` still parses.
    """
    text = str(entry or "")
    operator, separator, remainder = text.partition(":")
    if not separator:
        raise HTTPException(
            status_code=422,
            detail=f"path filter {text!r} must be operator:value, e.g. starts_with:/pricing",
        )
    # ``rpartition`` returns (before, separator, after), and the separator is empty
    # when there is no "@" - so the domain is the *after* part or nothing at all.
    # Getting that order wrong puts the path in the domain field and refuses
    # every page filter the route was written to accept.
    before, at, after = remainder.rpartition("@")
    return {
        "operator": operator.strip().lower(),
        "path": before or remainder,
        "domain": after.strip().lower() if at else "",
    }


@router.get("/companies/{company_key}")
def read_company(company_key: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """One company: the Visitors tab's row and the About tab's fields."""
    row = engine.company(company_key)
    if row is None:
        raise HTTPException(status_code=404, detail=f"company {company_key} not found in the table")
    return row


@router.patch("/companies/{company_key}")
def patch_company(
    company_key: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Update a company record: its CRM fields, its known IPs, or any property.

    ``lifecyclestage`` is forward-only and the move is refused rather than
    documented, because a company whose stage went backwards drops out of every
    view that filters on it without saying why. ``properties`` takes arbitrary
    keys, so a team adding a field needs no migration and no coordination.
    """
    return engine.update_company(company_key, payload, actor=actor, source=f"PATCH {router.prefix}/companies/{{company_key}}")


@router.get("/companies/{company_key}/card")
def read_card(company_key: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The Buyer Intent card, with the four fields the research names.

    "Settings -> Objects -> Companies -> Record Customization -> Add cards" for
    website visits, unique visitors, last seen, and top page views, plus the
    "View full visit activity" link's target.
    """
    card = engine.card(company_key)
    if not card.get("found"):
        raise HTTPException(status_code=404, detail=f"company {company_key} not found in the table")
    card["full_activity"]["href"] = f"{router.prefix}/companies/{company_key}/page-views"
    return card


@router.get("/companies/{company_key}/page-views")
def read_page_views(
    company_key: str,
    limit: int = Query(default=50, ge=1, le=200),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """The Recent page views drill-down, with the Intent tag on the qualifying ones.

    "To review the most recent page views from a company, including IP-derived
    country and date and time of the website visit" - both are stored with the
    visit. A visit's qualifying page is tagged with Intent, so the drill-down
    says *which* page made the company an intent company.
    """
    return engine.page_views(company_key, limit=limit)


@router.get("/companies/{company_key}/contacts")
def read_contacts(company_key: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The Contacts tab: last touch, last engagement, and planned meetings.

    "you can also review the contact's last touch, last engagement, and any
    recently scheduled interactions such as planned meetings."
    """
    return engine.contacts(company_key)


@router.post("/companies/{company_key}/enroll", status_code=201)
def enroll_company(
    company_key: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Enrol a company in a workflow, by hand, from the Visitors tab.

    The researched manual control beside the automated ones. No watermark,
    because this is a person pointing at a company in front of them; still
    permission-gated, because it writes a CRM record on the strength of a signal
    rather than on something the seller typed.

    Enrolling the same company in the same workflow twice is not an error: it
    answers 200 with ``already_enrolled``, because a double-click on a button
    should not produce a failure banner over a company that *was* enrolled.
    """
    result = engine.enroll(
        company_key, payload, actor=actor, source=f"POST {router.prefix}/companies/{{company_key}}/enroll"
    )
    if result.get("outcome") == "already_enrolled" and response is not None:
        response.status_code = 200
    return result


# --------------------------------------------------------------------------- #
# Saved views
# --------------------------------------------------------------------------- #


@router.get("/views")
def list_views(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The saved views, each with the filter set it persists."""
    listed = engine.views()
    return {"count": len(listed), "views": listed}


@router.post("/views", status_code=201)
def save_view(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """"Click Save view to persist the filter set as a named view."

    A name already in use is refused with 409 rather than overwritten, because an
    automation is attached to a view and two views under one name would make
    which one it watches a matter of load order.
    """
    return engine.save_view(payload, actor="system", source=f"POST {router.prefix}/views")


@router.get("/views/{view_id}")
def read_view(view_id: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """One saved view, with its companies and when each entered."""
    result = engine.view_companies(view_id)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"saved view {view_id} not found")
    return result


@router.delete("/views/{view_id}")
def withdraw_view(view_id: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """Remove a saved view and the automation attached to it.

    Together, because an automation whose view no longer exists has nothing to
    fire on and would report itself healthy while doing nothing.
    """
    return engine.withdraw_view(view_id, actor="system", source=f"DELETE {router.prefix}/views/{{view_id}}")


@router.get("/views/{view_id}/companies")
def view_companies(view_id: str, engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """Which companies are in a saved view, and when each entered it.

    Every row carries ``entered_after_auto_add``, so the researched note - "auto-
    add will only add companies that enter your saved views after enabling the
    auto-add" - is a field on the response rather than a claim in a document.
    """
    result = engine.view_companies(view_id)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"saved view {view_id} not found")
    return result


# --------------------------------------------------------------------------- #
# Automations
# --------------------------------------------------------------------------- #


@router.get("/automations")
def list_automations(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The left panel's Automations section: both toggles, per saved view.

    Each carries the moment its toggle was switched on, because that moment is
    the watermark the researched note is about.
    """
    listed = engine.automations()
    return {"count": len(listed), "automations": listed}


@router.post("/automations")
def save_automation(
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """"Toggle the switches on. At the bottom, click Save automation."

    Switching a toggle on stamps when it was switched on, and saving again does
    not re-stamp it: a moving watermark would mean a company that entered last
    week could never be added, which is the opposite of what the toggle promises.
    """
    return engine.save_automation(payload, actor="system", source=f"POST {router.prefix}/automations")


@router.post("/automations/{automation_id}/run")
def run_automation(automation_id: str, actor: str | None = Query(default=None), engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """Apply one view's automation to the companies currently in its view.

    The researched rule, in full: "auto-add will only add companies that enter
    your saved views after enabling the auto-add. It will not add all existing
    companies in your saved views." A company that was already qualifying when
    the switch was thrown comes back in ``held_back`` with that reason, and the
    per-company credit outcome says whether the combined add-and-track case was
    charged once or twice.
    """
    result = engine.run(
        automation_id, actor=actor, source=f"POST {router.prefix}/automations/{{automation_id}}/run"
    )
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"automation {automation_id} not found")
    return result


# --------------------------------------------------------------------------- #
# The four stock auto-add categories
# --------------------------------------------------------------------------- #


@router.get("/categories")
def list_categories(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The four stock auto-add categories, with their definitions and state.

    They are stock, so their predicates are served rather than accepted: a caller
    cannot redefine what "net-new companies with visitor intent" means. The
    target-market requirement is not uniform across the four and the asymmetry is
    read off the research's own wording.
    """
    listed = engine.categories()
    return {"count": len(listed), "categories": listed}


@router.post("/categories/{category_id}")
def set_category(
    category_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Turn one stock auto-add category on or off, stamping when it was enabled."""
    return engine.set_category(category_id, payload, actor="system", source=f"POST {router.prefix}/categories/{{category_id}}")


@router.post("/categories/{category_id}/run")
def run_category(
    category_id: str,
    actor: str | None = Query(default=None),
    engine: MarketIntentEngine = EngineDep,
) -> dict[str, Any]:
    """Apply one stock category across the whole table.

    Same watermark as the per-view automation: a category switched on today is a
    decision about companies that arrive today. No time frame, because the
    research names none for these and a company known only from a topic match
    has no last visit for a last-visit-based window to test.
    """
    return engine.run_category(
        category_id, actor=actor, source=f"POST {router.prefix}/categories/{{category_id}}/run"
    )


# --------------------------------------------------------------------------- #
# Tracking and credits
# --------------------------------------------------------------------------- #


@router.get("/tracked")
def list_tracked(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The companies under continuous tracking, and the periods they were charged."""
    listed = engine.tracked()
    return {"count": len(listed), "tracked": listed}


@router.post("/tracked/renew")
def renew_tracking(actor: str | None = Query(default=None), engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """"After that initial charge, tracking continues to be charged monthly."

    One charge per tracked company per billing period. A second call in the same
    period charges nothing, which falls out of the one-row-per-company-per-period
    ledger rather than out of a check in this function.
    """
    return engine.renew(actor=actor, source=f"POST {router.prefix}/tracked/renew")


@router.get("/credits")
def read_credits(engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The credit ledger, with the saving on every combined add-and-track."""
    return engine.credits()


# --------------------------------------------------------------------------- #
# The Overview tab
# --------------------------------------------------------------------------- #


@router.get("/overview")
def overview(actor: str | None = Query(default=None), engine: MarketIntentEngine = EngineDep) -> dict[str, Any]:
    """The Overview tab: the five researched numbers, and the four categories.

    "companies showing research intent, visitor intent, converted-to-lifecycle-
    stage, Added Companies, Popular auto-adds".
    """
    return engine.overview(actor=actor)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The three markets the demo defines, so "In my target markets" has something to
#: divide the table with. The last one exists so a company can be in no market at
#: all, which is the state the filter's negative case lives in.
DEMO_MARKETS: tuple[dict[str, Any], ...] = (
    {
        "name": "ANZ and UK enterprise software",
        "countries": ["AU", "NZ", "GB", "IE"],
        "industries": ["software", "saas"],
    },
    {
        "name": "DACH and Benelux manufacturing",
        "countries": ["DE", "AT", "CH", "NL", "BE"],
        "industries": ["manufacturing", "logistics"],
    },
    {
        "name": "North America financial services",
        "countries": ["US", "CA"],
        "industries": ["financial services", "insurance"],
    },
)

#: The criteria the demo defines. Each names a page, and two of them name the
#: custom property the research's extensibility quote is about, so "Showing SMB
#: Intent" is a field on a company record rather than a sentence in a document.
DEMO_CRITERIA: tuple[dict[str, Any], ...] = (
    {
        "name": "Priced up",
        "derived_property": "showing_priced_up",
        "page_filters": [{"operator": "starts_with", "path": "/pricing"}],
    },
    {
        "name": "Asked for sales",
        "derived_property": "showing_asked_for_sales",
        "page_filters": [
            {"operator": "eq", "path": "/contact-sales"},
            {"operator": "eq", "path": "/demo-request"},
        ],
    },
    {
        "name": "SMB Intent",
        "derived_property": "showing_smb_intent",
        "page_filters": [
            {"operator": "contains", "path": "/smb"},
            {"operator": "eq", "path": "/plans"},
        ],
    },
    {
        "name": "Read the security pack",
        "derived_property": "",
        "page_filters": [{"operator": "contains", "path": "/security"}],
    },
    {
        "name": "Careers browsing",
        "derived_property": "",
        "page_filters": [{"operator": "contains", "path": "/careers"}],
    },
)

DEMO_TOPICS: tuple[dict[str, Any], ...] = (
    {"name": "Cloud security posture management", "terms": ["cspm", "cloud security posture"]},
    {"name": "Digital sales room", "terms": ["digital sales room", "dsr"]},
    {"name": "Revenue intelligence", "terms": ["revenue intelligence"]},
)

#: One company's whole history, expressed as visits. Written as data rather than
#: generated so a reviewer can read exactly which state each row is here for -
#: including the states that are *not* successes.
#:
#: ``days_ago`` is relative to the seeder's clock, and every row is inside the
#: 90-day ceiling, because a visit outside it is not in any view and would teach a
#: reviewer nothing except that the cap exists.
#:
#: ``declare`` is the upstream IP-to-company match the research lists under
#: ``data_sources``, submitted with the page view. Contoso Health's rows leave it
#: out on purpose: that company is in the account with its IP addresses on file,
#: so its visits are matched by the "known companies' IP addresses" path instead,
#: and both attribution routes are rows rather than one route and a claim.
DEMO_VISITS: tuple[dict[str, Any], ...] = (
    # Contoso Health: in the account already, so the "In-CRM with visitor intent"
    # category is a row rather than a claim, and the Buyer-Intent card has a CRM
    # badge to draw.
    {
        "company_domain": "contoso-health.com",
        "declare": False,
        "ip": "198.51.100.24",
        "path": "/pricing",
        "days_ago": 2,
        "session": "contoso-a",
        "visitor": "contoso-visitor-1",
        "traffic_source": "organic_search",
        "country": "US",
    },
    {
        "company_domain": "www.contoso-health.com",
        "declare": False,
        "ip": "198.51.100.25",
        "path": "/security/compliance-pack",
        "days_ago": 2,
        "session": "contoso-a",
        "visitor": "contoso-visitor-1",
        "traffic_source": "organic_search",
        "country": "US",
    },
    {
        "company_domain": "portal.contoso-health.com",
        "declare": False,
        "ip": "198.51.100.24",
        "path": "/demo-request",
        "days_ago": 1,
        "session": "contoso-b",
        "visitor": "contoso-visitor-2",
        "traffic_source": "email",
        "country": "GB",
    },
    # Northwind Traders: entered the view long before the automation was switched
    # on, so it is the researched note made into a row. It is in the view, it has
    # intent, and it is *not* auto-added.
    {
        "company_domain": "northwind.com",
        "declare": True,
        "ip": "203.0.113.11",
        "path": "/plans",
        "days_ago": 61,
        "session": "northwind-a",
        "visitor": "northwind-visitor-1",
        "traffic_source": "organic_search",
        "country": "AU",
    },
    {
        "company_domain": "careers.northwind.com",
        "declare": True,
        "ip": "203.0.113.12",
        "path": "/careers/engineering",
        "days_ago": 60,
        "session": "northwind-b",
        "visitor": "northwind-visitor-2",
        "traffic_source": "referral",
        "country": "AU",
    },
    {
        "company_domain": "northwind.com",
        "declare": True,
        "ip": "203.0.113.11",
        "path": "/pricing/enterprise",
        "days_ago": 4,
        "session": "northwind-c",
        "visitor": "northwind-visitor-1",
        "traffic_source": "direct",
        "country": "AU",
    },
    # Fabrikam Logistics: entered *after* the automation was switched on, so it is
    # added and tracked - and because both happen in one billing period, the
    # ledger charges 10 once and records 10 waived.
    {
        "company_domain": "fabrikam.io",
        "declare": True,
        "ip": "203.0.113.21",
        "path": "/contact-sales",
        "days_ago": 3,
        "session": "fabrikam-a",
        "visitor": "fabrikam-visitor-1",
        "traffic_source": "paid_search",
        "country": "DE",
    },
    {
        "company_domain": "www.fabrikam.io",
        "declare": True,
        "ip": "203.0.113.22",
        "path": "/plans",
        "days_ago": 2,
        "session": "fabrikam-b",
        "visitor": "fabrikam-visitor-2",
        "traffic_source": "paid_search",
        "country": "DE",
    },
    # Adventure Works: not in a target market, so the net-new categories skip it
    # even though it qualifies on pages. The negative case for "In my target
    # markets", which is otherwise untestable from a table that only has matches.
    {
        "company_domain": "adventure-works.example",
        "declare": True,
        "ip": "203.0.113.31",
        "path": "/pricing",
        "days_ago": 5,
        "session": "adventure-a",
        "visitor": "adventure-visitor-1",
        "traffic_source": "social",
        "country": "BR",
    },
    # Tailspin Toys: matched the SMB criterion, then stopped. Its derived property
    # has to be able to go back to false, so this company is what proves it.
    {
        "company_domain": "tailspintoys.example",
        "declare": True,
        "ip": "203.0.113.41",
        "path": "/smb/pricing",
        "days_ago": 40,
        "session": "tailspin-a",
        "visitor": "tailspin-visitor-1",
        "traffic_source": "organic_search",
        "country": "AU",
    },
    {
        "company_domain": "tailspintoys.example",
        "declare": True,
        "ip": "203.0.113.41",
        "path": "/about",
        "days_ago": 2,
        "session": "tailspin-b",
        "visitor": "tailspin-visitor-1",
        "traffic_source": "direct",
        "country": "AU",
    },
    # A visitor from an address with no company on file: real traffic, stored, and
    # counted as unattributed rather than dropped. Without this row the
    # "connects anonymous web visitors to known companies' IP addresses" step has
    # nothing to fail at.
    # The agency the exclusions list exists for: reads the careers page under the
    # seller's own root domain, so it has intent and is in no target market. It is
    # the one company the table does not show, and the reason why is on the
    # Configuration/Exclusions tab rather than being a mystery.
    {
        "company_domain": "talent-insight-partners.example",
        "declare": True,
        "ip": "203.0.113.51",
        "path": "/careers/sales",
        "days_ago": 3,
        "session": "talent-a",
        "visitor": "talent-visitor-1",
        "traffic_source": "referral",
        "country": "GB",
    },
    {
        "company_domain": "203.0.113.7",
        "declare": False,
        "ip": "203.0.113.7",
        "path": "/",
        "days_ago": 1,
        "session": "anon-a",
        "visitor": "anon-visitor-1",
        "traffic_source": "other",
        "country": None,
    },
)

#: The research observations: two topic matches and three news signals, covering
#: every news type except the two the demo would only repeat.
DEMO_RESEARCH: tuple[dict[str, Any], ...] = (
    {
        "kind": "topic",
        "company_domain": "litware.example",
        "topic": "evaluating cspm vendors for a 2027 review",
        "country": "GB",
        "industry": "software",
        "days_ago": 6,
    },
    {
        "kind": "topic",
        "company_domain": "proseware.example",
        "topic": "looking for a digital sales room to replace the current one",
        "country": "US",
        "industry": "software",
        "days_ago": 9,
    },
    {
        "kind": "topic",
        "company_domain": "wingtip.example",
        "topic": "revenue intelligence for a mid-market team",
        "country": "US",
        "industry": "financial services",
        "days_ago": 14,
    },
    {
        "kind": "news",
        "company_domain": "litware.example",
        "signal_type": "funding",
        "headline": "Litware raises a 40M Series C to expand its security business",
        "country": "GB",
        "industry": "software",
        "days_ago": 7,
    },
    {
        "kind": "news",
        "company_domain": "proseware.example",
        "signal_type": "executive_hire",
        "headline": "Proseware names a new Chief Revenue Officer",
        "country": "US",
        "industry": "software",
        "days_ago": 11,
    },
    {
        "kind": "news",
        "company_domain": "wingtip.example",
        "signal_type": "layoff",
        "headline": "Wingtip lays off 40 in a field-services reorganisation",
        "country": "US",
        "industry": "financial services",
        "days_ago": 3,
    },
    {
        "kind": "news",
        "company_domain": "vanarsdel.example",
        "signal_type": "product_launch",
        "headline": "Van Arsdel launches a hosted pricing service",
        "country": "NL",
        "industry": "logistics",
        "days_ago": 12,
    },
    {
        "kind": "news",
        "company_domain": "wideworld.example",
        "signal_type": "merger",
        "headline": "Wide World Importers to merge with its largest distributor",
        "country": "IE",
        "industry": "logistics",
        "days_ago": 20,
    },
)

#: Known contacts, for the Contacts drill-down tab. One contact per company at
#: most, with the three things the research says the tab shows: last touch, last
#: engagement, and a recently scheduled interaction.
DEMO_CONTACTS: tuple[dict[str, Any], ...] = (
    {
        "company_domain": "contoso-health.com",
        "email": "priya.raman@contoso-health.com",
        "contact_id": "ctc_contoso_priya",
        "name": "Priya Raman",
        "last_touch_days_ago": 2,
        "last_engagement_days_ago": 1,
        "scheduled": [
            {"kind": "meeting", "title": "Security review", "at_days_ago": -3},
            {"kind": "demo", "title": "Platform walkthrough", "at_days_ago": -6},
        ],
    },
    {
        "company_domain": "northwind.com",
        "email": "dana.kelly@northwind.com",
        "contact_id": "ctc_northwind_dana",
        "name": "Dana Kelly",
        "last_touch_days_ago": 5,
        "last_engagement_days_ago": 4,
        "scheduled": [{"kind": "meeting", "title": "Commercial review", "at_days_ago": -9}],
    },
    {
        "company_domain": "fabrikam.io",
        "email": "lukas.weber@fabrikam.io",
        "contact_id": "ctc_fabrikam_lukas",
        "name": "Lukas Weber",
        "last_touch_days_ago": 3,
        "last_engagement_days_ago": 2,
        "scheduled": [],
    },
)

#: The saved view the demo automates. Named after what it selects, because the
#: name is what the left panel shows and what the automation is described by.
DEMO_VIEW_NAME = "In-market: priced or asked for sales, last 90 days"
DEMO_VIEW_FILTERS: dict[str, Any] = {
    "days": 90,
    "visitor_intent": True,
    "traffic_sources": [],
    "countries": [],
    "page_filters": [],
    "in_target_markets": False,
    "sort": "last_visit",
    "direction": "desc",
}

#: The view the demo leaves *without* an automation, so the "0 automations" and
#: the "automation with both toggles" states are both visible.
DEMO_SECOND_VIEW_FILTERS: dict[str, Any] = {
    "days": 30,
    "visitor_intent": True,
    "in_target_markets": True,
    "sort": "page_views",
    "direction": "desc",
}

#: The excluded domain. Excluded because its traffic is a recruiting agency's own
#: staff reading the careers page under the seller's own root domain - the case
#: an exclusion list exists for, and the reason the "Careers browsing" criterion
#: is seeded with exactly one company.
DEMO_EXCLUSION = {
    "domain": "talent-insight-partners.example",
    "reason": "Agency staff browsing the careers page on their own clients' behalf.",
}

#: When the demo's tracking was switched on, as a whole number of days before the
#: seeder's clock. Older than every "entered" the demo produces, so the note holds
#: a company back; and newer than the oldest visit, so the note is not vacuous.
DEMO_AUTO_ADD_ENABLED_DAYS_AGO = 45


def _seed_now(context: dict[str, Any]) -> datetime:
    value = context.get("now")
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)


def _demo_company(store: RecordStore, key: str, *, at: str, actor: str, source: str) -> dict[str, Any]:
    """Create the one company record the demo seeds directly.

    Contoso Health is in the account before any intent was seen, which is the
    only way the "In-CRM with visitor intent" stock category and the "converted
    to lifecycle stage" Overview number have anything to count. Its
    ``record_source`` is a plain "Imported" rather than ``Buyer-Intent`` on
    purpose: a company that was not added by buyer intent must not carry the
    property that says it was.
    """
    return store.create(
        collection_names.COMPANIES,
        {
            "root_domain": key,
            "name": "Contoso Health",
            "record_source": "Imported",
            "added_at": at,
            "added_via": "import",
            "segment": "enterprise",
            "lifecycle_stage": "customer",
            "deal_stage": "closed_won",
            "owner": "sam",
            "industry": "healthcare",
            "country": "US",
            "known_ips": ["198.51.100.24", "198.51.100.25"],
            "derived_properties": {},
        },
        actor=actor,
        source=source,
    )


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """The buyer-intent table, its configuration, and the states that are not successes.

    The rows are produced by running the real :class:`MarketIntentEngine`, so the
    demo cannot show a shape this workflow would not produce, and seeding never
    opens a socket. It is deliberately mixed, because a demo of only green
    teaches a reviewer nothing. What is here on purpose:

    * **A company already in the view before auto-add was enabled** - Northwind
      Traders, qualifying since 61 days ago with the automation switched on 45
      days ago. It appears in the view with an ``entered_at`` older than the
      watermark and is *not* added. That is the researched note, as a row.
    * **A company that entered after the automation was enabled** - Fabrikam
      Logistics, which is added *and* tracked in one billing period, so the
      ledger charges 10 credits once and records 10 waived.
    * **A company that stopped qualifying** - Tailspin Toys matched the SMB
      criterion 40 days ago and has not since, so its ``showing_smb_intent``
      derived property is ``false``. A property that could only be set and never
      unset would be a claim on a CRM record that outlasts its evidence.
    * **A company outside every target market** - Adventure Works, which
      qualifies on pages and is skipped by the net-new categories.
    * **A company already in the CRM** - Contoso Health, with a plain
      ``record_source`` of "Imported" rather than ``Buyer-Intent``, so the two
      kinds of company record are distinguishable.
    * **An excluded domain** and **an unattributed visit**, so the two ways a
      company fails to appear are both rows.
    * **Research-only companies** - Litware, Proseware, Wingtip and the rest,
      which have no website visits at all, with all five news signal types
      represented across them.
    * **A second saved view with no automation**, so "a view exists" and "a view
      is automated" are different states.
    * **The 90-day boundary exercised from both sides** - the oldest demo visit
      is 61 days old, so it is inside the ceiling, and the view's time frame is
      set to exactly 90 days rather than to a rounder number.
    """
    store = RecordStore(db)
    rng: random.Random = context.get("rng") or random.Random("wf033")
    base = _seed_now(context)
    actor = "dana"
    source = "seed"

    engine = MarketIntentEngine(store, now=lambda: base.isoformat())

    # HubSpot Credits and the Data enrichment permission are what make the
    # segment filter, the exclusion list, and the auto-add possible. Enabled here
    # because a demo whose every control is refused is a demo nobody can review;
    # both default to *off*, and both are what a fresh portal gets.
    engine.update_settings(
        {"credits_enabled": True, "enrichment_actors": ["dana", "sam"]},
        actor="sam",
        source=source,
    )

    for market in DEMO_MARKETS:
        engine.add_market(market, actor=actor, source=source)
    for criterion in DEMO_CRITERIA:
        engine.add_criterion(criterion, actor=actor, source=source)
    for topic in DEMO_TOPICS:
        engine.add_topic(topic, actor=actor, source=source)
    engine.exclude(DEMO_EXCLUSION, actor=actor, source=source)

    def at(days_ago: float) -> str:
        return (base - timedelta(days=days_ago)).isoformat()

    # The one company already in the account, with the IPs its visitors arrive
    # from - which is what makes the anonymous -> known step work for it.
    _demo_company(store, "contoso-health.com", at=at(120), actor=actor, source=source)

    for contact in DEMO_CONTACTS:
        store.create(
            collection_names.CONTACTS,
            {
                "company_key": str(contact["company_domain"]).lower(),
                "email": contact["email"],
                "contact_id": contact["contact_id"],
                "name": contact["name"],
                "last_touch_at": at(contact["last_touch_days_ago"]),
                "last_engagement_at": at(contact["last_engagement_days_ago"]),
                "scheduled": [
                    {
                        "kind": entry["kind"],
                        "title": entry["title"],
                        "starts_at": at(-float(entry["at_days_ago"])),
                    }
                    for entry in contact["scheduled"]
                ],
            },
            actor=actor,
            source=source,
        )

    # Visits, in chronological order so the trailing-run derivation in
    # ``views.entered_at`` sees them the way it would in production.
    visits = sorted(DEMO_VISITS, key=lambda row: -float(row["days_ago"]))
    for visit in visits:
        engine.record_visit(
            {
                "url": f"https://{visit['company_domain']}{visit['path']}",
                "occurred_at": at(visit["days_ago"]),
                "session_id": visit["session"],
                "visitor_id": visit["visitor"],
                "ip": visit["ip"],
                "traffic_source": visit["traffic_source"],
                "country": visit["country"],
                # Only sent when the demo says so: a row that left it off is
                # attributed by the address alone, which is the route the
                # research describes for "known companies' IP addresses".
                **({"company_domain": visit["company_domain"]} if visit["declare"] else {}),
            },
            actor=actor,
            source=source,
        )

    for observation in sorted(DEMO_RESEARCH, key=lambda row: -float(row["days_ago"])):
        engine.record_research(
            {
                "kind": observation["kind"],
                "company_domain": observation["company_domain"],
                "occurred_at": at(observation["days_ago"]),
                **(
                    {"topic": observation["topic"]}
                    if observation["kind"] == "topic"
                    else {
                        "signal_type": observation["signal_type"],
                        "headline": observation["headline"],
                    }
                ),
                "country": observation["country"],
                "industry": observation["industry"],
            },
            actor=actor,
            source=source,
        )

    # The watermark, set *before* the automation exists so its stamp is the one
    # the researched note talks about: 45 days ago, which is after Northwind
    # entered the view and after Fabrikam did not.
    engine_now = MarketIntentEngine(store, now=lambda: at(DEMO_AUTO_ADD_ENABLED_DAYS_AGO))
    view = engine_now.save_view(
        {"name": DEMO_VIEW_NAME, "filters": DEMO_VIEW_FILTERS}, actor=actor, source=source
    )
    automation = engine_now.save_automation(
        {"view_id": view["id"], AUTOMATION_ADD: True, AUTOMATION_TRACK: True},
        actor=actor,
        source=source,
    )
    engine.save_view(
        {"name": "Target markets, intent, last 30 days", "filters": DEMO_SECOND_VIEW_FILTERS},
        actor=actor,
        source=source,
    )

    # Both stock categories are stamped at the same 45-day watermark as the view
    # automation, so the runs below have companies to act on rather than
    # reporting every one of them held back.
    #
    # The in-CRM category runs *first*, while Contoso Health is the only company
    # in the account. That is deliberate: it is the one that enriches rather than
    # adds, and it has something to change only before the view automation's
    # refresh has already written the derived properties.
    engine_now.set_category("in_crm_visitor_intent", {"enabled": True}, actor=actor, source=source)
    in_crm = engine.run_category("in_crm_visitor_intent", actor=actor, source=source)

    result = engine.run(automation["id"], actor=actor, source=source)

    # And then the net-new one, which is the only route into the CRM that has to
    # satisfy "in your target markets" - so Adventure Works, which qualifies on
    # pages and is in no market, is skipped by it and only it.
    engine_now.set_category("net_new_visitor_intent", {"enabled": True}, actor=actor, source=source)
    net_new = engine.run_category("net_new_visitor_intent", actor=actor, source=source)

    # The manual control, from the Visitors tab: one enrolment, and a second
    # attempt at the same one, so idempotency is a row rather than a claim.
    enrolled = engine.enroll(
        "northwind.com", {"workflow": "Enterprise nurture", "segment": "enterprise"}, actor=actor, source=source
    )
    repeat = engine.enroll(
        "northwind.com", {"workflow": "Enterprise nurture", "segment": "enterprise"}, actor=actor, source=source
    )

    # A monthly renewal one period later, for the company that is tracked, so
    # "tracking continues to be charged monthly" is a second ledger row rather
    # than a sentence.
    next_month = base.replace(day=1) + timedelta(days=32)
    renewer = MarketIntentEngine(store, now=lambda: next_month.isoformat())
    renewal = renewer.renew(actor=actor, source=source)

    held = len(result.get("held_back") or [])
    news_types = sorted(
        {
            str(row.get("signal_type"))
            for row in DEMO_RESEARCH
            if row.get("kind") == "news"
        }
    )
    return (
        f"{len(DEMO_MARKETS)} target markets, {len(DEMO_CRITERIA)} intent criteria "
        f"(3 deriving a custom property), {len(DEMO_TOPICS)} research topics, "
        f"1 excluded domain, "
        f"{len(DEMO_VISITS)} page views (1 still anonymous), "
        f"{len(DEMO_RESEARCH)} research observations ({len(news_types)} news signal types), "
        f"{len(DEMO_CONTACTS)} known contacts, "
        f"2 saved views, 1 automation (auto-add and tracking both on, enabled "
        f"{DEMO_AUTO_ADD_ENABLED_DAYS_AGO} days ago): "
        f"{result.get('matched')} companies in view, {len(result.get('added') or [])} added, "
        f"{len(result.get('tracked') or [])} tracked, {held} held back because they entered before "
        f"auto-add was enabled; "
        f"2 stock categories run: net-new-with-visitor-intent matched "
        f"{net_new.get('matched')} and added {len(net_new.get('added') or [])}, "
        f"in-CRM-with-visitor-intent matched {in_crm.get('matched')} and enriched "
        f"{len([e for e in (in_crm.get('enriched') or []) if e.get('outcome') == 'enriched'])}; "
        f"1 manual enrolment ({enrolled['outcome']}, repeat {repeat['outcome']}), "
        f"1 monthly renewal charging {renewal.get('charged_total')} credits"
    )
