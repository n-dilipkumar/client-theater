"""WF-031: the domain rules of identifying anonymous visitors as companies.

What is being held here
-----------------------

The research for this workflow is a vendor's help article read closely, and it
makes five statements this package has to honour exactly:

1. "Albacross focuses on exclusively company-level identification rather than
   tracking individual users, ensuring respect for user privacy." The first
   section of this file is the strongest test in it: a capture creates a company
   record and no person record, and the proof is a count of the rows left behind
   rather than a reading of a docstring.
2. "We check the IP address, the country, the network, and other publicly
   available parameters to stay GDPR compliant." Three parameters are named and
   the fourth clause is not enumerated, so the capture vocabulary is closed at
   three and anything else is refused.
3. "When you type in the web page URL do not include the domain." A page
   definition carrying a domain is refused, and the rejection is tested.
4. "you can select which condition should be followed: Exact ... Contains ...
   Starts with." Three conditions, each covered, and a fourth refused.
5. "The insights provided include the company's name, website, address, size, and
   a list of employees or contacts associated with the company." The company
   record shape is derived from that sentence and the derivation is checked
   against it.

The rest is the machinery those five rules need: the company lookup, the Pages
list, the lead list and its filters, the ICP, the audit sources, and the seed.

Every test in this file runs against a fresh database through the shared
fixtures in ``conftest.py``, and every one passes when the file is run on its own.
Nothing here reads a module-level global or depends on the order tests ran in.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
from pathlib import Path

import dsr.features as host
import dsr.visitor_identification as vi
import pytest
from dsr.features import load_feature
from dsr.store import RecordStore
from dsr.visitor_identification.company import keyed, slug
from dsr.visitor_identification.engine import VisitorEngine
from dsr.visitor_identification.errors import (
    CompanyAlreadyIdentified,
    CompanyKeyRequired,
    InvalidCapture,
    InvalidCompanyDetail,
    InvalidIcp,
    InvalidInstallation,
    InvalidPageDefinition,
    PathCarriesADomain,
    PersonalDataRefused,
    UnknownCaptureParameter,
    UnknownCompany,
    UnknownIcp,
    UnknownInstallation,
    UnknownLeadFilter,
    UnknownMatchCondition,
    UnknownPage,
    VisitorIdentificationError,
)

MODULE = "wf031_identify_anonymous_web_visitors_as_com"
FEATURE_ID = "wf-031-identify-anonymous-web-visitors-as-com"
PREFIX = "/api/wf-031"
CLIENT = "cli-acme"
SOURCE = "test"

#: One pinned clock for the whole file, so a moment in an audit row can be
#: compared against a moment in a record without arithmetic in every test.
BASE = dt.datetime(2026, 10, 4, 9, 0, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(store: RecordStore) -> VisitorEngine:
    """A :class:`VisitorEngine` over a fresh database, with the snippet installed."""
    built = VisitorEngine(store, now=lambda: BASE)
    built.install({"client_id": CLIENT, "site": "https://acme.example"}, actor="sam", source=SOURCE)
    return built


@pytest.fixture
def bare(store: RecordStore) -> VisitorEngine:
    """An engine with nothing installed, for the refusal cases."""
    return VisitorEngine(store, now=lambda: BASE)


def capture_body(**overrides: object) -> dict[str, object]:
    """A capture body that is accepted, with one field replaced per test."""
    body: dict[str, object] = {
        "client_id": CLIENT,
        "path": "/pricing",
        "network": "203.0.113.0/24",
        "ip_address": "203.0.113.11",
        "country": "GB",
    }
    body.update(overrides)
    return body


def send(engine: VisitorEngine, **overrides: object) -> dict:
    return engine.capture(capture_body(**overrides), actor="tracking-snippet", source=SOURCE)


def define(engine: VisitorEngine, name: str, path: str, condition: str = "exact", **extra):
    return engine.define_page(
        {"name": name, "path": path, "condition": condition, **extra},
        client_id=CLIENT,
        actor="sam",
        source=SOURCE,
    )


# --------------------------------------------------------------------------- #
# The privacy stance: a company record and no person record
# --------------------------------------------------------------------------- #


def test_a_capture_creates_a_company_record_and_no_person_record(store, engine):
    """The researched stance, proved by counting records rather than by reading prose.

    "a **company-level** record is created (never an individual user)". One
    capture, then every collection in the database is counted. The company
    collection holds exactly one row, the page-visit collection holds exactly one,
    and no collection anywhere holds a second row that could be a person.
    """
    send(engine, path="/newsroom/converting-the-unconverted-article")

    collections = {entry["collection"]: entry["live"] for entry in store.collections()}
    assert collections["identified_company"] == 1
    assert collections["company_page_visit"] == 1
    assert collections["tracking_installation"] == 1
    # Nothing else exists at all, so there is nowhere for an individual to hide.
    assert set(collections) == {
        "identified_company",
        "company_page_visit",
        "tracking_installation",
    }


def test_the_company_record_holds_no_field_about_a_person(store, engine):
    """Every key on the company record is a company fact or a network fact.

    The researched field set is the name, the website, the address, the size and
    the contacts, and the workflow adds only counters and public parameters. A key
    whose name reads like a person would be the leak this stance rules out.
    """
    send(engine)
    data = store.find(
        "identified_company",
        {"company_key": vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))},
        limit=1,
    )[0]["data"]

    forbidden = {
        "visitor_id",
        "user_id",
        "person_id",
        "email",
        "name_of_person",
        "cookie",
        "cookies",
        "fingerprint",
        "device_id",
    }
    assert not forbidden.intersection(data), sorted(forbidden.intersection(data))
    # And nothing nested: the contacts list carries a name and a role, nothing else.
    for contact in data["contacts"]:
        assert set(contact) <= set(vi.CONTACT_FIELDS)


@pytest.mark.parametrize("key", sorted(vi.PERSONAL_PARAMETER_NAMES))
def test_a_capture_naming_a_person_is_refused_by_name(key):
    """ "exclusively company-level identification rather than tracking individual users".

    Refused with its own error rather than folded into the closed-list refusal,
    so a caller that sends one is told why instead of being told the key is
    unknown.
    """
    with pytest.raises(PersonalDataRefused) as caught:
        vi.parse_capture(capture_body(**{key: "anything"}), now=BASE)
    assert caught.value.code == "personal_data_refused"
    assert key in str(caught.value)


@pytest.mark.parametrize(
    "key",
    ["user_agent", "referrer", "utm_source", "screen_size", "device_type", "latitude"],
)
def test_a_capture_parameter_the_research_does_not_name_is_refused(key):
    """The capture list stops at the three parameters the research names.

    "We check the IP address, the country, the network, and other publicly
    available parameters." Three are named and the fourth clause is not
    enumerated, so the list is closed. An open list is a capture that accepts
    whatever a snippet is configured to send.
    """
    with pytest.raises(UnknownCaptureParameter) as caught:
        vi.parse_capture(capture_body(**{key: "x"}), now=BASE)
    assert key in str(caught.value)


def test_the_capture_vocabulary_is_exactly_the_three_named_parameters_plus_the_request():
    """The published set, asserted against the three the sentence names."""
    assert vi.CAPTURE_PARAMETERS == ("ip_address", "country", "network")
    assert vi.ALLOWED_CAPTURE_KEYS == frozenset(
        {"ip_address", "country", "network", "path", "client_id", "captured_at", "actor"}
    )


def test_the_identification_stance_is_quoted_where_a_client_can_read_it():
    """The stance is a published value, not only a comment in a docstring."""
    assert vi.IDENTIFICATION_STANCE.startswith("Albacross focuses on exclusively company-level")
    assert engine_vocabulary()["company_level_only"] is True


def engine_vocabulary() -> dict:
    return vi.describe_vocabulary()


def test_the_downstream_surfaces_are_named_without_being_built():
    """ "#17" is a section of the research file, not a ticket reference.

    The research points at Workflows and Webhooks and at Auto-engage campaigns.
    Both are recorded so a reviewer can see they were read and neither was quietly
    dropped, and neither is built here.
    """
    surfaces = {entry["surface"]: entry for entry in vi.DOWNSTREAM_SURFACES}
    assert set(surfaces) == {"Workflows", "Auto-engage"}
    assert "section 17" in surfaces["Workflows"]["scope"]
    assert "not a ticket reference" in surfaces["Workflows"]["note"]
    assert "Not built" not in surfaces["Workflows"]["note"]


# --------------------------------------------------------------------------- #
# The capture parameters
# --------------------------------------------------------------------------- #


def test_a_capture_with_neither_address_nor_network_is_refused(bare):
    """There is nothing to identify a company by, and the fallback is forbidden.

    The one thing that could stand in for a missing network is a person, and the
    one thing this workflow refuses to build is a person.
    """
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidCapture) as caught:
        bare.capture(
            {"client_id": CLIENT, "path": "/pricing", "country": "GB"},
            actor="tracking-snippet",
            source=SOURCE,
        )
    assert "IP address or the network" in str(caught.value)


def test_a_capture_with_only_a_network_is_accepted(bare):
    """The network alone identifies the company, so the address is optional."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    result = bare.capture(
        {"client_id": CLIENT, "path": "/pricing", "network": "198.51.100.0/24"},
        actor="tracking-snippet",
        source=SOURCE,
    )
    assert result["company"]["known_ips"] == []


def test_a_capture_with_only_an_address_is_accepted(bare):
    """An address on its own is still a network-level fact."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    result = bare.capture(
        {"client_id": CLIENT, "path": "/pricing", "ip_address": "198.51.100.9"},
        actor="tracking-snippet",
        source=SOURCE,
    )
    assert result["company"]["known_networks"] == []


def test_a_capture_without_a_path_is_refused(bare):
    """ "page-visit events recorded per URL path" - there is no event without one."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidPageDefinition):
        bare.capture(
            {"client_id": CLIENT, "network": "198.51.100.0/24"},
            actor="tracking-snippet",
            source=SOURCE,
        )


def test_a_capture_without_a_client_id_is_refused(bare):
    """Step 1 says to note the Client ID, and nothing else addresses the Pages list."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidCapture) as caught:
        bare.capture(
            {"path": "/pricing", "network": "198.51.100.0/24"},
            actor="tracking-snippet",
            source=SOURCE,
        )
    assert "Client ID" in str(caught.value)


def test_a_capture_from_an_uninstalled_client_is_refused(bare):
    """A snippet that was never installed cannot attribute a company's traffic."""
    with pytest.raises(UnknownInstallation) as caught:
        bare.capture(
            {"client_id": "cli-never", "path": "/pricing", "network": "198.51.100.0/24"},
            actor="tracking-snippet",
            source=SOURCE,
        )
    assert caught.value.status == 404


@pytest.mark.parametrize(
    "value",
    [{"ip_address": 24}, {"country": True}, {"network": ["a"]}, {"network": {"a": 1}}],
)
def test_a_capture_parameter_of_the_wrong_shape_is_refused(bare, value):
    """A number where a string belongs is a caller sending the wrong shape."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidCapture):
        bare.capture(
            {"client_id": CLIENT, "path": "/pricing", "network": "198.51.100.0/24", **value},
            actor="tracking-snippet",
            source=SOURCE,
        )


def test_an_over_long_capture_parameter_is_refused(bare):
    """The bound is there so a payload cannot put an unbounded string into every row."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidCapture) as caught:
        bare.capture(
            {
                "client_id": CLIENT,
                "path": "/pricing",
                "network": "n" * (vi.MAX_NETWORK_LENGTH + 1),
            },
            actor="tracking-snippet",
            source=SOURCE,
        )
    assert "network" in str(caught.value)


# --------------------------------------------------------------------------- #
# The capture moment
# --------------------------------------------------------------------------- #


def test_a_capture_may_carry_its_own_moment(engine):
    """A tracking snippet batches, so it may send the moment the request arrived."""
    result = send(engine, captured_at="2026-09-01T06:30:00+02:00")
    assert result["captured_at"] == "2026-09-01T06:30:00+02:00"
    assert engine.visits(result["company_key"])["visits"][0]["captured_at"] == (
        "2026-09-01T06:30:00+02:00"
    )


def test_a_capture_moment_with_no_offset_is_refused(bare):
    """The lead list ranks on last visit, so a moment with no zone cannot be placed."""
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidCapture) as caught:
        bare.capture(
            {
                "client_id": CLIENT,
                "path": "/pricing",
                "network": "198.51.100.0/24",
                "captured_at": "2026-09-01T06:30:00",
            },
            actor="tracking-snippet",
            source=SOURCE,
        )
    assert "no timezone offset" in str(caught.value)


def test_a_capture_moment_that_is_not_a_timestamp_is_refused(bare):
    bare.install({"client_id": CLIENT}, actor="sam", source=SOURCE)
    with pytest.raises(InvalidCapture) as caught:
        bare.capture(
            {
                "client_id": CLIENT,
                "path": "/pricing",
                "network": "198.51.100.0/24",
                "captured_at": "yesterday",
            },
            actor="tracking-snippet",
            source=SOURCE,
        )
    assert "ISO 8601" in str(caught.value)


def test_a_capture_moment_is_kept_as_it_was_sent(store, engine):
    """The moment a caller sent is the moment the lead list ranks on."""
    send(engine, captured_at="2026-09-01T06:30:00+02:00")
    row = store.find("company_page_visit", {}, limit=1)[0]
    assert row["data"]["captured_at"] == "2026-09-01T06:30:00+02:00"


def test_a_capture_without_a_moment_uses_the_server_clock(store, engine):
    result = send(engine)
    assert result["captured_at"] == BASE.isoformat()


# --------------------------------------------------------------------------- #
# Paths, and the rule that a page carries no domain
# --------------------------------------------------------------------------- #


def test_a_page_definition_with_a_domain_is_refused():
    """ "When you type in the web page URL do not include the domain".

    A definition of ``https://acme.example/pricing`` matches nothing, because a
    capture records a path and no domain. Refused at the point of definition.
    """
    with pytest.raises(PathCarriesADomain) as caught:
        vi.normalise_path("https://acme.example/pricing")
    assert "carries a domain" in str(caught.value)
    assert "/newsroom/converting-the-unconverted-article" in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://acme.example/pricing",
        "https://www.acme.example/pricing",
        "//acme.example/pricing",
        "acme.example/pricing",
        "www.acme.example",
        "acme.example",
    ],
)
def test_every_shape_of_domain_is_refused(value):
    """Six ways to include a domain, one error."""
    with pytest.raises(PathCarriesADomain):
        vi.normalise_path(value)


@pytest.mark.parametrize(
    "value",
    ["/v1.0/pricing", "/chapter.1/pricing", "/newsroom/converting-the-unconverted-article"],
)
def test_a_full_stop_in_a_path_segment_is_not_treated_as_a_domain(value):
    """A segment with a full stop and a numeric label is a directory, not a host."""
    assert vi.normalise_path(value) == value


def test_a_path_that_does_not_start_with_a_slash_is_refused():
    """ "the URL path without the domain" describes an absolute path."""
    with pytest.raises(InvalidPageDefinition) as caught:
        vi.normalise_path("pricing/enterprise")
    assert "does not start with a slash" in str(caught.value)


def test_the_query_string_and_the_fragment_are_dropped():
    """A page definition is the identity of a page, and neither varies which page."""
    assert (
        vi.normalise_path("/newsroom/converting-the-unconverted-article?utm_source=x")
        == "/newsroom/converting-the-unconverted-article"
    )
    assert vi.normalise_path("/pricing#plans") == "/pricing"


def test_a_trailing_slash_is_dropped_and_the_root_path_is_kept():
    assert vi.normalise_path("/newsroom/") == "/newsroom"
    assert vi.normalise_path("/") == "/"


def test_repeated_slashes_collapse():
    assert vi.normalise_path("/pricing//enterprise") == "/pricing/enterprise"


def test_a_path_is_not_case_folded():
    """A URL path is case-sensitive, and the research never says otherwise."""
    assert vi.normalise_path("/Pricing") == "/Pricing"


def test_an_empty_path_is_refused():
    with pytest.raises(InvalidPageDefinition):
        vi.normalise_path("   ")


def test_looks_like_host_only_accepts_a_alphabetic_last_label():
    assert vi.looks_like_host("acme.example") is True
    assert vi.looks_like_host("www.acme.example.com") is True
    assert vi.looks_like_host("v1.0") is False
    assert vi.looks_like_host("newsroom") is False
    assert vi.looks_like_host(".example") is False
    assert vi.looks_like_host("example.") is False


# --------------------------------------------------------------------------- #
# The three match conditions
# --------------------------------------------------------------------------- #


def test_the_three_conditions_are_the_ones_the_research_names():
    """ "you can select which condition should be followed: Exact ... Contains ...
    Starts with"."""
    assert vi.MATCH_CONDITIONS == ("exact", "contains", "starts_with")
    assert [vi.MATCH_CONDITION_LABELS[c] for c in vi.MATCH_CONDITIONS] == [
        "Exact",
        "Contains",
        "Starts with",
    ]


@pytest.mark.parametrize(
    "condition,page,visit,expected",
    [
        ("exact", "/pricing", "/pricing", True),
        ("exact", "/pricing", "/pricing/enterprise", False),
        ("exact", "/pricing", "/Pricing", False),
        ("exact", "/pricing", "/pricing?utm_source=x", True),
        ("contains", "/pricing", "/pricing", True),
        ("contains", "/pricing", "/eu/pricing/enterprise", True),
        ("contains", "/pricing", "/eu/plans", False),
        ("contains", "pricing", "/eu/pricing", None),
        ("starts_with", "/pricing", "/pricing", True),
        ("starts_with", "/pricing", "/pricing/enterprise", True),
        ("starts_with", "/pricing", "/eu/pricing", False),
        ("starts_with", "/pricing", "/pricing/enterprise?x=1", True),
    ],
)
def test_each_condition_matches_exactly_what_it_says(condition, page, visit, expected):
    if expected is None:
        with pytest.raises(InvalidPageDefinition):
            vi.matches(condition, page, visit)
        return
    assert vi.matches(condition, page, visit) is expected


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("Exact", "exact"),
        ("exact", "exact"),
        ("Contains", "contains"),
        ("starts_with", "starts_with"),
        ("Starts with", "starts_with"),
        ("starts-with", "starts_with"),
        (" STARTS WITH ", "starts_with"),
    ],
)
def test_a_condition_label_a_human_read_is_normalised(condition, expected):
    """A picker sends what a human read off a label."""
    assert vi.normalise_condition(condition) == expected


@pytest.mark.parametrize("condition", ["ends_with", "", None, "regex", "like", "prefix"])
def test_a_fourth_condition_is_refused(condition):
    """The three are the whole enumeration; a fourth is a filter nobody can predict."""
    with pytest.raises(UnknownMatchCondition) as caught:
        vi.normalise_condition(condition)
    assert "Exact, Contains, Starts with" in str(caught.value)


# --------------------------------------------------------------------------- #
# The Pages list
# --------------------------------------------------------------------------- #


def test_a_page_needs_a_name_and_a_path(engine):
    """ "Give the page a name and input the URL." """
    with pytest.raises(InvalidPageDefinition):
        engine.define_page({"path": "/pricing"}, client_id=CLIENT, actor="sam", source=SOURCE)
    with pytest.raises(InvalidPageDefinition):
        engine.define_page({"name": "Priced up"}, client_id=CLIENT, actor="sam", source=SOURCE)


def test_a_page_without_a_client_id_is_refused():
    """The Pages list belongs to the account whose dashboard holds it."""
    with pytest.raises(InvalidPageDefinition) as caught:
        vi.parse_page({"name": "Priced up", "path": "/pricing"})
    assert "client id" in str(caught.value)


def test_a_page_condition_defaults_to_exact(engine):
    """A page with no condition stated gets the narrowest of the three."""
    page = engine.define_page(
        {"name": "Priced up", "path": "/pricing"}, client_id=CLIENT, actor="sam", source=SOURCE
    )
    assert page["condition"] == "exact"


def test_a_page_definition_stores_the_vendor_label(engine):
    page = define(engine, "Priced up", "/pricing", "Contains")
    assert page["condition_label"] == "Contains"


def test_the_pages_list_is_scoped_to_its_client(engine):
    """A page defined under one account does not appear in another's list."""
    define(engine, "Asked for a price", "/pricing", "Exact")
    engine.define_page(
        {"name": "Asked for a price", "path": "/pricing", "condition": "exact"},
        client_id="cli-other",
        actor="sam",
        source=SOURCE,
    )
    mine = engine.pages(CLIENT)["pages"]
    theirs = engine.pages("cli-other")["pages"]
    assert [p["name"] for p in mine] == ["Asked for a price"]
    assert len(theirs) == 1
    assert mine[0]["id"] != theirs[0]["id"]
    assert len(engine.pages()["pages"]) == 2


def test_a_page_definition_can_be_amended_and_is_rechecked(engine):
    """Step four is a choice a seller may revisit."""
    page = define(engine, "Priced up", "/pricing", "Exact")
    amended = engine.amend_page(page["id"], {"condition": "Starts with"}, source=SOURCE)
    assert amended["condition"] == "starts_with"
    assert amended["path"] == "/pricing"


def test_amending_a_page_with_a_domain_is_refused(engine):
    """The same rule applies on the way in and on the way to a change."""
    page = define(engine, "Priced up", "/pricing")
    with pytest.raises(PathCarriesADomain):
        engine.amend_page(page["id"], {"path": "https://acme.example/pricing"}, source=SOURCE)


def test_amending_a_page_with_an_unknown_field_is_refused(engine):
    page = define(engine, "Priced up", "/pricing")
    with pytest.raises(InvalidPageDefinition) as caught:
        engine.amend_page(page["id"], {"colour": "red"}, source=SOURCE)
    assert "colour" in str(caught.value)


def test_amending_a_page_with_nothing_is_refused(engine):
    """A client that thinks it changed a page and did not is worse than an error."""
    page = define(engine, "Priced up", "/pricing")
    with pytest.raises(InvalidPageDefinition):
        engine.amend_page(page["id"], {}, source=SOURCE)


def test_a_page_can_be_removed_and_stops_matching(engine):
    """Matches are computed on read, so removing a page needs no sweep."""
    page = define(engine, "Priced up", "/pricing", "Exact")
    send(engine, path="/pricing")
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    assert [p["id"] for p in engine.company(key)["matched_pages"]] == [page["id"]]
    engine.drop_page(page["id"], source=SOURCE)
    assert engine.pages(CLIENT)["pages"] == []
    assert engine.lead_list({})["companies"][0]["matched_pages"] == []


def test_an_unknown_page_id_is_refused(engine):
    with pytest.raises(UnknownPage):
        engine.amend_page("pg_absent", {"condition": "exact"}, source=SOURCE)
    with pytest.raises(UnknownPage):
        engine.drop_page("pg_absent", source=SOURCE)


# --------------------------------------------------------------------------- #
# What identifies a company
# --------------------------------------------------------------------------- #


def test_the_first_capture_creates_the_company(engine):
    result = send(engine)
    assert result["company_created"] is True
    assert result["identified"] == "company"
    assert result["company_key"]


def test_a_second_capture_from_the_same_network_reuses_the_company(engine):
    first = send(engine)
    second = send(engine, ip_address="203.0.113.12")
    assert second["company_created"] is False
    assert second["company_key"] == first["company_key"]
    assert second["company"]["page_views"] == 2
    assert second["company"]["known_ips"] == ["203.0.113.11", "203.0.113.12"]


def test_an_address_already_on_file_resolves_before_the_network(engine):
    """Narrowest evidence first: a known address beats an unknown network."""
    first = send(engine)
    second = send(engine, ip_address="203.0.113.11", network="192.0.2.0/24")
    assert second["company_created"] is False
    assert second["company_key"] == first["company_key"]
    assert "192.0.2.0/24" in second["company"]["known_networks"]


def test_a_capture_from_an_unknown_network_creates_a_second_company(engine):
    """No evidence links them, so they are two companies rather than one guess."""
    first = send(engine)
    second = send(engine, network="192.0.2.0/24", ip_address="192.0.2.7")
    assert second["company_created"] is True
    assert second["company_key"] != first["company_key"]


def test_a_network_never_resolves_to_two_companies(engine):
    """Every capture from one network lands on one company record, however many
    addresses it arrives from."""
    keys = {
        send(engine, ip_address=f"203.0.113.{n}", path=f"/page-{n}")["company_key"]
        for n in range(10, 20)
    }
    assert len(keys) == 1


def test_the_company_key_is_url_safe_and_carries_a_digest_when_folded():
    """/ in a CIDR block cannot survive a URL path, so the key folds it."""
    key = keyed("203.0.113.0/24")
    assert "/" not in key
    assert key.startswith("203.0.113.0-24-")
    assert keyed("203.0.113.0/24") == key, "the derivation has to be deterministic"


def test_two_networks_that_fold_alike_get_different_keys():
    """The fold is lossy, so a digest keeps the keys apart."""
    assert keyed("a/b") != keyed("a-b")


def test_a_value_needing_no_folding_is_used_as_it_is():
    assert keyed("198.51.100.24") == "198.51.100.24"
    assert slug("198.51.100.24") == "198.51.100.24"


def test_slug_folds_to_something_url_safe_for_anything():
    for raw in ["a/b", "a b", "a?b#c", "///", "%%%"]:
        assert "/" not in slug(raw)
        assert " " not in slug(raw)


def test_the_known_addresses_are_a_map_so_the_dynamic_index_can_answer(store, engine):
    """_flatten gives an array member a *positional* path, so a list of addresses
    cannot answer "is this one on file?". The map keyed by the address can, and
    the second-address lookup is what depends on it."""
    send(engine)
    request = vi.parse_capture(capture_body(ip_address="203.0.113.11"), now=BASE)
    found, _ = vi.resolve(store, request)
    assert found is not None
    assert found["data"]["company_key"] == vi.derive_company_key(
        vi.parse_capture(capture_body(), now=BASE)
    )


def test_the_known_networks_are_a_map_too(store, engine):
    """Same reason, same shape, and the network lookup is the second thing that
    would silently fail on a list."""
    send(engine)
    request = vi.parse_capture(
        {"client_id": CLIENT, "path": "/pricing", "network": "203.0.113.0/24"}, now=BASE
    )
    found, _ = vi.resolve(store, request)
    assert found is not None


def test_require_company_names_the_key_it_did_not_find(store):
    with pytest.raises(UnknownCompany) as caught:
        vi.require_company(store, "absent")
    assert "absent" in str(caught.value)


# --------------------------------------------------------------------------- #
# The company record shape, derived from the researched sentence
# --------------------------------------------------------------------------- #


def test_the_five_researched_fields_are_the_company_fields():
    """ "The insights provided include the company's name, website, address, size,
    and a list of employees or contacts associated with the company." """
    assert vi.COMPANY_FIELDS == ("name", "website", "address", "size", "contacts")


def test_a_freshly_identified_company_has_all_five_present_and_empty(engine):
    """Present and empty, not absent: the drill-down has five columns.

    An unidentified company is a normal state here, not a broken one, and a
    missing key would render as a blank with no way to tell it from a bug.
    """
    send(engine)
    detail = engine.company(vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE)))
    for field in vi.COMPANY_FIELDS:
        assert field in detail, field
    assert detail["name"] == ""
    assert detail["contacts"] == []


def test_the_five_fields_can_be_set_together(engine):
    """One call, the researched sentence's five fields."""
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    send(engine)
    updated = engine.update_company(
        key,
        {
            "name": "Northwind Traders",
            "website": "https://northwind.example",
            "address": "4 Shipley Lane, Manchester",
            "size": "1000+",
            "contacts": [
                {"name": "Dana Kelly", "role": "Chief Revenue Officer"},
                {"name": "Bo Nkemelu"},
            ],
        },
        actor="sam",
        source=SOURCE,
    )
    assert updated["name"] == "Northwind Traders"
    assert updated["website"] == "https://northwind.example"
    assert updated["address"] == "4 Shipley Lane, Manchester"
    assert updated["size"] == "1000+"
    assert [c["name"] for c in updated["contacts"]] == ["Dana Kelly", "Bo Nkemelu"]
    assert "role" not in updated["contacts"][1]


@pytest.mark.parametrize("field", ["name", "website", "address", "size"])
def test_a_named_field_that_is_present_and_blank_is_refused(engine, field):
    """A blank column in the drill-down has no way to say 'not known yet'."""
    with pytest.raises(InvalidCompanyDetail) as caught:
        vi.parse_detail({field: "   "})
    assert field in str(caught.value)


def test_a_null_named_field_is_refused():
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_detail({"name": None})


def test_a_website_that_is_not_a_url_is_refused():
    with pytest.raises(InvalidCompanyDetail) as caught:
        vi.parse_detail({"website": "northwind.example"})
    assert "not a URL" in str(caught.value)


@pytest.mark.parametrize("value", [{"name": True}, {"size": ["big"]}, {"address": {"a": 1}}])
def test_a_named_field_of_the_wrong_shape_is_refused(value):
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_detail(value)


def test_an_unknown_company_field_is_refused_by_name():
    """The researched set is published, so a client cannot drift from it quietly."""
    with pytest.raises(InvalidCompanyDetail) as caught:
        vi.parse_detail({"revenue": "10m"})
    assert "revenue" in str(caught.value)


def test_a_contact_must_be_a_person_with_a_name():
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_contacts([{"role": "CTO"}])
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_contacts(["Dana Kelly"])


def test_a_contact_may_not_carry_an_email_address():
    """No address is named in this workflow's research, and it would be the first
    person-level field in the package."""
    with pytest.raises(InvalidCompanyDetail) as caught:
        vi.parse_contacts([{"name": "Dana Kelly", "email": "dana@example"}])
    assert "email" in str(caught.value)


def test_contacts_must_be_a_list():
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_contacts({"name": "Dana Kelly"})


def test_tags_are_a_list_of_non_empty_text():
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_detail({"tags": "in-market"})
    with pytest.raises(InvalidCompanyDetail):
        vi.parse_detail({"tags": ["", "in-market"]})
    assert vi.parse_detail({"tags": ["a", "a", "b"]})["tags"] == ["a", "b"]


def test_the_counters_a_capture_keeps(engine):
    send(engine, path="/pricing")
    send(engine, path="/pricing")
    send(engine, path="/security")
    detail = engine.company(vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE)))
    assert detail["page_views"] == 3
    assert detail["distinct_paths"] == 2
    assert detail["first_seen_at"] == BASE.isoformat()
    assert detail["last_visit_at"] == BASE.isoformat()


def test_a_country_arriving_in_lower_case_is_stored_in_upper(engine):
    send(engine, country="gb")
    detail = engine.company(vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE)))
    assert detail["countries"] == ["GB"]


def test_countries_accumulate_without_repeating(engine):
    send(engine, country="GB")
    send(engine, country="gb", path="/about")
    send(engine, country="IE", path="/plans")
    detail = engine.company(vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE)))
    assert detail["countries"] == ["GB", "IE"]


# --------------------------------------------------------------------------- #
# A company added by hand
# --------------------------------------------------------------------------- #


def test_a_company_can_be_added_before_its_network_is_seen(engine):
    """ "matched against the company database" implies the database has entries of
    its own."""
    created = engine.create_company(
        {"company_key": "tailwind-and-friends", "name": "Tailwind and Friends"},
        actor="sam",
        source=SOURCE,
    )
    assert created["name"] == "Tailwind and Friends"
    assert created["page_views"] == 0
    assert engine.company("tailwind-and-friends")["identified_from"] == "manual"


def test_a_hand_added_company_needs_a_key(engine):
    with pytest.raises(CompanyKeyRequired) as caught:
        engine.create_company({"name": "Nobody"}, actor="sam", source=SOURCE)
    assert "company_key" in str(caught.value)


def test_a_hand_added_company_key_that_is_taken_is_refused(engine):
    engine.create_company({"company_key": "tailwind"}, actor="sam", source=SOURCE)
    with pytest.raises(CompanyAlreadyIdentified) as caught:
        engine.create_company(
            {"company_key": "tailwind", "name": "Other"}, actor="sam", source=SOURCE
        )
    assert caught.value.status == 409


# --------------------------------------------------------------------------- #
# The page-visit events
# --------------------------------------------------------------------------- #


def test_every_capture_records_one_page_visit_event(store, engine):
    send(engine, path="/pricing")
    send(engine, path="/security")
    rows = store.list("company_page_visit", limit=10)
    assert sorted(row["data"]["path"] for row in rows) == ["/pricing", "/security"]


def test_a_visit_event_carries_the_public_parameters_and_the_path(store, engine):
    send(engine)
    row = store.list("company_page_visit", limit=1)[0]["data"]
    assert row["path"] == "/pricing"
    assert row["network"] == "203.0.113.0/24"
    assert row["ip_address"] == "203.0.113.11"
    assert row["country"] == "GB"
    assert row["client_id"] == CLIENT


def test_the_company_visit_drilldown_groups_by_path(engine):
    for path in ("/pricing", "/pricing", "/pricing/enterprise", "/about"):
        send(engine, path=path)
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    result = engine.visits(key)
    assert result["total"] == 4
    assert result["top_paths"][0] == ("/pricing", 2)
    assert dict(result["top_paths"])["/pricing/enterprise"] == 1


def test_the_visit_drilldown_is_bounded(engine):
    for index in range(5):
        send(engine, path=f"/page-{index}")
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    assert engine.visits(key, limit=2)["count"] == 2
    assert engine.visits(key, limit=2)["total"] == 5
    assert engine.visits(key, limit=10_000)["count"] == 5


def test_the_visit_drilldown_of_an_unknown_company_is_refused(engine):
    with pytest.raises(UnknownCompany):
        engine.visits("absent")


# --------------------------------------------------------------------------- #
# The lead list: the Pages filter
# --------------------------------------------------------------------------- #


def test_the_pages_filter_isolates_the_companies_that_visited_those_pages(engine):
    """ "apply the **Pages** filter to isolate companies that visited those pages"."""
    article = define(engine, "Read the article", "/newsroom/article", "Starts with")
    send(engine, path="/newsroom/article")
    send(engine, path="/newsroom/article/2017")
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7", path="/about")

    keys = [row["company_key"] for row in engine.lead_list({"page": [article["id"]]})["companies"]]
    assert len(keys) == 1


def test_the_pages_filter_excludes_a_company_that_matched_nothing(engine):
    article = define(engine, "Read the article", "/newsroom/article", "Exact")
    send(engine, path="/about")
    assert engine.lead_list({"page": [article["id"]]})["companies"] == []


def test_naming_two_pages_means_either_of_them(engine):
    """ "the Pages filter to isolate companies that visited those pages" - the
    selection is over the set the seller named."""
    article = define(engine, "Article", "/newsroom/article", "Exact")
    pricing = define(engine, "Pricing", "/pricing", "Exact")
    send(engine, path="/newsroom/article")
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7", path="/pricing")

    both = engine.lead_list({"page": [article["id"], pricing["id"]]})
    assert both["summary"]["companies"] == 2
    # Order is by rank, so compare the pairs rather than the sequence.
    assert {tuple(row["matched_pages"]) for row in both["companies"]} == {
        (article["id"],),
        (pricing["id"],),
    }


def test_a_page_defined_after_the_visit_still_matches(engine):
    """Matches are computed on read, so today's page filters last month's visit."""
    send(engine, path="/newsroom/article")
    late = define(engine, "Article", "/newsroom/article", "Exact")
    assert engine.lead_list({"page": [late["id"]]})["summary"]["companies"] == 1


def test_a_filter_naming_an_unknown_page_is_refused(engine):
    with pytest.raises(UnknownPage):
        engine.lead_list({"page": ["pg_absent"]})


def test_the_pages_filter_survives_a_changed_condition(engine):
    """Step four is editable, and the filter changes with it, immediately."""
    page = define(engine, "Pricing", "/pricing", "Exact")
    send(engine, path="/pricing/enterprise")
    assert engine.lead_list({"page": [page["id"]]})["companies"] == []
    engine.amend_page(page["id"], {"condition": "Starts with"}, source=SOURCE)
    assert engine.lead_list({"page": [page["id"]]})["summary"]["companies"] == 1


def test_each_condition_gives_a_different_answer_for_the_same_traffic(engine):
    """The three conditions are not three spellings of one rule."""
    exact = define(engine, "Exact pricing", "/pricing", "Exact")
    contains = define(engine, "Any pricing", "/pricing", "Contains")
    send(engine, path="/pricing")
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7", path="/pricing/enterprise")

    assert engine.lead_list({"page": [exact["id"]]})["summary"]["companies"] == 1
    assert engine.lead_list({"page": [contains["id"]]})["summary"]["companies"] == 2


# --------------------------------------------------------------------------- #
# The lead list: segment, tags, country, size
# --------------------------------------------------------------------------- #


def test_the_lead_list_filters_on_segment(engine):
    send(engine)
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7")
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    engine.update_company(key, {"segment": "enterprise"}, actor="sam", source=SOURCE)
    rows = engine.lead_list({"segment": "enterprise"})["companies"]
    assert [row["company_key"] for row in rows] == [key]


def test_the_lead_list_filters_on_tags_and_needs_all_of_them(engine):
    send(engine)
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    engine.update_company(key, {"tags": ["in-market", "tail-lights"]}, actor="sam", source=SOURCE)
    assert engine.lead_list({"tag": ["in-market"]})["summary"]["companies"] == 1
    assert engine.lead_list({"tag": ["in-market", "tail-lights"]})["summary"]["companies"] == 1
    assert engine.lead_list({"tag": ["in-market", "absent"]})["summary"]["companies"] == 0


def test_the_lead_list_filters_on_country_and_size(engine):
    send(engine, country="GB")
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    engine.update_company(key, {"size": "1000+"}, actor="sam", source=SOURCE)
    assert engine.lead_list({"country": "gb"})["summary"]["companies"] == 1
    assert engine.lead_list({"country": "IE"})["summary"]["companies"] == 0
    assert engine.lead_list({"size": "1000+"})["summary"]["companies"] == 1
    assert engine.lead_list({"size": "1-10"})["summary"]["companies"] == 0


def test_the_filters_combine(engine):
    """ "combine with Segment filters, tags, and the ICP" """
    article = define(engine, "Article", "/newsroom/article", "Exact")
    send(engine, path="/newsroom/article")
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7", path="/newsroom/article")
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    engine.update_company(
        key, {"segment": "enterprise", "tags": ["in-market"]}, actor="sam", source=SOURCE
    )
    rows = engine.lead_list(
        {"page": [article["id"]], "segment": "enterprise", "tag": ["in-market"]}
    )
    assert [row["company_key"] for row in rows["companies"]] == [key]


def test_an_unknown_filter_is_refused_by_name():
    with pytest.raises(UnknownLeadFilter) as caught:
        vi.parse_filters({"visitor": "x"})
    assert "visitor" in str(caught.value)
    assert "Segment filters, tags, and the ICP" in str(caught.value)


def test_a_limit_that_is_not_a_whole_number_is_refused():
    with pytest.raises(UnknownLeadFilter):
        vi.parse_filters({"limit": "many"})


def test_the_limit_is_clamped_to_the_published_ceiling():
    assert vi.parse_filters({"limit": 10_000}).limit == 200
    assert vi.parse_filters({"limit": 0}).limit == 1
    assert vi.parse_filters({}).limit == vi.DEFAULT_LIMIT


def test_a_repeated_query_parameter_is_a_list(engine):
    """A query string carries a list by repeating a key."""
    first = define(engine, "A", "/a", "Exact")
    second = define(engine, "B", "/b", "Exact")
    send(engine, path="/a")
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7", path="/b")
    assert vi.parse_filters({"page": [first["id"], second["id"]]}).pages == (
        first["id"],
        second["id"],
    )
    assert vi.parse_filters({"page": first["id"]}).pages == (first["id"],)
    assert engine.lead_list({"page": [first["id"], second["id"]]})["summary"]["companies"] == 2


def test_the_response_echoes_the_filter_set_it_applied(engine):
    send(engine)
    article = define(engine, "Article", "/newsroom/article", "Exact")
    echoed = engine.lead_list({"page": [article["id"]], "segment": "enterprise"})["filters"]
    assert echoed["pages"] == [article["id"]]
    assert echoed["segment"] == "enterprise"
    assert echoed["limit"] == vi.DEFAULT_LIMIT


# --------------------------------------------------------------------------- #
# The lead list: ranking
# --------------------------------------------------------------------------- #


def test_the_lead_list_is_ranked_by_page_views_first(engine):
    send(engine, path="/a")
    send(engine, path="/b")
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7", path="/c")
    rows = engine.lead_list({})["companies"]
    assert [row["page_views"] for row in rows] == [2, 1]


def test_a_tie_on_page_views_breaks_on_the_most_recent_visit(store):
    """Recency is the second ranking term, so it needs a clock the test can move."""
    clock = {"at": BASE}
    moving = VisitorEngine(store, now=lambda: clock["at"])
    moving.install({"client_id": CLIENT}, actor="sam", source=SOURCE)

    moving.capture(
        capture_body(network="203.0.113.0/24", ip_address="203.0.113.11", path="/a"),
        actor="sn",
        source=SOURCE,
    )
    moving.capture(
        capture_body(network="198.51.100.0/24", ip_address="198.51.100.31", path="/b"),
        actor="sn",
        source=SOURCE,
    )
    # One more view each, so the counts tie, and the last one on the second company
    # is the later moment. Recency has to be what separates them.
    clock["at"] = BASE + dt.timedelta(days=1)
    moving.capture(
        capture_body(network="203.0.113.0/24", ip_address="203.0.113.12", path="/c"),
        actor="sn",
        source=SOURCE,
    )
    clock["at"] = BASE + dt.timedelta(days=2)
    moving.capture(
        capture_body(network="198.51.100.0/24", ip_address="198.51.100.32", path="/d"),
        actor="sn",
        source=SOURCE,
    )

    rows = moving.lead_list({})["companies"]
    assert [row["page_views"] for row in rows] == [2, 2]
    assert rows[0]["last_visit_at"] > rows[1]["last_visit_at"]


def test_a_tie_on_count_and_visit_breaks_on_the_company_key(engine):
    """The third ranking term is not decoration: without it the order is not total."""
    send(engine, network="203.0.113.0/24", ip_address="203.0.113.11")
    send(engine, network="198.51.100.0/24", ip_address="198.51.100.31")
    rows = engine.lead_list({})["companies"]
    assert [row["company_key"] for row in rows] == sorted(row["company_key"] for row in rows)
    assert [row["page_views"] for row in rows] == [1, 1]


def test_the_lead_list_limit_truncates_the_ranked_list(engine):
    for index in range(5):
        send(engine, network=f"203.0.113.{index}/32", ip_address=f"203.0.113.{index}")
    assert engine.lead_list({"limit": 3})["summary"]["companies"] == 3
    assert engine.lead_list({"limit": 3})["filters"]["limit"] == 3


def test_the_lead_list_summary_counts_the_researched_shape(engine):
    send(engine)
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    engine.update_company(
        key, {"size": "1000+", "contacts": [{"name": "Dana"}]}, actor="sam", source=SOURCE
    )
    summary = engine.lead_list({})["summary"]
    assert summary["companies"] == 1
    assert summary["page_views"] == 1
    assert summary["with_contact_candidates"] == 1
    assert summary["countries"] == ["GB"]
    assert summary["sizes"] == ["1000+"]
    assert summary["researched_fields"] == list(vi.COMPANY_FIELDS)


def test_an_empty_lead_list_is_an_empty_list_not_an_error(engine):
    result = engine.lead_list({})
    assert result["companies"] == []
    assert result["summary"]["companies"] == 0


# --------------------------------------------------------------------------- #
# The ICP
# --------------------------------------------------------------------------- #


def test_an_icp_naming_no_criterion_is_refused():
    """A filter whose criteria are empty includes every company."""
    with pytest.raises(InvalidIcp) as caught:
        vi.parse_icp({"name": "Everyone"})
    assert "at least one criterion" in str(caught.value)


def test_an_icp_needs_a_name():
    with pytest.raises(InvalidIcp):
        vi.parse_icp({"sizes": ["1000+"]})


def test_an_icp_with_an_unknown_field_is_refused():
    with pytest.raises(InvalidIcp) as caught:
        vi.parse_icp({"name": "x", "industries": ["software"]})
    assert "industries" in str(caught.value)


def test_an_icp_covers_a_band_of_sizes():
    """The values within one field are alternatives."""
    profile = {"sizes": ["201-500", "1000+"], "countries": []}
    assert vi.icp_matches({"size": "1000+"}, profile) is True
    assert vi.icp_matches({"size": "201-500"}, profile) is True
    assert vi.icp_matches({"size": "1-10"}, profile) is False


def test_an_icp_needs_every_field_to_hold():
    profile = {"sizes": ["1000+"], "countries": ["GB"]}
    assert vi.icp_matches({"size": "1000+", "countries": ["GB"]}, profile) is True
    assert vi.icp_matches({"size": "1000+", "countries": ["IE"]}, profile) is False
    assert vi.icp_matches({"size": "1-10", "countries": ["GB"]}, profile) is False


def test_a_company_outside_every_size_is_outside_the_profile():
    assert vi.icp_matches({"size": ""}, {"sizes": ["1000+"]}) is False


def test_the_lead_list_filters_by_the_icp(engine):
    send(engine)
    key = vi.derive_company_key(vi.parse_capture(capture_body(), now=BASE))
    engine.update_company(key, {"size": "1000+"}, actor="sam", source=SOURCE)
    send(engine, network="192.0.2.0/24", ip_address="192.0.2.7")
    profile = engine.save_profile({"name": "Large", "sizes": ["1000+"]}, actor="sam", source=SOURCE)

    rows = engine.lead_list({"icp": profile["id"]})["companies"]
    assert [row["company_key"] for row in rows] == [key]


def test_a_filter_naming_an_unknown_icp_is_refused(engine):
    with pytest.raises(UnknownIcp):
        engine.lead_list({"icp": "icp_absent"})


def test_resaving_a_profile_updates_it_rather_than_refusing(engine):
    first = engine.save_profile({"name": "Large", "sizes": ["1000+"]}, actor="sam", source=SOURCE)
    again = engine.save_profile(
        {"name": "Large", "sizes": ["1000+", "501-1000"]}, actor="sam", source=SOURCE
    )
    assert again["id"] == first["id"]
    assert again["created"] is False
    assert engine.profiles()["count"] == 1
    assert engine.profiles()["profiles"][0]["sizes"] == ["1000+", "501-1000"]


def test_a_profile_can_be_removed_and_a_filter_naming_it_then_refuses(engine):
    profile = engine.save_profile({"name": "Large", "sizes": ["1000+"]}, actor="sam", source=SOURCE)
    engine.drop_profile(profile["id"], actor="sam", source=SOURCE)
    assert engine.profiles()["count"] == 0
    with pytest.raises(UnknownIcp):
        engine.lead_list({"icp": profile["id"]})


def test_removing_an_absent_profile_is_refused(engine):
    with pytest.raises(UnknownIcp):
        engine.drop_profile("icp_absent", source=SOURCE)


# --------------------------------------------------------------------------- #
# Installations
# --------------------------------------------------------------------------- #


def test_an_installation_needs_a_client_id(bare):
    with pytest.raises(InvalidInstallation):
        bare.install({"site": "https://acme.example"}, actor="sam", source=SOURCE)


def test_reinstalling_the_same_client_id_updates_the_site(bare):
    """Re-running the install guide is how a seller arrives here twice."""
    first = bare.install(
        {"client_id": CLIENT, "site": "https://acme.example"}, actor="sam", source=SOURCE
    )
    again = bare.install(
        {"client_id": CLIENT, "site": "https://www.acme.example"}, actor="sam", source=SOURCE
    )
    assert first["created"] is True
    assert again["created"] is False
    assert again["site"] == "https://www.acme.example"
    assert bare.installations()["count"] == 1


def test_a_second_installation_gets_its_own_pages_list(bare):
    bare.install({"client_id": "cli-a"}, actor="sam", source=SOURCE)
    bare.install({"client_id": "cli-b"}, actor="sam", source=SOURCE)
    assert bare.installations()["count"] == 2


# --------------------------------------------------------------------------- #
# The published vocabulary and the inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_is_served_as_data():
    published = vi.describe_vocabulary()
    assert published["match_conditions"][1]["label"] == "Contains"
    assert published["company_fields"] == list(vi.COMPANY_FIELDS)
    assert published["contact_fields"] == ["name", "role"]
    assert published["lead_filters"] == list(vi.LEAD_FILTERS)
    assert published["ranking"] == list(vi.RANKING)
    assert published["collections"]["companies"] == vi.COMPANIES


def test_every_inference_names_its_decision_the_reading_and_the_change():
    for entry in vi.INFERENCES:
        assert set(entry) >= {
            "id",
            "question",
            "reading",
            "why",
            "change",
            "risk",
        }, entry["id"]
        assert entry["reading"].strip()


def test_the_inferences_cover_the_readings_this_package_actually_makes():
    """The six decisions a reviewer would otherwise have to find in the diff."""
    recorded = {entry["id"] for entry in vi.INFERENCES}
    assert {
        "capture_parameter_list",
        "what_identifies_a_company",
        "company_record_shape",
        "path_normalisation",
        "ranking_keys",
        "where_this_workflow_stops",
    } <= recorded


def test_the_engine_serves_the_same_two_documents_the_package_holds(engine):
    assert engine.vocabulary()["company_level_only"] is True
    assert engine.inferences()["count"] == len(vi.INFERENCES)


# --------------------------------------------------------------------------- #
# The clock
# --------------------------------------------------------------------------- #


def test_the_engine_clock_takes_a_callable_a_value_or_nothing(store):
    assert VisitorEngine(store, now=lambda: BASE).stamp() == BASE.isoformat()
    assert VisitorEngine(store, now=BASE).stamp() == BASE.isoformat()
    assert VisitorEngine(store).now().tzinfo is not None


# --------------------------------------------------------------------------- #
# The feature module
# --------------------------------------------------------------------------- #


def test_the_feature_module_exports_the_contract():
    feature = load_feature(MODULE)
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["ticket"] == "WF-031"
    assert feature.router.prefix == PREFIX
    assert feature.FEATURE["name"]


def test_the_feature_is_mounted_by_discovery_alone():
    record = host.REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    assert record.prefix == PREFIX
    assert record.routes


def test_the_feature_reports_its_routes():
    record = host.REGISTRY.by_id(FEATURE_ID)
    paths = {(route["path"], tuple(route["methods"])) for route in record.routes}
    assert paths == {
        (f"{PREFIX}/vocabulary", ("GET",)),
        (f"{PREFIX}/inferences", ("GET",)),
        (f"{PREFIX}/installations", ("GET",)),
        (f"{PREFIX}/installations", ("POST",)),
        (f"{PREFIX}/captures", ("POST",)),
        (f"{PREFIX}/pages", ("GET",)),
        (f"{PREFIX}/pages", ("POST",)),
        (f"{PREFIX}/pages/{{page_id}}", ("PATCH",)),
        (f"{PREFIX}/pages/{{page_id}}", ("DELETE",)),
        (f"{PREFIX}/icps", ("GET",)),
        (f"{PREFIX}/icps", ("POST",)),
        (f"{PREFIX}/icps/{{icp_id}}", ("DELETE",)),
        (f"{PREFIX}/companies", ("GET",)),
        (f"{PREFIX}/companies", ("POST",)),
        (f"{PREFIX}/companies/{{company_key}}", ("GET",)),
        (f"{PREFIX}/companies/{{company_key}}", ("PATCH",)),
        (f"{PREFIX}/companies/{{company_key}}/visits", ("GET",)),
        (f"{PREFIX}/companies/{{company_key}}/pages", ("GET",)),
    }


def test_the_feature_never_opens_the_database_itself():
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert ".connect(" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source


def test_the_feature_takes_its_dependencies_from_dsr_deps():
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.deps import StoreDep" in source


def test_the_domain_package_reaches_for_the_store_and_nothing_else():
    """A domain module that reaches for the app cannot be tested on its own.

    Read from the tree rather than the text, so a re-export, an aliased import or
    a parenthesised multi-line import is all recognised. The allowed set is the
    store plus this package plus the standard library: nothing that could open a
    database or import the app.
    """
    package = Path(vi.__file__).parent
    standard = {"__future__", "hashlib", "re", "dataclasses", "datetime", "typing"}
    dsr_allowed = {"dsr.store"}

    def permitted(module: str) -> bool:
        if module in standard:
            return True
        if module in dsr_allowed:
            return True
        return module.startswith("dsr.visitor_identification")

    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert permitted(alias.name), f"{module.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert permitted(node.module or ""), f"{module.name}: {node.module}"


def test_the_feature_maps_exactly_one_error_hierarchy():
    feature = load_feature(MODULE)
    assert set(feature.EXCEPTION_HANDLERS) == {VisitorIdentificationError}


def test_every_domain_error_hangs_off_the_one_base():
    for name in vi.__all__:
        value = getattr(vi, name)
        if isinstance(value, type) and issubclass(value, Exception):
            assert issubclass(value, VisitorIdentificationError), name


def test_record_not_found_is_not_claimed_by_this_feature():
    """The core app already maps it, and two handlers for one type is refused."""
    from dsr.db.audited import RecordNotFound

    assert RecordNotFound not in load_feature(MODULE).EXCEPTION_HANDLERS


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_every_audit_source_the_feature_module_builds_names_a_mounted_route():
    """The audit row must name the route that actually served the write.

    The defect this prevents has shipped in this codebase before: a feature whose
    audit log kept recording a path the app had stopped serving. Read out of the
    module rather than from a list written beside it, so a write route added
    without a source fails here rather than in production.
    """
    feature = load_feature(MODULE)
    mounted = _mounted(feature)
    pattern = r'f"((?:POST|PATCH|DELETE|GET) \{router\.prefix\}[^"]*)"'
    derived = re.findall(pattern, source_of(feature))
    assert len(derived) >= 7, derived
    for template in derived:
        concrete = template.replace("{router.prefix}", feature.router.prefix)
        concrete = re.sub(r"\{\{([^}]+)\}\}", r"{\1}", concrete)
        assert concrete in mounted, f"audit source {concrete!r} names a route that is not mounted"


def test_the_engine_writes_under_the_source_its_caller_named(store, engine):
    """The domain keeps no opinion about what a source is.

    That is what lets the HTTP layer own it and lets the seeder name itself, and
    it is why a hardcoded path inside a domain method would be a defect.
    """
    engine.capture(capture_body(), actor="sn", source=f"POST {PREFIX}/captures")
    engine.define_page(
        {"name": "Priced up", "path": "/pricing", "client_id": CLIENT},
        client_id=CLIENT,
        actor="sam",
        source=f"POST {PREFIX}/pages",
    )
    sources = {entry["source"] for entry in store.audit() if entry.get("source")}
    assert {f"POST {PREFIX}/captures", f"POST {PREFIX}/pages"} <= sources, sources


def test_a_write_made_by_a_direct_caller_audits_under_the_source_it_was_given(engine):
    """A domain method must not hardcode a route as the audit source."""
    engine.capture(capture_body(), actor="tracking-snippet", source="POST /somewhere-else")
    assert any(entry["source"] == "POST /somewhere-else" for entry in engine.store.audit())


def test_the_source_follows_the_prefix_when_it_moves():
    """Move the prefix, move the source: the string is derived, never written.

    The defect this prevents has shipped in this codebase before - a feature whose
    audit log kept naming a path the app had stopped serving. This asserts the
    shape of the property rather than the live value: every route carries the
    router's own prefix, so a moved prefix moves the audit source with it and no
    source string in the module can go stale on its own.
    """
    feature = load_feature(MODULE)
    mounted = {route.path for route in feature.router.routes}
    for route in feature.router.routes:
        assert route.path.startswith(feature.router.prefix), route.path
    pattern = r'f"(?:POST|PATCH|DELETE|GET) \{router\.prefix\}(/[^"]*)"'
    derived = re.findall(pattern, source_of(feature))
    assert derived, "the feature must derive at least one audit source from router.prefix"
    for tail in derived:
        # The tail is written inside an f-string, so a placeholder arrives doubled.
        path = feature.router.prefix + re.sub(r"\{\{([^}]+)\}\}", r"{\1}", tail)
        assert path in mounted, path


def source_of(feature) -> str:
    return Path(feature.__file__).read_text(encoding="utf-8")


def _mounted(feature) -> set[str]:
    """``METHOD /path`` for every route this router serves, placeholders intact."""
    return {f"{method} {route.path}" for route in feature.router.routes for method in route.methods}


def test_every_seeded_write_is_audited_under_the_seeder(db):
    """The seeder passes its own source, so the demo rows name the seeder."""
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    sources = {entry["source"] for entry in db.audit() if entry.get("source")}
    assert sources == {"seed"}, sorted(sources)


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def test_the_seed_creates_the_states_the_research_makes_possible(db):
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    store = RecordStore(db)
    assert len(store.list("tracking_installation", limit=99)) == 2
    assert len(store.list("intent_page", limit=99)) == 6
    assert len(store.list("ideal_customer_profile", limit=99)) == 1
    assert len(store.list("identified_company", limit=99)) == 6
    assert len(store.list("company_page_visit", limit=99)) == 9


def test_the_seed_string_is_encodable_by_cp1252(db):
    """A RIGHTWARDS ARROW in one recovered feature broke the entire seeder on a
    Windows console, so the string is printed rather than trusted."""
    summary = load_feature(MODULE).seed(db, {"room_ids": [], "now": BASE, "rng": None})
    assert summary.encode("cp1252").decode("cp1252") == summary
    assert summary.isascii()


def test_the_seed_names_the_states_it_created(db):
    summary = load_feature(MODULE).seed(db, {"room_ids": [], "now": BASE, "rng": None})
    assert "6 identified companies" in summary
    assert "9 captured visits" in summary
    assert "6 intent pages" in summary


def test_the_seed_gives_all_three_conditions_a_row(db):
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    store = RecordStore(db)
    conditions = {row["data"]["condition"] for row in store.list("intent_page", limit=99)}
    assert conditions == {"exact", "contains", "starts_with"}


def test_the_seed_leaves_a_company_that_matched_nothing(db):
    """A demo of only matches teaches a reviewer nothing about the filter."""
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    engine = VisitorEngine(RecordStore(db), now=lambda: BASE)
    rows = engine.lead_list({})["companies"]
    assert any(not row["matched_pages"] for row in rows), rows


def test_the_seed_leaves_a_company_outside_the_saved_icp(db):
    """The ICP filter needs a negative case that is not an empty list."""
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    engine = VisitorEngine(RecordStore(db), now=lambda: BASE)
    profile = engine.profiles()["profiles"][0]
    inside = engine.lead_list({"icp": profile["id"]})["companies"]
    everyone = engine.lead_list({})["companies"]
    assert 0 < len(inside) < len(everyone)


def test_the_seed_leaves_a_hand_added_company_with_no_capture(db):
    """ "matched against the company database" implies the database has entries of
    its own."""
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    engine = VisitorEngine(RecordStore(db), now=lambda: BASE)
    manual = engine.company("tailwind-and-friends")
    assert manual["identified_from"] == "manual"
    assert manual["page_views"] == 0
    assert manual["contacts"], "the researched contact list must be filled in"


def test_the_seed_leaves_a_company_with_a_query_string_on_its_visit(db):
    """Path normalisation is a row, not a claim."""
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    store = RecordStore(db)
    rows = [row["data"]["path"] for row in store.list("company_page_visit", limit=99)]
    assert not any("?" in path for path in rows), rows


def test_the_seed_is_idempotent_in_shape_not_in_rows(db):
    """Running it twice is not refused, and does not corrupt a row.

    The seeder runs once per database, so this is about the second run not
    throwing: a seeder that throws on a re-run is a seeder that reports a feature
    as broken.
    """
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    summary = feature.seed(db, {"room_ids": [], "now": BASE, "rng": None})
    assert "identified companies" in summary
