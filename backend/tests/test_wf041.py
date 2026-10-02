"""Tests for WF-041: detect and block duplicate records during sync.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-041.md``. Nothing here is a
preference of this build unless it is listed in
:mod:`dsr.dedupe.inferences`, and every inference in that registry has a test
that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* The ``Duplicate Rule Header`` and its three fields, whose defaults are all
  false, and which apply to "the record that is being created, updated, or
  upserted" (headers page).
* ``allowSave`` as "allow the user to acknowledge the alert and save the
  duplicate record" - which is exactly user_flow step 4(c).
* ``includeRecordDetails`` as "return all fields in the duplicate record" - which
  is why a decision only stores payloads when the header asked for them.
* ``runAsCurrentUser`` as "use the current user's sharing rules".
* ``300`` when an external ID matches more than one record, and "no records are
  created or updated" - so it is a hard block and not a policy option.
* "The ``Unique`` attribute prevents the creation of duplicates", so a unique
  index outranks a permissive ``allow`` policy.
* HubSpot's "primary unique identifier" email, and domain as an additional
  identifier for companies.
* Dataverse's alternate keys enforcing uniqueness.
* The three-step user flow and the per-connection policy enum from
  extensibility, including the documented merge escalation.

Three bugs these tests were written to catch, each of which shipped during the
build and would not have been visible without a behavioural test:

* Two registered matchers finding the *same* record was counted as a multi-match,
  turning a clean block into a hard block. Match counts and record counts are
  not the same thing.
* Two *keys* matching the *same* record was treated as ambiguous. It is the
  opposite: it is a stronger match.
* The in-room pre-check answered from rows belonging to other rooms, because the
  CRM's keyed lookup was not restricted to the room's own scope.

The HTTP half runs against the real app over a temporary database, the way
``test_features.py`` does, and asserts that every ``source`` recorded in the
audit log names a route the host actually mounted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.dedupe import (
    BLOCKED,
    CONNECTION_COLLECTION,
    CREATED,
    CREATED_DUPLICATE,
    DECISION_COLLECTION,
    DEFAULT_POLICY,
    DEFAULT_UNIQUE_KEYS,
    DEFAULT_VENDOR,
    DUPLICATE_RULE_API_VERSION,
    DUPLICATE_RULE_HEADER,
    DUPLICATE_RULE_HEADER_DEFAULTS,
    DUPLICATE_RULE_HEADER_NAME,
    ESCALATED,
    HARD_BLOCKED,
    MATCH_KEYS,
    MULTIPLE_MATCH_STATUS,
    OUTCOMES,
    POLICIES,
    RECORD_COLLECTION,
    ROOM_ANNOTATION_LIMIT,
    UPDATED,
    VENDORS,
    DedupeEngine,
    DedupeError,
    Match,
    Matcher,
    MatcherRegistry,
    build_duplicate_rule_header,
    catalogue,
    clean,
    decide,
    decisive,
    header_defaults,
    inferences as dedupe_inferences,
    key_spec,
    match_rows,
    matched,
    multiple,
    normalise_connection,
    normalise_domain,
    normalise_email,
    normalise_exact,
    published_vocabulary,
    require_keys,
    require_policy,
    require_result,
    require_vendor,
    serialise_duplicate_rule_header,
)
from dsr.dedupe.matching import EXACT_SCORE, SCOPE_ROW
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-041"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/ingest"

MODULE = "wf041_detect_and_block_duplicate_records_dur"
FEATURE_ID = "wf-041-detect-and-block-duplicate-records-dur"

ALL_KEYS = [entry["key"] for entry in MATCH_KEYS]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db():
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured. No test
    # in this file reads the audit mirror off the filesystem, so the file bought nothing.
    database = AuditedDatabase()
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def clock():
    moment = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    return lambda: moment


@pytest.fixture()
def engine(store, clock):
    return DedupeEngine(store, clock=clock)


@pytest.fixture()
def room(store):
    return store.create(
        "room", {"name": "Northwind — Enterprise Evaluation", "account": "Northwind"}, actor="dana"
    )


@pytest.fixture()
def other_room(store):
    return store.create(
        "room", {"name": "Contoso — Security Review", "account": "Contoso"}, actor="sam"
    )


def connection(engine, name="SF", **overrides):
    spec = {"name": name} | overrides
    return engine.create_connection(spec, actor="dana", source=SOURCE)


def add_row(engine, *, room_id=None, **fields):
    """Register a row the rules will match against.

    ``room_id`` is lifted out of the payload deliberately: it is a keyword on
    ``create_record``, so leaving it in the fields would quietly create an
    *unscoped* row and make the in-room pre-check tests pass for the wrong
    reason.
    """
    return engine.create_record(fields, room_id=room_id, actor="dana", source=SOURCE)


def crm_rows(store):
    """How many rows a duplicate rule could match against."""
    return len(store.list(RECORD_COLLECTION))


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory):
    """One application for the module. A fresh database for each test.

    The lifespan in ``dsr/api.py`` only assigns ``app.state.db`` and
    ``app.state.store``, and ``dsr/deps.py`` reads ``app.state.store`` on every
    request. A test therefore needs a fresh *database*, not a fresh
    *application*. Entering a TestClient costs 46 ms measured; swapping the two
    attributes costs about 1.25 ms.

    The environment is patched here rather than per test because a module-scoped
    fixture cannot use the function-scoped ``monkeypatch``. It is undone on the
    way out so it reaches no other module. ``DSR_DB_PATH`` is ``:memory:`` so the
    lifespan's own database costs nothing either.
    """
    scratch = tmp_path_factory.mktemp("wf041-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def http(_shared_client):
    """The shared application, over a database this test owns alone.

    ``dependency_overrides`` is cleared on the way in and on the way out: the
    application is module-scoped, so an override one test installs would
    otherwise still be installed for the next one.
    """
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    try:
        yield _shared_client
    finally:
        _shared_client.app.dependency_overrides.clear()
        db.close()


def mounted_routes(client, feature_id=FEATURE_ID):
    """Every (method, path) the host mounted for one feature, templates intact."""
    entry = next(
        (f for f in client.get("/api/features").json()["features"] if f["id"] == feature_id), None
    )
    assert entry is not None, f"{feature_id} is not mounted"
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def all_served_routes(client):
    """Every (method, path) the running app serves, core routes included.

    The audit log is shared, so a check of what it records has to be allowed to
    see a core write as well as a feature one.
    """
    routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, getattr(route, "path", "")))
    for feature in client.get("/api/features").json()["features"]:
        for route in feature["routes"]:
            for method in route["methods"]:
                routes.add((method, route["path"]))
    return routes


def source_names_a_mounted_route(source, routes):
    """Does ``"POST /api/wf-041/rooms/abc/ingest"`` name a route that exists?

    A recorded source carries concrete ids; a mounted path carries FastAPI's
    ``{param}`` placeholders. The pattern is built from the *template* and matched
    against the source, with each placeholder as one wildcard segment, and the
    literal parts escaped so a segment that happens to contain a regex
    metacharacter cannot make the pattern match something else.
    """
    method, _, path = source.partition(" ")
    for mounted_method, template in routes:
        if mounted_method != method:
            continue
        parts = re.split(r"(\{[^}]+\})", template)
        pattern = (
            "^"
            + "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
            + "$"
        )
        if re.match(pattern, path):
            return True
    return False


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-041"
    assert entry["exception_handlers"] == ["DedupeError"]
    assert entry["routes"]


def test_feature_is_not_reported_as_failed(http):
    """A feature that fails to import is reported and skipped; this one must not."""
    body = http.get("/api/features").json()
    assert FEATURE_ID not in {f["id"] for f in body["failed"]}
    assert any("route collision" in f["error"] for f in body["failed"]) is False


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    body = http.get("/api/features").json()
    served = {
        (method, route["path"])
        for feature in body["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}

    assert mine
    assert not mine & others


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert load_feature(MODULE).FEATURE["id"] in text
    assert f"id: {FEATURE_ID!r}" in text


def test_room_scoped_paths_are_room_scoped(http):
    """Step 5 puts the decision on the room, so those routes take a room id."""
    routes = {path for _, path in mounted_routes(http)}
    assert "/api/wf-041/rooms/{room_id}/ingest" in routes
    assert "/api/wf-041/rooms/{room_id}/check" in routes
    assert "/api/wf-041/rooms/{room_id}/dedupe" in routes
    assert "/api/wf-041/rooms/{room_id}/decisions" in routes


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


def test_the_header_has_exactly_the_three_researched_fields():
    """allowSave, includeRecordDetails, runAsCurrentUser - nothing else."""
    assert DUPLICATE_RULE_HEADER == ("allowSave", "includeRecordDetails", "runAsCurrentUser")
    assert DUPLICATE_RULE_HEADER_NAME == "Sforce-Duplicate-Rule-Header"


def test_every_header_field_defaults_to_false():
    """ "The default value for all fields is `false`." """
    assert DUPLICATE_RULE_HEADER_DEFAULTS == {
        "allowSave": False,
        "includeRecordDetails": False,
        "runAsCurrentUser": False,
    }


def test_the_header_is_available_from_api_version_52():
    """ "This header is available in API version 52.0 and later." """
    assert DUPLICATE_RULE_API_VERSION == 52.0


def test_the_four_policies_are_the_ones_the_research_names():
    """Step 4 gives block/update/allow; extensibility adds merge."""
    assert POLICIES == ("block", "update", "allow", "merge")


def test_the_six_outcomes_are_published():
    assert OUTCOMES == (CREATED, UPDATED, BLOCKED, CREATED_DUPLICATE, HARD_BLOCKED, ESCALATED)


def test_the_four_matching_keys_are_the_ones_data_sources_names():
    """email, domain, external ID, account number."""
    assert ALL_KEYS == ["external_id", "email", "account_number", "domain"]


def test_the_three_vendors_are_the_ones_the_research_cites():
    assert VENDORS == ("salesforce", "hubspot", "dataverse")


def test_the_300_is_the_only_status_the_research_numbers():
    assert MULTIPLE_MATCH_STATUS == 300


def test_email_is_the_only_key_unique_by_default():
    """Email is the one key the research calls the primary unique identifier."""
    assert DEFAULT_UNIQUE_KEYS == ("email",)
    assert [entry["key"] for entry in MATCH_KEYS if entry["unique_by_default"]] == ["email"]


def test_each_matching_key_carries_its_sourced_justification():
    """A key with no provenance is a key nobody can argue with."""
    for entry in MATCH_KEYS:
        assert entry["sourced_from"], f"{entry['key']} has no sourced_from"
        assert entry["label"]


def test_vocabulary_serves_every_published_term():
    served = published_vocabulary()
    assert served["policies"] == list(POLICIES)
    assert served["outcomes"] == list(OUTCOMES)
    assert served["vendors"] == list(VENDORS)
    assert served["duplicate_rule_header"] == list(DUPLICATE_RULE_HEADER)
    assert served["multiple_match_status"] == 300
    assert served["key_precedence"] == ALL_KEYS
    assert len(served["match_keys"]) == 4


def test_vocabulary_explains_each_vendor_mechanism():
    """An administrator debugging a duplicate needs to know which page to open."""
    served = published_vocabulary()
    for vendor in VENDORS:
        assert served["vendor_mechanisms"][vendor]
        assert served["vendor_features"][vendor]


def test_vocabulary_does_not_serialise_a_normaliser():
    """The picker needs the key's meaning, not a callable it cannot send."""
    for entry in published_vocabulary()["match_keys"]:
        assert "normalise" not in entry


def test_the_vocabulary_function_does_not_shadow_its_own_submodule():
    """`from dsr.dedupe import vocabulary` must give the module, not a function.

    A package ``__init__`` that exports a function named after one of its own
    submodules silently breaks every ``from ... import <submodule>`` in the
    codebase. This build hit exactly that, so it is now a test.
    """
    import dsr.dedupe.vocabulary as module

    assert module.__name__.endswith("dedupe.vocabulary")
    assert callable(module.published_vocabulary)
    assert not callable(module)


# --------------------------------------------------------------------------- #
# The header, per policy
# --------------------------------------------------------------------------- #


def test_allow_sets_allowsave_and_nothing_else():
    """Step 4(c) is the header's own allowSave description, word for word."""
    assert build_duplicate_rule_header("allow") == {"allowSave": True}


@pytest.mark.parametrize("policy", ["block", "update", "merge"])
def test_every_other_policy_asks_for_the_duplicate_record_details(policy):
    """(a) shows the record and (b) updates it, so both need its fields."""
    assert build_duplicate_rule_header(policy) == {"includeRecordDetails": True}


def test_run_as_current_user_is_a_connection_setting_not_a_policy_one():
    """ "use the current user's sharing rules" - visibility, not the decision."""
    assert build_duplicate_rule_header("block", run_as_current_user=True) == {
        "includeRecordDetails": True,
        "runAsCurrentUser": True,
    }
    assert build_duplicate_rule_header("allow", run_as_current_user=True) == {
        "allowSave": True,
        "runAsCurrentUser": True,
    }


def test_a_false_field_is_omitted_rather_than_sent_as_false():
    """The researched default for all three fields is already false."""
    header = build_duplicate_rule_header("allow", run_as_current_user=False)
    assert "runAsCurrentUser" not in header
    assert all(header[name] is True for name in header)


def test_the_serialised_header_names_only_what_is_true():
    wire = serialise_duplicate_rule_header({"allowSave": True, "includeRecordDetails": False})
    assert wire == '{"allowSave": "true"}'


def test_an_unknown_policy_cannot_build_a_header():
    with pytest.raises(DedupeError):
        build_duplicate_rule_header("auto_merge")


def test_header_defaults_is_a_copy():
    """A caller mutating the published defaults must not change the source."""
    published = header_defaults()
    published["allowSave"] = True
    assert header_defaults()["allowSave"] is False


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  PRIYA@Example.com ", "priya@example.com"),
        ("Priya@Example.COM", "priya@example.com"),
        (None, ""),
    ],
)
def test_email_normalisation_is_case_and_whitespace_insensitive(raw, expected):
    assert normalise_email(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("WWW.Example.com", "example.com"),
        ("www.example.com.", "example.com"),
        ("Example.COM.", "example.com"),
        ("example.com", "example.com"),
        (None, ""),
    ],
)
def test_domain_normalisation_drops_www_and_a_trailing_dot(raw, expected):
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("  ext-1 ", "ext-1"), ("EXT-1", "EXT-1"), (None, "")],
)
def test_opaque_codes_are_trimmed_but_not_case_folded(raw, expected):
    """Case-folding an external ID would merge values a CRM keeps distinct."""
    assert normalise_exact(raw) == expected


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def test_an_exact_email_match_is_found_case_insensitively():
    rows = [{"id": "a", "email": "Priya@Example.com"}]
    found = match_rows({"email": "  priya@example.COM"}, rows, ["email"])
    assert [(m.key, m.record_id, m.score) for m in found] == [("email", "a", EXACT_SCORE)]


def test_a_different_email_does_not_match():
    assert (
        match_rows(
            {"email": "other@example.com"}, [{"id": "a", "email": "a@example.com"}], ["email"]
        )
        == []
    )


def test_a_missing_field_is_not_a_zero_score_match():
    """A row with no email is not a match on email; it is no comparison at all."""
    assert match_rows({"email": "a@example.com"}, [{"id": "a"}], ["email"]) == []
    assert match_rows({"email": "a@example.com"}, [{"id": "a", "email": None}], ["email"]) == []


def test_a_row_with_no_id_cannot_be_a_duplicate():
    """There would be nothing to log as "the matching record id"."""
    assert match_rows({"email": "a@example.com"}, [{"email": "a@example.com"}], ["email"]) == []


def test_the_fuzzy_matcher_scores_a_near_name_at_the_same_domain():
    registry = MatcherRegistry()
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    found = match_rows(
        {"name": "Jose A. Ramirez", "domain": "northwind.example"}, rows, ["domain"], registry
    )
    fuzzy = [m for m in found if m.matcher == "fuzzy:domain_name"]
    assert fuzzy and fuzzy[0].record_id == "a"
    assert fuzzy[0].score < 1.0


def test_the_fuzzy_matcher_refuses_a_different_domain():
    """A domain alone is never enough: one company has hundreds of people."""
    registry = MatcherRegistry()
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    found = match_rows(
        {"name": "Jose Ramirez", "domain": "contoso.example"}, rows, ["domain"], registry
    )
    assert [m for m in found if m.matcher == "fuzzy:domain_name"] == []


def test_the_fuzzy_matcher_refuses_two_different_people_at_one_domain():
    registry = MatcherRegistry()
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    found = match_rows(
        {"name": "Dana Okoro", "domain": "northwind.example"}, rows, ["domain"], registry
    )
    assert [m for m in found if m.matcher == "fuzzy:domain_name"] == []


def test_the_fuzzy_matcher_is_registered_by_default_because_the_research_names_it():
    """A registration seam nothing uses is a seam nothing tests."""
    ids = {matcher.id for matcher in MatcherRegistry().all()}
    assert "fuzzy:domain_name" in ids
    assert "exact:email" in ids


def test_two_matchers_finding_one_record_produce_two_matches_for_one_record():
    """The caller collapses these by record id; a multi-match means several records.

    The name needs a middle initial so the fuzzy matcher has a reason to fire -
    with an identical name it declines, leaving only the exact matcher.
    """
    rows = [{"id": "a", "name": "Tomas Vela", "domain": "contoso.example"}]
    found = match_rows({"name": "Tomas A. Vela", "domain": "contoso.example"}, rows, ["domain"])
    assert {m.record_id for m in found} == {"a"}
    assert {m.matcher for m in found} == {"exact:domain", "fuzzy:domain_name"}


def test_a_connection_can_require_exact_matches_and_silence_the_fuzzy_matcher():
    """min_score 1.0 is the one value a fuzzy scorer can never reach.

    Which only works because the fuzzy score is capped below 1.0. A fuzzy matcher
    that reported full containment as 1.0 would be indistinguishable from an exact
    key match, and this knob would silently do nothing.
    """
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    inbound = {"name": "Jose A. Ramirez", "domain": "northwind.example"}
    assert [
        m
        for m in match_rows(inbound, rows, ["domain"], min_score=1.0)
        if m.matcher == "fuzzy:domain_name"
    ] == []


def test_the_fuzzy_matcher_never_scores_a_perfect_one():
    """Full name containment is not certainty, and must not look like it."""
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    inbound = {"name": "Jose A. Ramirez", "domain": "northwind.example"}
    fuzzy = [m for m in match_rows(inbound, rows, ["domain"]) if m.matcher == "fuzzy:domain_name"]
    assert fuzzy and fuzzy[0].score < EXACT_SCORE


def test_the_fuzzy_matcher_leaves_an_identical_name_to_the_exact_matcher():
    """Same token count means the same name, which is the exact matcher's job."""
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    inbound = {"name": "Jose Ramirez", "domain": "northwind.example"}
    assert [
        m for m in match_rows(inbound, rows, ["domain"]) if m.matcher == "fuzzy:domain_name"
    ] == []


def test_a_one_token_name_is_refused_by_the_fuzzy_matcher():
    """A surname is not a person, and would match everyone who shares it."""
    rows = [{"id": "a", "name": "Jose Ramirez", "domain": "northwind.example"}]
    inbound = {"name": "Jose", "domain": "northwind.example"}
    assert [
        m for m in match_rows(inbound, rows, ["domain"]) if m.matcher == "fuzzy:domain_name"
    ] == []


def test_an_exact_match_survives_the_exact_threshold():
    rows = [{"id": "a", "email": "a@example.com"}]
    assert match_rows({"email": "A@Example.com"}, rows, ["email"], min_score=1.0)


def test_a_third_party_can_register_a_matcher():
    """The research's extensibility note, exercised rather than described."""
    registry = MatcherRegistry()
    registry.register(
        Matcher(
            id="custom:account_suffix",
            label="Account number suffix",
            key="account_number",
            compare=lambda a, b: 1.0 if str(a or "")[-4:] == str(b or "")[-4:] else None,
            threshold=1.0,
        )
    )
    found = match_rows(
        {"account_number": "X-4471"},
        [{"id": "a", "account_number": "NW-4471"}],
        ["account_number"],
        registry,
    )
    assert [(m.matcher, m.record_id) for m in found] == [("custom:account_suffix", "a")]


def test_registering_the_same_id_replaces_the_matcher():
    registry = MatcherRegistry()
    before = registry.get("exact:email")
    registry.register(
        Matcher(id="exact:email", label="never", key="email", compare=lambda a, b: 0.0)
    )
    assert registry.get("exact:email") is not before
    assert len([m for m in registry.all() if m.id == "exact:email"]) == 1


def test_a_matcher_that_raises_does_not_break_a_decision():
    """A third party's bug must not take the product's decision down with it."""

    def explode(_a, _b):
        raise RuntimeError("third-party matcher bug")

    registry = MatcherRegistry()
    registry.register(Matcher(id="bad", label="bad", key="email", compare=explode))
    found = match_rows(
        {"email": "a@example.com"}, [{"id": "a", "email": "a@example.com"}], ["email"], registry
    )
    assert any(m.matcher == "exact:email" for m in found)


def test_a_matcher_returning_a_score_over_one_is_clamped():
    registry = MatcherRegistry()
    registry.register(
        Matcher(id="loud", label="loud", key="email", compare=lambda a, b: 4.2, threshold=1.0)
    )
    found = match_rows(
        {"email": "a@example.com"}, [{"id": "a", "email": "a@example.com"}], ["email"], registry
    )
    assert [m.score for m in found if m.matcher == "loud"] == [1.0]


def test_the_registry_refuses_a_matcher_for_an_unknown_key():
    with pytest.raises(DedupeError):
        MatcherRegistry().register(
            Matcher(id="x", label="x", key="phone", compare=lambda a, b: 1.0)
        )


def test_the_registry_refuses_an_impossible_threshold():
    with pytest.raises(DedupeError):
        MatcherRegistry().register(
            Matcher(id="x", label="x", key="email", compare=lambda a, b: 1.0, threshold=1.5)
        )


def test_the_registry_refuses_a_matcher_with_no_id():
    with pytest.raises(DedupeError):
        MatcherRegistry().register(Matcher(id="", label="x", key="email", compare=lambda a, b: 1.0))


def test_the_registry_refuses_something_that_is_not_a_matcher():
    with pytest.raises(DedupeError):
        MatcherRegistry().register("exact:email")


def test_unregistering_a_matcher_removes_it():
    registry = MatcherRegistry()
    registry.unregister("fuzzy:domain_name")
    assert "fuzzy:domain_name" not in {m.id for m in registry.all()}


def test_resetting_the_registry_restores_the_built_ins():
    registry = MatcherRegistry()
    registry.unregister("fuzzy:domain_name")
    registry.reset()
    assert "fuzzy:domain_name" in {m.id for m in registry.all()}


def test_the_registry_is_ordered_so_two_runs_agree():
    """A decision has to be reproducible to be arguable."""
    assert [m.id for m in MatcherRegistry().all()] == [m.id for m in MatcherRegistry().all()]


def test_the_row_scope_matcher_is_declared_as_such():
    matcher = MatcherRegistry().get("fuzzy:domain_name")
    assert matcher.scope == SCOPE_ROW
    assert MatcherRegistry().get("exact:email").scope != SCOPE_ROW


# --------------------------------------------------------------------------- #
# Which key decides
# --------------------------------------------------------------------------- #


def match(key, record_id, value="v", score=1.0):
    return Match(key=key, value=value, record_id=record_id, score=score, matcher=f"exact:{key}")


def test_no_matches_is_a_clean_create():
    assert decisive([], ALL_KEYS) == (None, [])


def test_one_key_one_record_names_that_record():
    key, subset = decisive([match("email", "a")], ALL_KEYS)
    assert key == "email"
    assert [m.record_id for m in subset] == ["a"]


def test_two_keys_on_the_same_record_is_a_stronger_match_not_an_ambiguous_one():
    """A lead whose email and domain both point at one contact is certainly known."""
    key, subset = decisive([match("email", "a"), match("domain", "a")], ALL_KEYS)
    assert key == "email"
    assert {m.record_id for m in subset} == {"a"}


def test_one_key_two_records_keeps_that_key():
    key, subset = decisive([match("email", "a"), match("email", "b")], ALL_KEYS)
    assert key == "email"
    assert {m.record_id for m in subset} == {"a", "b"}


def test_two_keys_on_two_records_is_ambiguous():
    """No single "matching record id" exists, so there is nothing to act on."""
    key, subset = decisive([match("email", "a"), match("external_id", "b")], ALL_KEYS)
    assert key is None
    assert {m.record_id for m in subset} == {"a", "b"}


def test_the_highest_precedence_key_is_reported_for_one_record():
    """external_id is a value the connector wrote, so it outranks a domain."""
    key, _ = decisive(
        [match("domain", "a"), match("email", "a"), match("external_id", "a")], ALL_KEYS
    )
    assert key == "external_id"


# --------------------------------------------------------------------------- #
# The state machine
# --------------------------------------------------------------------------- #


def row(record_id="a", **fields):
    return {"id": record_id, **fields}


def test_a_clean_result_creates_under_every_policy():
    for policy in POLICIES:
        assert decide(clean(), policy).outcome == CREATED


def test_a_clean_result_names_no_match_and_asks_for_nothing_that_is_false():
    decision = decide(clean(), "block")
    assert decision.match_key is None
    assert decision.matched_ids == []
    assert decision.header == {"includeRecordDetails": True}


def test_block_refuses_the_write_and_keeps_the_existing_record_id():
    """Step 4(a): "blocks the write and shows the existing record"."""
    decision = decide(matched([row("a", email="x@y")], match_key="email"), "block")
    assert decision.outcome == BLOCKED
    assert decision.matched_ids == ["a"]
    assert decision.hard_block is False
    assert decision.needs_human is False


def test_block_asks_for_the_duplicate_record_fields_so_it_can_show_them():
    decision = decide(matched([row("a", email="x@y")], match_key="email"), "block")
    assert decision.header == {"includeRecordDetails": True}
    assert decision.matched[0]["email"] == "x@y"


def test_update_reaches_the_updated_outcome():
    """Step 4(b): "updates the existing record instead"."""
    decision = decide(matched([row("a")], match_key="email"), "update")
    assert decision.outcome == UPDATED
    assert decision.matched_ids == ["a"]


def test_allow_creates_the_duplicate_and_acknowledges_it():
    """Step 4(c), and the header field of the same name."""
    decision = decide(matched([row("a")], match_key="email"), "allow")
    assert decision.outcome == CREATED_DUPLICATE
    assert decision.acknowledged is True
    assert decision.header == {"allowSave": True}


def test_allow_does_not_store_the_payloads_it_did_not_request():
    """includeRecordDetails is off for allow, so there is nothing to store."""
    decision = decide(matched([row("a", email="x@y")], match_key="email"), "allow")
    assert decision.matched == []
    assert decision.matched_ids == ["a"]


def test_a_unique_index_refuses_the_permissive_policy():
    """ "The Unique attribute prevents the creation of duplicates." """
    decision = decide(matched([row("a")], match_key="email"), "allow", unique_keys=["email"])
    assert decision.outcome == HARD_BLOCKED
    assert decision.hard_block is True
    assert "unique index" in decision.reason


def test_a_unique_index_on_another_key_does_not_refuse_this_match():
    decision = decide(matched([row("a")], match_key="email"), "allow", unique_keys=["external_id"])
    assert decision.outcome == CREATED_DUPLICATE


def test_merge_escalates_and_claims_no_action():
    """The research's gaps: the merge action is *not* claimed."""
    decision = decide(matched([row("a")], match_key="email"), "merge")
    assert decision.outcome == ESCALATED
    assert decision.needs_human is True
    assert "not claimed" in decision.detail


def test_merge_asks_for_both_sides_because_a_human_has_to_look():
    decision = decide(matched([row("a", email="x@y")], match_key="email"), "merge")
    assert decision.header == {"includeRecordDetails": True}
    assert decision.matched[0]["email"] == "x@y"


def test_a_multi_match_is_a_300_hard_block_on_an_external_id():
    """The researched status, and "no records are created or updated"."""
    result = multiple([row("a"), row("b")], match_key="external_id")
    decision = decide(result, "block")
    assert decision.outcome == HARD_BLOCKED
    assert decision.status == MULTIPLE_MATCH_STATUS == 300
    assert decision.hard_block is True
    assert decision.needs_human is True


def test_a_multi_match_hard_blocks_under_every_policy():
    """A hard block is not a policy option, so the policy is never consulted."""
    result = multiple([row("a"), row("b")], match_key="external_id")
    for policy in POLICIES:
        assert decide(result, policy).outcome == HARD_BLOCKED


def test_a_multi_match_on_a_non_external_key_gets_no_invented_status():
    """The research numbers 300 for the external ID only."""
    decision = decide(multiple([row("a"), row("b")], match_key="email"), "block")
    assert decision.outcome == HARD_BLOCKED
    assert decision.status is None


def test_an_ambiguous_result_is_a_hard_block_with_no_status():
    decision = decide(multiple([row("a"), row("b")], match_key=None), "block")
    assert decision.outcome == HARD_BLOCKED
    assert decision.match_key is None
    assert decision.status is None


def test_needing_a_human_is_the_two_states_that_wrote_nothing_unsafely():
    decision = decide(matched([row("a")], match_key="email"), "merge")
    assert decision.needs_human is True
    assert decide(matched([row("a")], match_key="email"), "block").needs_human is False


def test_every_decision_records_the_header_it_would_send():
    decision = decide(matched([row("a")], match_key="email"), "update")
    assert decision.to_dict()["header_wire"] == '{"includeRecordDetails": "true"}'
    assert decision.to_dict()["header_name"] == DUPLICATE_RULE_HEADER_NAME


def test_a_result_with_no_ids_reports_nothing_as_matched():
    """A payload with no record id cannot be logged as a matching record id."""
    assert matched([{"email": "x@y"}], match_key="email").matched_ids == ()


# --------------------------------------------------------------------------- #
# Connection validation
# --------------------------------------------------------------------------- #


def test_a_connection_needs_a_name():
    with pytest.raises(DedupeError):
        normalise_connection({"policy": "block"})


def test_a_blank_name_is_not_a_name():
    with pytest.raises(DedupeError):
        normalise_connection({"name": "   "})


def test_a_connection_defaults_to_the_documented_policy_and_vendor():
    spec = normalise_connection({"name": "SF"})
    assert spec["policy"] == DEFAULT_POLICY == "block"
    assert spec["vendor"] == DEFAULT_VENDOR == "salesforce"


def test_a_connection_defaults_to_every_published_key_in_precedence_order():
    assert normalise_connection({"name": "SF"})["keys"] == ALL_KEYS


def test_a_connection_defaults_email_to_unique_and_nothing_else():
    assert normalise_connection({"name": "SF"})["unique_keys"] == ["email"]


def test_key_precedence_does_not_depend_on_the_order_a_caller_lists_them():
    """A connection must not change the answer by reordering its own config."""
    assert require_keys(["domain", "email"]) == ("email", "domain")
    assert require_keys(["email", "domain"]) == require_keys(["domain", "email"])


def test_a_requested_key_list_narrows_the_defaults():
    assert require_keys(["email"]) == ("email",)


def test_an_unknown_key_is_refused_by_name():
    with pytest.raises(DedupeError) as caught:
        require_keys(["email", "phone"])
    assert "phone" in str(caught.value)


def test_a_string_is_not_a_key_list():
    with pytest.raises(DedupeError):
        require_keys("email")


def test_a_unique_key_must_also_be_a_configured_key():
    """A key cannot be unique without being matched on."""
    with pytest.raises(DedupeError) as caught:
        normalise_connection({"name": "SF", "keys": ["email"], "unique_keys": ["external_id"]})
    assert "unique" in str(caught.value)


def test_a_unique_key_may_be_emptied_deliberately():
    """How the demo shows allow creating a duplicate at all."""
    spec = normalise_connection({"name": "SF", "keys": ["email"], "unique_keys": []})
    assert spec["unique_keys"] == []


def test_min_score_is_absent_by_default_so_matcher_thresholds_apply():
    assert normalise_connection({"name": "SF"})["min_score"] is None


def test_min_score_must_be_a_number_between_zero_and_one():
    with pytest.raises(DedupeError):
        normalise_connection({"name": "SF", "min_score": 4})
    with pytest.raises(DedupeError):
        normalise_connection({"name": "SF", "min_score": "high"})


def test_an_empty_min_score_means_absent():
    assert normalise_connection({"name": "SF", "min_score": ""})["min_score"] is None


@pytest.mark.parametrize("vendor", VENDORS)
def test_every_published_vendor_is_accepted(vendor):
    assert normalise_connection({"name": "SF", "vendor": vendor})["vendor"] == vendor


def test_an_unknown_vendor_is_refused():
    with pytest.raises(DedupeError):
        require_vendor("pipedrive")


def test_policy_and_vendor_spellings_are_normalised():
    assert require_policy(" BLOCK ") == "block"
    assert require_vendor("SalesForce") == "salesforce"


def test_an_unknown_policy_is_refused():
    with pytest.raises(DedupeError):
        require_policy("auto-merge")


def test_an_unknown_result_is_refused():
    with pytest.raises(DedupeError):
        require_result("maybe")


def test_a_key_spec_carries_its_normaliser_and_provenance():
    spec = key_spec("email")
    assert spec["normalise"]("A@B.com") == "a@b.com"
    assert "primary unique identifier" in spec["sourced_from"]


def test_an_unknown_key_spec_is_refused():
    with pytest.raises(DedupeError):
        key_spec("phone")


def test_the_policy_catalogue_marks_the_two_that_write_nothing():
    """A picker should say so before an administrator saves one."""
    served = {entry["policy"]: entry for entry in catalogue()["policies"]}
    assert served["block"]["writes"] is False
    assert served["merge"]["writes"] is False
    assert served["update"]["writes"] is True
    assert served["allow"]["writes"] is True


def test_the_policy_catalogue_cites_the_research_for_each_policy():
    for entry in catalogue()["policies"]:
        assert entry["sourced_from"]
        assert entry["summary"]


def test_the_catalogue_defaults_to_block():
    assert catalogue()["default"] == "block"


# --------------------------------------------------------------------------- #
# Connections over the engine
# --------------------------------------------------------------------------- #


def test_a_connection_is_created_with_its_resolved_shape(engine):
    record = connection(engine, "SF", policy="update", keys=["email", "domain"])
    assert record["data"]["policy"] == "update"
    assert record["data"]["keys"] == ["email", "domain"]
    assert record["collection"] == CONNECTION_COLLECTION


def test_a_connection_defaults_to_enabled_and_to_not_running_as_the_user(engine):
    record = connection(engine, "SF")
    assert record["data"]["enabled"] is True
    assert record["data"]["run_as_current_user"] is False


def test_a_bad_connection_writes_nothing(engine, store):
    with pytest.raises(DedupeError):
        engine.create_connection({"name": "SF", "policy": "nope"}, source=SOURCE)
    assert store.stats()["records"] == 0
    assert store.stats()["audit_entries"] == 0


def test_connections_filter_by_vendor_and_policy(engine):
    connection(engine, "SF", vendor="salesforce", policy="block")
    connection(engine, "HS", vendor="hubspot", policy="update")
    assert len(engine.list_connections(vendor="hubspot")) == 1
    assert len(engine.list_connections(policy="block")) == 1
    assert len(engine.list_connections()) == 2


def test_a_connection_filter_on_an_unknown_policy_is_refused_not_empty(engine):
    with pytest.raises(DedupeError):
        engine.list_connections(policy="nope")


def test_a_connection_scoped_to_a_room_is_listed_for_it(engine, room, other_room):
    connection(engine, "SF", room_id=room["id"])
    assert len(engine.list_connections(room_id=room["id"])) == 1
    assert len(engine.list_connections(room_id=other_room["id"])) == 0


def test_an_unscoped_connection_is_listed_for_every_room(engine, room, other_room):
    """A connection with no room is the general one, so it applies everywhere."""
    connection(engine, "SF")
    assert len(engine.list_connections(room_id=room["id"])) == 1
    assert len(engine.list_connections(room_id=other_room["id"])) == 1


def test_patching_a_connection_revalidates_against_the_merged_result(engine):
    record = connection(engine, "SF", keys=["email"], unique_keys=["email"])
    with pytest.raises(DedupeError):
        engine.update_connection(record["id"], {"keys": ["domain"]}, source=SOURCE)


def test_patching_a_connection_changes_only_what_was_asked(engine):
    record = connection(engine, "SF", keys=["email", "domain"])
    patched = engine.update_connection(record["id"], {"policy": "merge"}, source=SOURCE)
    assert patched["data"]["policy"] == "merge"
    assert patched["data"]["keys"] == ["email", "domain"]


def test_patching_a_connection_keeps_its_unchanged_flags(engine):
    record = connection(engine, "SF", run_as_current_user=True, enabled=False)
    patched = engine.update_connection(record["id"], {"policy": "update"}, source=SOURCE)
    assert patched["data"]["run_as_current_user"] is True
    assert patched["data"]["enabled"] is False


def test_deleting_a_connection_is_soft_so_its_history_survives(engine, store):
    record = connection(engine, "SF")
    engine.delete_connection(record["id"], source=SOURCE)
    assert engine.get_connection(record["id"]) is None
    assert store.db.get(record["id"], include_deleted=True) is not None


def test_deleting_a_connection_that_does_not_exist_is_refused(engine):
    with pytest.raises(DedupeError):
        engine.delete_connection("nope", source=SOURCE)


def test_the_default_connection_is_resolved_not_stored(engine, store):
    resolved = engine.resolve_connection(None)
    assert resolved["id"] is None
    assert resolved["data"]["policy"] == DEFAULT_POLICY
    assert store.list(CONNECTION_COLLECTION) == []


def test_a_disabled_connection_refuses_the_write_rather_than_skipping_the_check(engine, room):
    """Evaluating with the rules off would write rows no rule had checked."""
    record = connection(engine, "SF", enabled=False)
    with pytest.raises(DedupeError) as caught:
        engine.ingest(
            room["id"], {"email": "a@b.example"}, connection_id=record["id"], source=SOURCE
        )
    assert "disabled" in str(caught.value)


# --------------------------------------------------------------------------- #
# The rows a rule matches against
# --------------------------------------------------------------------------- #


def test_a_row_with_no_matching_key_cannot_be_a_duplicate(engine, store):
    with pytest.raises(DedupeError) as caught:
        add_row(engine, name="Nameless")
    assert "matching key" in str(caught.value)
    assert store.list(RECORD_COLLECTION) == []


def test_a_row_is_registered_with_its_object_type_defaulting_to_contact(engine):
    record = add_row(engine, email="a@b.example")
    assert record["data"]["object_type"] == "contact"


def test_rows_filter_by_object_type_and_room(engine, room):
    add_row(engine, email="a@b.example", object_type="contact")
    add_row(engine, domain="b.example", object_type="company", room_id=room["id"])
    assert len(engine.list_records(object_type="company")) == 1
    assert len(engine.list_records(room_id=room["id"])) == 1


def test_a_row_seeded_to_a_room_is_in_that_room_s_precheck_scope(engine, room):
    record = add_row(engine, email="a@b.example", room_id=room["id"])
    assert [row["id"] for row in engine.crm.room_rows(room["id"])] == [record["id"]]


def test_a_row_with_no_room_is_in_no_room_s_scope(engine, room):
    add_row(engine, email="a@b.example")
    assert engine.crm.room_rows(room["id"]) == []


def test_the_crm_returns_a_row_as_its_own_fields_plus_its_id(engine):
    record = add_row(engine, email="a@b.example", name="Ada")
    view = engine.crm.view(record)
    assert view["name"] == "Ada"
    assert view["id"] == record["id"]


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #


def test_a_clean_lead_creates_a_row(engine, store, room):
    decision = engine.ingest(room["id"], {"email": "new@b.example", "name": "New"}, source=SOURCE)
    assert decision["data"]["outcome"] == CREATED
    assert len(store.list(RECORD_COLLECTION)) == 1


def test_a_created_row_is_attributed_to_the_room_that_produced_it(engine, store, room):
    decision = engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    assert store.get(decision["data"]["write"]["record_id"])["room_id"] == room["id"]


def test_the_block_policy_writes_no_crm_row_at_all(engine, store, room):
    """A refusal that left a row behind would be counted as a write."""
    add_row(engine, email="a@b.example")
    before = crm_rows(store)
    decision = engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    assert decision["data"]["outcome"] == BLOCKED
    assert decision["data"]["write"] == {"kind": "none"}
    assert crm_rows(store) == before


def test_the_block_policy_names_the_matched_record_id(engine, room):
    existing = add_row(engine, email="a@b.example")
    decision = engine.ingest(room["id"], {"email": "A@B.example"}, source=SOURCE)
    assert decision["data"]["matched_ids"] == [existing["id"]]


def test_the_block_policy_returns_the_existing_record_so_a_rep_can_see_it(engine, room):
    add_row(engine, email="a@b.example", name="Ada", title="CTO")
    decision = engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    assert decision["data"]["matched"][0]["name"] == "Ada"


def test_the_update_policy_patches_the_matched_row_and_creates_none(engine, store, room):
    existing = add_row(engine, email="a@b.example", name="Ada")
    conn = connection(engine, "SF", policy="update", keys=["email"])
    before = crm_rows(store)
    decision = engine.ingest(
        room["id"], {"email": "a@b.example", "title": "VP"}, connection_id=conn["id"], source=SOURCE
    )
    assert decision["data"]["outcome"] == UPDATED
    assert decision["data"]["write"]["record_id"] == existing["id"]
    assert decision["data"]["write"]["fields"] == ["email", "title"]
    assert crm_rows(store) == before
    assert store.get(existing["id"])["data"]["title"] == "VP"


def test_the_update_policy_does_not_write_the_connectors_own_bookkeeping(engine, store, room):
    existing = add_row(engine, email="a@b.example")
    conn = connection(engine, "SF", policy="update", keys=["email"])
    engine.ingest(
        room["id"],
        {"email": "a@b.example", "synced_from": "spoofed", "duplicate_of": "spoofed"},
        connection_id=conn["id"],
        source=SOURCE,
    )
    assert store.get(existing["id"])["data"]["synced_from"] != "spoofed"


def test_the_update_policy_does_not_write_a_null_field(engine, store, room):
    """A form that submits an empty optional field must not blank a CRM column."""
    existing = add_row(engine, email="a@b.example", title="CTO")
    conn = connection(engine, "SF", policy="update", keys=["email"])
    engine.ingest(
        room["id"], {"email": "a@b.example", "title": None}, connection_id=conn["id"], source=SOURCE
    )
    assert store.get(existing["id"])["data"]["title"] == "CTO"


def test_the_allow_policy_creates_a_second_row_recorded_as_a_duplicate(engine, store, room):
    existing = add_row(engine, email="a@b.example")
    conn = connection(engine, "HS", policy="allow", keys=["email"], unique_keys=[])
    before = crm_rows(store)
    decision = engine.ingest(
        room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
    )
    assert decision["data"]["outcome"] == CREATED_DUPLICATE
    assert decision["data"]["acknowledged"] is True
    assert crm_rows(store) == before + 1
    created = store.get(decision["data"]["write"]["record_id"])["data"]
    assert created["duplicate_of"] == existing["id"]
    assert created["acknowledged"] is True


def test_a_unique_index_beats_the_allow_policy_and_writes_nothing(engine, store, room):
    add_row(engine, email="a@b.example")
    conn = connection(engine, "HS", policy="allow", keys=["email"], unique_keys=["email"])
    before = crm_rows(store)
    decision = engine.ingest(
        room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
    )
    assert decision["data"]["outcome"] == HARD_BLOCKED
    assert crm_rows(store) == before


def test_the_merge_policy_writes_nothing_and_asks_for_a_person(engine, store, room):
    add_row(engine, email="a@b.example")
    conn = connection(engine, "DV", policy="merge", keys=["email"])
    before = crm_rows(store)
    decision = engine.ingest(
        room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
    )
    assert decision["data"]["outcome"] == ESCALATED
    assert decision["data"]["needs_human"] is True
    assert crm_rows(store) == before


def test_one_external_id_on_two_rows_is_the_researched_300(engine, store, room):
    add_row(engine, email="a@b.example", external_id="dup")
    add_row(engine, email="c@d.example", external_id="dup")
    before = crm_rows(store)
    decision = engine.ingest(room["id"], {"external_id": "dup"}, source=SOURCE)
    assert decision["data"]["outcome"] == HARD_BLOCKED
    assert decision["data"]["status"] == 300
    assert crm_rows(store) == before


def test_the_300_names_both_matching_records(engine, room):
    first = add_row(engine, email="a@b.example", external_id="dup")
    second = add_row(engine, email="c@d.example", external_id="dup")
    decision = engine.ingest(room["id"], {"external_id": "dup"}, source=SOURCE)
    assert set(decision["data"]["matched_ids"]) == {first["id"], second["id"]}


def test_two_keys_matching_two_different_records_is_ambiguous_and_blocks(engine, store, room):
    add_row(engine, email="a@b.example", external_id="one")
    add_row(engine, email="z@b.example", external_id="two")
    decision = engine.ingest(
        room["id"], {"email": "a@b.example", "external_id": "two"}, source=SOURCE
    )
    assert decision["data"]["outcome"] == HARD_BLOCKED
    assert decision["data"]["match_key"] is None
    assert "no single matching record id" in decision["data"]["reason"]


def test_two_keys_matching_the_same_record_is_not_ambiguous(engine, room):
    existing = add_row(engine, email="a@b.example", domain="b.example")
    decision = engine.ingest(
        room["id"], {"email": "a@b.example", "domain": "b.example"}, source=SOURCE
    )
    assert decision["data"]["outcome"] == BLOCKED
    assert decision["data"]["matched_ids"] == [existing["id"]]


def test_two_matchers_agreeing_on_one_record_is_not_a_multi_match(engine, room):
    """The bug this suite exists for: a match count is not a record count."""
    add_row(engine, name="Tomas Vela", email="t@b.example", domain="b.example")
    decision = engine.ingest(
        room["id"], {"name": "Tomas Vela", "domain": "b.example"}, source=SOURCE
    )
    assert decision["data"]["outcome"] == BLOCKED
    assert len(decision["data"]["matched_ids"]) == 1


def test_a_fuzzy_hit_under_a_blocking_policy_blocks(engine, room):
    add_row(engine, name="Jose Ramirez", domain="northwind.example", email="jr@n.example")
    decision = engine.ingest(
        room["id"],
        {"name": "Jose A. Ramirez", "domain": "northwind.example", "email": "new@n.example"},
        source=SOURCE,
    )
    assert decision["data"]["outcome"] == BLOCKED
    assert decision["data"]["match_key"] == "domain"


def test_a_connection_can_demand_exact_matches_and_silence_the_fuzzy_one(engine, room):
    """min_score 1.0 silences every fuzzy matcher; the exact one still fires.

    Worth stating precisely, because it is easy to overclaim: with the built-in
    exact matchers registered, raising the floor to 1.0 cannot change *this*
    outcome, because the exact domain matcher scores 1.0 too. The knob's effect
    is on fuzzy matchers, which is what the next test demonstrates by removing
    the exact matcher and leaving the fuzzy one.
    """
    add_row(engine, name="Jose Ramirez", domain="northwind.example", email="jr@n.example")
    conn = connection(engine, "SF", keys=["domain"], min_score=1.0)
    decision = engine.ingest(
        room["id"],
        {"name": "Jose A. Ramirez", "domain": "northwind.example"},
        connection_id=conn["id"],
        source=SOURCE,
    )
    assert decision["data"]["outcome"] == BLOCKED


def test_the_fuzzy_matcher_can_be_the_only_one_that_fires(engine, room):
    """The registration seam works both ways: unregister exact, keep fuzzy."""
    add_row(engine, name="Jose Ramirez", domain="northwind.example", email="jr@n.example")
    engine.registry.unregister("exact:domain")
    try:
        conn = connection(engine, "SF", keys=["domain"])
        decision = engine.ingest(
            room["id"],
            {"name": "Jose A. Ramirez", "domain": "northwind.example"},
            connection_id=conn["id"],
            source=SOURCE,
        )
        assert decision["data"]["outcome"] == BLOCKED
    finally:
        engine.registry.reset()


# --------------------------------------------------------------------------- #
# The in-room pre-check
# --------------------------------------------------------------------------- #


def test_the_in_room_check_answers_a_match_on_this_room_s_own_rows(engine, room):
    conn = connection(engine, "SF", policy="block")
    add_row(engine, email="a@b.example", room_id=room["id"])
    engine.crm.queries.clear()
    decision = engine.ingest(
        room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
    )
    assert decision["data"]["crm_called"] is False


def test_the_in_room_check_does_not_answer_from_another_room_s_rows(engine, room, other_room):
    """A pre-check that reads other rooms' data is not an in-room pre-check."""
    conn = connection(engine, "SF", policy="block")
    add_row(engine, email="a@b.example", room_id=other_room["id"])
    decision = engine.ingest(
        room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
    )
    assert decision["data"]["crm_called"] is True
    assert decision["data"]["outcome"] == BLOCKED


def test_the_in_room_check_never_short_circuits_a_non_blocking_policy(engine, room):
    """Only a policy that would refuse can be satisfied by a local refusal."""
    add_row(engine, email="a@b.example", room_id=room["id"])
    for policy in ("update", "allow", "merge"):
        conn = connection(engine, f"c-{policy}", policy=policy, keys=["email"], unique_keys=[])
        decision = engine.ingest(
            room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
        )
        assert decision["data"]["crm_called"] is True, policy


def test_the_in_room_check_falls_through_when_it_finds_nothing(engine, room):
    add_row(engine, email="other@b.example", room_id=room["id"])
    decision = engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    assert decision["data"]["crm_called"] is True
    assert decision["data"]["outcome"] == CREATED


# --------------------------------------------------------------------------- #
# Step 5: the room row annotation
# --------------------------------------------------------------------------- #


def test_the_decision_and_matched_record_id_are_logged_on_the_room_row(engine, store, room):
    """Step 5: "The decision and the matched record id are logged on the room row"."""
    existing = add_row(engine, email="a@b.example")
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    annotation = store.get(room["id"])["data"]["dedupe"]
    assert annotation["last_outcome"] == BLOCKED
    assert annotation["last_match_record_id"] == existing["id"]
    assert annotation["last_match_key"] == "email"


def test_the_annotation_names_the_decision_it_came_from(engine, store, room):
    decision = engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    assert store.get(room["id"])["data"]["dedupe"]["last_decision_id"] == decision["id"]


def test_the_annotation_keeps_a_bounded_history(engine, store, room):
    for index in range(ROOM_ANNOTATION_LIMIT + 5):
        engine.ingest(room["id"], {"email": f"lead{index}@b.example"}, source=SOURCE)
    annotation = store.get(room["id"])["data"]["dedupe"]
    assert len(annotation["history"]) == ROOM_ANNOTATION_LIMIT
    assert annotation["history"][-1]["outcome"] == CREATED


def test_the_full_match_detail_stays_on_the_decision_not_the_room(engine, store, room):
    """A capped array cannot answer a question about an older decision."""
    add_row(engine, email="a@b.example")
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    decisions = engine.decisions(room_id=room["id"])
    assert decisions[0]["data"]["matched"][0]["email"] == "a@b.example"


def test_the_annotation_is_scoped_to_its_own_room(engine, store, room, other_room):
    engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    assert "dedupe" not in store.get(other_room["id"])["data"]


def test_ingesting_to_a_room_that_does_not_exist_is_a_404_not_a_400(engine, store):
    """A room that does not exist is what every other route calls a 404.

    ``RecordNotFound`` is raised rather than ``DedupeError`` precisely so the
    core's own handler answers it, and so this feature does not register a
    second handler for a type the app already maps.
    """
    from dsr.db.audited import RecordNotFound

    with pytest.raises(RecordNotFound):
        engine.ingest("nope", {"email": "a@b.example"}, source=SOURCE)
    assert store.stats()["records"] == 0


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_decisions_filter_by_outcome_policy_and_room(engine, room, other_room):
    add_row(engine, email="a@b.example")
    add_row(engine, email="c@d.example")
    update = connection(engine, "SF", policy="update", keys=["email"])
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    engine.ingest(
        other_room["id"], {"email": "c@d.example"}, connection_id=update["id"], source=SOURCE
    )
    assert len(engine.decisions(room_id=room["id"])) == 1
    assert len(engine.decisions(outcome=BLOCKED)) == 1
    assert len(engine.decisions(outcome=UPDATED)) == 1
    assert len(engine.decisions(policy="block")) == 1
    assert len(engine.decisions(policy="update")) == 1


def test_decisions_filter_by_needing_a_human(engine, room):
    add_row(engine, email="a@b.example", external_id="dup")
    add_row(engine, email="c@d.example", external_id="dup")
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    engine.ingest(room["id"], {"external_id": "dup"}, source=SOURCE)
    assert len(engine.decisions(outcome=BLOCKED)) == 1
    assert len(engine.decisions(needs_human=False)) == 1
    assert len(engine.decisions(needs_human=True)) == 1


def test_a_block_is_not_needing_a_human_but_a_hard_block_is(engine, room):
    add_row(engine, email="a@b.example", external_id="dup")
    add_row(engine, email="c@d.example", external_id="dup")
    blocked = engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)["data"]
    hard = engine.ingest(room["id"], {"external_id": "dup"}, source=SOURCE)["data"]
    assert blocked["needs_human"] is False
    assert hard["needs_human"] is True


def test_the_summary_counts_each_outcome_and_the_avoided_calls(engine, room):
    add_row(engine, email="a@b.example")
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    summary = engine.summary()
    assert summary["decisions"] == 2
    assert summary["by_outcome"] == {BLOCKED: 1, CREATED: 1}
    assert summary["crm_calls_avoided"] == 0


def test_the_summary_counts_the_avoided_crm_calls(engine, room):
    add_row(engine, email="a@b.example", room_id=room["id"])
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    assert engine.summary()["crm_calls_avoided"] == 1


def test_the_summary_is_room_scoped_too(engine, room, other_room):
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    engine.ingest(other_room["id"], {"email": "b@b.example"}, source=SOURCE)
    assert engine.summary(room_id=room["id"])["decisions"] == 1


def test_the_summary_counts_the_rows_and_connections_it_covers(engine, room):
    add_row(engine, email="a@b.example")
    connection(engine, "SF")
    summary = engine.summary()
    assert summary["connections"] == 1
    assert summary["records"] == 1


def test_the_header_preview_answers_without_sending_anything(engine):
    preview = engine.header_preview("allow")
    assert preview["name"] == DUPLICATE_RULE_HEADER_NAME
    assert preview["options"] == {"allowSave": True}
    assert preview["wire"] == '{"allowSave": "true"}'


def test_the_header_preview_refuses_an_unknown_policy(engine):
    with pytest.raises(DedupeError):
        engine.header_preview("auto_merge")


# --------------------------------------------------------------------------- #
# The audit trail, and the source rule
# --------------------------------------------------------------------------- #


def test_every_decision_is_audited_with_the_source_it_was_given(engine, store, room):
    engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    entries = store.audit(collection=DECISION_COLLECTION)
    assert len(entries) == 1
    assert entries[0]["source"] == SOURCE
    assert entries[0]["room_id"] == room["id"]


def test_the_room_annotation_is_audited_too(engine, store, room):
    engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    # The room already has an insert from the fixture; the annotation is the
    # update that follows it, and it is audited under the same source.
    room_entries = store.audit(collection="room")
    assert [row["action"] for row in room_entries] == ["update", "insert"]
    assert room_entries[0]["source"] == SOURCE


def test_a_created_row_is_audited_under_the_sources_route(engine, store, room):
    engine.ingest(room["id"], {"email": "new@b.example"}, source=SOURCE)
    assert store.audit(collection=RECORD_COLLECTION)[0]["source"] == SOURCE


def test_a_refusal_still_records_a_decision(engine, store, room):
    """Nothing happened, and here is why - which is what a rep needs to read."""
    add_row(engine, email="a@b.example")
    engine.ingest(room["id"], {"email": "a@b.example"}, source=SOURCE)
    assert len(store.audit(collection=DECISION_COLLECTION)) == 1


def test_every_write_method_requires_a_source(engine, room):
    """The defect this prevents: an audit row naming a path nobody served.

    ``source`` is keyword-only and has no default, so a caller that forgets it
    fails loudly rather than writing a null into the audit log.
    """
    for call in (
        lambda: engine.create_connection({"name": "SF"}),
        lambda: engine.create_record({"email": "a@b.example"}),
        lambda: engine.ingest(room["id"], {"email": "a@b.example"}),
        lambda: engine.update_connection("x", {"policy": "block"}),
        lambda: engine.delete_connection("x"),
    ):
        with pytest.raises(TypeError):
            call()


def test_the_audit_source_names_the_route_that_served_the_write(http):
    """Every recorded source matches a route the app actually serves.

    The defect this exists to catch shipped in this codebase before: a feature's
    audit log kept naming a route the app had stopped serving. The check is
    behavioural - it reads what the audit log actually recorded, after driving
    every write endpoint, and asks the running app what it actually mounted.

    Checked against *every* route, core included, because the audit log is
    shared: the test creates its room through the core records API, and that
    write's source is a core route. Asserting only against this feature's routes
    would pass for the wrong reason and fail for an unrelated one.
    """
    connection = http.post(f"{PREFIX}/connections", json={"name": "SF", "policy": "block"}).json()
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/records", json={"email": "a@b.example"})
    http.patch(f"{PREFIX}/connections/{connection['id']}", json={"policy": "update"})
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "new@b.example"})
    http.post(f"{PREFIX}/rooms/{room['id']}/check", json={"email": "other@b.example"})
    http.delete(f"{PREFIX}/connections/{connection['id']}")

    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(limit=1000) if row["source"]}
    routes = all_served_routes(http)

    assert sources, "no source was recorded, so the check proved nothing"
    for source in sorted(sources):
        assert source_names_a_mounted_route(source, routes), (
            f"{source!r} names no route this app serves"
        )


def test_every_source_this_feature_records_is_under_its_own_prefix(http):
    """And the sharper half: this feature never records a route that is not its own.

    The weaker check above would pass even if a domain function hardcoded a
    perfectly valid *core* path. This one cannot.
    """
    connection = http.post(f"{PREFIX}/connections", json={"name": "SF"}).json()
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/records", json={"email": "a@b.example"})
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "new@b.example"})
    http.patch(f"{PREFIX}/connections/{connection['id']}", json={"policy": "update"})
    http.delete(f"{PREFIX}/connections/{connection['id']}")

    store = RecordStore(client_store(http))
    mine = {
        row["source"]
        for row in store.audit(limit=1000)
        if row["collection"] in (DECISION_COLLECTION, RECORD_COLLECTION, CONNECTION_COLLECTION)
        and row["source"]
    }
    routes = mounted_routes(http)

    assert len(mine) >= 4, "the feature recorded fewer sources than it has write routes"
    for source in sorted(mine):
        assert (
            source.startswith(f"POST {PREFIX}")
            or source.startswith(f"PATCH {PREFIX}")
            or (source.startswith(f"DELETE {PREFIX}"))
        ), f"{source!r} does not name a route under this feature's own prefix"
        assert source_names_a_mounted_route(source, routes), f"{source!r} names no mounted route"


def test_a_decision_written_over_http_records_its_own_route(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "new@b.example"}).json()
    store = RecordStore(client_store(http))
    sources = {row["source"] for row in store.audit(collection=DECISION_COLLECTION)}
    assert sources == {f"POST {PREFIX}/rooms/{room['id']}/ingest"}


def client_store(client):
    return client.app.state.store.db


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["policies"] == list(POLICIES)
    assert body["outcomes"] == list(OUTCOMES)
    assert body["policies_detail"]["count"] == 4
    assert body["matchers"]["count"] >= 5
    # The two outcome keys must not collide: `outcomes` is the flat list a
    # picker renders, `outcome_table` the mapping a form submits against.
    assert isinstance(body["outcomes"], list)
    assert body["outcome_table"]["policy_outcomes"]["merge"] == ESCALATED


def test_inferences_are_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(dedupe_inferences.INFERENCES)
    assert body["sourced_quote"]


def test_matchers_are_served_over_http(http):
    body = http.get(f"{PREFIX}/matchers").json()
    assert "fuzzy:domain_name" in {m["id"] for m in body["matchers"]}
    assert body["precedence"] == ALL_KEYS


def test_the_policy_list_and_one_policy_over_http(http):
    listed = http.get(f"{PREFIX}/policies").json()
    assert listed["default"] == "block"
    one = http.get(f"{PREFIX}/policies/allow").json()
    assert one["header"] == {"allowSave": True}
    assert one["sourced_from"]


def test_an_unknown_policy_over_http_is_a_400_naming_the_set(http):
    response = http.get(f"{PREFIX}/policies/nope")
    assert response.status_code == 400
    assert response.json()["error"] == "dedupe_error"
    assert "block" in response.json()["detail"]


def test_the_header_preview_over_http(http):
    body = http.get(
        f"{PREFIX}/header", params={"policy": "allow", "run_as_current_user": True}
    ).json()
    assert body["options"] == {"allowSave": True, "runAsCurrentUser": True}


def test_the_connection_lifecycle_over_http(http):
    created = http.post(
        f"{PREFIX}/connections", json={"name": "SF", "policy": "block", "vendor": "salesforce"}
    )
    assert created.status_code == 201
    connection_id = created.json()["id"]

    assert http.get(f"{PREFIX}/connections").json()["count"] == 1
    assert http.get(f"{PREFIX}/connections/{connection_id}").json()["data"]["policy"] == "block"
    assert (
        http.patch(f"{PREFIX}/connections/{connection_id}", json={"policy": "merge"}).status_code
        == 200
    )
    assert http.delete(f"{PREFIX}/connections/{connection_id}").status_code == 204
    assert http.get(f"{PREFIX}/connections/{connection_id}").status_code == 404


def test_an_unknown_connection_is_a_404_over_http(http):
    assert http.get(f"{PREFIX}/connections/nope").status_code == 404


def test_deleting_an_unknown_connection_is_a_404_over_http(http):
    assert http.delete(f"{PREFIX}/connections/nope").status_code == 404


def test_patching_an_unknown_connection_is_a_400_over_http(http):
    assert http.patch(f"{PREFIX}/connections/nope", json={"policy": "block"}).status_code == 400


def test_a_bad_connection_is_a_400_over_http(http):
    response = http.post(f"{PREFIX}/connections", json={"name": "SF", "policy": "nope"})
    assert response.status_code == 400
    assert "published policies" in response.json()["detail"]


def test_the_records_lifecycle_over_http(http):
    created = http.post(f"{PREFIX}/records", json={"email": "a@b.example", "name": "Ada"})
    assert created.status_code == 201
    assert http.get(f"{PREFIX}/records").json()["count"] == 1
    assert http.get(f"{PREFIX}/records", params={"object_type": "company"}).json()["count"] == 0


def test_a_record_with_no_matching_key_is_a_400_over_http(http):
    response = http.post(f"{PREFIX}/records", json={"name": "Nameless"})
    assert response.status_code == 400
    assert "matching key" in response.json()["detail"]


def test_ingesting_over_http_reports_the_outcome_and_the_write(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/records", json={"email": "a@b.example", "name": "Ada"})

    created = http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "new@b.example"})
    assert created.status_code == 201
    assert created.json()["data"]["outcome"] == CREATED

    blocked = http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "a@b.example"})
    assert blocked.status_code == 201
    assert blocked.json()["data"]["outcome"] == BLOCKED
    assert len(blocked.json()["data"]["matched_ids"]) == 1


def test_check_over_http_answers_without_writing_anything(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/records", json={"email": "a@b.example"})

    before = http.get("/api/stats").json()["records"]
    answer = http.post(f"{PREFIX}/rooms/{room['id']}/check", json={"email": "a@b.example"})
    assert answer.status_code == 200
    assert answer.json()["outcome"] == BLOCKED
    assert http.get("/api/stats").json()["records"] == before


def test_check_and_ingest_agree_on_the_outcome(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/records", json={"email": "a@b.example"})
    payload = {"email": "A@B.example", "name": "Ada"}
    checked = http.post(f"{PREFIX}/rooms/{room['id']}/check", json=payload).json()
    ingested = http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json=payload).json()["data"]
    assert checked["outcome"] == ingested["outcome"]
    assert checked["matched_ids"] == ingested["matched_ids"]


def test_the_room_annotation_over_http_is_read_off_the_room_row(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    record = http.post(f"{PREFIX}/records", json={"email": "a@b.example"}).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "a@b.example"})

    body = http.get(f"{PREFIX}/rooms/{room['id']}/dedupe").json()
    assert body["dedupe"]["last_outcome"] == BLOCKED
    assert body["dedupe"]["last_match_record_id"] == record["id"]
    assert body["limit"] == ROOM_ANNOTATION_LIMIT


def test_the_room_annotation_is_404_for_an_unknown_room(http):
    assert http.get(f"{PREFIX}/rooms/nope/dedupe").status_code == 404


def test_ingesting_to_an_unknown_room_is_a_404_over_http(http):
    assert (
        http.post(f"{PREFIX}/rooms/nope/ingest", json={"email": "a@b.example"}).status_code == 404
    )


def test_checking_an_unknown_room_is_a_404_over_http(http):
    assert http.post(f"{PREFIX}/rooms/nope/check", json={}).status_code == 404


def test_a_rooms_decisions_are_listed_and_one_can_be_read(http):
    room = http.post("/api/records/room", json={"name": "Northwind"}).json()
    decision = http.post(
        f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "new@b.example"}
    ).json()

    listed = http.get(f"{PREFIX}/rooms/{room['id']}/decisions").json()
    assert listed["count"] == 1
    assert listed["room_id"] == room["id"]

    one = http.get(f"{PREFIX}/rooms/{room['id']}/decisions/{decision['id']}")
    assert one.status_code == 200
    assert one.json()["data"]["outcome"] == CREATED


def test_a_decision_belonging_to_another_room_is_a_404(http):
    first = http.post("/api/records/room", json={"name": "One"}).json()
    second = http.post("/api/records/room", json={"name": "Two"}).json()
    decision = http.post(
        f"{PREFIX}/rooms/{first['id']}/ingest", json={"email": "a@b.example"}
    ).json()
    assert http.get(f"{PREFIX}/rooms/{second['id']}/decisions/{decision['id']}").status_code == 404


def test_an_unknown_decision_is_a_404(http):
    room = http.post("/api/records/room", json={"name": "One"}).json()
    assert http.get(f"{PREFIX}/rooms/{room['id']}/decisions/nope").status_code == 404


def test_a_rooms_decisions_for_an_unknown_room_is_a_404(http):
    assert http.get(f"{PREFIX}/rooms/nope/decisions").status_code == 404


def test_decisions_filter_over_http(http):
    room = http.post("/api/records/room", json={"name": "One"}).json()
    http.post(f"{PREFIX}/records", json={"email": "a@b.example"})
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "a@b.example"})
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "new@b.example"})
    assert http.get(f"{PREFIX}/decisions", params={"outcome": CREATED}).json()["count"] == 1
    assert http.get(f"{PREFIX}/decisions", params={"outcome": BLOCKED}).json()["count"] == 1
    assert http.get(f"{PREFIX}/decisions", params={"needs_human": "true"}).json()["count"] == 0


def test_the_summary_over_http_is_room_scoped_when_asked(http):
    room = http.post("/api/records/room", json={"name": "One"}).json()
    http.post(f"{PREFIX}/rooms/{room['id']}/ingest", json={"email": "a@b.example"})
    assert http.get(f"{PREFIX}/summary").json()["decisions"] == 1
    assert http.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()["decisions"] == 1


def test_a_disabled_connection_is_a_400_over_http(http):
    connection = http.post(f"{PREFIX}/connections", json={"name": "SF", "enabled": False}).json()
    room = http.post("/api/records/room", json={"name": "One"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/ingest",
        json={"email": "a@b.example"},
        params={"connection_id": connection["id"]},
    )
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


def test_an_unknown_connection_on_ingest_is_a_400_over_http(http):
    room = http.post("/api/records/room", json={"name": "One"}).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/ingest", json={}, params={"connection_id": "nope"}
    )
    assert response.status_code == 400


def test_an_unknown_policy_filter_is_a_400_over_http(http):
    assert http.get(f"{PREFIX}/decisions", params={"policy": "nope"}).status_code == 400


def test_the_actor_query_parameter_reaches_the_audit_row(http):
    room = http.post("/api/records/room", json={"name": "One"}).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/ingest",
        json={"email": "a@b.example"},
        params={"actor": "sam"},
    )
    store = RecordStore(client_store(http))
    entry = store.audit(collection=DECISION_COLLECTION)[0]
    assert entry["actor"] == "sam"


def test_the_core_app_still_works_with_the_feature_mounted(http):
    assert http.get("/api/health").status_code == 200
    assert http.get("/api/records/room").status_code == 200


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_and_its_ids_are_unique():
    ids = [entry["id"] for entry in dedupe_inferences.INFERENCES]
    assert all(ids)
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("entry_id", [entry["id"] for entry in dedupe_inferences.INFERENCES])
def test_every_inference_says_why_it_chooses_and_how_to_change_it(entry_id):
    """A judgement call with no `change_it` cannot be argued with."""
    entry = dedupe_inferences.by_id(entry_id)
    assert entry is not None
    for field in ("topic", "basis", "value", "why", "change_it", "blast_radius"):
        assert entry[field], f"{entry_id} is missing {field}"


def test_the_inference_registry_describes_both_halves_of_the_workflow():
    body = dedupe_inferences.describe()
    assert body["sourced_quote"] == dedupe_inferences.MERGE_GAP
    assert body["sourced"]["duplicate_rule_header"] == list(DUPLICATE_RULE_HEADER)
    assert body["sourced"]["allow_save_header"] == {"allowSave": True}
    assert body["sourced"]["block_header"] == {"includeRecordDetails": True}
    assert body["outcomes"]["multiple_match_status"] == 300


def test_the_largest_inference_is_the_merge_the_research_declines_to_claim():
    """The gaps section is explicit, and the policy has to honour it."""
    entry = dedupe_inferences.by_id("merge-escalates-and-does-not-merge")
    assert entry["basis"] == dedupe_inferences.MERGE_GAP
    assert entry["value"]["writes"] is False
    assert entry["value"]["needs_human"] is True


def test_the_unique_index_inference_matches_what_the_rules_do():
    entry = dedupe_inferences.by_id("unique-index-outranks-allow-policy")
    decision = decide(matched([row("a")], match_key="email"), "allow", unique_keys=["email"])
    assert decision.outcome == HARD_BLOCKED
    assert entry["value"]["rule"].startswith("allow")


def test_the_multi_match_inference_matches_what_the_rules_do():
    entry = dedupe_inferences.by_id("multiple-matches-hard-block-regardless-of-key")
    assert decide(multiple([row("a")], match_key="email"), "block").outcome == HARD_BLOCKED
    assert entry["value"]["policy_consulted"] is False


def test_the_ambiguous_key_inference_matches_what_the_rules_do():
    entry = dedupe_inferences.by_id("ambiguous-key-match-is-a-hard-block")
    assert decide(multiple([row("a")], match_key=None), "block").outcome == HARD_BLOCKED
    assert entry["value"]["status"] is None


def test_the_default_policy_inference_matches_the_published_default():
    entry = dedupe_inferences.by_id("default-policy-is-block")
    assert entry["value"]["default"] == DEFAULT_POLICY


def test_the_key_precedence_inference_matches_the_published_order():
    entry = dedupe_inferences.by_id("key-precedence")
    assert entry["value"]["order"] == ALL_KEYS


def test_the_unique_key_inference_matches_the_published_defaults():
    entry = dedupe_inferences.by_id("unique-key-defaults")
    assert entry["value"]["default_unique_keys"] == list(DEFAULT_UNIQUE_KEYS)


def test_the_fuzzy_matcher_inference_names_the_matcher_that_exists():
    entry = dedupe_inferences.by_id("fuzzy-matcher-is-registered-not-special")
    matcher = MatcherRegistry().get(entry["value"]["id"])
    assert matcher.threshold == entry["value"]["threshold"]
    assert matcher.builtin is False


def test_the_min_score_inference_matches_the_matching_code():
    entry = dedupe_inferences.by_id("min-score-is-per-connection")
    assert entry["value"]["default"] is None
    assert normalise_connection({"name": "SF"})["min_score"] is None


def test_the_vendor_inference_matches_the_engine(engine, room):
    """All three vendors' mechanisms produce the same three answers, so branding
    the decision by vendor would be three code paths agreeing by coincidence."""
    entry = dedupe_inferences.by_id("vendor-is-configuration-not-behaviour")
    assert entry["value"]["changes_the_decision"] is False
    add_row(engine, email="a@b.example")
    outcomes = set()
    for vendor in VENDORS:
        conn = connection(engine, vendor, vendor=vendor, policy="block", keys=["email"])
        outcomes.add(
            engine.ingest(
                room["id"], {"email": "a@b.example"}, connection_id=conn["id"], source=SOURCE
            )["data"]["outcome"]
        )
    assert outcomes == {BLOCKED}


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seed_module():
    return load_feature(MODULE)


def test_the_seed_reports_what_it_added(db, seed_module):
    from dsr.store import RecordStore as RS

    store = RS(db)
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam")
    ]
    summary = seed_module.seed(db, {"room_ids": rooms, "now": datetime.now(timezone.utc)})
    assert isinstance(summary, str)
    assert "8 rows" in summary
    assert "6 connections" in summary


def test_the_seed_covers_every_outcome_the_research_names(db, seed_module):
    from dsr.store import RecordStore as RS

    engine = DedupeEngine(RS(db))
    rooms = [
        (RS(db).create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam")
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": datetime.now(timezone.utc)})
    reached = {record["data"]["outcome"] for record in engine.decisions(limit=50)}
    assert reached == {CREATED, UPDATED, BLOCKED, CREATED_DUPLICATE, HARD_BLOCKED, ESCALATED}


def test_the_seed_shows_both_hard_blocks_not_only_one(db, seed_module):
    from dsr.store import RecordStore as RS

    engine = DedupeEngine(RS(db))
    store = RS(db)
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam")
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": datetime.now(timezone.utc)})
    hard = [r["data"] for r in engine.decisions(outcome=HARD_BLOCKED, limit=50)]
    assert any(record["status"] == 300 for record in hard)
    assert any("unique index" in record["reason"] for record in hard)


def test_the_seed_shows_the_in_room_check_answering_without_a_call(db, seed_module):
    from dsr.store import RecordStore as RS

    engine = DedupeEngine(RS(db))
    store = RS(db)
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam")
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": datetime.now(timezone.utc)})
    assert engine.summary()["crm_calls_avoided"] >= 1


def test_the_seed_annotates_the_rooms_it_used(db, seed_module):
    from dsr.store import RecordStore as RS

    store = RS(db)
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam")
    ]
    seed_module.seed(db, {"room_ids": rooms, "now": datetime.now(timezone.utc)})
    annotated = [room_id for room_id, _ in rooms if "dedupe" in store.get(room_id)["data"]]
    assert annotated


def test_the_seed_audits_its_own_writes(db, seed_module):
    from dsr.store import RecordStore as RS

    store = RS(db)
    rooms = [(store.create("room", {"name": "One"}, actor="dana", source="core")["id"], "One")]
    seed_module.seed(db, {"room_ids": rooms, "now": datetime.now(timezone.utc)})
    sources = {row["source"] for row in store.audit(collection=DECISION_COLLECTION)}
    assert sources == {"seed"}


def test_the_seed_survives_being_run_with_no_rooms(db, seed_module):
    """A feature that cannot seed itself is visible rather than silently empty."""
    summary = seed_module.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc)})
    assert "no rooms" in summary


def test_the_seeded_rows_include_a_shared_external_id_for_the_300(db, seed_module):
    externals = [
        spec.get("external_id") for spec in seed_module.DEMO_RECORDS if spec.get("external_id")
    ]
    assert len(externals) > len(set(externals)), "no two seeded rows share an external ID"


def test_the_seeded_cases_reach_the_outcome_they_are_labelled_with(db, seed_module):
    """Every demo row does what its label claims, so the demo teaches something.

    Run in declaration order rather than read back from the store, because two
    decisions written in the same millisecond tie on ``updated_at`` and cannot be
    told apart afterwards.
    """
    from dsr.store import RecordStore as RS

    store = RS(db)
    engine = DedupeEngine(store, clock=lambda: datetime.now(timezone.utc))
    rooms = [
        (store.create("room", {"name": name}, actor="dana", source="core")["id"], name)
        for name in ("Northwind", "Contoso", "Fabrikam")
    ]
    for spec in seed_module.DEMO_RECORDS:
        body = dict(spec)
        index = body.pop("room_index", None)
        engine.create_record(
            body,
            room_id=rooms[index % 3][0] if index is not None else None,
            actor="dana",
            source="seed",
        )
    by_name = {
        record["data"]["name"]: record["id"]
        for record in (
            engine.create_connection(spec, actor="dana", source="seed")
            for spec in seed_module.DEMO_CONNECTIONS
        )
    }
    expected = {
        "created:": CREATED,
        "blocked:": BLOCKED,
        "blocked by": BLOCKED,
        "hard blocked:": HARD_BLOCKED,
        "updated:": UPDATED,
        "created anyway:": CREATED_DUPLICATE,
        "escalated:": ESCALATED,
    }
    for index, case in enumerate(seed_module.DEMO_INGESTS):
        room_id = rooms[case["room_index"] % 3][0] if "room_index" in case else rooms[index % 3][0]
        decision = engine.ingest(
            room_id,
            case["inbound"],
            connection_id=by_name[case["connection"]],
            actor="dana",
            source="seed",
        )["data"]
        wanted = next(
            outcome for prefix, outcome in expected.items() if case["label"].startswith(prefix)
        )
        assert decision["outcome"] == wanted, case["label"]


def test_the_demo_labels_the_one_case_the_in_room_check_answers(db, seed_module):
    """crm_called is false there and true for every other block in the demo."""
    case = next(entry for entry in seed_module.DEMO_INGESTS if "in-room" in entry["label"])
    assert "room_index" in case
