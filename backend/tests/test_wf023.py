"""Tests for WF-023: relate buyer engagement to CRM pipeline and close rate.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-023.md``, and the design decisions come
from ``WF-023-design.md`` beside it. This is a **build**, not a port, so there is no branch
to compare against: the specification is the research document, and the researched
sentences each test pins are quoted in the test that pins them.

Four groups, in the order a reviewer would want them:

* **Vocabulary** - stage classification across both documented CRMs and all three match
  levels, the order the two closed sets are tried in, the ``sales`` workspace type, and
  the tolerant scalar readers.
* **Filters** - the researched filter vocabulary, every rejection path, and the rule that
  an undated row is never silently dropped.
* **Rollup** - the two-part admission gate, the eight tiles, the researched close-rate
  fraction, days to close, the currency choice, the two panels, engagement, coverage, and
  the determinism of every ordering.
* **HTTP and audit** - every route through this feature's own router, the four error
  statuses, the guarantee that reads never write, and the audit-source rule: every row
  this feature's HTTP layer produces names a route the host actually mounted.

Three of these are regression tests for defects found while building, and they say so in
their names, because each one produced a report that was confidently wrong:

``test_a_row_is_projected_exactly_once``
    The report re-projected its input, and because a projected row is flat while a stored
    record is enveloped, the second projection read ``record["data"]`` off a dict that had
    none. Every deal arrived at the tiles with no stage, no amount and no date, and the
    report cheerfully returned three unknown-stage deals worth nothing.
``test_active_pipeline_is_not_permanently_zero``
    ``active_pipeline`` tested ``stage_class == "open"``, which ``classify_stage`` never
    returns - it returns won, lost or unknown. So the tile was zero on a report whose every
    deal was mid-negotiation.
``test_a_none_workspace_type_is_not_compared_against_nothing``
    ``is_sales_type(value, sales_type=None)`` normalised ``None`` to the empty string, so
    *every* workspace failed the type test: the report came back empty with
    ``sales_typed_rooms: 0`` while the excluded rows still read ``type: "Sales"``.

The HTTP fixture points ``DSR_DB_PATH`` at a temporary file the way ``test_features.py``
does. The engine is built per request from a dependency, so ``app.dependency_overrides``
is the seam - there is nothing on ``app.state`` to replace, which is the point of not
editing the shared app.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.salesimpact import (
    DealConflict,
    InvalidDeal,
    InvalidFilter,
    SalesImpact,
    UnknownWorkspace,
    parse_filters,
)
from dsr.salesimpact import rollup
from dsr.salesimpact.deals import DEAL_COLLECTION, DealBook
from dsr.salesimpact.filters import BUCKETS, bucket_key, date_range
from dsr.salesimpact.inferences import INFERENCES
from dsr.salesimpact.vocabulary import (
    API_CONSTRAINTS,
    FIELD_SYNONYMS,
    LOST_STAGES,
    SALES_TYPE,
    STAGE_CLASSES,
    VIEW_ACTIONS,
    WON_STAGES,
    as_number,
    as_text,
    classify_stage,
    is_sales_type,
    normalise,
    pick,
)
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated rather than imported so renaming the route fails
#: here instead of following silently - which is what a test is for.
PREFIX = "/api/wf-023"

#: What the pure-domain tests pass as ``source``: deliberately the exact shape a route
#: passes, so a test asserting on an audit row is asserting on the real thing.
SOURCE = f"POST {PREFIX}/deals"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf023.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def book(store):
    return DealBook(store)


@pytest.fixture()
def impact(store):
    return SalesImpact(store)


def make_room(store, **data):
    payload = {"name": "Room", "account": "Account", "owner": "dana"}
    payload.update(data)
    return store.create("room", payload, actor=payload.get("owner", "dana"), source="seed")


def make_deal(store, room_id=None, **data):
    payload = {"crm_deal_id": f"006-{abs(hash(tuple(sorted(data.items())))) % 10**6:06d}"}
    payload.update(data)
    return store.create(DEAL_COLLECTION, payload, room_id=room_id, actor="dana", source="seed")


def make_event(store, room_id, person, action, day):
    """One buyer engagement event, written through whichever handle was passed.

    Accepts a :class:`~dsr.store.RecordStore` or a ``TestClient``, because the same
    fixture data is needed by the domain tests (which want a store) and by the HTTP tests
    (which want requests to go through the real audit path). Posting through
    ``/api/records`` keeps the HTTP half honest rather than reaching around the app.
    """
    payload = {"person": person, "action": action, "occurred_at": day}
    if hasattr(store, "post"):
        return store.post("/api/records/activity", params={"room_id": room_id}, json=payload).json()
    return store.create("activity", payload, room_id=room_id, actor="system", source="seed")


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, with this feature's router mounted by discovery."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf023.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def seeded(http):
    """A database carrying the researched states: in scope, out of scope, and broken.

    Four workspaces, deliberately:
    ``sales`` typed ``Sales`` with two deals (one won, one mid-negotiation);
    ``bare`` typed ``Sales`` with **no deal** - the researched missing-data case;
    ``other`` typed something else with a deal on it anyway - the type gate's case;
    ``untouched`` with nothing at all.

    A demo and a fixture containing only success would leave the gate, the coverage panel
    and the close rate untested, which is the whole point of the research.
    """
    sales = http.post(
        "/api/records/room",
        json={"name": "Northwind", "account": "Northwind", "type": "Sales", "owner": "dana"},
    ).json()
    bare = http.post(
        "/api/records/room",
        json={"name": "Contoso", "account": "Contoso", "type": "Sales", "owner": "sam"},
    ).json()
    other = http.post(
        "/api/records/room",
        json={"name": "Fabrikam", "account": "Fabrikam", "type": "onboarding", "owner": "sam"},
    ).json()
    untouched = http.post(
        "/api/records/room", json={"name": "Wingtip", "account": "Wingtip", "owner": "dana"}
    ).json()

    won = http.post(
        f"{PREFIX}/deals",
        params={"room_id": sales["id"], "actor": "dana"},
        json={
            "crm_deal_id": "006NW-1",
            "name": "Northwind platform",
            "stage": "Closed Won",
            "amount": 1000,
            "currency": "USD",
            "owner": "dana",
            "team": "enterprise",
            "created_date": "2026-01-01",
            "closed_at": "2026-01-31",
        },
    ).json()
    open_deal = http.post(
        f"{PREFIX}/deals",
        params={"room_id": sales["id"], "actor": "dana"},
        json={
            "crm_deal_id": "006NW-2",
            "name": "Northwind add-on",
            "stage": "Negotiation",
            "amount": 500,
            "currency": "USD",
            "team": "midmarket",
            "created_date": "2026-02-01",
        },
    ).json()
    lost = http.post(
        f"{PREFIX}/deals",
        params={"room_id": sales["id"], "actor": "dana"},
        json={
            "crm_deal_id": "006NW-3",
            "name": "Northwind cancelled",
            "stage": "Closed Lost",
            "amount": 250,
            "currency": "USD",
            "owner": "dana",
            "created_date": "2026-01-01",
            "closed_at": "2026-01-11",
        },
    ).json()
    misfiled = http.post(
        f"{PREFIX}/deals",
        params={"room_id": other["id"], "actor": "sam"},
        json={
            "crm_deal_id": "006FK-1",
            "name": "Fabrikam renewal",
            "stage": "Closed Won",
            "amount": 9000,
            "currency": "USD",
            "created_date": "2026-01-05",
            "closed_at": "2026-02-05",
        },
    ).json()

    http.patch(f"{PREFIX}/integration", json={"connected": True, "provider": "salesforce"})

    for day in (1, 2, 3, 4):
        make_event(http, sales["id"], "a.buyer@northwind.example", "viewed", f"2026-02-0{day}")
    make_event(http, sales["id"], "a.buyer@northwind.example", "downloaded", "2026-02-05")
    make_event(http, sales["id"], "b.buyer@northwind.example", "viewed", "2026-02-05")
    make_event(http, other["id"], "ops@fabrikam.example", "viewed", "2026-02-05")

    return {
        "sales": sales,
        "bare": bare,
        "other": other,
        "untouched": untouched,
        "won": won,
        "open": open_deal,
        "lost": lost,
        "misfiled": misfiled,
    }


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f for f in http.get("/api/features").json()["features"] if f["id"] == "wf-023-relate-buyer-engagement-to-crm-pipelin"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-023"
    assert entry["exception_handlers"] == [
        "DealConflict",
        "InvalidDeal",
        "InvalidFilter",
        "UnknownWorkspace",
    ]
    assert len(entry["routes"]) == 14


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-023-relate-buyer-engagement-to-crm-pipelin"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature("wf023_relate_buyer_engagement_to_crm_pipelin")
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature("wf023_relate_buyer_engagement_to_crm_pipelin").__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}
    assert len(mine) == 14
    assert not mine & others


def test_room_scoped_paths_stay_room_scoped(http):
    """The build brief is explicit about this, and a generic path would not be."""
    paths = {route["path"] for route in http.get("/api/features/wf-023-relate-buyer-engagement-to-crm-pipelin").json()["routes"]}
    assert f"{PREFIX}/report/rooms/{{room_id}}" in paths
    assert not any(path.startswith(f"{PREFIX}/rooms") for path in paths)


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "stage",
    [
        "Closed Won",
        "closed won",
        "CLOSED WON",
        "closed_won",
        "closed-won",
        "Closed Won (100%)",
        "Salesforce Closed Won",
        "won",
        "closedwon",
    ],
)
def test_both_documented_crm_spellings_of_a_win_classify_as_won(stage):
    assert classify_stage(stage) == "won"


@pytest.mark.parametrize(
    "stage",
    [
        "Closed Lost",
        "closed lost",
        "CLOSED LOST",
        "closed_lost",
        "closed-lost",
        "Renewal - Lost",
        "lost",
        "closedlost",
    ],
)
def test_both_documented_crm_spellings_of_a_loss_classify_as_lost(stage):
    assert classify_stage(stage) == "lost"


def test_lost_is_tried_before_won_across_every_level():
    """"won/lost" is a label a CRM really does use, and it has to land on lost.

    Normalised it is "won lost": a *prefix* match for "won" claims it, while "lost" matches
    it only as a *substring*. Level-major ordering - exact, then startswith, then substring,
    trying lost first within each level - gets this wrong, because the startswith level finds
    "won" before the substring level ever looks for "lost". That ordering was the first
    implementation and it put a lost deal into Revenue.
    """
    assert classify_stage("won/lost") == "lost"
    assert classify_stage("Won/Lost") == "lost"
    assert classify_stage("closed_won_lost") == "lost"


def test_the_term_major_order_costs_no_real_win():
    """No string in the won set contains "lost", so trying lost first cannot lose one."""
    for stage in sorted(WON_STAGES):
        assert classify_stage(stage) == "won"


def test_an_unclassifiable_stage_is_unknown_rather_than_a_refusal():
    """A team's own stage string is stored, counted, and shown - never rejected."""
    for stage in ("Escalated to the board", "On hold — legal", "", "   ", "?!?"):
        assert classify_stage(stage) == "unknown"


def test_the_configured_stage_sets_replace_the_built_in_ones():
    """A team's CRM spelling is a record, not a code change."""
    assert classify_stage("Gewonnen", won={"gewonnen"}, lost={"verloren"}) == "won"
    assert classify_stage("Verloren", won={"gewonnen"}, lost={"verloren"}) == "lost"
    assert classify_stage("Closed Won", won={"gewonnen"}, lost={"verloren"}) == "unknown"


def test_the_sales_workspace_type_is_the_researched_one():
    assert SALES_TYPE == "sales"
    for spelling in ("Sales", "sales", "SALES", " sales ", "  Sales  "):
        assert is_sales_type(spelling)
    for other in ("onboarding", "Sales Workspace", "sales-lead", "SalesType", "", None, {}):
        assert not is_sales_type(other)


def test_a_none_workspace_type_is_not_compared_against_nothing():
    """The regression: ``sales_type=None`` used to normalise to "", failing every room.

    The report came back empty with ``sales_typed_rooms: 0`` while its own excluded rows
    still read ``type: "Sales"``, which is the state a reviewer would have no way to
    diagnose from the response.
    """
    assert is_sales_type("Sales", sales_type=None) is True
    assert is_sales_type("onboarding", sales_type=None) is False


def test_normalisation_folds_every_separator_a_crm_uses():
    for spelling in ("Closed_Won", "closed-won", "closed won", "CLOSED  WON", "closed.won", "closed/won"):
        assert normalise(spelling) == "closed won"


def test_amounts_are_read_from_the_spellings_a_crm_sends():
    for value in (45000, 45000.5, "45000", "45,000", "$45,000.00", "45000 USD", " 45000 "):
        assert as_number(value) == pytest.approx(45000, abs=0.01) or as_number(value) == 45000.5
    assert as_number("not a number") is None
    assert as_number(None) is None
    assert as_number(True) is None, "a boolean is not a money value"


def test_text_reading_does_not_coerce_containers_into_strings():
    assert as_text({"a": 1}) == ""
    assert as_text(["a"]) == ""
    assert as_text("  padded  ") == "padded"
    assert as_text(None) == ""


def test_the_substring_level_is_whole_word_not_a_plain_in():
    """A plain substring test files ``"Gewonnen"`` as won, because that word contains
    ``"won"``. A team whose CRM is not English hits that the first time they read the
    report, and a misclassified deal in the revenue figure is the worst thing the
    classifier can do - so the third level means "the word appears"."""
    assert classify_stage("Gewonnen") == "unknown"
    assert classify_stage("Gewonnen - Renewal") == "unknown"
    assert classify_stage("Verloren") == "unknown"
    # The cases the level exists for still work.
    assert classify_stage("Salesforce Closed Won") == "won"
    assert classify_stage("Renewal - Lost") == "lost"
    assert classify_stage("Reopened - Closed Won") == "won"


def test_field_lookup_falls_back_to_the_configured_synonym_list():
    data = {"stage_name": "Negotiation"}
    assert pick(data, (), concept="stage") == "Negotiation"
    assert pick(data, (), concept="stage", synonyms={"stage": ["stage_name"]}) == "Negotiation"
    assert pick(data, (), concept="stage", synonyms={"stage": ["nope"]}) is None
    # An explicit key list wins, and an empty one with no concept finds nothing rather than
    # silently falling back to every synonym in the module.
    assert pick(data, ("stage_name",)) == "Negotiation"
    assert pick(data, ()) is None
    assert pick(None, (), concept="stage") is None


def test_the_reserved_created_at_is_not_advertised_as_a_payload_spelling():
    """It is a reserved envelope key, so ``create`` strips it from a payload.

    Listing it first in the creation-date synonyms advertised a spelling that silently
    disappeared, and a deal's "when did the CRM create this" landed on the store's own
    write time without saying so.
    """
    assert "created_at" not in FIELD_SYNONYMS["created_at"]
    assert FIELD_SYNONYMS["created_at"][0] == "created_date"


def test_the_documented_api_constraints_are_served_not_claimed():
    """"the 429 'Too many requests' rate-limit response and properties parameter are the
    documented API constraints to design around" - so they are served, and no socket is
    opened anywhere in this feature."""
    assert "429" in API_CONSTRAINTS["rate_limit"]
    assert "properties" in API_CONSTRAINTS["constraints"] if "constraints" in API_CONSTRAINTS else True
    assert "properties" in API_CONSTRAINTS["properties"]
    assert "no request or response schema" in API_CONSTRAINTS["endpoints_named_but_undocumented"]


def test_vocabulary_serves_what_the_page_needs_to_render_its_pickers():
    payload = SalesImpact.__init__  # the class is importable on its own
    del payload
    from dsr.salesimpact.vocabulary import describe

    served = describe()
    assert served["workspace_type"]["value"] == SALES_TYPE
    assert served["stage_classes"] == list(STAGE_CLASSES)
    assert served["won_stages"] == sorted(WON_STAGES)
    assert served["lost_stages"] == sorted(LOST_STAGES)
    assert served["view_actions"] == sorted(VIEW_ACTIONS)
    assert served["filters"]["bucket"]["values"] == list(BUCKETS)
    assert served["field_synonyms"]["stage"] == list(FIELD_SYNONYMS["stage"])


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


def test_every_researched_filter_is_accepted():
    """The research: "Filter by date range, CRM stage, owners, teams"."""
    parsed = parse_filters(
        {
            "from": "2026-01-01",
            "to": "2026-03-31",
            "stage": "Closed Won",
            "owner": "dana,sam",
            "team": "enterprise",
        }
    )
    assert parsed.date_from == date(2026, 1, 1)
    assert parsed.date_to == date(2026, 3, 31)
    assert parsed.stages == ("closed won",)
    assert parsed.owners == ("dana", "sam")
    assert parsed.teams == ("enterprise",)


def test_a_stage_filter_accepts_a_raw_string_or_a_class():
    parsed = parse_filters({"stage": "won,lost,Negotiation"})
    assert parsed.stage_classes == ("won", "lost")
    assert parsed.stages == ("negotiation",)


def test_a_reversed_date_range_is_refused():
    with pytest.raises(InvalidFilter) as excinfo:
        parse_filters({"from": "2026-03-31", "to": "2026-01-01"})
    assert "is after to" in str(excinfo.value)


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", "01/02/2026", "next tuesday"])
def test_an_unparseable_date_is_refused(value):
    with pytest.raises(InvalidFilter) as excinfo:
        parse_filters({"from": value})
    assert "from" in str(excinfo.value)


def test_an_unknown_bucket_is_refused_rather_than_silently_defaulted():
    with pytest.raises(InvalidFilter) as excinfo:
        parse_filters({"bucket": "fortnight"})
    assert "bucket must be one of" in str(excinfo.value)


def test_a_non_numeric_limit_is_refused():
    with pytest.raises(InvalidFilter):
        parse_filters({"limit": "lots"})


def test_an_undated_row_is_never_dropped_by_a_date_range():
    """Dropping it would shrink a total for a reason nobody can see.

    The coverage panel exists to make incompleteness nameable, and silently removing rows
    from under a total is exactly the failure it exists to prevent.
    """
    parsed = parse_filters({"from": "2026-01-01", "to": "2026-12-31"})
    assert parsed.in_date_range(None) is True
    assert parsed.in_date_range(date(2020, 1, 1)) is False
    assert parsed.in_date_range(date(2026, 6, 1)) is True
    assert parsed.in_date_range(date(2026, 12, 31)) is True


def test_the_applied_filter_comes_back_on_every_response():
    parsed = parse_filters({"from": "2026-01-01", "stage": "won", "owner": "dana", "bucket": "week"})
    assert parsed.echo() == {
        "from": "2026-01-01",
        "to": None,
        "stage": [],
        "stage_class": ["won"],
        "owner": ["dana"],
        "team": [],
        "bucket": "week",
    }


def test_an_unknown_query_parameter_is_ignored_not_refused():
    """A client newer than this server should still get a report."""
    parsed = parse_filters({"from": "2026-01-01", "forecast": "weighted"})
    assert parsed.extras == {"forecast": "weighted"}
    assert parsed.date_from == date(2026, 1, 1)


def test_time_series_fill_the_gaps_inside_the_range():
    rows = date_range(date(2026, 2, 1), date(2026, 2, 5), [date(2026, 2, 1), date(2026, 2, 5)])
    assert [row["date"] for row in rows] == [
        "2026-02-01",
        "2026-02-02",
        "2026-02-03",
        "2026-02-04",
        "2026-02-05",
    ]
    assert [row["deals"] for row in rows] == [0, 0, 0, 0, 0]


def test_a_series_with_no_bounds_covers_only_what_carries_data():
    rows = date_range(None, None, [date(2026, 2, 3), date(2026, 2, 5)])
    assert [row["date"] for row in rows] == ["2026-02-03", "2026-02-04", "2026-02-05"]


def test_buckets_group_by_day_week_and_month():
    moment = date(2026, 3, 18)  # a Wednesday
    assert bucket_key(moment, "day") == "2026-03-18"
    assert bucket_key(moment, "week") == "2026-03-16", "weeks start on Monday, matching ISO-8601"
    assert bucket_key(moment, "month") == "2026-03-01"


def test_a_week_bucket_groups_seven_days_at_a_time():
    rows = date_range(date(2026, 3, 16), date(2026, 4, 5), [], bucket="week")
    assert [row["date"] for row in rows] == ["2026-03-16", "2026-03-23", "2026-03-30"]
    assert len(rows) < 31, "a week bucket must be shorter than a day bucket over the same range"


def test_a_week_bucket_ends_on_the_week_the_range_ends_in():
    """Both ends round down to their bucket, not just the first.

    Ranging 2026-02-01 (a Sunday) to 2026-02-04 in weeks is really 2026-01-26 to
    2026-02-02. Stepping from the first to the unrounded end stops a bucket early, so
    every event in the final week is counted nowhere and the series comes back short -
    which is how two views arrived as one.
    """
    rows = date_range(date(2026, 2, 1), date(2026, 2, 4), [], bucket="week")
    assert [row["date"] for row in rows] == ["2026-01-26", "2026-02-02"]
    assert bucket_key(date(2026, 2, 1), "week") == "2026-01-26"
    assert bucket_key(date(2026, 2, 4), "week") == "2026-02-02"


def test_an_absurd_span_is_refused_rather_than_enumerated():
    assert date_range(date(1900, 1, 1), date(2026, 1, 1), []) == []


# --------------------------------------------------------------------------- #
# The admission gate
# --------------------------------------------------------------------------- #


def test_a_workspace_needs_both_sales_type_and_an_attached_deal(store):
    """"The Sales Impact report pulls in any workspace designated as a 'Sales' type that
    has a CRM opportunity." Two conditions, conjunctive, both tested."""
    sales = make_room(store, name="Sales room", type="Sales")
    bare = make_room(store, name="Bare room", type="Sales")
    other = make_room(store, name="Other room", type="onboarding")
    make_deal(store, room_id=sales["id"], crm_deal_id="A", stage="Negotiation")
    make_deal(store, room_id=other["id"], crm_deal_id="B", stage="Negotiation")

    scope = rollup.population(
        [rollup.project_room(r) for r in (sales, bare, other)],
        [rollup.project_deal(r) for r in store.list(DEAL_COLLECTION)],
    )
    assert [entry["name"] for entry in scope["included"]] == ["Sales room"]
    assert {(row["name"], row["reason"]) for row in scope["excluded"]} == {
        ("Bare room", "no_deal"),
        ("Other room", "not_sales"),
    }
    assert scope["rooms_total"] == 3
    assert scope["sales_typed_rooms"] == 2
    assert scope["in_scope_rooms"] == 1


def test_a_deal_attached_to_nothing_is_in_neither_list(store):
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="A", stage="Negotiation")
    orphan = make_deal(store, crm_deal_id="ORPHAN", stage="Closed Won", amount=5000)

    scope = rollup.population(
        [rollup.project_room(room)],
        [rollup.project_deal(r) for r in store.list(DEAL_COLLECTION)],
    )
    assert [row["id"] for row in scope["deals_not_attached"]] == [orphan["id"]]
    assert len(scope["included"][0]["deals"]) == 1


def test_an_excluded_deal_contributes_to_no_figure_at_all(store, impact):
    """The type gate, proved by seeding the money and checking none of it lands."""
    other = make_room(store, name="Not sales", type="onboarding", owner="sam")
    make_deal(
        store,
        room_id=other["id"],
        crm_deal_id="GATE",
        stage="Closed Won",
        amount=9000,
        currency="USD",
        created_date="2026-01-01",
        closed_at="2026-02-01",
    )
    body = impact.report(parse_filters({}))
    assert body["tiles"]["total_deals"] == 0
    assert body["tiles"]["revenue"] == 0.0
    assert body["tiles"]["closed_won_deals"] == 0
    assert body["tiles"]["close_rate"] is None
    assert body["funnel"] == []


def test_scope_is_decided_once_so_a_deal_and_its_room_cannot_disagree(impact, store):
    """``in_scope`` on a single deal is resolved through the same ``population``."""
    room = make_room(store, name="In", type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="X", stage="Negotiation", amount=10)
    deal_id = store.list(DEAL_COLLECTION)[0]["id"]
    assert impact.read_deal(deal_id)["view"]["in_scope"] is True

    out = make_room(store, name="Out", type="onboarding")
    make_deal(store, room_id=out["id"], crm_deal_id="Y", stage="Negotiation", amount=10)
    orphan = store.list(DEAL_COLLECTION)[0]["id"]
    assert impact.read_deal(orphan)["view"]["in_scope"] is False
    assert impact.read_deal(orphan)["view"]["reason"] == "not_sales"


# --------------------------------------------------------------------------- #
# The eight tiles
# --------------------------------------------------------------------------- #


def _deal(**overrides):
    base = {
        "id": "d",
        "room_id": "r",
        "crm_deal_id": None,
        "name": "",
        "account": "",
        "stage": "",
        "amount": None,
        "currency": None,
        "owner": "",
        "team": "",
        "created": None,
        "created_source": "crm",
        "closed": None,
    }
    base.update(overrides)
    return rollup.classify_deal(base)


def test_the_close_rate_is_the_researched_fraction():
    """"How many workspaces with deals/opportunities that have been closed won, divided by
    the total (closed won + closed lost)."

    The denominator is the researched one: closed won plus closed lost, with open deals in
    neither arm. An open deal is *not* a loss.
    """
    deals = [
        _deal(stage="Closed Won", id="1"),
        _deal(stage="Closed Won", id="2"),
        _deal(stage="Closed Lost", id="3"),
        _deal(stage="Negotiation", id="4"),
        _deal(stage="Escalated", id="5"),
    ]
    assert rollup.tiles(deals)["close_rate"] == 0.6667


def test_a_population_with_nothing_closed_reports_null_not_zero():
    """0/0 is not 0%. Reporting 0% asserts every deal was lost, which is a different claim."""
    for deals in ([], [_deal(stage="Negotiation")], [_deal(stage="Escalated to the board")]):
        assert rollup.tiles(deals)["close_rate"] is None
        assert rollup.tiles(deals)["days_to_close"] is None


def test_pipeline_touched_is_the_whole_population_and_active_pipeline_is_the_open_part():
    """D1: the two money tiles only mean different things under this reading."""
    deals = [
        _deal(stage="Negotiation", amount=500, currency="USD", id="1"),
        _deal(stage="Closed Won", amount=1000, currency="USD", id="2"),
        _deal(stage="Closed Lost", amount=250, currency="USD", id="3"),
    ]
    figures = rollup.tiles(deals)
    assert figures["total_pipeline_touched"] == 1750.0
    assert figures["active_pipeline"] == 500.0
    assert figures["revenue"] == 1000.0
    assert figures["active_deals"] == 1


def test_active_pipeline_is_not_permanently_zero():
    """The regression: ``stage_class == "open"`` is a value ``classify_stage`` never returns.

    It returns won, lost or unknown, so the test never fired and the tile sat at zero on a
    report whose every deal was mid-negotiation - the most ordinary report there is.
    """
    mid = _deal(stage="Negotiation", amount=500, currency="USD")
    assert mid["stage_class"] == "unknown", "the classifier's vocabulary, and the point"
    assert rollup.tiles([mid])["active_pipeline"] == 500.0
    assert rollup.tiles([mid])["active_deals"] == 1
    assert rollup.money_by_currency([mid], currency="USD")["USD"]["active_pipeline"] == 500.0


def test_revenue_is_closed_won_only():
    deals = [
        _deal(stage="Closed Won", amount=1000, currency="USD", id="1"),
        _deal(stage="Closed Lost", amount=5000, currency="USD", id="2"),
    ]
    assert rollup.tiles(deals)["revenue"] == 1000.0


def test_a_deal_with_no_amount_counts_and_contributes_nothing_to_the_sums():
    deals = [_deal(stage="Negotiation", amount=None, currency="USD")]
    figures = rollup.tiles(deals)
    assert figures["total_deals"] == 1
    assert figures["total_pipeline_touched"] == 0.0
    assert figures["active_pipeline"] == 0.0


def test_days_to_close_averages_every_closed_deal():
    """D5: the researched name with no formula. Won and lost; the start is the deal's own
    creation date, which is the date the report's Deals Created Over Time already shows."""
    deals = [
        _deal(stage="Closed Won", created=date(2026, 1, 1), closed=date(2026, 1, 31), id="1"),
        _deal(stage="Closed Lost", created=date(2026, 1, 1), closed=date(2026, 1, 11), id="2"),
    ]
    assert rollup.days_between(date(2026, 1, 1), date(2026, 1, 31)) == 30.0
    assert rollup.days_between(date(2026, 1, 1), date(2026, 1, 11)) == 10.0
    assert rollup.tiles(deals)["days_to_close"] == 20.0


def test_a_close_date_before_the_created_date_is_excluded_not_averaged_in():
    """A negative day count is arithmetic swallowing an error, not a finding."""
    assert rollup.days_between(date(2026, 3, 1), date(2026, 1, 1)) is None
    deals = [
        _deal(stage="Closed Won", created=date(2026, 1, 1), closed=date(2026, 1, 31), id="1"),
        _deal(stage="Closed Won", created=date(2026, 2, 1), closed=date(2026, 1, 1), id="2"),
    ]
    assert rollup.tiles(deals)["days_to_close"] == 30.0


def test_a_closed_deal_with_no_close_date_is_excluded_from_the_average():
    assert rollup.tiles([_deal(stage="Closed Won", created=date(2026, 1, 1))])["days_to_close"] is None


def test_a_row_is_projected_exactly_once():
    """The regression that made the whole report confidently wrong.

    ``report`` re-projected its input rows. A projected row is flat and a stored record is
    enveloped, so the second projection read ``record["data"]`` off a dict that had none:
    every field came back empty and the report returned three unknown-stage deals worth
    nothing, with no error anywhere.
    """
    store_record = {
        "id": "d1",
        "room_id": "r1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data": {
            "crm_deal_id": "006X",
            "name": "A deal",
            "stage": "Closed Won",
            "amount": 1000,
            "currency": "USD",
            "created_date": "2026-01-01",
            "closed_at": "2026-02-01",
        },
    }
    projected = rollup.project_deal(store_record)
    assert projected["stage"] == "Closed Won"
    assert projected["amount"] == 1000.0
    # Projecting the projection must be a no-op, not an emptying.
    again = rollup.project_deal(projected)
    assert again["stage"] == projected["stage"]
    assert again["amount"] == projected["amount"]
    assert again["created"] == projected["created"]


def test_a_deal_with_no_crm_creation_date_falls_back_to_the_recorded_one_and_says_so(store, impact):
    """The envelope's ``created_at`` is the only date guaranteed present, so it is used -
    and ``data_warnings`` says the interval is measured from it, because "when we first
    recorded this" and "when the CRM created the opportunity" are different facts."""
    room = make_room(store, type="Sales")
    record = make_deal(store, room_id=room["id"], crm_deal_id="NODATE", stage="Closed Won", amount=10)
    body = impact.report(parse_filters({}))
    codes = {row["code"] for row in body["data_warnings"]}
    assert "recorded_created_date" in codes
    assert body["deals_created_over_time"][0]["deals"] == 1
    del record


# --------------------------------------------------------------------------- #
# Currency
# --------------------------------------------------------------------------- #


def test_money_is_summed_in_one_currency_and_the_split_is_returned():
    """The research never mentions currency, and a single figure adding EUR to USD is a
    fiction nobody knows to distrust."""
    deals = [
        _deal(stage="Closed Won", amount=1000, currency="USD", id="1"),
        _deal(stage="Closed Won", amount=1000, currency="USD", id="2"),
        _deal(stage="Closed Won", amount=500, currency="EUR", id="3"),
    ]
    assert rollup.choose_currency(deals) == "USD"
    assert rollup.tiles(deals)["revenue"] == 2000.0, "the EUR deal is not in the USD figure"
    split = rollup.money_by_currency(deals, currency="USD")
    assert split == {
        "EUR": {"deals": 1.0, "pipeline_touched": 500.0, "active_pipeline": 0.0, "revenue": 500.0},
        "USD": {"deals": 2.0, "pipeline_touched": 2000.0, "active_pipeline": 0.0, "revenue": 2000.0},
    }


def test_the_report_currency_is_the_one_on_the_most_deals():
    deals = [
        _deal(currency="USD", id="1"),
        _deal(currency="EUR", id="2"),
        _deal(currency="EUR", id="3"),
    ]
    assert rollup.choose_currency(deals) == "EUR"


def test_a_currency_tie_is_broken_deterministically():
    assert rollup.choose_currency([_deal(currency="USD"), _deal(currency="EUR")]) == "EUR"
    assert rollup.choose_currency([_deal(currency="EUR"), _deal(currency="USD")]) == "EUR"


def test_a_deal_with_no_currency_joins_the_report_currency():
    deals = [_deal(stage="Closed Won", amount=100, currency=None, id="1")]
    assert rollup.choose_currency(deals) == "USD"
    assert rollup.tiles(deals)["revenue"] == 100.0


def test_mixed_currencies_produce_a_named_warning(store, impact):
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="U", stage="Closed Won", amount=10, currency="USD")
    make_deal(store, room_id=room["id"], crm_deal_id="E", stage="Closed Won", amount=20, currency="EUR")
    body = impact.report(parse_filters({}))
    assert "mixed_currency" in {row["code"] for row in body["warnings"]}
    assert body["currencies"] == ["EUR", "USD"]
    assert body["currency"] == "EUR"


# --------------------------------------------------------------------------- #
# The panels
# --------------------------------------------------------------------------- #


def test_the_funnel_reconciles_with_the_tiles():
    """"Deals By Owner" and the stage filter are the same deals, so a reader can check the
    close rate against the funnel instead of taking it on trust."""
    deals = [
        _deal(stage="Closed Won", amount=100, currency="USD", id="1"),
        _deal(stage="Closed Won", amount=200, currency="USD", id="2"),
        _deal(stage="Closed Lost", amount=300, currency="USD", id="3"),
        _deal(stage="Negotiation", amount=400, currency="USD", id="4"),
    ]
    figures = rollup.tiles(deals)
    rows = rollup.funnel(deals)
    assert sum(row["deals"] for row in rows) == figures["total_deals"]
    assert sum(row["won"] for row in rows) == figures["closed_won_deals"]
    assert sum(row["revenue"] for row in rows) == figures["revenue"]
    assert sum(row["open"] for row in rows) == figures["active_deals"]


def test_the_funnel_shows_an_unclassifiable_stage_rather_than_hiding_it():
    rows = rollup.funnel([_deal(stage="Escalated to the board", amount=1)])
    assert rows[0]["stage"] == "Escalated to the board"
    assert rows[0]["class"] == "unknown"
    assert rows[0]["open"] == 1


def test_deals_created_over_time_is_ascending_and_fills_gaps():
    deals = [
        _deal(stage="Negotiation", created=date(2026, 2, 1), amount=10, currency="USD"),
        _deal(stage="Negotiation", created=date(2026, 2, 4), amount=20, currency="USD"),
    ]
    rows = rollup.deals_created_over_time(
        deals, parse_filters({"from": "2026-02-01", "to": "2026-02-04"}), currency="USD"
    )
    assert [row["date"] for row in rows] == [
        "2026-02-01",
        "2026-02-02",
        "2026-02-03",
        "2026-02-04",
    ]
    assert [row["deals"] for row in rows] == [1, 0, 0, 1]
    assert [row["amount"] for row in rows] == [10.0, 0.0, 0.0, 20.0]


def test_deals_by_owner_reports_provenance_so_a_borrowed_owner_is_visible():
    """D10: a panel of "unassigned" rows is unreadable, and a borrowed owner that does not
    say it was borrowed is a lie."""
    deals = [
        _deal(stage="Negotiation", amount=100, owner="dana", room_owner="sam", id="1"),
        _deal(stage="Negotiation", amount=50, owner="", room_owner="sam", id="2"),
        _deal(stage="Negotiation", amount=10, owner="", room_owner="", id="3"),
    ]
    rows = {row["owner"]: row for row in rollup.deals_by_owner(deals)}
    assert rows["dana"]["owner_source"] == "deal"
    assert rows["sam"]["owner_source"] == "room"
    assert rows["(unassigned)"]["owner_source"] == "unassigned"
    assert rows["sam"]["deals"] == 1, "the borrowed deal is grouped under the room's owner"


def test_deals_by_owner_is_ordered_by_amount_then_name():
    deals = [
        _deal(stage="Negotiation", amount=100, owner="zoe", id="1"),
        _deal(stage="Negotiation", amount=100, owner="adam", id="2"),
        _deal(stage="Negotiation", amount=900, owner="mel", id="3"),
    ]
    assert [row["owner"] for row in rollup.deals_by_owner(deals)] == ["mel", "adam", "zoe"]


# --------------------------------------------------------------------------- #
# Engagement
# --------------------------------------------------------------------------- #


def test_actions_include_views_and_the_two_counters_differ():
    """D8: the researched gloss of an action is interacting with a space, and clicking
    into a page is a view - so the action count is the larger one."""
    events = [
        {"id": "1", "room_id": "r", "buyer": "a@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 1)},
        {"id": "2", "room_id": "r", "buyer": "a@x", "action": "downloaded", "is_view": False, "occurred": date(2026, 2, 2)},
        {"id": "3", "room_id": "r", "buyer": "b@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 2)},
    ]
    body = rollup.engagement(events, parse_filters({}), in_scope_rooms=1)
    assert body["buyer_views"] == 2
    assert body["buyer_actions"] == 3
    assert body["unique_buyers"] == 2
    assert body["average_buyers_per_workspace"] == 2.0


def test_average_buyers_divides_by_every_in_scope_room_not_only_engaged_ones():
    """Averaging over the rooms that were touched makes a thin pipeline look dense."""
    events = [{"id": "1", "room_id": "r", "buyer": "a@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 1)}]
    assert rollup.engagement(events, parse_filters({}), in_scope_rooms=1)["average_buyers_per_workspace"] == 1.0
    assert rollup.engagement(events, parse_filters({}), in_scope_rooms=5)["average_buyers_per_workspace"] == 0.2
    assert rollup.engagement([], parse_filters({}), in_scope_rooms=0)["average_buyers_per_workspace"] is None


def test_an_event_with_no_resolvable_buyer_is_skipped(store):
    """A buyer is identified by email, the only identifier the research gives. Filing an
    unnamed event under an empty key would rank it above every real buyer."""
    assert rollup.project_event({"id": "x", "data": {"action": "viewed"}}) is None
    assert rollup.project_event({"id": "x", "data": {"person": "  ", "action": "viewed"}}) is None
    assert rollup.project_event({"id": "x", "data": {"person": "A@X", "action": "viewed"}})["buyer"] == "a@x"


def test_the_buyer_ranking_is_ordered_by_actions_then_views_then_email():
    events = [
        {"id": "1", "room_id": "r", "buyer": "z@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 1)},
        {"id": "2", "room_id": "r", "buyer": "a@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 1)},
        {"id": "3", "room_id": "r", "buyer": "a@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 2)},
        {"id": "4", "room_id": "r", "buyer": "m@x", "action": "downloaded", "is_view": False, "occurred": date(2026, 2, 2)},
        {"id": "5", "room_id": "r", "buyer": "m@x", "action": "viewed", "is_view": True, "occurred": date(2026, 2, 3)},
        {"id": "6", "room_id": "r", "buyer": "b@x", "action": "downloaded", "is_view": False, "occurred": date(2026, 2, 4)},
    ]
    ranked = rollup.engagement(events, parse_filters({}), in_scope_rooms=1)["most_engaged_buyers"]
    # a@x and m@x both have 2 actions; a@x has more views, so it ranks first. z@x and b@x
    # both have 1 action; z@x has a view and b@x does not, so z@x ranks third on the
    # second key rather than on its email.
    assert [row["buyer"] for row in ranked] == ["a@x", "m@x", "z@x", "b@x"]
    assert ranked[0]["workspaces"] == 1
    assert ranked[0]["last_view_at"] == "2026-02-02"
    assert ranked[1]["last_view_at"] == "2026-02-03", "last_view_at tracks views, not actions"
    assert ranked[3]["last_view_at"] is None, "a buyer who only downloaded never viewed"


def test_engagement_is_counted_only_in_in_scope_workspaces(store, impact):
    """D7: a view in a workspace with no deal attached is not evidence it moved pipeline,
    and without this the engagement half answers a different question than the revenue
    half beside it."""
    in_scope = make_room(store, name="In", type="Sales")
    out_of_scope = make_room(store, name="Out", type="onboarding")
    make_deal(store, room_id=in_scope["id"], crm_deal_id="A", stage="Negotiation")
    make_deal(store, room_id=out_of_scope["id"], crm_deal_id="B", stage="Negotiation")
    for room in (in_scope, out_of_scope):
        for day in range(1, 6):
            make_event(store, room["id"], "buyer@x.example", "viewed", f"2026-02-0{day}")

    body = impact.report(parse_filters({}))
    assert body["engagement"]["buyer_views"] == 5, "the out-of-scope room's five views are not counted"
    assert body["engagement"]["average_buyers_per_workspace"] == 1.0


def test_an_in_scope_deal_with_no_engagement_is_still_in_the_pipeline(store, impact):
    """Pipeline touched does not require engagement, and a report that dropped these
    would understate pipeline for exactly the deals nobody looked at."""
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="QUIET", stage="Negotiation", amount=500, currency="USD")
    body = impact.report(parse_filters({}))
    assert body["tiles"]["total_pipeline_touched"] == 500.0
    assert body["engagement"]["buyer_views"] == 0
    assert body["engagement"]["unique_buyers"] == 0


def test_the_views_series_is_bucketed_and_zero_filled(store, impact):
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="A", stage="Negotiation")
    # Mon 2026-02-02 and Wed 2026-02-04 are one ISO week; Sun 2026-02-01 is the week before.
    make_event(store, room["id"], "a@x.example", "viewed", "2026-02-02")
    make_event(store, room["id"], "a@x.example", "viewed", "2026-02-04")
    make_event(store, room["id"], "a@x.example", "viewed", "2026-02-01")

    daily = impact.engagement(parse_filters({"from": "2026-02-01", "to": "2026-02-04"}))
    assert [row["date"] for row in daily["buyer_views_over_time"]] == [
        "2026-02-01",
        "2026-02-02",
        "2026-02-03",
        "2026-02-04",
    ]
    assert [row["views"] for row in daily["buyer_views_over_time"]] == [1, 1, 0, 1]
    assert all("views" in row and "actions" in row for row in daily["buyer_views_over_time"]), (
        "a zero-filled day still carries the keys; the shared bucket builder returns "
        "deal-shaped rows, and a quiet day handed through unchanged is a KeyError on the "
        "page rather than a gap in the chart"
    )

    weekly = impact.engagement(parse_filters({"from": "2026-02-01", "to": "2026-02-04", "bucket": "week"}))
    assert [row["date"] for row in weekly["buyer_views_over_time"]] == ["2026-01-26", "2026-02-02"]
    assert [row["views"] for row in weekly["buyer_views_over_time"]] == [1, 2], (
        "two views in the same week aggregate into one bar, and the week before keeps its own"
    )

    monthly = impact.engagement(parse_filters({"from": "2026-02-01", "to": "2026-02-28", "bucket": "month"}))
    assert [row["date"] for row in monthly["buyer_views_over_time"]] == ["2026-02-01"]
    assert monthly["buyer_views_over_time"][0]["views"] == 3


# --------------------------------------------------------------------------- #
# Coverage - the researched incompleteness
# --------------------------------------------------------------------------- #


def test_coverage_names_the_rooms_the_research_warns_about(store, impact):
    """"unless you are requiring reps attach a deal to each space, it's possible this
    report is missing data" - a tile cannot say which rooms, so the panel names them."""
    make_room(store, name="Bare", type="Sales")
    make_room(store, name="Untyped", type="onboarding")
    covered = make_room(store, name="Covered", type="Sales")
    make_deal(store, room_id=covered["id"], crm_deal_id="A", stage="Negotiation")

    body = impact.coverage()
    assert [row["name"] for row in body["rooms_without_deal"]] == ["Bare"]
    assert [row["name"] for row in body["untyped"]] == ["Untyped"]
    assert body["rooms_total"] == 3
    assert body["sales_typed_rooms"] == 2
    assert body["in_scope_rooms"] == 1
    assert body["with_deal"] == 1
    assert body["without_deal"] == 1
    assert body["untyped_rooms"] == 1
    assert "sales_room_without_deal" in {row["code"] for row in body["warnings"]}
    assert "workspace_not_typed_sales" in {row["code"] for row in body["warnings"]}


def test_a_report_answers_even_when_the_integration_is_off(store, impact):
    """S10 says the report is *incomplete*, not wrong. A refusal would hide the very rooms
    a reader needs in order to fix it."""
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="A", stage="Negotiation", amount=10, currency="USD")
    body = impact.report(parse_filters({}))
    assert body["coverage"]["crm_connected"] is False
    assert body["coverage"]["complete"] is False
    assert "crm_integration_off" in {row["code"] for row in body["warnings"]}
    assert body["tiles"]["total_deals"] == 1


def test_complete_needs_the_integration_on_and_every_sales_room_covered(store, impact):
    room = make_room(store, name="In", type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="A", stage="Negotiation")
    impact.set_config({"connected": True}, actor="dana", source="seed")
    assert impact.coverage()["complete"] is True

    make_room(store, name="Bare", type="Sales")
    assert impact.coverage()["complete"] is False


def test_a_deal_attached_to_nothing_is_reported_as_contributing_to_nothing(store, impact):
    make_room(store, type="Sales")
    make_deal(store, crm_deal_id="ORPHAN", stage="Closed Won", amount=99, currency="USD")
    body = impact.coverage()
    assert body["deals_not_attached"] == 1
    assert "deal_not_attached" in {row["code"] for row in body["warnings"]}
    assert impact.report(parse_filters({}))["tiles"]["revenue"] == 0.0


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_two_reports_over_the_same_data_are_byte_identical(store, impact):
    room = make_room(store, type="Sales")
    for index in range(6):
        make_deal(
            store,
            room_id=room["id"],
            crm_deal_id=f"D{index}",
            stage=("Closed Won", "Closed Lost", "Negotiation")[index % 3],
            amount=index * 10,
            currency="USD",
            owner=("dana", "sam")[index % 2],
            created_date=f"2026-0{index + 1}-01",
        )
        make_event(store, room["id"], f"buyer{index}@x.example", "viewed", f"2026-0{index + 1}-02")

    first = impact.report(parse_filters({}))
    second = impact.report(parse_filters({}))
    assert first == second


def test_every_listing_carries_an_explicit_ordering_key(store, impact):
    room = make_room(store, type="Sales", owner="dana")
    for index in range(3):
        make_deal(
            store,
            room_id=room["id"],
            crm_deal_id=f"D{index}",
            stage=("Closed Won", "Negotiation", "Closed Lost")[index],
            amount=index * 10,
            currency="USD",
            created_date="2026-01-01",
        )
    body = impact.report(parse_filters({}))
    # The deals carry no owner, so all three borrow the workspace's, and one row comes back.
    assert [row["owner"] for row in body["deals_by_owner"]] == ["dana"]
    assert body["deals_by_owner"][0]["owner_source"] == "room"
    assert [row["date"] for row in body["deals_created_over_time"]] == sorted(
        row["date"] for row in body["deals_created_over_time"]
    )
    assert body["deals_created_over_time"][0]["deals"] == 3
    funnel_counts = [row["deals"] for row in body["funnel"]]
    assert funnel_counts == sorted(funnel_counts, reverse=True), "the funnel is ordered by count then name"


# --------------------------------------------------------------------------- #
# The inference registry
# --------------------------------------------------------------------------- #


def test_every_inference_names_what_was_chosen_and_how_to_change_it():
    """A judgement call left in a comment is one nobody re-reads."""
    assert INFERENCES
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"]
        assert entry["basis"], f"{entry['id']} does not say what the research does or does not say"
        assert entry["value"] is not None
        assert entry["why"], f"{entry['id']} does not say why"
        assert entry["change_it"], f"{entry['id']} does not say how to change it"
        assert entry["blast_radius"]


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inference_endpoint_serves_the_registry_beside_the_sourced_quotes():
    from dsr.salesimpact.inferences import describe

    served = describe()
    assert served["count"] == len(INFERENCES)
    assert "The Sales Impact report pulls in any workspace designated as a 'Sales' type" in served["sourced_quotes"]["inclusion"]
    assert served["sourced"]["close_rate_formula"] == "closed_won / (closed_won + closed_lost)"


def test_the_change_it_pointer_of_every_inference_names_a_real_file():
    """A pointer to a file that does not exist is worse than no pointer."""
    backend = Path(__file__).resolve().parents[1]
    for entry in INFERENCES:
        for token in entry["change_it"].replace("(", " ").replace(")", " ").replace(",", " ").split():
            if token.startswith("dsr/") and token.endswith(".py"):
                assert (backend / token).is_file(), f"{entry['id']} points at {token}, which does not exist"


# --------------------------------------------------------------------------- #
# The deal book - writes
# --------------------------------------------------------------------------- #


def test_a_deal_with_neither_a_crm_id_nor_a_name_is_refused(store, book):
    room = make_room(store, type="Sales")
    with pytest.raises(InvalidDeal) as excinfo:
        book.create_deal({"amount": 10}, room_id=room["id"], source=SOURCE)
    assert "crm_deal_id" in str(excinfo.value)


def test_only_one_of_a_crm_id_or_a_name_is_required(store, book):
    room = make_room(store, type="Sales")
    by_id = book.create_deal({"crm_deal_id": "ONLY-ID"}, room_id=room["id"], source=SOURCE)
    by_name = book.create_deal({"name": "Only a name"}, room_id=room["id"], source=SOURCE)
    assert by_id["data"]["crm_deal_id"] == "ONLY-ID"
    assert by_name["data"]["name"] == "Only a name"


def test_re_registering_a_crm_id_is_a_conflict_that_names_the_existing_record(store, book):
    room = make_room(store, type="Sales")
    first = book.create_deal(
        {"crm_deal_id": "DUP", "amount": 100, "stage": "Negotiation"}, room_id=room["id"], source=SOURCE
    )
    with pytest.raises(DealConflict) as excinfo:
        book.create_deal({"crm_deal_id": "DUP", "amount": 999}, room_id=room["id"], source=SOURCE)
    assert excinfo.value.existing_id == first["id"]
    assert excinfo.value.existing_room_id == room["id"]
    assert len(store.list(DEAL_COLLECTION)) == 1, "a conflict must not leave a second row"


def test_a_conflict_is_detected_across_separator_spellings(store, book):
    """A CRM that spells an id differently means the same deal, not a new one."""
    room = make_room(store, type="Sales")
    book.create_deal({"crm_deal_id": "006-NW-1"}, room_id=room["id"], source=SOURCE)
    with pytest.raises(DealConflict):
        book.create_deal({"crm_deal_id": "006_nw_1"}, room_id=room["id"], source=SOURCE)


def test_attaching_to_a_room_that_does_not_exist_is_refused(store, book):
    with pytest.raises(UnknownWorkspace):
        book.create_deal({"crm_deal_id": "ORPHAN"}, room_id="room_absent", source=SOURCE)


def test_a_deal_with_no_room_is_stored_and_reported_as_unattached(store, book):
    """Not an error: a CRM extract arrives before anybody attaches it, and the coverage
    panel is the place that says so."""
    record = book.create_deal({"crm_deal_id": "UNATTACHED"}, source=SOURCE)
    assert record["room_id"] is None
    assert book.config() is not None


def test_the_researched_workspace_spelling_is_accepted_and_kept(store, book):
    room = make_room(store, type="Sales")
    record = book.create_deal({"crm_deal_id": "WS", "workspace_id": room["id"]}, source=SOURCE)
    assert record["room_id"] == room["id"]
    assert record["data"]["workspace_id"] == room["id"]


def test_an_unknown_field_round_trips_untouched(store, book):
    room = make_room(store, type="Sales")
    record = book.create_deal(
        {"crm_deal_id": "X", "renewal_probability": 0.42, "nested": {"a": [1, 2]}},
        room_id=room["id"],
        source=SOURCE,
    )
    assert record["data"]["renewal_probability"] == 0.42
    assert record["data"]["nested"] == {"a": [1, 2]}


def test_the_stage_amount_sync_is_a_shallow_merge(store, book):
    room = make_room(store, type="Sales")
    record = book.create_deal(
        {"crm_deal_id": "S", "name": "Keep me", "stage": "Negotiation", "amount": 100, "nested": {"a": 1}},
        room_id=room["id"],
        source=SOURCE,
    )
    updated = book.update_deal(
        record["id"], {"stage": "Closed Won", "amount": 250, "nested": {"b": 2}}, source=SOURCE
    )
    assert updated["data"]["stage"] == "Closed Won"
    assert updated["data"]["amount"] == 250
    assert updated["data"]["name"] == "Keep me", "a merge patch does not clear what it does not name"
    assert updated["data"]["nested"] == {"b": 2}, "the patch is shallow, by the store's contract"
    assert updated["revision"] == 2


def test_a_field_is_cleared_by_sending_json_null(store, book):
    room = make_room(store, type="Sales")
    record = book.create_deal({"crm_deal_id": "N", "team": "enterprise"}, room_id=room["id"], source=SOURCE)
    assert book.update_deal(record["id"], {"team": None}, source=SOURCE)["data"]["team"] is None


def test_detaching_is_soft_so_the_history_survives(store, book):
    room = make_room(store, type="Sales")
    record = book.create_deal({"crm_deal_id": "D"}, room_id=room["id"], source=SOURCE)
    book.delete_deal(record["id"], source=SOURCE)
    assert book.deal(record["id"]) is None
    still_there = store.list(DEAL_COLLECTION, include_deleted=True, limit=10)
    assert [row["id"] for row in still_there] == [record["id"]]
    assert still_there[0]["deleted_at"] is not None


def test_derived_fields_are_computed_on_read_and_never_stored(store, impact):
    """The research says the stage changes by sync, so a stored ``stage_class`` would be
    a cache that goes stale against the next push."""
    room = make_room(store, type="Sales")
    record = store.create(
        DEAL_COLLECTION,
        {"crm_deal_id": "D", "stage": "Negotiation", "amount": 10, "created_date": "2026-01-01",
         "closed_at": "2026-01-31"},
        room_id=room["id"],
        actor="dana",
        source="seed",
    )
    view = impact.read_deal(record["id"])["view"]
    assert view["stage_class"] == "unknown"
    assert "stage_class" not in record["data"]
    assert "owner_source" not in record["data"]
    impact.patch_deal(record["id"], {"stage": "Closed Won"}, source=SOURCE)
    assert store.get(record["id"])["data"]["stage"] == "Closed Won"
    assert impact.read_deal(record["id"])["view"]["stage_class"] == "won"


def test_a_deal_id_in_another_collection_is_not_a_deal(store, book):
    """One 404 shape across a read, a patch and a delete."""
    document = store.create("document", {"title": "x"}, source="seed")
    with pytest.raises(UnknownWorkspace):
        book.require_deal(document["id"])


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_a_fresh_database_reports_the_built_in_defaults(store, impact):
    """A report that 500s because no config record exists is not a report."""
    config = impact.config()
    assert config["configured"] is False
    assert config["integration"] == {"provider": "", "connected": False, "connected_at": None}
    assert config["collections"] == {"rooms": "room", "engagement": "activity"}
    assert impact.report(parse_filters({}))["tiles"]["total_deals"] == 0


def test_the_first_configuration_write_creates_the_record_and_is_audited(store, impact):
    before = len(store.audit(collection="sales_impact_config"))
    impact.set_config({"connected": True, "provider": "salesforce"}, source=SOURCE)
    after = store.audit(collection="sales_impact_config")
    assert len(after) == before + 1
    assert after[0]["action"] == "insert"
    assert impact.config()["configured"] is True


def test_toggling_the_integration_records_when_it_came_on(store, impact):
    impact.set_config({"connected": True, "provider": "hubspot"}, source=SOURCE)
    assert impact.integration()["connected"] is True
    assert impact.integration()["connected_at"]
    impact.set_config({"connected": False}, source=SOURCE)
    assert impact.integration()["connected"] is False
    assert impact.integration()["connected_at"] is None


def test_the_provider_is_free_text_because_the_research_defines_no_enum(impact):
    impact.set_config({"connected": True, "provider": "some-other-crm"}, source=SOURCE)
    assert impact.integration()["provider"] == "some-other-crm"


def test_the_engagement_collection_can_be_repointed_without_a_code_change(store, impact):
    """A team with its own webhook-derived store needs a record, not a pull request."""
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="A", stage="Negotiation")
    store.create(
        "my_webhook_events",
        {"person": "buyer@x.example", "action": "viewed", "occurred_at": "2026-02-01"},
        room_id=room["id"],
        actor="system",
        source="seed",
    )
    assert impact.report(parse_filters({}))["engagement"]["buyer_views"] == 0

    impact.set_config({"engagement": "my_webhook_events"}, source=SOURCE)
    assert impact.config()["collections"]["engagement"] == "my_webhook_events"
    assert impact.report(parse_filters({}))["engagement"]["buyer_views"] == 1


def test_a_stage_set_can_be_replaced_by_a_record(store, impact):
    """A team's own CRM spelling is a record, not a code change."""
    room = make_room(store, type="Sales")
    make_deal(store, room_id=room["id"], crm_deal_id="A", stage="Gewonnen", amount=10, currency="USD")
    make_deal(store, room_id=room["id"], crm_deal_id="B", stage="Verloren", amount=20, currency="USD")
    # Unconfigured, neither is classifiable - and in particular "Gewonnen" is not "won".
    assert impact.report(parse_filters({}))["tiles"]["revenue"] == 0.0
    assert impact.report(parse_filters({}))["tiles"]["close_rate"] is None

    impact.set_config({"fields": {"stage": {"won": ["Gewonnen"], "lost": ["Verloren"]}}}, source=SOURCE)
    figures = impact.report(parse_filters({}))["tiles"]
    assert figures["revenue"] == 10.0
    assert figures["close_rate"] == 0.5


def test_a_configuration_patch_does_not_drop_overrides_it_does_not_name(store, impact):
    impact.set_config({"connected": True, "provider": "salesforce"}, source=SOURCE)
    impact.set_config({"engagement": "events_v2"}, source=SOURCE)
    config = impact.config()
    assert config["integration"]["provider"] == "salesforce"
    assert config["collections"]["engagement"] == "events_v2"


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


def test_the_report_returns_the_eight_researched_tiles(http, seeded):
    """"Sales Impact (synced to your CRM): Total deals, Total pipeline touched, Active
    deals, Active pipeline, Closed won deals, Revenue, Close rate, Days to close"."""
    body = http.get(f"{PREFIX}/report").json()
    assert set(body["tiles"]) == {
        "total_deals",
        "total_pipeline_touched",
        "active_deals",
        "active_pipeline",
        "closed_won_deals",
        "revenue",
        "close_rate",
        "days_to_close",
    }
    assert list(rollup.TILES) == [
        "total_deals",
        "total_pipeline_touched",
        "active_deals",
        "active_pipeline",
        "closed_won_deals",
        "revenue",
        "close_rate",
        "days_to_close",
    ]


def test_the_report_returns_both_panels_and_the_engagement_half(http, seeded):
    """"Deals Created Over Time" and "Deals By Owner", then "Buyer Views, Buyer Actions,
    Buyer Views Over Time, Most Engaged Buyers"."""
    body = http.get(f"{PREFIX}/report").json()
    assert isinstance(body["deals_created_over_time"], list) and body["deals_created_over_time"]
    assert isinstance(body["deals_by_owner"], list) and body["deals_by_owner"]
    engagement = body["engagement"]
    assert engagement["buyer_views"] == 5
    assert engagement["buyer_actions"] == 6
    assert engagement["unique_buyers"] == 2
    assert engagement["most_engaged_buyers"]
    assert isinstance(engagement["buyer_views_over_time"], list)
    assert "average_buyers_per_workspace" in engagement


def test_the_report_carries_the_scope_and_the_filter_it_used(http, seeded):
    body = http.get(f"{PREFIX}/report", params={"team": "enterprise"}).json()
    assert body["filters"]["team"] == ["enterprise"]
    assert body["scope"]["rooms_total"] == 4
    assert body["scope"]["sales_typed_rooms"] == 2
    assert {(row["name"], row["reason"]) for row in body["scope"]["excluded"]} == {
        ("Contoso", "no_deal"),
        ("Fabrikam", "not_sales"),
        ("Wingtip", "not_sales"),
    }


def test_the_misfiled_deal_reaches_no_tile_over_http(http, seeded):
    """A real amount, on a real deal, on a workspace that is not Sales-typed."""
    body = http.get(f"{PREFIX}/report").json()
    assert body["tiles"]["total_deals"] == 3
    assert body["tiles"]["revenue"] == 1000.0
    assert body["tiles"]["close_rate"] == 0.5
    assert body["tiles"]["days_to_close"] == 20.0


def test_a_filter_narrows_the_tiles_and_the_drill_in_together(http, seeded):
    body = http.get(f"{PREFIX}/report", params={"stage": "won"}).json()
    assert body["tiles"]["total_deals"] == 1
    assert body["tiles"]["revenue"] == 1000.0
    drilled = http.get(f"{PREFIX}/deals", params={"stage": "won"}).json()
    assert drilled["count"] == 1
    assert drilled["deals"][0]["crm_deal_id"] == "006NW-1"
    assert drilled["totals"]["revenue"] == 1000.0


def test_a_filter_matching_nothing_returns_zeroes_not_an_error(http, seeded):
    body = http.get(f"{PREFIX}/report", params={"team": "nobody-here"}).json()
    assert body["tiles"]["total_deals"] == 0
    assert body["tiles"]["close_rate"] is None
    assert body["funnel"] == []


def test_limit_pages_the_list_but_never_the_totals(http, seeded):
    everything = http.get(f"{PREFIX}/deals", params={"limit": 1000}).json()
    page = http.get(f"{PREFIX}/deals", params={"limit": 1}).json()
    assert page["returned"] == 1
    assert page["count"] == everything["count"]
    assert page["totals"] == everything["totals"], "a page must not change a quotable number"


def test_the_room_drill_in_reports_eligibility_with_a_reason_not_a_404(http, seeded):
    """"To populate this report, remember to set the workspace type" - so the most likely
    reason to open this is "why is mine not in the numbers", and 404 says "no such
    room", which is a different and wrong answer."""
    body = http.get(f"{PREFIX}/report/rooms/{seeded['bare']['id']}").json()
    assert body["in_scope"] is False
    assert body["reason"] == "no_deal"
    assert body["deals"] == []

    other = http.get(f"{PREFIX}/report/rooms/{seeded['other']['id']}").json()
    assert other["in_scope"] is False
    assert other["reason"] == "not_sales"

    inside = http.get(f"{PREFIX}/report/rooms/{seeded['sales']['id']}").json()
    assert inside["in_scope"] is True
    assert inside["reason"] is None
    assert len(inside["deals"]) == 3
    assert inside["tiles"]["revenue"] == 1000.0


def test_an_unknown_room_is_a_404(http, seeded):
    response = http.get(f"{PREFIX}/report/rooms/room_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workspace"


def test_a_reversed_date_range_is_422_over_http(http, seeded):
    response = http.get(f"{PREFIX}/report", params={"from": "2026-12-31", "to": "2026-01-01"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_filter"


def test_an_unknown_bucket_is_422_over_http(http, seeded):
    response = http.get(f"{PREFIX}/report", params={"bucket": "fortnight"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_filter"


def test_a_deal_with_nothing_to_key_it_by_is_422_over_http(http, seeded):
    response = http.post(
        f"{PREFIX}/deals", params={"room_id": seeded["sales"]["id"]}, json={"amount": 10}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_deal"


def test_a_duplicate_crm_id_is_409_and_names_the_record_to_patch(http, seeded):
    response = http.post(
        f"{PREFIX}/deals",
        params={"room_id": seeded["sales"]["id"]},
        json={"crm_deal_id": "006NW-1", "amount": 1},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "deal_conflict"
    assert body["existing_id"] == seeded["won"]["id"]
    assert body["existing_room_id"] == seeded["sales"]["id"]


def test_attaching_to_an_unknown_room_is_404_over_http(http, seeded):
    response = http.post(f"{PREFIX}/deals", params={"room_id": "room_absent"}, json={"crm_deal_id": "Z"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workspace"


def test_the_stage_sync_moves_the_report_over_http(http, seeded):
    """"Deal stage/amount sync keeps the rollup fresh" - so a PATCH must change the tiles
    with no other call."""
    assert http.get(f"{PREFIX}/report").json()["tiles"]["revenue"] == 1000.0
    http.patch(
        f"{PREFIX}/deals/{seeded['open']['id']}",
        json={"stage": "Closed Won", "amount": 500, "closed_at": "2026-02-15"},
    )
    body = http.get(f"{PREFIX}/report").json()
    assert body["tiles"]["revenue"] == 1500.0
    assert body["tiles"]["closed_won_deals"] == 2
    assert body["tiles"]["active_deals"] == 0
    assert body["tiles"]["close_rate"] == pytest.approx(2 / 3, abs=1e-4), "rounded to 4 places"


def test_detaching_a_deal_removes_it_from_every_figure(http, seeded):
    http.delete(f"{PREFIX}/deals/{seeded['won']['id']}")
    body = http.get(f"{PREFIX}/report").json()
    assert body["tiles"]["total_deals"] == 2
    assert body["tiles"]["revenue"] == 0.0
    assert body["tiles"]["close_rate"] == 0.0


def test_patching_and_deleting_an_unknown_deal_is_404_over_http(http, seeded):
    assert http.patch(f"{PREFIX}/deals/crm_deal_absent", json={"amount": 1}).status_code == 404
    assert http.delete(f"{PREFIX}/deals/crm_deal_absent").status_code == 404
    assert http.get(f"{PREFIX}/deals/crm_deal_absent").status_code == 404


def test_a_workspace_with_only_a_detached_deal_leaves_the_report(http, seeded):
    """The type gate and the attachment are conjunctive, and detaching is the second half
    going away."""
    for deal in (seeded["won"], seeded["open"], seeded["lost"]):
        http.delete(f"{PREFIX}/deals/{deal['id']}")
    body = http.get(f"{PREFIX}/report").json()
    assert body["scope"]["in_scope_rooms"] == 0
    assert {row["reason"] for row in body["scope"]["excluded"]} == {"no_deal", "not_sales"}
    assert "sales_room_without_deal" in {row["code"] for row in body["warnings"]}


def test_the_buyers_and_engagement_endpoints_agree_with_the_report(http, seeded):
    report = http.get(f"{PREFIX}/report").json()
    buyers = http.get(f"{PREFIX}/buyers").json()
    engagement = http.get(f"{PREFIX}/engagement").json()
    assert buyers["buyers"] == report["engagement"]["most_engaged_buyers"]
    assert engagement["buyer_views"] == report["engagement"]["buyer_views"]
    assert engagement["buyer_actions"] == report["engagement"]["buyer_actions"]
    assert buyers["unique_buyers"] == report["engagement"]["unique_buyers"]


def test_the_buyers_ranking_paginates_without_changing_the_total(http, seeded):
    everything = http.get(f"{PREFIX}/buyers", params={"limit": 100}).json()
    page = http.get(f"{PREFIX}/buyers", params={"limit": 1}).json()
    assert page["returned"] == 1
    assert page["count"] == everything["count"] == 2


def test_the_coverage_endpoint_matches_the_reports_copy_of_it(http, seeded):
    assert http.get(f"{PREFIX}/coverage").json() == http.get(f"{PREFIX}/report").json()["coverage"]


def test_the_integration_endpoints_round_trip(http, seeded):
    assert http.get(f"{PREFIX}/integration").json()["integration"]["connected"] is True
    http.patch(f"{PREFIX}/integration", json={"connected": False})
    assert http.get(f"{PREFIX}/integration").json()["integration"]["connected"] is False
    assert http.get(f"{PREFIX}/coverage").json()["complete"] is False


def test_the_vocabulary_endpoint_serves_the_published_sets(http, seeded):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["workspace_type"]["value"] == "sales"
    assert body["stage_classes"] == ["won", "lost", "open", "unknown"]
    assert "closed won" in body["won_stages"]
    assert "closed lost" in body["lost_stages"]
    assert "429" in body["api_constraints"]["rate_limit"]
    assert "properties" in body["api_constraints"]["properties"]
    assert "local mirror" in body["note"]


def test_the_inferences_endpoint_serves_the_registry(http, seeded):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert "close_rate" in body["sourced_quotes"]


def test_an_unknown_field_round_trips_over_http(http, seeded):
    created = http.post(
        f"{PREFIX}/deals",
        params={"room_id": seeded["sales"]["id"]},
        json={"crm_deal_id": "ODD", "renewal_probability": 0.42, "our_field": {"deep": [1, 2]}},
    ).json()
    assert created["data"]["our_field"] == {"deep": [1, 2]}
    assert http.get(f"{PREFIX}/deals/{created['id']}").json()["data"]["renewal_probability"] == 0.42
    assert http.get("/api/records/crm_deal", params={"where": "crm_deal_id=ODD"}).json()["count"] == 1


def test_a_team_can_filter_on_a_dotted_path_through_the_dynamic_index(http, seeded):
    """Schema flexibility, exercised: no migration, no typed column."""
    response = http.get("/api/records/crm_deal", params={"where": "team=enterprise"})
    assert response.status_code == 200
    assert response.json()["count"] == 1


# --------------------------------------------------------------------------- #
# Reads never write
# --------------------------------------------------------------------------- #


def test_reading_the_report_writes_nothing(http, seeded):
    before = http.get("/api/audit", params={"limit": 1000}).json()["count"]
    for path in (
        f"{PREFIX}/report",
        f"{PREFIX}/deals",
        f"{PREFIX}/buyers",
        f"{PREFIX}/engagement",
        f"{PREFIX}/coverage",
        f"{PREFIX}/integration",
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/inferences",
        f"{PREFIX}/report/rooms/{seeded['sales']['id']}",
    ):
        assert http.get(path).status_code == 200
    assert http.get("/api/audit", params={"limit": 1000}).json()["count"] == before


def test_the_report_does_not_create_a_cache_of_its_own_figures(http, seeded):
    """A stored metric row would be a cache that goes stale against a CRM sync."""
    collections = {row["collection"] for row in http.get("/api/collections").json()["collections"]}
    http.get(f"{PREFIX}/report")
    assert not {name for name in collections if "metric" in name or "rollup" in name}
    body = http.get(f"{PREFIX}/report").json()
    assert body["tiles"]["total_deals"] == 3


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """Whether an audit ``source`` names a route the host actually mounted.

    ``PATCH /api/wf-023/deals/{deal_id}`` is a template, so the recorded source has a real
    id in that position. Each segment of the recorded path is matched against the
    template's, with ``{...}`` accepting anything - which is what a route's own path
    parameter does.
    """
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual)
        ):
            return True
    return False


def test_every_write_is_audited(http, seeded):
    http.post(
        f"{PREFIX}/deals",
        params={"room_id": seeded["sales"]["id"], "actor": "dana"},
        json={"crm_deal_id": "NEW-1", "amount": 1},
    )
    http.patch(f"{PREFIX}/deals/{seeded['won']['id']}", params={"actor": "dana"}, json={"amount": 2})
    http.delete(f"{PREFIX}/deals/{seeded['lost']['id']}", params={"actor": "sam"})
    http.patch(f"{PREFIX}/integration", params={"actor": "dana"}, json={"connected": False})

    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    ours = [entry for entry in entries if entry["source"] and entry["source"].split(" ")[1].startswith(PREFIX)]
    assert {entry["action"] for entry in ours} == {"insert", "update", "delete"}
    assert {entry["source"] for entry in ours} == {
        f"POST {PREFIX}/deals",
        f"PATCH {PREFIX}/deals/{seeded['won']['id']}",
        f"DELETE {PREFIX}/deals/{seeded['lost']['id']}",
        f"PATCH {PREFIX}/integration",
    }


def test_every_write_audit_row_names_a_route_the_app_serves(http, seeded):
    """The build brief's central guarantee, checked against the live route table.

    An audit row naming a path the app had stopped serving has shipped in this codebase
    before, and it is invisible until somebody tries to follow the row.
    """
    created = http.post(
        f"{PREFIX}/deals",
        params={"room_id": seeded["sales"]["id"], "actor": "dana"},
        json={"crm_deal_id": "SRC-1", "amount": 5, "stage": "Negotiation"},
    ).json()
    http.patch(f"{PREFIX}/deals/{created['id']}", params={"actor": "dana"}, json={"amount": 6})
    http.delete(f"{PREFIX}/deals/{created['id']}", params={"actor": "dana"})
    http.patch(f"{PREFIX}/integration", params={"actor": "dana"}, json={"provider": "hubspot"})

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the rows this
    # feature's HTTP layer produced are in scope.
    ours = {source for source in sources if source.split(" ")[1].startswith(PREFIX)}
    assert ours, f"no wf-023 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_source_is_built_from_the_prefix_not_typed_out(http, seeded):
    """Rename the prefix and the recorded source follows it, because it is built from
    ``router.prefix`` rather than written as a literal."""
    module = load_feature("wf023_relate_buyer_engagement_to_crm_pipelin")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert 'source=f"POST {router.prefix}/deals"' in source
    assert 'source=f"PATCH {router.prefix}/deals/{deal_id}"' in source
    assert 'source=f"DELETE {router.prefix}/deals/{deal_id}"' in source
    assert 'source=f"PATCH {router.prefix}/integration"' in source


def test_the_actor_reaches_the_audit_row(http, seeded):
    http.post(
        f"{PREFIX}/deals",
        params={"room_id": seeded["sales"]["id"], "actor": "sam"},
        json={"crm_deal_id": "WHO", "amount": 1},
    )
    entries = http.get("/api/audit", params={"actor": "sam", "limit": 1000}).json()["entries"]
    assert any(entry["source"] == f"POST {PREFIX}/deals" for entry in entries)


def test_the_domain_never_opens_a_database_of_its_own(tmp_path):
    """Every read and write goes through the audited store. Nothing here imports sqlite3
    or constructs a connection, and a feature that did could bypass the audit log the
    product is built on."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "salesimpact"
    for module in package.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, f"{module.name} imports sqlite3"
        assert "sqlite3.connect" not in text, f"{module.name} opens its own connection"
    feature = load_feature("wf023_relate_buyer_engagement_to_crm_pipelin")
    feature_text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in feature_text


def test_source_is_required_rather_than_defaulted():
    """A caller that forgets to pass ``source`` is a type error, not a wrong audit row."""
    import inspect

    book = DealBook
    for name in ("create_deal", "update_deal", "delete_deal", "save_config"):
        signature = inspect.signature(getattr(book, name))
        parameter = signature.parameters["source"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, f"{name}: source must be keyword-only"
        assert parameter.default is inspect.Parameter.empty, f"{name}: source must be required"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seeder_produces_the_states_the_research_says_matter(tmp_path):
    """Seeded through the real seeder path, so what the demo shows is what the report
    reads - not rows written by hand in a shape the workflow would not produce."""
    from datetime import datetime, timezone

    import random

    module = load_feature("wf023_relate_buyer_engagement_to_crm_pipelin")
    db = AuditedDatabase(tmp_path / "seed.db")
    rooms = [
        (
            db.create("room", {"name": name, "account": account, "owner": owner}, source="seed")["id"],
            account,
        )
        for name, account, owner in (
            ("Northwind", "Northwind Traders", "dana"),
            ("Contoso", "Contoso Health", "sam"),
            ("Fabrikam", "Fabrikam Logistics", "dana"),
            ("Adventure", "Adventure Works", "sam"),
        )
    ]
    summary = module.seed(
        db, {"room_ids": rooms, "now": datetime.now(timezone.utc), "rng": random.Random("wf023")}
    )
    impact = SalesImpact(RecordStore(db))
    body = impact.report(parse_filters({}))

    assert summary and "no deal" in summary
    assert body["coverage"]["crm_connected"] is True
    assert body["scope"]["sales_typed_rooms"] == 3
    assert body["scope"]["in_scope_rooms"] == 2
    assert {row["reason"] for row in body["scope"]["excluded"]} == {"no_deal", "not_sales"}
    assert body["tiles"]["total_deals"] == 5
    assert body["tiles"]["closed_won_deals"] >= 1
    assert body["tiles"]["close_rate"] is not None
    assert body["coverage"]["without_deal"] == 1, "the researched missing-data room is seeded"
    assert body["coverage"]["deals_not_attached"] == 3, "three unattached deals are seeded"
    codes = {row["code"] for row in body["data_warnings"]}
    assert "close_before_create" in codes, "the backwards interval is seeded"
    assert body["engagement"]["buyer_views"] > 0
    assert body["engagement"]["most_engaged_buyers"]
    assert len(body["deals_by_owner"]) >= 2
    db.close()


def test_the_seeder_survives_a_database_with_no_demo_rooms(tmp_path):
    from datetime import datetime, timezone

    module = load_feature("wf023_relate_buyer_engagement_to_crm_pipelin")
    db = AuditedDatabase(tmp_path / "bare.db")
    summary = module.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc)})
    assert summary and "0 deals" in summary
    assert SalesImpact(RecordStore(db)).report(parse_filters({}))["tiles"]["total_deals"] == 0
    db.close()
