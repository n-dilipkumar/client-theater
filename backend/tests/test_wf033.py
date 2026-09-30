"""Tests for WF-033: auto-add and continuously track in-market companies from
intent signals.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-033.md`` (section 18 of
``docs/research/raw/analytics-intent.md``). They are, in order:

* the hierarchical root-domain model: "activity from subdomains is rolled up into
  the root domain", and "truncates 'www' for display purposes";
* "Buyer intent connects anonymous web visitors to known companies' IP addresses"
  and "Companies currently in your account will appear with a HubSpot icon";
* the table's four columns - "website visits, unique visitors, last visit, and top
  page views" - and the card's "the count sessions of website visits from this
  company";
* the five path filters, verbatim: "Path is equal to / Path is not equal to /
  Path contains / Path does not contain / Path starts with", plus Domain;
* "You can only set timeframes within the last 90 days" and "This timeframe is
  based on midnight UTC", and that the time frame is last-visit based;
* "If you're reviewing companies that have shown intent, the specific domain and
  page path that qualified the company will be tagged with Intent";
* the three sort keys: "Page views, Unique visitors, or Last visit (asc/desc)";
* "Click Save view to persist the filter set as a named view";
* "auto-add will only add companies that enter your saved views after enabling
  the auto-add. It will not add all existing companies in your saved views." -
  the note the whole automation is built around, and the reason several tests
  below assert that something is *absent*;
* the four stock auto-add categories, each with the research's own description;
* "When adding a company to your CRM from buyer intent, the company will have a
  `Record source` property value of `Buyer-Intent`";
* "if you have SMB Intent as a criterion and a custom property called Showing SMB
  Intent, that property will automatically update to reflect whether an existing
  company meets or no longer meets the SMB Intent criteria";
* the billing rule: "Tracking a company costs 10 credits. However, if a company
  is added and tracked in the same billing period, you're only charged once for
  tracking (10 credits) - not for both actions separately. After that initial
  charge, tracking continues to be charged monthly";
* the two gates: "To access buyer intent features like filtering by segments and
  excluding companies, you need HubSpot Credits" and "To add and enrich companies
  from buyer intent, Super Admin must assign users with Data enrichment
  permissions";
* "lifecyclestage is forward-only", quoted off the CRM API the research cites.

The last one is not a buyer-intent sentence, which is why it gets a test of its
own: it is the only forward-only rule in this workflow with a primary source
behind it, and a company whose stage moved backwards would drop out of every view
that filters on it without saying why.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every write against the route table the host actually reported.
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import REGISTRY, load_feature
from dsr.market_intent import (
    AUTOMATION_ADD,
    AUTOMATION_TRACK,
    CATEGORY_IDS,
    MAX_DAYS,
    RECORD_SOURCE_BUYER_INTENT,
    AlreadyExcluded,
    CreditsRequired,
    DomainExcluded,
    DuplicateViewName,
    EnrichmentPermissionRequired,
    FilterSet,
    InvalidConfiguration,
    InvalidPathFilter,
    InvalidSort,
    InvalidTimeframe,
    LifecycleStageRegression,
    MarketIntentEngine,
    MarketIntentError,
    TimeframeTooLong,
    UnknownCategory,
    UnknownVocabularyValue,
    build_rows,
    charge,
    derived_properties,
    display_name,
    entered_at,
    is_valid_domain,
    matches_filters,
    midnight_utc,
    normalise_research,
    normalise_visit,
    parse_country,
    period_for,
    qualify_view,
    resolve,
    resolve_window,
    root_domain,
    same_company,
    sort_rows,
    summarise,
)
from dsr.market_intent import names as collection_names
from dsr.market_intent.criteria import PATH_OPERATOR_LABELS, PATH_OPERATORS, Criterion
from dsr.market_intent.domains import MULTI_LABEL_SUFFIXES
from dsr.market_intent.errors import InvalidObservation
from dsr.market_intent.inferences import INFERENCES, by_id
from dsr.market_intent.observations import contact_domain, is_forward_only, mark_topics
from dsr.market_intent.table import Snapshot, market_for, top_page_views
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-033"

MODULE = "wf033_auto_add_and_continuously_track_in_mar"
FEATURE_ID = "wf-033-auto-add-and-continuously-track-in-mar"

#: Scratch stays inside the worktree. The build brief is explicit that the system
#: temp folder is not to be used, because reading from outside the worktree
#: raises a permission dialog this session cannot answer. ``backend/data`` is
#: gitignored, so nothing written here can be committed by accident.
SCRATCH = Path(__file__).resolve().parents[1] / "data" / "wf033-test"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SOURCE = f"POST {PREFIX}/visits"


def ago(days: float = 0, hours: float = 0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


#: The audit source a helper records against. The pure-domain tests use the same
#: shape, so a test asserting on an audit row is asserting on the real thing.
TEST_SOURCE = "test:visits"


def _enable_category(
    engine: MarketIntentEngine, category_id: str, *, enabled_days_ago: float = 45
) -> dict[str, Any]:
    """Switch a stock category on at a chosen instant.

    A category enabled "now" holds back every company already in the table, which
    is the researched watermark behaving correctly. The tests below are about the
    predicate, so the switch-on moment is a parameter.
    """
    engine.clock["now"] = NOW - timedelta(days=enabled_days_ago)  # type: ignore[attr-defined]
    try:
        return engine.set_category(
            category_id, {"enabled": True}, actor="sam", source="test:categories"
        )
    finally:
        engine.clock["now"] = NOW  # type: ignore[attr-defined]


def _view_with_automation(
    engine: MarketIntentEngine,
    *,
    days: int = 90,
    add: bool = True,
    track: bool = False,
    enabled_days_ago: float = 45,
    name: str | None = None,
) -> dict[str, str]:
    """A saved view with its automation switched on at a chosen instant.

    The watermark is the whole point of the researched note, so the switch-on
    moment is a parameter rather than a side effect of "now".
    """
    view = engine.save_view(
        {"name": name or f"In market, last {days} days", "filters": {"days": days, "visitor_intent": True}},
        actor="sam",
        source="test:views",
    )
    engine.clock["now"] = NOW - timedelta(days=enabled_days_ago)  # type: ignore[attr-defined]
    try:
        automation = engine.save_automation(
            {"view_id": view["id"], AUTOMATION_ADD: add, AUTOMATION_TRACK: track},
            actor="sam",
            source="test:automations",
        )
    finally:
        engine.clock["now"] = NOW  # type: ignore[attr-defined]
    return {"view_id": view["id"], "automation_id": automation["id"]}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db_path() -> Iterator[Path]:
    """A fresh database per test, inside the worktree."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = SCRATCH / f"{uuid.uuid4().hex}.db"
    yield path
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


@pytest.fixture()
def db(db_path: Path) -> Iterator[AuditedDatabase]:
    database = AuditedDatabase(str(db_path), actor="test")
    yield database
    database.close()


@pytest.fixture()
def store(db: AuditedDatabase) -> RecordStore:
    return RecordStore(db)


@pytest.fixture()
def engine(store: RecordStore) -> MarketIntentEngine:
    """An engine on a clock the test can move.

    The researched note - "auto-add will only add companies that enter your
    saved views after enabling the auto-add" - is a comparison between two
    instants, so the clock has to be settable: with a frozen one there would be
    no way to write a company that entered *after* anything.
    """
    clock = {"now": NOW}

    def read() -> str:
        return clock["now"].isoformat()

    built = MarketIntentEngine(store, now=read)
    built.clock = clock  # type: ignore[attr-defined]
    return built


@pytest.fixture()
def configured(engine: MarketIntentEngine) -> MarketIntentEngine:
    """The researched prerequisites, so a test can be about one rule at a time.

    Target markets, intent criteria, a research topic, HubSpot Credits and the
    Data enrichment permission: the first four steps of the user flow, without
    which nothing in the workflow is reachable and every test would be testing the
    same refusal.
    """
    engine.update_settings(
        {"credits_enabled": True, "enrichment_actors": ["dana"]}, actor="sam", source="test:settings"
    )
    engine.add_market(
        {"name": "ANZ enterprise software", "countries": ["AU", "NZ"], "industries": ["software"]},
        actor="sam",
        source="test:markets",
    )
    engine.add_market(
        {"name": "DACH manufacturing", "countries": ["DE"], "industries": ["manufacturing"]},
        actor="sam",
        source="test:markets",
    )
    engine.add_criterion(
        {
            "name": "Priced up",
            "derived_property": "showing_priced_up",
            "page_filters": [{"operator": "starts_with", "path": "/pricing"}],
        },
        actor="sam",
        source="test:criteria",
    )
    engine.add_criterion(
        {
            "name": "SMB Intent",
            "derived_property": "showing_smb_intent",
            "page_filters": [{"operator": "contains", "path": "/smb"}],
        },
        actor="sam",
        source="test:criteria",
    )
    engine.add_topic(
        {"name": "Cloud security posture management", "terms": ["cspm"]},
        actor="sam",
        source="test:topics",
    )
    return engine


def visit_payload(**overrides: Any) -> dict[str, Any]:
    """A well-formed tracked page view, as the tracking code would send it."""
    payload: dict[str, Any] = {
        "url": "https://www.northwind.com/pricing/enterprise",
        "occurred_at": ago(days=1),
        "session_id": "sess-1",
        "visitor_id": "visitor-1",
        "traffic_source": "organic_search",
        "country": "AU",
        # The upstream IP-to-company match the research lists under data_sources.
        # Pass "" for a visit that has to stay anonymous.
        "company_domain": "northwind.com",
    }
    payload.update(overrides)
    return payload


def record_visit(
    engine: MarketIntentEngine,
    *,
    domain: str = "northwind.com",
    path: str = "/pricing",
    days_ago: float = 1,
    session: str = "sess-1",
    visitor: str = "visitor-1",
    country: str | None = "AU",
    traffic: str = "organic_search",
    declare: bool = True,
    ip: str | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    """One page view, with the clock moved to the moment it happened."""
    moment = ago(days=days_ago)
    engine.clock["now"] = NOW - timedelta(days=days_ago)  # type: ignore[attr-defined]
    try:
        return engine.record_visit(
            visit_payload(
                url=f"https://{domain}{path}",
                path=path,
                occurred_at=moment,
                session_id=session,
                visitor_id=visitor,
                traffic_source=traffic,
                country=country,
                ip=ip,
                known_contact=contact,
                **({"company_domain": domain} if declare else {}),
            ),
            actor="system",
            source=TEST_SOURCE,
        )
    finally:
        engine.clock["now"] = NOW  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# The root-domain model
# --------------------------------------------------------------------------- #


def test_subdomains_roll_up_into_the_root_domain():
    """"activity from subdomains is rolled up into the root domain"."""
    assert resolve("careers.northwind.com").root == "northwind.com"
    assert resolve("eu.shop.northwind.com").root == "northwind.com"


def test_www_is_truncated_for_display():
    """"truncates 'www' for display purposes"."""
    info = resolve("https://www.northwind.com/pricing?x=1")
    assert info.root == "northwind.com"
    assert info.display == "northwind.com"
    assert info.subdomains == ("www",)


def test_the_hierarchy_below_the_root_is_kept():
    """A hierarchical model keeps the levels, so a filter can name one."""
    info = resolve("eu.shop.northwind.com")
    assert info.subdomains == ("eu", "shop")
    assert info.host == "eu.shop.northwind.com"


def test_a_second_level_suffix_keeps_three_labels():
    assert resolve("shop.example.co.uk").root == "example.co.uk"
    assert root_domain("a.b.example.com.au") == "example.com.au"
    assert "co.uk" in MULTI_LABEL_SUFFIXES


def test_an_unknown_suffix_rolls_up_two_labels():
    assert resolve("www.example.zz").root == "example.zz"


def test_an_address_is_not_reduced_to_a_domain():
    """203.0.113.7 reduced naively becomes 113.7 and merges every visitor."""
    info = resolve("203.0.113.7")
    assert info.is_ip is True
    assert info.root == "203.0.113.7"
    assert info.subdomains == ()


def test_a_three_octet_address_is_not_treated_as_a_domain():
    assert resolve("1.2.3").is_ip is False
    assert resolve("1.2.3").root == "2.3"


def test_a_port_and_a_path_are_stripped_from_a_url():
    assert resolve("https://user:pw@www.northwind.com:8443/pricing?a=1#b").host == "www.northwind.com"


def test_a_trailing_dot_and_uppercase_are_normalised():
    assert resolve("WWW.Northwind.COM.").root == "northwind.com"


def test_every_leading_www_goes_with_the_reduction():
    """The model "truncates www for display purposes" needs no depth rule of its own,
    because www is a subdomain label and every one of them goes with the reduction.
    """
    assert resolve("www.www.northwind.com").root == "northwind.com"
    assert resolve("www.www.northwind.com").subdomains == ("www", "www")


def test_same_company_compares_roots():
    assert same_company("https://careers.northwind.com/x", "www.northwind.com") is True
    assert same_company("northwind.com", "southwind.com") is False


def test_same_company_does_not_match_on_nothing():
    assert same_company("", "northwind.com") is False


def test_is_valid_domain_rejects_an_address_and_a_bare_label():
    assert is_valid_domain("northwind.com") is True
    assert is_valid_domain("203.0.113.7") is False
    assert is_valid_domain("localhost") is False
    assert is_valid_domain("not a domain") is False


def test_a_company_with_no_name_gets_a_readable_placeholder():
    """The tracking code matched an address, not a company, so there is no name."""
    assert display_name("northwind.com") == "Northwind"
    assert display_name("adventure-works.example") == "Adventure Works"
    assert display_name("www.fabrikam.io") == "Fabrikam"
    assert display_name("") == ""


# --------------------------------------------------------------------------- #
# The time frame
# --------------------------------------------------------------------------- #


def test_ninety_days_is_allowed_and_ninety_one_is_not():
    """"You can only set timeframes within the last 90 days."""
    assert resolve_window(now=NOW, days=MAX_DAYS).requested_days == 90
    with pytest.raises(TimeframeTooLong):
        resolve_window(now=NOW, days=MAX_DAYS + 1)


def test_the_ninety_day_boundary_is_a_midnight_utc_line():
    """"This timeframe is based on midnight UTC"."""
    window = resolve_window(now=NOW, days=7)
    assert window.start == datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    assert window.start.hour == 0 and window.start.minute == 0


def test_ninety_days_always_fits_even_late_in_the_day():
    """The cap must not make the documented maximum unusable for most of a day."""
    late = NOW.replace(hour=23, minute=59)
    assert resolve_window(now=late, days=MAX_DAYS).start >= midnight_utc(late) - timedelta(days=MAX_DAYS)


def test_an_explicit_start_is_snapped_down_to_midnight_and_reports_the_move():
    window = resolve_window(now=NOW, start="2026-09-20T09:15:00+00:00")
    assert window.start == datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    assert window.snapped_from == "2026-09-20T09:15:00+00:00"


def test_an_explicit_start_inside_the_window_is_not_reported_as_snapped():
    window = resolve_window(now=NOW, start="2026-09-20T00:00:00+00:00")
    assert window.snapped_from is None


def test_an_explicit_start_older_than_ninety_days_is_refused():
    with pytest.raises(TimeframeTooLong):
        resolve_window(now=NOW, start="2026-01-01T00:00:00+00:00")


def test_a_naive_timestamp_is_refused_rather_than_assumed_utc():
    """"This timeframe is based on midnight UTC", so a naive time has no place on it."""
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, start="2026-09-20T00:00:00")


def test_a_window_that_closes_before_it_opens_is_refused():
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, start="2026-09-26T00:00:00+00:00", end="2026-09-20T00:00:00+00:00")


def test_a_zero_or_negative_window_is_refused():
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, days=0)
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, days=-3)


def test_a_fractional_or_non_numeric_window_is_refused():
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, days=2.5)
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, days="30")


def test_sending_both_days_and_a_start_is_refused_as_ambiguous():
    with pytest.raises(InvalidTimeframe):
        resolve_window(now=NOW, days=30, start="2026-09-20T00:00:00+00:00")


def test_both_endpoints_of_the_window_are_inclusive():
    window = resolve_window(now=NOW, days=1)
    assert window.contains(window.start) is True
    assert window.contains(window.end) is True
    assert window.contains(ago(days=2)) is False


def test_window_contains_ignores_a_naive_or_absent_timestamp():
    window = resolve_window(now=NOW, days=1)
    assert window.contains("2026-09-27T12:00:00") is False
    assert window.contains(None) is False


# --------------------------------------------------------------------------- #
# The path filter grammar
# --------------------------------------------------------------------------- #


def test_the_grammar_is_exactly_the_five_documented_operators():
    assert PATH_OPERATORS == ("eq", "neq", "contains", "not_contains", "starts_with")
    assert PATH_OPERATOR_LABELS["eq"] == "Path is equal to"
    assert PATH_OPERATOR_LABELS["starts_with"] == "Path starts with"


@pytest.mark.parametrize(
    ("operator", "path", "expected"),
    [
        ("eq", "/pricing", True),
        ("eq", "/pricing/enterprise", False),
        ("neq", "/pricing", False),
        ("neq", "/plans", True),
        ("contains", "/pricing", True),
        ("contains", "/plans", False),
        ("not_contains", "/pricing", False),
        ("not_contains", "/plans", True),
        ("starts_with", "/pricing", True),
        ("starts_with", "/plans", False),
    ],
)
def test_each_operator_behaves_as_its_label_says(operator, path, expected):
    filters = FilterSet.parse({"page_filters": [{"operator": operator, "path": "/pricing"}]})
    matched = filters.page_filters[0].matches({"path": path, "host": "northwind.com"})
    assert matched is expected


def test_a_path_filter_is_case_sensitive():
    """A filter that matched a differently-cased path would tag a page nobody saw."""
    filters = FilterSet.parse({"page_filters": [{"operator": "eq", "path": "/Pricing"}]})
    assert filters.page_filters[0].matches({"path": "/pricing"}) is False


def test_a_sixth_operator_is_refused():
    with pytest.raises(InvalidPathFilter):
        FilterSet.parse({"page_filters": [{"operator": "ends_with", "path": "/pricing"}]})


def test_a_path_filter_needs_an_absolute_path():
    with pytest.raises(InvalidPathFilter):
        FilterSet.parse({"page_filters": [{"operator": "eq", "path": "pricing"}]})


def test_a_domain_on_a_page_filter_compares_roots():
    """The roll-up is why a page filter can be expressed against a domain at all."""
    filters = FilterSet.parse(
        {"page_filters": [{"operator": "eq", "path": "/pricing", "domain": "northwind.com"}]}
    )
    assert filters.page_filters[0].matches({"path": "/pricing", "host": "careers.northwind.com"})
    assert not filters.page_filters[0].matches({"path": "/pricing", "host": "southwind.com"})


def test_a_page_filter_domain_must_be_a_registrable_domain():
    with pytest.raises(InvalidPathFilter):
        FilterSet.parse({"page_filters": [{"operator": "eq", "path": "/p", "domain": "203.0.113.7"}]})


def test_several_page_filters_in_one_criterion_are_ored():
    criterion = engine_criterion(
        "Asked for sales",
        [
            {"operator": "eq", "path": "/contact-sales"},
            {"operator": "eq", "path": "/demo-request"},
        ],
    )
    assert criterion.matches({"path": "/demo-request", "host": "n.com"}) is True
    assert criterion.matches({"path": "/about", "host": "n.com"}) is False


def test_a_criterion_with_no_page_is_refused(configured):
    """"intent criteria per page": an unfinished criterion, not a broad one."""
    with pytest.raises(InvalidConfiguration):
        configured.add_criterion({"name": "Anything"}, actor="sam", source="test:criteria")


def test_a_criterion_needs_a_name(configured):
    with pytest.raises(InvalidConfiguration):
        configured.add_criterion(
            {"name": "", "page_filters": [{"operator": "eq", "path": "/p"}]}, actor="s", source="t"
        )


# Small helpers so the two tests above can be read on their own.
def engine_criterion(name: str, filters: list[dict[str, Any]]) -> Criterion:
    return Criterion.parse({"name": name, "page_filters": filters}, record_id="c1")


# --------------------------------------------------------------------------- #
# Intent qualification and the Intent tag
# --------------------------------------------------------------------------- #


def test_a_qualifying_page_view_names_the_filter_and_the_path(configured):
    """The tag is the researched "specific domain and page path that qualified"."""
    result = configured.record_visit(visit_payload(), actor="system", source="test")
    assert result["company_key"] == "northwind.com"
    snapshot = configured._snapshot()
    tagged = qualify_view(snapshot.visits[0], snapshot.criteria)
    assert tagged is not None
    assert tagged["criterion"] == "Priced up"
    assert tagged["path"] == "/pricing"
    assert tagged["visited_path"] == "/pricing/enterprise"
    assert tagged["operator_label"] == "Path starts with"


def test_a_non_qualifying_page_view_tags_nothing(configured):
    configured.record_visit(visit_payload(url="https://northwind.com/about"), actor="system", source="t")
    snapshot = configured._snapshot()
    assert qualify_view(snapshot.visits[0], snapshot.criteria) is None


def test_a_criterion_added_today_tags_a_visit_from_last_week(configured):
    """The tag has to be computed, or yesterday's visits are invisible to it."""
    configured.record_visit(
        visit_payload(
            url="https://northwind.com/product/pricing",
            path="/product/pricing",
            occurred_at=ago(days=7),
        ),
        actor="system",
        source="t",
    )
    snapshot = configured._snapshot()
    assert qualify_view(snapshot.visits[0], snapshot.criteria) is None
    configured.add_criterion(
        {"name": "Product pricing", "page_filters": [{"operator": "starts_with", "path": "/product"}]},
        actor="s",
        source="t",
    )
    assert qualify_view(configured._snapshot().visits[0], configured.criteria()) is not None


def test_a_withdrawn_criterion_stops_tagging(configured):
    criterion_id = configured.add_criterion(
        {"name": "Product pricing", "page_filters": [{"operator": "starts_with", "path": "/product"}]},
        actor="s",
        source="t",
    )["id"]
    # /product/pricing, not /pricing-plans: the seeded "Priced up" criterion is
    # starts_with "/pricing" and /pricing-plans still matches it, so this visit
    # would stay tagged after the withdrawal - the opposite of the point.
    configured.record_visit(
        visit_payload(url="https://northwind.com/product/pricing", path="/product/pricing"),
        actor="system",
        source="t",
    )
    assert qualify_view(configured._snapshot().visits[0], configured.criteria()) is not None
    configured.withdraw_criterion(criterion_id, actor="s", source="t")
    assert qualify_view(configured._snapshot().visits[0], configured.criteria()) is None


def test_a_derived_property_can_go_back_to_false(configured):
    """"meets or no longer meets" - so a value that can only be set is wrong."""
    criterion = Criterion.parse(
        {
            "name": "SMB Intent",
            "derived_property": "showing_smb_intent",
            "page_filters": [{"operator": "contains", "path": "/smb"}],
        },
        record_id="c1",
    )
    smb = {"path": "/smb/pricing", "host": "n.com"}
    other = {"path": "/about", "host": "n.com"}
    assert derived_properties([smb], [criterion]) == {"showing_smb_intent": True}
    assert derived_properties([other], [criterion]) == {"showing_smb_intent": False}
    assert derived_properties([], [criterion]) == {"showing_smb_intent": False}


def test_a_criterion_with_no_derived_property_derives_nothing(configured):
    criterion = engine_criterion("Careers", [{"operator": "contains", "path": "/careers"}])
    assert derived_properties([{"path": "/careers", "host": "n.com"}], [criterion]) == {}


def test_a_criterion_scoped_to_a_site_ignores_another_site(configured):
    criterion = Criterion.parse(
        {
            "name": "Site-scoped",
            "site": "northwind.com",
            "page_filters": [{"operator": "eq", "path": "/pricing"}],
        },
        record_id="c1",
    )
    assert criterion.matches({"path": "/pricing", "host": "careers.northwind.com"}) is True
    assert criterion.matches({"path": "/pricing", "host": "other.com"}) is False


# --------------------------------------------------------------------------- #
# Observations: what the tracking code may send
# --------------------------------------------------------------------------- #


def test_a_page_view_needs_a_host():
    with pytest.raises(InvalidObservation):
        normalise_visit({"url": "", "occurred_at": ago(days=1), "session_id": "s", "visitor_id": "v"})


def test_a_page_view_needs_a_session_because_visits_are_counted_by_session():
    """"the count of sessions of website visits from this company"."""
    with pytest.raises(InvalidObservation):
        normalise_visit(visit_payload(session_id=""))


def test_a_page_view_needs_a_visitor_because_unique_visitors_are_counted():
    with pytest.raises(InvalidObservation):
        normalise_visit(visit_payload(visitor_id=""))


def test_a_page_view_needs_a_path_beginning_with_a_slash():
    with pytest.raises(InvalidObservation):
        normalise_visit({"host": "northwind.com", "occurred_at": ago(days=1), "session_id": "s", "visitor_id": "v"})


def test_a_naive_occurrence_time_is_refused():
    with pytest.raises(InvalidTimeframe):
        normalise_visit(visit_payload(occurred_at="2026-09-26T09:00:00"))


def test_an_unknown_traffic_source_is_refused():
    with pytest.raises(UnknownVocabularyValue):
        normalise_visit(visit_payload(traffic_source="carrier-pigeon"))


def test_a_country_must_be_two_letters():
    assert parse_country("au", where="country") == "AU"
    with pytest.raises(InvalidObservation):
        parse_country("Australia", where="country")


def test_a_path_is_derived_from_a_url_when_not_given():
    data = normalise_visit(visit_payload(path=None))
    assert data["path"] == "/pricing/enterprise"
    assert data["host"] == "www.northwind.com"
    assert data["root_domain"] == "northwind.com"


def test_a_page_view_is_stored_even_when_it_is_still_anonymous():
    """"Buyer intent connects anonymous web visitors" - one it cannot connect is traffic."""
    data = normalise_visit(visit_payload(company_domain=""))
    assert data["company_key"] is None
    assert data["attribution"] == "none"
    assert "Still anonymous" in __import__(
        "dsr.market_intent.observations", fromlist=["describe_attribution"]
    ).describe_attribution("none")


def test_a_news_signal_type_outside_the_researched_list_is_refused():
    """"funding, executive hires, layoffs, product launches, and mergers"."""
    with pytest.raises(UnknownVocabularyValue):
        normalise_research(
            {
                "kind": "news",
                "company_domain": "northwind.com",
                "signal_type": "acquisition",
                "headline": "Northwind buys a rival",
                "occurred_at": ago(days=1),
            }
        )


def test_every_researched_news_signal_type_is_accepted():
    for signal in ("funding", "executive_hire", "layoff", "product_launch", "merger"):
        data = normalise_research(
            {
                "kind": "news",
                "company_domain": "northwind.com",
                "signal_type": signal,
                "headline": "Something happened",
                "occurred_at": ago(days=1),
            }
        )
        assert data["signal_type"] == signal


def test_a_news_signal_needs_a_headline():
    with pytest.raises(InvalidObservation):
        normalise_research(
            {
                "kind": "news",
                "company_domain": "northwind.com",
                "signal_type": "funding",
                "occurred_at": ago(days=1),
            }
        )


def test_a_topic_observation_needs_a_topic():
    with pytest.raises(InvalidObservation):
        normalise_research(
            {"kind": "topic", "company_domain": "northwind.com", "occurred_at": ago(days=1)}
        )


def test_a_research_observation_needs_to_name_its_company():
    with pytest.raises(InvalidObservation):
        normalise_research({"kind": "topic", "topic": "cspm", "occurred_at": ago(days=1)})


def test_a_topic_observation_is_matched_against_a_configured_topic(configured):
    configured.record_research(
        {
            "kind": "topic",
            "company_domain": "litware.example",
            "topic": "evaluating cspm vendors",
            "occurred_at": ago(days=2),
        },
        actor="system",
        source="t",
    )
    stored = configured._snapshot().research[0]
    assert stored["topic_id"] is not None
    assert stored["topic_matched"] == "cspm"


def test_a_topic_with_no_terms_matches_its_own_name(configured):
    configured.add_topic({"name": "Digital sales room"}, actor="s", source="t")
    observation = {
        "kind": "topic",
        "topic": "looking for a digital sales room",
        "company_domain": "x.example",
    }
    mark_topics(observation, configured.topics())
    assert observation["topic_matched"] == "digital sales room"


def test_a_news_signal_is_not_matched_against_a_topic(configured):
    observation = {"kind": "news", "topic": "cspm", "company_domain": "x.example"}
    mark_topics(observation, configured.topics())
    assert observation.get("topic_id") is None


def test_contact_domain_reads_an_email_or_a_bare_domain():
    assert contact_domain("dana@northwind.com") == "northwind.com"
    assert contact_domain("careers.northwind.com") == "northwind.com"
    assert contact_domain("ctc_12345") == ""


# --------------------------------------------------------------------------- #
# Attribution: anonymous to known company
# --------------------------------------------------------------------------- #


def test_a_visit_is_matched_by_a_known_companys_ip_address(configured, store):
    """"Buyer intent connects anonymous web visitors to known companies' IP addresses"."""
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "known_ips": ["198.51.100.9"], "name": "Northwind"},
        actor="sam",
        source="seed",
    )
    result = configured.record_visit(visit_payload(ip="198.51.100.9", company_domain=""), actor="system", source="t")
    assert result["company_key"] == "northwind.com"
    assert result["attribution"] == "ip"
    assert result["known"] is True


def test_a_visit_is_matched_by_a_known_contact(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "known_ips": []},
        actor="sam",
        source="seed",
    )
    store.create(
        collection_names.CONTACTS,
        {"company_key": "northwind.com", "email": "dana@northwind.com", "contact_id": "ctc_1"},
        actor="sam",
        source="seed",
    )
    result = configured.record_visit(
        visit_payload(company_domain="", known_contact="dana@northwind.com"), actor="system", source="t"
    )
    assert result["company_key"] == "northwind.com"
    assert result["attribution"] == "contact"


def test_a_known_contacts_email_domain_matches_a_company_with_no_ip_on_file(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "known_ips": []},
        actor="sam",
        source="seed",
    )
    result = configured.record_visit(
        visit_payload(company_domain="", known_contact="dana@northwind.com"), actor="system", source="t"
    )
    assert result["company_key"] == "northwind.com"
    assert result["attribution"] == "email_domain"


def test_a_declared_company_is_attributed_to_the_upstream_ip_match(configured):
    """"company IP-to-company matching" - the pipeline's result, submitted with the visit."""
    result = configured.record_visit(
        visit_payload(company_domain="newco.example"), actor="system", source="t"
    )
    assert result["company_key"] == "newco.example"
    assert result["attribution"] == "ip_match"


def test_a_visit_from_a_known_address_beats_a_declared_company(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "known.example", "known_ips": ["198.51.100.9"]},
        actor="sam",
        source="seed",
    )
    result = configured.record_visit(
        visit_payload(ip="198.51.100.9", company_domain="claimed.example"), actor="system", source="t"
    )
    assert result["company_key"] == "known.example"


def test_an_unmatched_visit_is_stored_and_reported_as_anonymous(configured):
    result = configured.record_visit(
        visit_payload(company_domain="", ip="203.0.113.200"), actor="system", source="t"
    )
    assert result["company_key"] is None
    assert result["attribution"] == "none"
    assert configured.companies()["unattributed_views"] == 1


def test_a_company_in_the_account_is_badged_for_the_hubspot_icon(configured, store):
    """"Companies currently in your account will appear with a HubSpot icon"."""
    store.create(
        collection_names.COMPANIES, {"root_domain": "northwind.com"}, actor="sam", source="seed"
    )
    configured.record_visit(visit_payload(company_domain="northwind.com"), actor="system", source="t")
    row = configured.companies()["companies"][0]
    assert row["in_crm"] is True
    assert row["crm_icon"] == "hubspot"
    assert configured.card("northwind.com")["crm_icon"] == "hubspot"


# --------------------------------------------------------------------------- #
# The table and its four columns
# --------------------------------------------------------------------------- #


def test_website_visits_counts_sessions_and_page_views_counts_pages(configured):
    """Two rules from two sentences: "count of sessions" on the card, "page
    views" as a sort key."""
    record_visit(configured, path="/pricing", days_ago=3, session="s1", visitor="v1")
    record_visit(configured, path="/pricing/enterprise", days_ago=2, session="s1", visitor="v1")
    record_visit(configured, path="/smb", days_ago=1, session="s2", visitor="v2")
    row = configured.companies()["companies"][0]
    assert row["website_visits"] == 2
    assert row["page_views"] == 3
    assert row["unique_visitors"] == 2


def test_unique_visitors_counts_identities_not_visits(configured):
    for index, days in enumerate((3, 2, 1)):
        record_visit(configured, session=f"s{index}", visitor="same-person", days_ago=days)
    assert configured.companies()["companies"][0]["unique_visitors"] == 1


def test_last_visit_and_last_seen_are_the_newest_observation(configured):
    record_visit(configured, days_ago=5)
    record_visit(configured, days_ago=1, path="/smb")
    row = configured.companies()["companies"][0]
    assert row["last_visit_at"] == ago(days=1)
    assert row["first_visit_at"] == ago(days=5)
    assert row["last_seen_at"] == ago(days=1)


def test_a_research_only_company_has_a_last_seen_and_no_last_visit(configured):
    configured.record_research(
        {
            "kind": "news",
            "company_domain": "litware.example",
            "signal_type": "funding",
            "headline": "Litware raises",
            "occurred_at": ago(days=2),
        },
        actor="system",
        source="t",
    )
    row = configured.companies()["companies"][0]
    assert row["last_visit_at"] is None
    assert row["last_seen_at"] == ago(days=2)


def test_top_page_views_are_ordered_by_visits_then_recency(configured):
    record_visit(configured, path="/pricing", days_ago=1, session="a", visitor="a")
    record_visit(configured, path="/pricing", days_ago=2, session="b", visitor="b")
    record_visit(configured, path="/smb", days_ago=3, session="c", visitor="c")
    top = configured.companies()["companies"][0]["top_page_views"]
    assert [entry["path"] for entry in top] == ["/pricing", "/smb"]
    assert top[0]["visits"] == 2
    assert top[0]["unique_visitors"] == 2


def test_top_page_views_tie_breaks_on_recency_then_path(configured):
    """Two paths with one visit each must not reorder between two reads."""
    record_visit(configured, path="/zeta", days_ago=1, session="a", visitor="a")
    record_visit(configured, path="/alpha", days_ago=1, session="b", visitor="b")
    first = [entry["path"] for entry in configured.companies()["companies"][0]["top_page_views"]]
    second = [entry["path"] for entry in configured.companies()["companies"][0]["top_page_views"]]
    assert first == second == ["/alpha", "/zeta"]


def test_one_company_is_one_row_however_many_hosts_it_visited(configured):
    record_visit(configured, domain="northwind.com", days_ago=3)
    record_visit(configured, domain="careers.northwind.com", days_ago=1)
    record_visit(configured, domain="eu.shop.northwind.com", days_ago=2)
    result = configured.companies()
    assert result["count"] == 1
    assert result["companies"][0]["hosts"] == [
        "careers.northwind.com",
        "eu.shop.northwind.com",
        "northwind.com",
    ]


def test_an_excluded_domain_is_not_in_the_table_at_all(configured):
    configured.exclude({"domain": "northwind.com"}, actor="sam", source="t")
    record_visit(configured, days_ago=1)
    assert configured.companies()["count"] == 0


def test_the_table_reports_how_many_unattributed_views_it_holds(configured):
    configured.record_visit(visit_payload(company_domain=""), actor="system", source="t")
    assert configured.companies()["unattributed_views"] == 1


def test_a_company_with_no_name_gets_a_readable_placeholder_on_its_row(configured):
    """The tracking code matched an address, not a company, so there is no name."""
    record_visit(configured, days_ago=1)
    assert configured.companies()["companies"][0]["name"] == "Northwind"


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #


def _rows_for_sorting() -> list[dict[str, Any]]:
    return [
        {"company_key": "a.example", "page_views": 1, "unique_visitors": 5, "last_visit_at": "2026-01-01"},
        {"company_key": "b.example", "page_views": 5, "unique_visitors": 1, "last_visit_at": "2026-09-01"},
        {"company_key": "c.example", "page_views": 3, "unique_visitors": 3, "last_visit_at": None},
    ]


def test_page_views_sorts_by_the_page_view_count():
    assert [row["company_key"] for row in sort_rows(_rows_for_sorting(), key="page_views")] == [
        "b.example",
        "c.example",
        "a.example",
    ]


def test_unique_visitors_sorts_by_the_distinct_visitor_count():
    assert [row["company_key"] for row in sort_rows(_rows_for_sorting(), key="unique_visitors")] == [
        "a.example",
        "c.example",
        "b.example",
    ]


def test_last_visit_sorts_newest_first_and_puts_a_company_with_no_visit_last():
    assert [row["company_key"] for row in sort_rows(_rows_for_sorting(), key="last_visit")] == [
        "b.example",
        "a.example",
        "c.example",
    ]


def test_every_sort_key_works_in_both_directions():
    """"(asc/desc)" - both directions are researched."""
    for key, expected in (
        ("page_views", ["a.example", "c.example", "b.example"]),
        ("unique_visitors", ["b.example", "c.example", "a.example"]),
    ):
        assert [row["company_key"] for row in sort_rows(_rows_for_sorting(), key=key, direction="asc")] == expected
    assert [row["company_key"] for row in sort_rows(_rows_for_sorting(), key="last_visit", direction="asc")] == [
        "a.example",
        "b.example",
        "c.example",
    ]


def test_a_fourth_sort_key_is_refused():
    with pytest.raises(InvalidSort):
        FilterSet.parse({"sort": "pipeline_value"})


def test_a_bad_direction_is_refused():
    with pytest.raises(InvalidSort):
        FilterSet.parse({"direction": "sideways"})


# --------------------------------------------------------------------------- #
# The left panel's filters
# --------------------------------------------------------------------------- #


def test_showing_visitor_intent_off_is_unconstrained_rather_than_its_negation(configured):
    """It is a switch on a filter panel, not a predicate.

    Read as the negation, switching the filter *off* would return exactly the
    companies the seller was filtering out.
    """
    record_visit(configured, domain="northwind.com", path="/pricing", days_ago=2)
    record_visit(configured, domain="south.example", path="/about", days_ago=1, session="s2", visitor="v2")
    off = configured.companies(FilterSet.parse({"days": None}))
    on = configured.companies(FilterSet.parse({"days": None, "visitor_intent": True}))
    assert off["count"] == 2
    assert on["count"] == 1
    assert [row["company_key"] for row in on["companies"]] == ["northwind.com"]


def test_the_time_frame_filter_is_last_visit_based(configured):
    record_visit(configured, days_ago=2)
    assert configured.companies(FilterSet.parse({"days": 30}))["count"] == 1
    assert configured.companies(FilterSet.parse({"days": 1}))["count"] == 0


def test_a_company_with_no_website_visit_cannot_pass_a_view_with_a_time_frame(configured):
    """The direct consequence of the time frame being last-visit based."""
    configured.record_research(
        {
            "kind": "news",
            "company_domain": "litware.example",
            "signal_type": "funding",
            "headline": "Litware raises",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    assert configured.companies(FilterSet.parse({"days": 30}))["count"] == 0
    assert configured.companies(FilterSet.parse({"days": None}))["count"] == 1


def test_an_explicit_null_days_asks_for_no_time_frame(configured):
    filters = FilterSet.parse({"days": None})
    assert filters.timeframe is False
    assert filters.to_dict()["days"] is None


def test_in_target_markets_matches_a_country(configured):
    record_visit(configured, domain="northwind.com", country="AU", days_ago=1)
    assert configured.companies(FilterSet.parse({"in_target_markets": True}))["count"] == 1


def test_in_target_markets_can_be_false_for_a_company(configured):
    record_visit(configured, domain="adventure.example", country="BR", days_ago=1)
    result = configured.companies(FilterSet.parse({"in_target_markets": True}))
    assert result["count"] == 0
    assert result["total_before_filters"] == 1


def test_in_target_markets_matches_an_industry(configured):
    # No time frame: the time frame is last-visit based and this company has no
    # visit, so leaving the default in place would be testing the window.
    configured.record_research(
        {
            "kind": "topic",
            "company_domain": "wingtip.example",
            "topic": "cspm",
            "industry": "manufacturing",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    assert configured.companies(FilterSet.parse({"days": None, "in_target_markets": True}))["count"] == 1


def test_a_traffic_source_filter_narrows_the_table(configured):
    record_visit(configured, traffic="organic_search", days_ago=2)
    record_visit(configured, domain="south.example", traffic="email", days_ago=1)
    result = configured.companies(FilterSet.parse({"traffic_sources": ["email"]}))
    assert [row["company_key"] for row in result["companies"]] == ["south.example"]


def test_a_country_filter_narrows_the_table(configured):
    record_visit(configured, country="AU", days_ago=1)
    record_visit(configured, domain="south.example", country="DE", days_ago=1)
    result = configured.companies(FilterSet.parse({"countries": ["DE"]}))
    assert [row["company_key"] for row in result["companies"]] == ["south.example"]


def test_the_hubspot_crm_filters_read_the_company_record(configured, store):
    record_visit(configured, days_ago=1)
    record = store.create(
        collection_names.COMPANIES,
        {
            "root_domain": "northwind.com",
            "lifecycle_stage": "opportunity",
            "deal_stage": "negotiation",
            "owner": "dana",
            "segment": "enterprise",
        },
        actor="sam",
        source="seed",
    )
    assert configured.companies(FilterSet.parse({"lifecycle_stages": ["opportunity"]}))["count"] == 1
    assert configured.companies(FilterSet.parse({"deal_stages": ["negotiation"]}))["count"] == 1
    assert configured.companies(FilterSet.parse({"owners": ["dana"]}))["count"] == 1
    assert configured.companies(FilterSet.parse({"lifecycle_stages": ["customer"]}))["count"] == 0
    assert record["id"]


def test_a_page_filter_narrows_the_table_to_the_qualifying_companies(configured):
    record_visit(configured, path="/pricing", days_ago=2)
    record_visit(configured, domain="south.example", path="/about", days_ago=1)
    filters = FilterSet.parse({"page_filters": [{"operator": "starts_with", "path": "/pricing"}]})
    assert [row["company_key"] for row in configured.companies(filters)["companies"]] == ["northwind.com"]


# --------------------------------------------------------------------------- #
# The credit gate
# --------------------------------------------------------------------------- #


def test_the_exclusion_list_needs_hubspot_credits(engine):
    """"To access buyer intent features like ... excluding companies, you need
    HubSpot Credits"."""
    with pytest.raises(CreditsRequired) as caught:
        engine.exclusions()
    assert caught.value.status == 402
    assert caught.value.capability == "exclusions"


def test_the_segment_filter_needs_hubspot_credits(engine):
    record_visit(engine, days_ago=1)
    with pytest.raises(CreditsRequired) as caught:
        engine.companies(FilterSet.parse({"segment": "enterprise"}))
    assert caught.value.capability == "segment_filter"


def test_both_gated_features_open_once_credits_are_on(configured):
    assert configured.companies(FilterSet.parse({"segment": "enterprise"}))["count"] == 0
    assert configured.exclusions() == []


def test_capabilities_publishes_both_gates(engine):
    granted = engine.capabilities("dana")
    assert granted["credits_enabled"] is False
    assert granted["can_manage_exclusions"] is False
    assert granted["enrichment_granted"] is False
    assert set(granted["credit_gated_capabilities"]) == {"segment_filter", "exclusions"}
    assert set(granted["enrichment_gated_operations"]) == {"auto_add", "auto_track", "manual_enrol"}


def test_capabilities_reflects_a_granted_actor(configured):
    granted = configured.capabilities("dana")
    assert granted["enrichment_granted"] is True
    assert granted["can_add_companies"] is True
    assert configured.capabilities("stranger")["can_add_companies"] is False


def test_switching_credits_off_closes_the_gate_again(configured):
    configured.update_settings({"credits_enabled": False}, actor="sam", source="t")
    with pytest.raises(CreditsRequired):
        configured.exclusions()


def test_the_settings_patch_validates_its_own_input(engine):
    with pytest.raises(InvalidConfiguration):
        engine.update_settings({"credits_enabled": "yes"}, actor="sam", source="t")
    with pytest.raises(InvalidConfiguration):
        engine.update_settings({"enrichment_actors": "dana"}, actor="sam", source="t")


# --------------------------------------------------------------------------- #
# Exclusions
# --------------------------------------------------------------------------- #


def test_an_exclusion_rolls_up_to_the_root_domain(configured):
    """Otherwise it stops excluding anything the moment another host is used."""
    result = configured.exclude({"domain": "www.south.example"}, actor="sam", source="t")
    assert result["root_domain"] == "south.example"
    assert configured.exclusions()[0]["domain"] == "south.example"


def test_excluding_the_same_domain_twice_is_refused(configured):
    configured.exclude({"domain": "south.example"}, actor="sam", source="t")
    with pytest.raises(AlreadyExcluded):
        configured.exclude({"domain": "careers.south.example"}, actor="sam", source="t")


def test_an_exclusion_must_be_a_registrable_domain(configured):
    with pytest.raises(InvalidConfiguration):
        configured.exclude({"domain": "203.0.113.7"}, actor="sam", source="t")


def test_an_exclusion_can_be_lifted_and_the_company_returns(configured):
    record_visit(configured, days_ago=1)
    record_visit(configured, domain="south.example", days_ago=1, session="s2", visitor="v2")
    configured.exclude({"domain": "south.example"}, actor="sam", source="t")
    assert configured.companies()["count"] == 1  # northwind is still there
    assert configured.unexclude("south.example", actor="sam", source="t")["removed"] is True
    assert configured.companies()["count"] == 2


def test_lifting_an_exclusion_that_does_not_exist_says_so(configured):
    assert configured.unexclude("nope.example", actor="sam", source="t")["removed"] is False


def test_an_excluded_company_cannot_be_enrolled(configured):
    record_visit(configured, days_ago=1)
    configured.exclude({"domain": "northwind.com"}, actor="sam", source="t")
    with pytest.raises(DomainExcluded):
        configured.enroll("northwind.com", {"workflow": "Nurture"}, actor="dana", source="t")


# --------------------------------------------------------------------------- #
# The enrichment gate
# --------------------------------------------------------------------------- #


def test_auto_add_needs_the_data_enrichment_permission(configured):
    """Super Admin must assign users with Data enrichment permissions."""
    row = configured.companies()["companies"][0] if configured.companies()["companies"] else None
    record_visit(configured, days_ago=1)
    row = configured.companies()["companies"][0]
    with pytest.raises(EnrichmentPermissionRequired) as caught:
        configured._add_company(
            row,
            configured._snapshot(),
            via="test",
            actor="stranger",
            source="t",
            at=NOW.isoformat(),
        )
    assert caught.value.status == 403
    assert caught.value.actor == "stranger"


def test_manual_enrolment_needs_the_permission_too(configured):
    record_visit(configured, days_ago=1)
    with pytest.raises(EnrichmentPermissionRequired):
        configured.enroll("northwind.com", {"workflow": "Nurture"}, actor="stranger", source="t")


def test_enrichment_needs_the_permission_too(configured, store):
    record_visit(configured, days_ago=1)
    store.create(
        collection_names.COMPANIES, {"root_domain": "northwind.com"}, actor="sam", source="seed"
    )
    row = configured.companies()["companies"][0]
    with pytest.raises(EnrichmentPermissionRequired):
        configured._enrich_company(
            row,
            configured._snapshot(),
            via="test",
            actor="stranger",
            source="t",
            at=NOW.isoformat(),
        )


def test_an_unknown_actor_is_refused_rather_than_assumed_permitted(configured):
    record_visit(configured, days_ago=1)
    row = configured.companies()["companies"][0]
    with pytest.raises(EnrichmentPermissionRequired):
        configured._add_company(
            row, configured._snapshot(), via="t", actor=None, source="t", at=NOW.isoformat()
        )


# --------------------------------------------------------------------------- #
# Saved views
# --------------------------------------------------------------------------- #


def test_saving_a_view_persists_its_filter_set(configured):
    filters = {
        "days": 45,
        "visitor_intent": True,
        "traffic_sources": ["direct"],
        "countries": ["AU"],
        "page_filters": [{"operator": "starts_with", "path": "/pricing"}],
        "in_target_markets": True,
        "sort": "unique_visitors",
        "direction": "asc",
    }
    view = configured.save_view({"name": "In market", "filters": filters}, actor="sam", source="t")
    stored = configured.view(view["id"])
    assert stored["name"] == "In market"
    assert stored["filters"]["days"] == 45
    assert stored["filters"]["traffic_sources"] == ["direct"]
    assert stored["filters"]["sort"] == "unique_visitors"


def test_a_view_needs_a_name(configured):
    with pytest.raises(InvalidConfiguration):
        configured.save_view({"name": "", "filters": {}}, actor="sam", source="t")


def test_two_views_cannot_share_a_name(configured):
    """An automation is attached to a view, so load order must not decide."""
    configured.save_view({"name": "In market", "filters": {}}, actor="sam", source="t")
    with pytest.raises(DuplicateViewName) as caught:
        configured.save_view({"name": "In market", "filters": {"days": 7}}, actor="sam", source="t")
    assert caught.value.status == 409


def test_removing_a_view_removes_its_automation_too(configured):
    view = configured.save_view({"name": "In market", "filters": {}}, actor="sam", source="t")
    automation = configured.save_automation(
        {"view_id": view["id"], AUTOMATION_ADD: True}, actor="sam", source="t"
    )
    result = configured.withdraw_view(view["id"], actor="sam", source="t")
    assert result["automations_removed"] == [automation["id"]]
    assert configured.automation_for(view["id"]) is None


def test_removing_a_view_that_does_not_exist_says_so(configured):
    assert configured.withdraw_view("nope", actor="sam", source="t")["removed"] is False


def test_a_view_companies_response_says_whether_each_entered_after_the_switch(configured):
    view = _view_with_automation(configured, days=90)
    result = configured.view_companies(view["view_id"])
    assert result["found"] is True
    assert "companies" in result


# --------------------------------------------------------------------------- #
# The researched note: only companies that enter after enabling
# --------------------------------------------------------------------------- #


def test_a_company_already_in_the_view_is_not_auto_added(configured):
    """"It will not add all existing companies in your saved views"."""
    record_visit(configured, days_ago=60)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["added"] == []
    assert result["held_back"][0]["company_key"] == "northwind.com"
    assert result["held_back"][0]["reason"] == "entered_before_auto_add_was_enabled"
    assert configured.company("northwind.com")["in_crm"] is False


def test_a_company_that_enters_after_the_switch_is_auto_added(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert [entry["company_key"] for entry in result["added"]] == ["northwind.com"]
    assert result["added"][0]["record_source"] == RECORD_SOURCE_BUYER_INTENT
    assert configured.company("northwind.com")["in_crm"] is True


def test_a_company_that_entered_before_and_revisited_after_the_switch_is_not_added(configured):
    """It never left the view, so it never entered again."""
    record_visit(configured, days_ago=60)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    record_visit(configured, days_ago=1, session="s2", visitor="v2")
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["added"] == []
    assert result["held_back"][0]["entered_at"] == ago(days=60)


def test_a_company_that_left_the_view_and_came_back_is_added_again(configured):
    """Re-entry is a fresh entry, and the derivation has to see it."""
    record_visit(configured, path="/pricing", days_ago=40)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    record_visit(configured, path="/about", days_ago=20, session="s2", visitor="v2")
    assert configured.view_companies(view["view_id"])["count"] == 0
    record_visit(configured, path="/pricing", days_ago=1, session="s3", visitor="v3")
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert [entry["company_key"] for entry in result["added"]] == ["northwind.com"]
    assert result["added"][0]["outcome"] == "added"


def test_the_entry_time_is_the_oldest_of_the_trailing_run(configured):
    for index, days in enumerate((40, 30, 20, 10)):
        record_visit(configured, days_ago=days, session=f"s{index}", visitor=f"v{index}")
    result = configured.view_companies(_view_with_automation(configured, days=90)["view_id"])
    assert result["companies"][0]["entered_at"] == ago(days=40)


def test_the_watermark_is_strict(configured):
    """A company qualifying at the switch-on instant is one of the existing ones."""
    record_visit(configured, days_ago=45)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["added"] == []
    assert result["held_back"][0]["enabled_at"] == ago(days=45)


def test_saving_an_enabled_automation_again_does_not_move_the_watermark(configured):
    """A moving watermark would mean a company that entered last week never lands."""
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    first = configured.automation_for(view["view_id"])["add_enabled_at"]
    configured.clock["now"] = NOW - timedelta(days=20)  # type: ignore[attr-defined]
    try:
        again = configured.save_automation(
            {"view_id": view["view_id"], AUTOMATION_ADD: True}, actor="sam", source="t"
        )
    finally:
        configured.clock["now"] = NOW  # type: ignore[attr-defined]
    assert again["add_enabled_at"] == first


def test_an_automation_with_both_toggles_off_adds_nothing(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    configured.save_automation(
        {"view_id": view["view_id"], AUTOMATION_ADD: False, AUTOMATION_TRACK: False},
        actor="sam",
        source="t",
    )
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["added"] == []


def test_an_automation_for_a_view_that_does_not_exist_is_refused(configured):
    from dsr.market_intent.errors import InvalidAutomation

    with pytest.raises(InvalidAutomation):
        configured.save_automation({"view_id": "nope", AUTOMATION_ADD: True}, actor="sam", source="t")
    with pytest.raises(InvalidAutomation):
        configured.save_automation({AUTOMATION_ADD: True}, actor="sam", source="t")


def test_running_an_automation_that_does_not_exist_says_so(configured):
    assert configured.run("nope", actor="dana", source="t")["found"] is False


def test_an_excluded_company_in_the_view_is_held_back_with_its_reason(configured):
    record_visit(configured, days_ago=1)
    configured.exclude({"domain": "northwind.com"}, actor="sam", source="t")
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["held_back"][0]["reason"] == "domain_excluded"
    assert configured.view_companies(view["view_id"])["count"] == 0


def test_a_view_with_a_page_filter_cannot_see_a_research_only_company(configured):
    configured.record_research(
        {
            "kind": "news",
            "company_domain": "litware.example",
            "signal_type": "funding",
            "headline": "Litware raises",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    view = configured.save_view(
        {
            "name": "Site pages",
            "filters": {"days": None, "page_filters": [{"operator": "eq", "path": "/pricing"}]},
        },
        actor="sam",
        source="t",
    )
    assert configured.view_companies(view["id"])["count"] == 0


# --------------------------------------------------------------------------- #
# Record source, the credit rule, and the monthly renewal
# --------------------------------------------------------------------------- #


def test_an_added_company_carries_the_buyer_intent_record_source(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    record = configured.store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=1)[0]
    assert record["data"]["record_source"] == RECORD_SOURCE_BUYER_INTENT
    assert RECORD_SOURCE_BUYER_INTENT == "Buyer-Intent"


def test_a_company_already_in_the_account_does_not_carry_the_buyer_intent_record_source(
    configured, store
):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "record_source": "Imported"},
        actor="sam",
        source="seed",
    )
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=True, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    record = store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=1)[0]
    assert record["data"]["record_source"] == "Imported"


def test_adding_and_tracking_in_one_period_is_charged_once(configured):
    """"you're only charged once for tracking (10 credits) - not for both actions
    separately"."""
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=True, enabled_days_ago=45)
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["added"][0]["credit"]["charged"] == 10
    assert result["added"][0]["credit"]["outcome"] == "charged"
    assert result["tracked"][0]["credit"]["charged"] == 0
    assert result["tracked"][0]["credit"]["waived"] == 10
    ledger = configured.credits()
    assert ledger["total_charged"] == 10
    assert ledger["total_waived"] == 10
    entry = ledger["entries"][0]
    assert entry["actions"] == ["add", "track"]
    assert entry["amount"] == 10
    assert entry["waived"] == 10


def test_adding_in_one_period_and_tracking_in_the_next_is_charged_twice(configured, store):
    first = charge(store, company_key="n.example", action="add", at=ago(days=40), actor="dana", source="t")
    second = charge(store, company_key="n.example", action="track", at=ago(days=1), actor="dana", source="t")
    assert first["charged"] == 10
    assert second["charged"] == 10
    assert summarise([entry["row"]["data"] for entry in (first, second)])["total_charged"] == 20


def test_one_ledger_row_per_company_per_period(configured, store):
    charge(store, company_key="n.example", action="add", at=ago(days=2), actor="dana", source="t")
    charge(store, company_key="n.example", action="track", at=ago(days=1), actor="dana", source="t")
    charge(store, company_key="n.example", action="track", at=ago(days=0), actor="dana", source="t")
    rows = store.list(collection_names.CREDITS, limit=50)
    assert len(rows) == 1
    assert rows[0]["data"]["actions"] == ["add", "track"]
    assert rows[0]["data"]["waived"] == 10


def test_repeating_an_action_in_a_period_does_not_inflate_the_saving(configured, store):
    charge(store, company_key="n.example", action="add", at=ago(days=2), actor="dana", source="t")
    repeat = charge(store, company_key="n.example", action="add", at=ago(days=1), actor="dana", source="t")
    assert repeat["charged"] == 0
    assert repeat["waived"] == 0
    assert repeat["row"]["data"]["waived"] == 0


def test_two_companies_in_one_period_are_charged_separately(configured, store):
    charge(store, company_key="a.example", action="add", at=ago(days=2), actor="dana", source="t")
    charge(store, company_key="b.example", action="add", at=ago(days=1), actor="dana", source="t")
    assert len(store.list(collection_names.CREDITS, limit=50)) == 2


def test_the_billing_period_is_a_utc_calendar_month():
    assert period_for("2026-09-30T23:59:59+00:00") == "2026-09"
    assert period_for("2026-09-01T00:00:00+00:00") == "2026-09"
    assert period_for("2026-10-01T00:00:00+00:00") == "2026-10"
    assert period_for("2026-10-01T00:00:00+10:00") == "2026-09"


def test_tracking_continues_to_be_charged_monthly(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=False, track=True, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    assert len(configured.store.list(collection_names.TRACKING, limit=50)) == 1
    next_month = NOW.replace(day=1) + timedelta(days=32)
    configured.clock["now"] = next_month  # type: ignore[attr-defined]
    try:
        renewal = configured.renew(actor="dana", source="t")
    finally:
        configured.clock["now"] = NOW  # type: ignore[attr-defined]
    assert renewal["charged_total"] == 10
    assert renewal["period"] != "2026-09"
    assert configured.credits()["total_charged"] == 20


def test_a_second_renewal_in_the_same_period_charges_nothing(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=False, track=True, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    first = configured.renew(actor="dana", source="t")
    second = configured.renew(actor="dana", source="t")
    assert first["charged_total"] == 0
    assert second["charged_total"] == 0
    assert configured.credits()["total_charged"] == 10


def test_tracking_a_company_twice_is_a_no_op(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=False, track=True, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    again = configured._track_company(
        configured.companies()["companies"][0],
        via="test",
        actor="dana",
        source="t",
        at=NOW.isoformat(),
    )
    assert again["outcome"] == "already_tracked"
    assert len(configured.store.list(collection_names.TRACKING, limit=50)) == 1


def test_adding_a_company_twice_is_reported_rather_than_duplicated(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["added"] == []
    assert len(configured.store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=5)) == 1


def test_an_added_company_records_the_documented_crm_plan_rather_than_calling_it(configured):
    record_visit(configured, days_ago=1)
    view = _view_with_automation(configured, days=90, add=True, track=False, enabled_days_ago=45)
    configured.run(view["automation_id"], actor="dana", source="t")
    record = configured.store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=1)[0]
    plan = record["data"]["crm_plan"]
    assert plan["executed"] is False
    assert plan["record_source"]["value"] == RECORD_SOURCE_BUYER_INTENT
    assert any("batch/upsert" in call["path"] for call in plan["cited_primitives"])
    assert "crm.objects.contacts.write" in plan["scopes"]


# --------------------------------------------------------------------------- #
# The four stock auto-add categories
# --------------------------------------------------------------------------- #


def test_there_are_exactly_four_and_they_are_served_with_their_definitions(configured):
    categories = configured.categories()
    assert [row["id"] for row in categories] == list(CATEGORY_IDS)
    assert len(categories) == 4
    assert all(row["description"] for row in categories)


def test_an_unknown_category_is_refused(configured):
    with pytest.raises(UnknownCategory):
        configured.set_category("net_new_with_vibes", {"enabled": True}, actor="sam", source="t")
    with pytest.raises(UnknownCategory):
        configured.run_category("net_new_with_vibes", actor="dana", source="t")


def test_a_disabled_category_run_does_nothing_and_says_so(configured):
    record_visit(configured, days_ago=1)
    result = configured.run_category("net_new_visitor_intent", actor="dana", source="t")
    assert result["enabled"] is False
    assert result["added"] == []


def test_net_new_with_visitor_intent_requires_a_target_market_and_no_crm_record(configured):
    record_visit(configured, domain="in.example", country="AU", days_ago=1)
    record_visit(configured, domain="out.example", country="BR", days_ago=1, session="s2", visitor="v2")
    _enable_category(configured, "net_new_visitor_intent")
    result = configured.run_category("net_new_visitor_intent", actor="dana", source="t")
    # The company outside every target market is not even a match: "companies
    # that are in your target markets and visiting high-intent pages".
    assert result["matched_companies"] == ["in.example"]
    assert [entry["company_key"] for entry in result["added"]] == ["in.example"]


def test_in_crm_with_visitor_intent_enriches_rather_than_adding_a_second_record(
    configured, store
):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "derived_properties": {}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, days_ago=1)
    _enable_category(configured, "in_crm_visitor_intent")
    result = configured.run_category("in_crm_visitor_intent", actor="dana", source="t")
    assert result["added"] == []
    assert result["enriched"][0]["outcome"] == "enriched"
    assert result["enriched"][0]["changed"] == ["derived_properties"]
    assert len(store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=5)) == 1


def test_an_enrichment_that_changes_nothing_says_unchanged(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "derived_properties": {}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, days_ago=1)
    _enable_category(configured, "in_crm_visitor_intent")
    configured.run_category("in_crm_visitor_intent", actor="dana", source="t")
    again = configured.run_category("in_crm_visitor_intent", actor="dana", source="t")
    assert again["enriched"][0]["outcome"] == "unchanged"


def test_net_new_with_research_intent_matches_a_topic_searcher(configured):
    configured.record_research(
        {
            "kind": "topic",
            "company_domain": "wingtip.example",
            "topic": "cspm shortlist",
            "country": "AU",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    _enable_category(configured, "net_new_research_intent")
    result = configured.run_category("net_new_research_intent", actor="dana", source="t")
    assert result["matched_companies"] == ["wingtip.example"]
    assert [entry["company_key"] for entry in result["added"]] == ["wingtip.example"]


def test_net_new_with_both_intents_needs_both_signals(configured):
    record_visit(configured, domain="both.example", country="AU", days_ago=1)
    record_visit(configured, domain="visitoronly.example", country="AU", days_ago=1, session="s2", visitor="v2")
    configured.record_research(
        {
            "kind": "topic",
            "company_domain": "both.example",
            "topic": "cspm",
            "country": "AU",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    _enable_category(configured, "net_new_both_intents")
    result = configured.run_category("net_new_both_intents", actor="dana", source="t")
    assert result["matched_companies"] == ["both.example"]


def test_a_category_holds_back_a_company_last_seen_before_it_was_enabled(configured):
    # Last seen 60 days ago, category switched on 45 days ago: the company is in
    # the category's predicate and arrived before the switch, so it is held back.
    record_visit(configured, days_ago=60)
    _enable_category(configured, "net_new_visitor_intent", enabled_days_ago=45)
    result = configured.run_category("net_new_visitor_intent", actor="dana", source="t")
    assert result["held_back"][0]["reason"] == "last_seen_before_the_category_was_enabled"


def test_a_category_cannot_redefine_its_own_predicate(configured):
    """"stock" means the definition is fixed, not only the label."""
    with pytest.raises(InvalidConfiguration) as caught:
        configured.set_category("net_new_visitor_intent", {"enabled": True, "requires_in_crm": True}, actor="s", source="t"
        )
    assert "requires_in_crm" in str(caught.value)


def test_every_category_predicate_matches_its_own_wording(configured):
    from dsr.market_intent.vocabulary import CATEGORY_REQUIREMENTS

    in_market_visitor = {
        "in_target_markets": ["m1"],
        "visitor_intent": True,
        "research_intent": False,
        "in_crm": False,
    }
    in_crm_visitor = dict(in_market_visitor, in_target_markets=[], in_crm=True)
    assert MarketIntentEngine.category_matches(in_market_visitor, CATEGORY_REQUIREMENTS["net_new_visitor_intent"])
    assert not MarketIntentEngine.category_matches(in_crm_visitor, CATEGORY_REQUIREMENTS["net_new_visitor_intent"])
    # The research's wording for the in-CRM one omits the target market, and adding
    # the requirement to it would stop tracking every existing customer who visits.
    assert MarketIntentEngine.category_matches(in_crm_visitor, CATEGORY_REQUIREMENTS["in_crm_visitor_intent"])


# --------------------------------------------------------------------------- #
# The Buyer Intent card and the drill-downs
# --------------------------------------------------------------------------- #


def test_the_card_carries_exactly_the_four_researched_fields(configured):
    record_visit(configured, path="/pricing", days_ago=1)
    record_visit(configured, path="/smb", days_ago=2, session="s2", visitor="v2")
    card = configured.card("northwind.com")
    assert card["found"] is True
    assert set(card["fields"]) == {"website_visits", "unique_visitors", "last_seen", "top_page_views"}
    assert card["fields"]["website_visits"]["value"] == 2
    assert card["fields"]["unique_visitors"]["value"] == 2
    assert card["fields"]["last_seen"]["value"] == ago(days=1)
    assert card["full_activity"]["tab"] == "page-views"


def test_the_card_of_a_company_the_table_has_never_seen_says_so(configured):
    assert configured.card("nope.example")["found"] is False


def test_recent_page_views_carry_the_ip_derived_country_and_the_intent_tag(configured):
    record_visit(configured, path="/pricing", days_ago=1, country="AU")
    record_visit(configured, path="/about", days_ago=2, session="s2", visitor="v2")
    result = configured.page_views("northwind.com")
    newest = result["page_views"][0]
    assert newest["path"] == "/pricing"
    assert newest["country"] == "AU"
    assert newest["country_source"] == "ip"
    assert newest["intent"]["tagged"] == "Intent"
    assert result["page_views"][1]["intent"] is None


def test_recent_page_views_are_newest_first_and_bounded(configured):
    for index in range(5):
        record_visit(configured, days_ago=index + 1, session=f"s{index}", visitor=f"v{index}")
    result = configured.page_views("northwind.com", limit=3)
    assert result["count"] == 3
    assert result["total"] == 5
    assert result["page_views"][0]["occurred_at"] == ago(days=1)


def test_the_contacts_tab_shows_last_touch_last_engagement_and_planned_meetings(configured, store):
    store.create(
        collection_names.CONTACTS,
        {
            "company_key": "northwind.com",
            "email": "dana@northwind.com",
            "name": "Dana Kelly",
            "last_touch_at": ago(days=3),
            "last_engagement_at": ago(days=1),
            "scheduled": [{"kind": "meeting", "title": "Commercial review", "starts_at": ago(days=-4)}],
        },
        actor="sam",
        source="seed",
    )
    result = configured.contacts("northwind.com")
    assert result["count"] == 1
    contact = result["contacts"][0]
    assert contact["last_touch_at"] == ago(days=3)
    assert contact["last_engagement_at"] == ago(days=1)
    assert contact["scheduled"][0]["title"] == "Commercial review"


def test_the_contacts_tab_of_a_company_with_none_is_empty_not_broken(configured):
    assert configured.contacts("northwind.com") == {
        "company_key": "northwind.com",
        "count": 0,
        "contacts": [],
        "fields": ["last_touch_at", "last_engagement_at", "scheduled"],
    }


# --------------------------------------------------------------------------- #
# Manual enrolment
# --------------------------------------------------------------------------- #


def test_a_manual_enrolment_is_recorded_once(configured):
    record_visit(configured, days_ago=1)
    first = configured.enroll("northwind.com", {"workflow": "Nurture"}, actor="dana", source="t")
    second = configured.enroll("northwind.com", {"workflow": "Nurture"}, actor="dana", source="t")
    assert first["outcome"] == "enrolled"
    assert second["outcome"] == "already_enrolled"
    assert first["enrolment_id"] == second["enrolment_id"]


def test_a_manual_enrolment_into_a_different_workflow_is_a_second_record(configured):
    record_visit(configured, days_ago=1)
    configured.enroll("northwind.com", {"workflow": "Nurture"}, actor="dana", source="t")
    second = configured.enroll("northwind.com", {"workflow": "Events"}, actor="dana", source="t")
    assert second["outcome"] == "enrolled"
    assert len(configured.store.list(collection_names.ENROLMENTS, limit=10)) == 2


def test_a_manual_enrolment_needs_a_workflow(configured):
    with pytest.raises(InvalidConfiguration):
        configured.enroll("northwind.com", {"workflow": "  "}, actor="dana", source="t")


def test_an_enrolment_appears_on_the_company_row(configured):
    record_visit(configured, days_ago=1)
    configured.enroll("northwind.com", {"workflow": "Nurture"}, actor="dana", source="t")
    assert configured.company("northwind.com")["enrolments"][0]["workflow"] == "Nurture"


# --------------------------------------------------------------------------- #
# Derived properties, refreshed by a run
# --------------------------------------------------------------------------- #


def test_a_run_refreshes_a_derived_property_that_has_gone_stale(configured, store):
    record = store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "derived_properties": {"showing_smb_intent": True}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, path="/pricing", days_ago=1)
    view = _view_with_automation(configured, days=90, add=False, track=False)
    result = configured.run(view["automation_id"], actor="dana", source="t")
    assert result["derived_properties_refreshed"] == 1
    refreshed = store.get(record["id"])["data"]["derived_properties"]
    assert refreshed["showing_smb_intent"] is False
    assert refreshed["showing_priced_up"] is True


def test_a_second_run_reports_nothing_to_refresh(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "derived_properties": {}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, path="/pricing", days_ago=1)
    view = _view_with_automation(configured, days=90, add=False, track=False)
    configured.run(view["automation_id"], actor="dana", source="t")
    assert configured.run(view["automation_id"], actor="dana", source="t")["derived_properties_refreshed"] == 0


def test_an_enrichment_writes_a_derived_property_onto_a_company_already_in_the_crm(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "derived_properties": {}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, path="/smb/plans", days_ago=1)
    _enable_category(configured, "in_crm_visitor_intent")
    configured.run_category("in_crm_visitor_intent", actor="dana", source="t")
    record = store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=1)[0]
    assert record["data"]["derived_properties"]["showing_smb_intent"] is True


# --------------------------------------------------------------------------- #
# lifecyclestage is forward-only
# --------------------------------------------------------------------------- #


def test_a_lifecycle_stage_cannot_move_backwards(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "lifecycle_stage": "customer"},
        actor="sam",
        source="seed",
    )
    with pytest.raises(LifecycleStageRegression) as caught:
        configured.update_company("northwind.com", {"lifecycle_stage": "lead"}, actor="dana", source="t")
    assert caught.value.status == 409
    assert "forward-only" in str(caught.value)


def test_a_lifecycle_stage_can_move_forwards(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "lifecycle_stage": "lead"},
        actor="sam",
        source="seed",
    )
    result = configured.update_company(
        "northwind.com", {"lifecycle_stage": "opportunity"}, actor="dana", source="t"
    )
    assert result["lifecycle_stage"] == "opportunity"
    assert result["lifecycle_move"] == "forward"


def test_a_stage_the_product_cannot_order_is_allowed_and_reported(configured, store):
    """A team adding a stage must not need coordination to write it."""
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "lifecycle_stage": "evangelist"},
        actor="sam",
        source="seed",
    )
    result = configured.update_company(
        "northwind.com", {"lifecycle_stage": "brand_fan"}, actor="dana", source="t"
    )
    assert result["lifecycle_stage"] == "brand_fan"
    # The product cannot order a stage it has never heard of, so it does not claim
    # the move was forward; it says the check did not run.
    assert result["lifecycle_move"] == "unchecked"


def test_is_forward_only_is_false_when_either_side_is_unknown():
    assert is_forward_only("customer", "lead") is True
    assert is_forward_only("lead", "customer") is False
    assert is_forward_only("brand_fan", "lead") is False
    assert is_forward_only(None, "lead") is False
    assert is_forward_only("customer", None) is False


def test_an_arbitrary_property_can_be_written_without_a_migration(configured, store):
    store.create(collection_names.COMPANIES, {"root_domain": "northwind.com"}, actor="sam", source="seed")
    configured.update_company(
        "northwind.com",
        {"properties": {"renewal_risk": "high", "team": {"pod": "alpha"}}},
        actor="dana",
        source="t",
    )
    record = store.find(collection_names.COMPANIES, {"root_domain": "northwind.com"}, limit=1)[0]
    assert record["data"]["properties"]["renewal_risk"] == "high"
    # The dynamic index makes the new field queryable with no change anywhere.
    assert len(store.find(collection_names.COMPANIES, {"properties.renewal_risk": "high"}, limit=5)) == 1


def test_known_ips_can_be_registered_so_the_ip_route_keeps_working(configured, store):
    store.create(collection_names.COMPANIES, {"root_domain": "northwind.com"}, actor="sam", source="seed")
    configured.update_company(
        "northwind.com", {"known_ips": ["203.0.113.5"]}, actor="dana", source="t"
    )
    result = configured.record_visit(
        visit_payload(company_domain="", ip="203.0.113.5"), actor="system", source="t"
    )
    assert result["company_key"] == "northwind.com"
    assert result["attribution"] == "ip"


def test_updating_a_company_that_is_not_in_the_crm_says_so(configured):
    assert configured.update_company("nope.example", {"segment": "x"}, actor="dana", source="t")["updated"] is False


# --------------------------------------------------------------------------- #
# The Overview tab
# --------------------------------------------------------------------------- #


def test_the_overview_counts_the_five_researched_numbers(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "lifecycle_stage": "opportunity", "derived_properties": {}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, path="/pricing", days_ago=1)
    configured.record_research(
        {
            "kind": "news",
            "company_domain": "litware.example",
            "signal_type": "funding",
            "headline": "Litware raises",
            "country": "AU",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    _enable_category(configured, "net_new_visitor_intent")
    configured.run_category("net_new_visitor_intent", actor="dana", source="t")
    overview = configured.overview(actor="dana")
    assert overview["companies_showing_visitor_intent"] >= 1
    assert overview["companies_showing_research_intent"] == 1
    assert overview["added_companies"] == 1
    assert overview["companies_converted_to_lifecycle_stage"] == 1
    assert overview["news_signals"] == 1
    assert "popular_auto_adds" in overview
    assert len(overview["popular_auto_adds"]) == 4


def test_popular_auto_adds_counts_adds_and_enrichments(configured, store):
    store.create(
        collection_names.COMPANIES,
        {"root_domain": "northwind.com", "derived_properties": {}},
        actor="sam",
        source="seed",
    )
    record_visit(configured, days_ago=1)
    record_visit(configured, domain="in.example", days_ago=1, session="s2", visitor="v2")
    _enable_category(configured, "in_crm_visitor_intent")
    configured.run_category("in_crm_visitor_intent", actor="dana", source="t")
    popular = {row["id"]: row for row in configured.overview()["popular_auto_adds"]}
    assert popular["in_crm_visitor_intent"]["enriched"] == 1
    assert popular["in_crm_visitor_intent"]["companies"] == 1


def test_the_overview_reports_an_excluded_domain_count(configured):
    configured.exclude({"domain": "northwind.com"}, actor="sam", source="t")
    assert configured.overview()["excluded_domains"] == 1


# --------------------------------------------------------------------------- #
# The research tab
# --------------------------------------------------------------------------- #


def test_the_research_tab_separates_topics_from_news(configured):
    configured.record_research(
        {"kind": "topic", "company_domain": "a.example", "topic": "cspm", "occurred_at": ago(days=2)},
        actor="system",
        source="t",
    )
    configured.record_research(
        {
            "kind": "news",
            "company_domain": "b.example",
            "signal_type": "funding",
            "headline": "B raises",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    result = configured.research_tab()
    assert result["count"] == 2
    assert result["topic_matches"] == {"cspm": 1}
    assert result["news_signals"] == {"funding": 1}
    assert result["news_signal_types"][0]["companies"] == ["b.example"]


def test_news_counts_as_research_intent(configured):
    """"broader intent signals beyond your website" - a funding round is one."""
    configured.record_research(
        {
            "kind": "news",
            "company_domain": "b.example",
            "signal_type": "merger",
            "headline": "B merges",
            "occurred_at": ago(days=1),
        },
        actor="system",
        source="t",
    )
    row = configured.companies()["companies"][0]
    assert row["research_intent"] is True
    assert row["research_evidence"][0]["kind"] == "news"
    assert row["news_signals"] == 1


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_carries_the_five_operators_with_the_vendor_labels(configured):
    vocabulary = configured.vocabulary()
    assert [row["id"] for row in vocabulary["path_operators"]] == list(PATH_OPERATORS)
    assert vocabulary["path_operators"][0]["label"] == "Path is equal to"
    assert [row["id"] for row in vocabulary["sort_keys"]] == ["page_views", "unique_visitors", "last_visit"]
    assert [row["id"] for row in vocabulary["automation_toggles"]] == [AUTOMATION_ADD, AUTOMATION_TRACK]
    assert vocabulary["record_source"] == RECORD_SOURCE_BUYER_INTENT
    assert vocabulary["credit_cost_add"] == 10
    assert vocabulary["credit_cost_track"] == 10
    assert len(vocabulary["auto_add_categories"]) == 4
    assert vocabulary["root_domain_model"]["truncates"] == "www"
    assert [row["id"] for row in vocabulary["card_fields"]] == [
        "website_visits",
        "unique_visitors",
        "last_seen",
        "top_page_views",
    ]


def test_the_vocabulary_lists_exactly_the_five_researched_news_types(configured):
    assert [row["id"] for row in configured.vocabulary()["news_signal_types"]] == [
        "funding",
        "executive_hire",
        "layoff",
        "product_launch",
        "merger",
    ]


def test_every_inference_is_named_and_explained(configured):
    described = configured.inferences()
    assert described["count"] == len(INFERENCES) >= 20
    for entry in described["inferences"]:
        assert entry["id"]
        assert entry["question"]
        assert entry["reading"]
        assert entry["why"]
        assert entry["change"]


def test_the_inference_that_shapes_the_whole_package_is_served(configured):
    entry = by_id("entry_time_is_derived_not_observed")
    assert entry is not None
    assert "It will not add all existing companies" in entry["why"]
    assert "views_are_portal_scoped" in {item["id"] for item in INFERENCES}


def test_by_id_returns_none_for_an_unknown_inference():
    assert by_id("no-such-inference") is None


# --------------------------------------------------------------------------- #
# HTTP surface, through the mounted router
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(db_path: Path, monkeypatch) -> Iterator[TestClient]:
    """A client on the real app, with this feature mounted by discovery."""
    monkeypatch.setenv("DSR_DB_PATH", str(db_path))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(db_path.parent / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", db_path.parent / "absent-frontend")
    load_feature(MODULE)
    with TestClient(app) as test_client:
        yield test_client


def http_configure(client: TestClient) -> dict[str, Any]:
    """The researched prerequisites, over HTTP."""
    client.patch(
        f"{PREFIX}/settings",
        json={"credits_enabled": True, "enrichment_actors": ["dana"]},
        params={"actor": "sam"},
    )
    client.post(
        f"{PREFIX}/markets",
        json={"name": "ANZ enterprise software", "countries": ["AU"], "industries": ["software"]},
    )
    client.post(
        f"{PREFIX}/criteria",
        json={
            "name": "Priced up",
            "derived_property": "showing_priced_up",
            "page_filters": [{"operator": "starts_with", "path": "/pricing"}],
        },
    )
    client.post(f"{PREFIX}/topics", json={"name": "CSPM", "terms": ["cspm"]})
    return {}


def test_the_feature_is_mounted_by_discovery(client):
    body = client.get("/api/features").json()
    feature = next(row for row in body["features"] if row["id"] == FEATURE_ID)
    assert feature["prefix"] == PREFIX
    assert feature["ticket"] == "WF-033"
    assert feature["exception_handlers"] == ["MarketIntentError"]
    assert len(feature["routes"]) == 41


def test_the_registry_reports_no_failed_features(client):
    body = client.get("/api/features").json()
    offenders = [row for row in body["failed"] if "wf033" in row["id"]]
    assert offenders == []


def test_vocabulary_and_inferences_are_served_over_http(client):
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 20


def http_enter_after_the_switch(
    client: TestClient, *, domain: str = "northwind.com", path: str = "/pricing", seconds_ahead: int = 90
) -> Any:
    """One page view that arrived *after* an automation was switched on.

    Over HTTP the switch-on stamp is the moment the request was served, so this is
    the only way a company can have entered after it: the visit carries a timestamp
    a little ahead of the server's own clock. A tracking code whose clock runs fast is
    an ordinary event, and the researched note has to give the same answer either
    way - which is why this is a helper and not a way around the rule."""
    return client.post(
        f"{PREFIX}/visits",
        json={
            "url": f"https://{domain}{path}",
            "occurred_at": (
                datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead)
            ).isoformat(),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": domain,
            "country": "AU",
        },
    )


def test_settings_and_capabilities_over_http(client):
    before = client.get(f"{PREFIX}/settings").json()
    assert before["credits_enabled"] is False
    assert before["enrichment_actors"] == []
    response = client.patch(
        f"{PREFIX}/settings",
        json={"credits_enabled": True, "enrichment_actors": ["dana"]},
        params={"actor": "sam"},
    )
    assert response.status_code == 200
    assert response.json()["credits_enabled"] is True
    capabilities = client.get(f"{PREFIX}/capabilities", params={"actor": "dana"}).json()
    assert capabilities["can_add_companies"] is True
    assert client.get(f"{PREFIX}/capabilities", params={"actor": "nobody"}).json()["can_add_companies"] is False


def test_a_domain_refusal_is_mapped_by_the_features_own_handler(client):
    response = client.get(f"{PREFIX}/exclusions")
    assert response.status_code == 402
    body = response.json()
    assert body["error"] == "credits_required"
    assert body["capability"] == "exclusions"
    assert body["status"] == 402


def test_a_path_filter_refusal_is_422_over_http(client):
    response = client.post(
        f"{PREFIX}/criteria",
        json={"name": "Bad", "page_filters": [{"operator": "ends_with", "path": "/pricing"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_path_filter"


def test_a_criterion_with_no_page_is_422_over_http(client):
    response = client.post(f"{PREFIX}/criteria", json={"name": "Anything"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_configuration"


def test_a_topic_and_a_market_round_trip_over_http(client):
    assert client.post(f"{PREFIX}/topics", json={"name": "CSPM", "terms": ["cspm"]}).status_code == 201
    assert client.post(f"{PREFIX}/markets", json={"name": "ANZ", "countries": ["AU"]}).status_code == 201
    assert client.get(f"{PREFIX}/topics").json()["count"] == 1
    assert client.get(f"{PREFIX}/markets").json()["count"] == 1


def test_a_market_naming_neither_country_nor_industry_is_refused_over_http(client):
    response = client.post(f"{PREFIX}/markets", json={"name": "Everything"})
    assert response.status_code == 422
    assert "every company would be in it" in response.json()["detail"]


def test_a_criterion_can_be_withdrawn_over_http(client):
    created = client.post(
        f"{PREFIX}/criteria",
        json={"name": "Careers", "page_filters": [{"operator": "contains", "path": "/careers"}]},
    ).json()
    response = client.delete(f"{PREFIX}/criteria/{created['id']}")
    assert response.status_code == 200
    assert response.json()["withdrawn"] is True
    assert client.get(f"{PREFIX}/criteria").json()["criteria"][0]["active"] is False


def test_a_topic_can_be_withdrawn_over_http(client):
    created = client.post(f"{PREFIX}/topics", json={"name": "CSPM"}).json()
    assert client.delete(f"{PREFIX}/topics/{created['id']}").json()["withdrawn"] is True


def test_a_visit_and_its_table_row_over_http(client):
    http_configure(client)
    response = client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://www.northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": "northwind.com",
            "country": "AU",
        },
    )
    assert response.status_code == 201
    assert response.json()["company_key"] == "northwind.com"
    body = client.get(f"{PREFIX}/companies").json()
    assert body["count"] == 1
    assert body["companies"][0]["visitor_intent"] is True


def test_a_visit_with_no_host_is_422_over_http(client):
    response = client.post(
        f"{PREFIX}/visits",
        json={"occurred_at": ago(days=1), "session_id": "s1", "visitor_id": "v1"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_observation"


def test_the_left_panel_filters_work_through_the_query_string(client):
    http_configure(client)
    for path, domain, source in (
        ("/pricing", "northwind.com", "organic_search"),
        ("/about", "south.example", "email"),
    ):
        client.post(
            f"{PREFIX}/visits",
            json={
                "url": f"https://{domain}{path}",
                "occurred_at": ago(days=1),
                "session_id": f"s-{domain}",
                "visitor_id": f"v-{domain}",
                "company_domain": domain,
                "traffic_source": source,
                "country": "AU",
            },
        )
    only_pricing = client.get(
        f"{PREFIX}/companies", params={"path": ["starts_with:/pricing"]}
    ).json()
    assert [row["company_key"] for row in only_pricing["companies"]] == ["northwind.com"]
    with_domain = client.get(
        f"{PREFIX}/companies", params={"path": ["starts_with:/pricing@south.example"]}
    ).json()
    assert with_domain["count"] == 0
    by_source = client.get(f"{PREFIX}/companies", params={"traffic_source": ["email"]}).json()
    assert [row["company_key"] for row in by_source["companies"]] == ["south.example"]
    sorted_by_visitors = client.get(
        f"{PREFIX}/companies", params={"sort": "unique_visitors", "direction": "asc"}
    ).json()
    assert sorted_by_visitors["filters"]["sort"] == "unique_visitors"


def test_a_malformed_path_filter_query_is_422_over_http(client):
    http_configure(client)
    response = client.get(f"{PREFIX}/companies", params={"path": ["/pricing"]})
    assert response.status_code == 422
    assert "operator:value" in response.json()["detail"]


def test_a_time_frame_over_ninety_days_is_422_over_http(client):
    response = client.get(f"{PREFIX}/companies", params={"days": 91})
    assert response.status_code == 422
    assert response.json()["error"] == "timeframe_too_long"
    assert "midnight UTC" in response.json()["detail"]


def test_a_bad_sort_is_422_over_http(client):
    response = client.get(f"{PREFIX}/companies", params={"sort": "revenue"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_sort"


def test_an_unknown_company_is_404_over_http(client):
    assert client.get(f"{PREFIX}/companies/nope.example").status_code == 404
    assert client.get(f"{PREFIX}/companies/nope.example/card").status_code == 404


def test_the_card_and_its_drilldowns_over_http(client):
    http_configure(client)
    client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://www.northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": "northwind.com",
            "country": "AU",
        },
    )
    card = client.get(f"{PREFIX}/companies/northwind.com/card").json()
    assert card["fields"]["website_visits"]["value"] == 1
    assert card["full_activity"]["href"] == f"{PREFIX}/companies/northwind.com/page-views"
    page_views = client.get(f"{PREFIX}/companies/northwind.com/page-views").json()
    assert page_views["page_views"][0]["intent"]["tagged"] == "Intent"
    assert client.get(f"{PREFIX}/companies/northwind.com/contacts").json()["count"] == 0


def test_a_company_can_be_patched_over_http(client):
    http_configure(client)
    view = client.post(
        f"{PREFIX}/views", json={"name": "In market", "filters": {"days": None, "visitor_intent": True}}
    ).json()
    automation = client.post(
        f"{PREFIX}/automations", json={"view_id": view["id"], AUTOMATION_ADD: True}
    ).json()
    http_enter_after_the_switch(client)
    client.post(f"{PREFIX}/automations/{automation['id']}/run", params={"actor": "dana"})
    response = client.patch(
        f"{PREFIX}/companies/northwind.com", json={"lifecycle_stage": "opportunity"}
    )
    assert response.status_code == 200
    assert response.json()["lifecycle_stage"] == "opportunity"


def test_a_lifecycle_regression_is_409_over_http(client):
    http_configure(client)
    view = client.post(
        f"{PREFIX}/views", json={"name": "In market", "filters": {"days": None, "visitor_intent": True}}
    ).json()
    automation = client.post(
        f"{PREFIX}/automations", json={"view_id": view["id"], AUTOMATION_ADD: True}
    ).json()
    http_enter_after_the_switch(client)
    client.post(f"{PREFIX}/automations/{automation['id']}/run", params={"actor": "dana"})
    client.patch(f"{PREFIX}/companies/northwind.com", json={"lifecycle_stage": "customer"})
    response = client.patch(
        f"{PREFIX}/companies/northwind.com", json={"lifecycle_stage": "lead"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "lifecycle_stage_regression"


def test_a_research_observation_over_http(client):
    response = client.post(
        f"{PREFIX}/research",
        json={
            "kind": "news",
            "company_domain": "litware.example",
            "signal_type": "funding",
            "headline": "Litware raises a Series C",
            "occurred_at": ago(days=1),
            "country": "AU",
        },
    )
    assert response.status_code == 201
    assert response.json()["signal_type"] == "funding"
    tab = client.get(f"{PREFIX}/research").json()
    assert tab["count"] == 1
    assert tab["news_signals"] == {"funding": 1}


def test_a_duplicate_view_name_is_409_over_http(client):
    client.post(f"{PREFIX}/views", json={"name": "In market", "filters": {}})
    response = client.post(f"{PREFIX}/views", json={"name": "In market", "filters": {}})
    assert response.status_code == 409
    assert response.json()["error"] == "view_name_taken"


def test_an_unknown_view_is_404_over_http(client):
    assert client.get(f"{PREFIX}/views/nope").status_code == 404
    assert client.get(f"{PREFIX}/views/nope/companies").status_code == 404


def test_saving_and_removing_a_view_over_http(client):
    created = client.post(f"{PREFIX}/views", json={"name": "In market", "filters": {"days": 7}}).json()
    assert client.get(f"{PREFIX}/views").json()["count"] == 1
    assert client.get(f"{PREFIX}/views/{created['id']}").json()["found"] is True
    assert client.delete(f"{PREFIX}/views/{created['id']}").json()["removed"] is True
    assert client.get(f"{PREFIX}/views").json()["count"] == 0


def test_running_an_unknown_automation_is_404_over_http(client):
    assert client.post(f"{PREFIX}/automations/nope/run").status_code == 404


def test_the_whole_automation_path_over_http(client):
    http_configure(client)
    # A company that was already qualifying before the automation existed, so the
    # run below has something to hold back.
    client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s0",
            "visitor_id": "v0",
            "company_domain": "northwind.com",
            "country": "AU",
        },
    )
    view = client.post(
        f"{PREFIX}/views",
        json={"name": "In market", "filters": {"days": None, "visitor_intent": True}},
    ).json()
    automation = client.post(
        f"{PREFIX}/automations",
        json={"view_id": view["id"], AUTOMATION_ADD: True, AUTOMATION_TRACK: True},
    ).json()
    assert automation["add_enabled_at"] is not None
    # And a second company that arrives afterwards, so the researched note is a
    # difference between two companies inside one response.
    http_enter_after_the_switch(client, domain="fabrikam.io")
    response = client.post(f"{PREFIX}/automations/{automation['id']}/run", params={"actor": "dana"})
    assert response.status_code == 200
    body = response.json()
    assert [entry["company_key"] for entry in body["added"]] == ["fabrikam.io"]
    assert body["added"][0]["record_source"] == RECORD_SOURCE_BUYER_INTENT
    assert body["tracked"][0]["credit"]["waived"] == 10
    assert {row["company_key"] for row in body["held_back"]} == {"northwind.com"}
    assert {row["reason"] for row in body["held_back"]} == {
        "entered_before_auto_add_was_enabled",
        "entered_before_tracking_was_enabled",
    }
    assert client.get(f"{PREFIX}/credits").json()["total_charged"] == 10


def test_the_categories_over_http(client):
    listed = client.get(f"{PREFIX}/categories").json()
    assert listed["count"] == 4
    toggled = client.post(f"{PREFIX}/categories/net_new_visitor_intent", json={"enabled": True})
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is True
    assert client.post(f"{PREFIX}/categories/net_new_visitor_intent/run", params={"actor": "dana"}).json()["enabled"] is True
    assert client.post(f"{PREFIX}/categories/nope").status_code == 422
    assert client.post(f"{PREFIX}/categories/nope/run").status_code == 422


def test_the_manual_enrolment_over_http(client):
    http_configure(client)
    client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": "northwind.com",
        },
    )
    first = client.post(
        f"{PREFIX}/companies/northwind.com/enroll",
        json={"workflow": "Nurture"},
        params={"actor": "dana"},
    )
    assert first.status_code == 201
    assert first.json()["outcome"] == "enrolled"
    repeat = client.post(
        f"{PREFIX}/companies/northwind.com/enroll",
        json={"workflow": "Nurture"},
        params={"actor": "dana"},
    )
    assert repeat.status_code == 200
    assert repeat.json()["outcome"] == "already_enrolled"


def test_the_enrolment_permission_is_enforced_over_http(client):
    http_configure(client)
    client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": "northwind.com",
        },
    )
    response = client.post(
        f"{PREFIX}/companies/northwind.com/enroll",
        json={"workflow": "Nurture"},
        params={"actor": "stranger"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "data_enrichment_permission_required"


def test_the_exclusions_round_trip_over_http(client):
    http_configure(client)
    created = client.post(f"{PREFIX}/exclusions", json={"domain": "www.south.example"}).json()
    assert created["root_domain"] == "south.example"
    assert client.get(f"{PREFIX}/exclusions").json()["count"] == 1
    assert client.post(f"{PREFIX}/exclusions", json={"domain": "south.example"}).status_code == 409
    assert client.delete(f"{PREFIX}/exclusions/south.example").json()["removed"] is True


def test_tracking_and_renewal_over_http(client):
    http_configure(client)
    view = client.post(
        f"{PREFIX}/views", json={"name": "In market", "filters": {"days": None, "visitor_intent": True}}
    ).json()
    automation = client.post(
        f"{PREFIX}/automations", json={"view_id": view["id"], AUTOMATION_TRACK: True}
    ).json()
    http_enter_after_the_switch(client)
    client.post(f"{PREFIX}/automations/{automation['id']}/run", params={"actor": "dana"})
    assert client.get(f"{PREFIX}/tracked").json()["count"] == 1
    renewal = client.post(f"{PREFIX}/tracked/renew", params={"actor": "dana"}).json()
    assert renewal["tracked_companies"] == 1
    assert client.get(f"{PREFIX}/credits").json()["total_charged"] == 10


def test_the_overview_over_http(client):
    http_configure(client)
    client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": "northwind.com",
            "country": "AU",
        },
    )
    body = client.get(f"{PREFIX}/overview", params={"actor": "dana"}).json()
    assert body["companies_showing_visitor_intent"] == 1
    assert body["added_companies"] == 0
    assert body["tab"] == "Overview"


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_feature_records_names_a_route_the_host_mounted():
    """An audit row that names a path the app stopped serving is a shipped defect.

    Sources are built from ``router.prefix`` in the feature module and recorded
    with a ``{placeholder}`` where the route has one, so this compares the
    recorded sources against the registry's own route list rather than against a
    hand-copied table that could drift.
    """
    mounted = {
        (method, path)
        for record in REGISTRY.features
        if record.id == FEATURE_ID
        for shape in record.routes
        for path in [shape["path"]]
        for method in shape["methods"]
    }
    assert mounted, "the feature is not mounted; the check below would pass vacuously"

    feature = load_feature(MODULE)
    sources = set(re.findall(r'source=(f?"[^"]*")', Path(feature.__file__).read_text(encoding="utf-8")))
    assert sources, "the module records no source at all, which is a defect in itself"

    for literal in sources:
        # The module's *source* is read here, so the f-string's own placeholders
        # are still doubled and ``router.prefix`` is a name rather than a value.
        # Both are resolved before the comparison, which is against the route
        # table the host itself reported.
        recorded = (
            literal.strip('f"')
            .replace("{router.prefix}", PREFIX)
            .replace("{{", "{")
            .replace("}}", "}")
        )
        method, _, path = recorded.partition(" ")
        assert (method, path) in mounted, (
            f"{recorded!r} is recorded as an audit source but the host never mounted it"
        )


def test_every_audit_row_this_feature_wrote_names_this_features_prefix(client, db_path: Path):
    http_configure(client)
    client.post(
        f"{PREFIX}/visits",
        json={
            "url": "https://northwind.com/pricing",
            "occurred_at": ago(days=1),
            "session_id": "s1",
            "visitor_id": "v1",
            "company_domain": "northwind.com",
            "country": "AU",
        },
    )
    view = client.post(
        f"{PREFIX}/views", json={"name": "In market", "filters": {"days": None, "visitor_intent": True}}
    ).json()
    automation = client.post(
        f"{PREFIX}/automations", json={"view_id": view["id"], AUTOMATION_ADD: True}
    ).json()
    client.post(f"{PREFIX}/automations/{automation['id']}/run", params={"actor": "dana"})

    database = AuditedDatabase(str(db_path), actor="reader")
    try:
        rows = [
            row
            for row in database.audit(limit=1000)
            if row["source"] and PREFIX in str(row["source"])
        ]
    finally:
        database.close()
    assert rows, "the writes this test performed recorded no source naming the feature"
    for row in rows:
        assert str(row["source"]).startswith(("GET ", "POST ", "PATCH ", "DELETE ", "PUT "))
        assert str(row["source"]).split(" ", 1)[1].startswith(PREFIX)


def test_the_audit_row_and_the_change_land_in_one_transaction(client, db_path: Path):
    """The product guarantee, exercised on one of this feature's own writes."""
    client.patch(
        f"{PREFIX}/settings", json={"credits_enabled": True}, params={"actor": "sam"}
    )
    database = AuditedDatabase(str(db_path), actor="reader")
    try:
        rows = database.audit(collection=collection_names.SETTINGS, limit=10)
    finally:
        database.close()
    assert rows
    assert rows[0]["action"] == "insert"
    assert rows[0]["after_state"]["credits_enabled"] is True
    assert rows[0]["source"] == f"PATCH {PREFIX}/settings"


def test_a_refused_write_records_no_audit_row(client, db_path: Path):
    client.post(f"{PREFIX}/criteria", json={"name": "No pages"})
    database = AuditedDatabase(str(db_path), actor="reader")
    try:
        rows = database.audit(collection=collection_names.CRITERIA, limit=10)
    finally:
        database.close()
    assert rows == []


# --------------------------------------------------------------------------- #
# Isolation: the feature registers by adding files
# --------------------------------------------------------------------------- #


def test_no_collection_this_feature_writes_is_another_features():
    """A shared collection name is a silent collision, not a loud one."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "features"
    others: set[str] = set()
    for module in package.glob("*.py"):
        if module.name == f"{MODULE}.py":
            continue
        others |= set(re.findall(r'"([a-z][a-z0-9_]{3,})"', module.read_text(encoding="utf-8")))
    assert set(collection_names.ALL).isdisjoint(others)


def test_the_feature_module_does_not_import_the_app():
    """Importing dsr.api from a feature reintroduces the shared-file coupling."""
    feature = load_feature(MODULE)
    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "from dsr.deps import StoreDep" in text


def test_no_route_this_feature_declares_collides_with_another():
    seen: set[tuple[str, str]] = set()
    for record in REGISTRY.features:
        for shape in record.routes:
            for method in shape["methods"]:
                key = (method, shape["path"])
                assert key not in seen, f"{record.id} duplicates {key}"
                seen.add(key)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_produces_the_states_the_research_makes_interesting(db: AuditedDatabase):
    import random

    from dsr.features import load_feature as load

    feature = load(MODULE)
    summary = feature.seed(db, {"room_ids": [], "now": NOW, "rng": random.Random("wf033")})
    assert isinstance(summary, str) and summary
    for fragment in (
        "excluded domain",
        "still anonymous",
        "held back because they entered before auto-add was enabled",
        "manual enrolment",
        "monthly renewal",
    ):
        assert fragment in summary, f"the seed summary does not mention {fragment!r}: {summary}"

    engine = MarketIntentEngine(RecordStore(db), now=lambda: NOW.isoformat())
    table_body = engine.companies()
    assert table_body["unattributed_views"] == 1
    keys = {row["company_key"] for row in table_body["companies"]}
    assert "talent-insight-partners.example" not in keys, "the excluded agency is still in the table"
    assert "contoso-health.com" in keys

    ledger = engine.credits()
    combined = [
        entry for entry in ledger["entries"] if entry.get("actions") == ["add", "track"]
    ]
    assert combined, "no company was added and tracked in one billing period"
    assert combined[0]["amount"] == 10
    assert combined[0]["waived"] == 10

    periods = {entry["period"] for entry in ledger["entries"]}
    assert len(periods) > 1, "nothing was charged in a second billing period"

    overview = engine.overview(actor="dana")
    assert overview["companies_showing_research_intent"] > 0
    assert overview["companies_showing_visitor_intent"] > 0
    assert overview["companies_converted_to_lifecycle_stage"] == 1
    assert overview["added_companies"] > 0
    assert overview["news_signals"] == 5

    news_types = {
        (row.get("data") or {}).get("signal_type")
        for row in db.list(collection_names.RESEARCH, limit=100)
        if (row.get("data") or {}).get("kind") == "news"
    }
    assert news_types == {"funding", "executive_hire", "layoff", "product_launch", "merger"}


def test_the_seed_leaves_the_derived_property_of_a_stopped_company_false(db: AuditedDatabase):
    import random

    from dsr.features import load_feature as load

    load(MODULE).seed(db, {"room_ids": [], "now": NOW, "rng": random.Random("wf033")})
    engine = MarketIntentEngine(RecordStore(db), now=lambda: NOW.isoformat())
    row = engine.company("northwind.com")
    assert row is not None
    # Northwind was added by the stock category, not by the view automation.
    assert row["record_source"] == RECORD_SOURCE_BUYER_INTENT
    assert row["added_via"].startswith("auto-add:net_new_visitor_intent")
    assert engine.company("tailspintoys.example")["derived_properties"]["showing_smb_intent"] is False


def test_the_seed_holds_northwind_back_from_the_view_automation(db: AuditedDatabase):
    """The researched note, as a row the seeder's own summary names."""
    import random

    from dsr.features import load_feature as load

    load(MODULE).seed(db, {"room_ids": [], "now": NOW, "rng": random.Random("wf033")})
    engine = MarketIntentEngine(RecordStore(db), now=lambda: NOW.isoformat())
    automated = next(
        view for view in engine.views() if engine.automation_for(view["id"]) is not None
    )
    members = engine.view_companies(automated["id"])
    northwind = next(row for row in members["companies"] if row["company_key"] == "northwind.com")
    assert northwind["entered_after_auto_add"] is False
    assert northwind["entered_at"] < members["automation"]["add_enabled_at"]


def test_the_seed_is_reproducible(db_path: Path):
    import random

    from dsr.features import load_feature as load

    summaries = []
    for index in range(2):
        database = AuditedDatabase(str(db_path).replace(".db", f"-{index}.db"), actor="seed")
        try:
            summaries.append(
                load(MODULE).seed(
                    database, {"room_ids": [], "now": NOW, "rng": random.Random("wf033")}
                )
            )
        finally:
            database.close()
    assert summaries[0] == summaries[1]


# --------------------------------------------------------------------------- #
# The HTTP surface, as a whole
# --------------------------------------------------------------------------- #


def test_every_declared_route_answers(client):
    """A route the registry reports but the app does not serve is a reported failure."""
    http_configure(client)
    feature = next(row for row in client.get("/api/features").json()["features"] if row["id"] == FEATURE_ID)
    for shape in feature["routes"]:
        for method in shape["methods"]:
            if method in ("POST", "PATCH", "DELETE"):
                continue
            path = shape["path"].replace("{company_key}", "northwind.com").replace("{view_id}", "x")
            path = path.replace("{automation_id}", "x").replace("{category_id}", "net_new_visitor_intent")
            path = path.replace("{criterion_id}", "x").replace("{topic_id}", "x").replace("{domain}", "x.example")
            response = client.get(path)
            assert response.status_code != 405, f"{method} {path} is mounted but not served"
            assert response.status_code < 500, f"{method} {path} failed with {response.status_code}"


def test_a_domain_error_carries_its_own_code_and_status():
    """The handler reads both off the exception; neither is hard-coded there."""
    error = MarketIntentError("boom")
    assert error.status == 400
    assert error.code == "market_intent_error"
    assert isinstance(CreditsRequired("x"), MarketIntentError)
    assert isinstance(EnrichmentPermissionRequired("x"), MarketIntentError)
    assert isinstance(InvalidPathFilter("x"), InvalidConfiguration)


def test_the_pure_helpers_are_importable_without_a_store():
    """A feature has to be testable on its own, without the app or a database."""
    assert root_domain("www.a.example") == "a.example"
    assert FilterSet.parse({"days": 30}).days == 30
    assert sort_rows([], key="page_views") == []
    assert build_rows(Snapshot()) == []
    assert qualify_view({"path": "/pricing"}, []) is None
    assert market_for({"AU"}, "", [{"id": "m1", "countries": ["AU"], "industries": []}]) == ["m1"]
    assert top_page_views([]) == []
    assert summarise([])["total_charged"] == 0
    assert entered_at({}, FilterSet.parse({}), Snapshot()) is None
    assert matches_filters({}, {}, window=None) is True
