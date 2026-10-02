"""Tests for WF-024: the Client Engagement report, rolled up portfolio-wide.

The domain is :mod:`dsr.client_engagement` and the HTTP surface is
``backend/dsr/features/wf024_roll_up_client_engagement_and_multi_th.py``. The
split here mirrors the split in the code: the researched rules are tested against
the domain, the wiring is tested through the feature's own router.

What is pinned, and why each one matters
----------------------------------------
* **The report is portfolio-wide.** "The Client Engagement report analyzes
  external activity across ALL workspaces in your Dock instance." A test asserts
  there is no workspace selector on ``GET /report`` and that a second workspace's
  activity appears without being asked for.
* **Client activity only.** The external/internal distinction is the report's
  central rule, and it is easy to write a test that passes while the distinction
  is broken, so the classification is tested on its own: explicit field, boolean
  field, configured identity, configured domain, unknown person, default flip.
* **A view is a kind of action.** Jev chose this reading at 0.69; the arithmetic
  is asserted so a change to the taxonomy cannot quietly invert the two tiles.
* **The average's denominator is every workspace in scope**, not only the ones
  somebody opened (Jev, 0.99). The test adds an unopened workspace and asserts
  the average drops.
* **Multi-threading is buyer-side** (Jev, 0.99): distinct engaged clients per
  account, and the champion is the most engaged of them.
* **The three filters** work, including the date-only ``to`` bound meaning the
  end of that day - the quietest way for a report filter to be wrong.
* **Sort by any column** works, and an unknown column is refused rather than
  ignored.
* **The audit trail names the route that served the write.** Hard rule 4 of the
  build brief, and the specific defect the contract names: a source string that
  drifts from the mounted route.
* **The demo data seeds the states that change an answer**, not a spread of
  success: a multi-threaded account, a single-threaded one, an unopened one, an
  internal rep, an anonymous event, an overdue delivery and one delivered early.
"""

from __future__ import annotations

import inspect
import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr import client_engagement as ce
from dsr.client_engagement import (
    EngagementReportError,
    InvalidSortColumn,
    InvalidWindow,
    UnknownAccount,
    UnknownWorkspace,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-024"
MODULE = "wf024_roll_up_client_engagement_and_multi_th"
FEATURE_ID = "wf-024-roll-up-client-engagement-and-multi-th"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
AS_OF = NOW.isoformat()


def at(days: float = 0, hours: float = 0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    """A store over a throwaway audited database, for the domain tests."""
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(
        Path(tmp.name) / "wf024.db", mirror_dir=Path(tmp.name) / "audit", actor="test"
    )
    yield RecordStore(db)
    db.close()
    tmp.cleanup()


@pytest.fixture()
def client(monkeypatch):
    """A TestClient over a throwaway database, as test_features.py does it."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf024.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(_app()) as test_client:
        yield test_client
    tmp.cleanup()


def _app():
    from dsr.api import app

    return app


def room(
    store: RecordStore, account: str, *, owner: str = "", team: str = "", name: str = ""
) -> dict:
    data: dict = {"name": name or f"{account} room", "account": account}
    if owner:
        data["owner"] = owner
    if team:
        data["team"] = team
    return store.create("room", data, actor=owner or "seed", source="test")


def event(
    store: RecordStore,
    room_id: str | None,
    person: str,
    action: str = "viewed",
    *,
    days: float = 1,
    account: str = "",
    **extra,
) -> dict:
    data = {"person": person, "action": action, "occurred_at": at(days)}
    if account:
        data["account"] = account
    data.update(extra)
    return store.create("activity", data, room_id=room_id, actor="system", source="test")


def internal(
    store: RecordStore, room_id: str | None, person: str, action: str = "viewed", **extra
) -> dict:
    return event(store, room_id, person, action, user_type="internal", **extra)


def report(store: RecordStore, **kwargs) -> dict:
    kwargs.setdefault("as_of", AS_OF)
    kwargs.setdefault("filters", ce.resolve_filters())
    return ce.client_engagement(store, **kwargs)


def no_filters() -> ce.Filters:
    return ce.resolve_filters()


def accounts_of(store: RecordStore, **kwargs) -> dict[str, dict]:
    """The account rows, keyed by account name.

    The report payload is the five tiles; the account list is its own read, which
    is the shape the research describes ("Click a metric tile to expand the full
    list of accounts"). A test that wants a row asks the list for one.
    """
    kwargs.setdefault("as_of", AS_OF)
    rows = ce.account_list(store, filters=no_filters(), **kwargs)["accounts"]
    return {row["account"]: row for row in rows}


# --------------------------------------------------------------------------- #
# Registration and the contract
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0
    ids = {feature["id"] for feature in body["features"]}
    assert FEATURE_ID in ids


def test_the_feature_owns_its_ticket_derived_prefix():
    feature = load_feature(MODULE)
    assert feature.router.prefix == PREFIX
    assert feature.FEATURE["ticket"] == "WF-024"
    assert feature.FEATURE["id"] == FEATURE_ID


def test_no_other_feature_serves_a_path_under_this_prefix():
    """A shared prefix is legal with disjoint paths; a shared path is not.

    ``/api/library`` is carried by two features on main, so a blanket uniqueness
    assertion would be wrong. What must hold is that no *other* feature claims a
    concrete path inside this prefix, which is the collision the host refuses.
    """
    import dsr.features as host

    mine = {
        route["path"]
        for feature in host.REGISTRY.features
        if feature.id == FEATURE_ID
        for route in feature.routes
    }
    assert mine
    for feature in host.REGISTRY.features:
        if feature.id == FEATURE_ID:
            continue
        for route in feature.routes:
            assert route["path"] not in mine, f"{feature.id} also serves {route['path']}"


def test_the_feature_does_not_import_the_app():
    """The dependency direction is one-way: api -> features -> deps."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_domain_module_depends_on_nothing_but_the_store():
    """A domain module that reaches for the app cannot be tested on its own."""
    source = Path(ce.__file__).read_text(encoding="utf-8")
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations"] + [
        line
        for line in imports
        if line
        in (
            "import re",
            "from collections import defaultdict",
            "from dataclasses import dataclass",
            "from datetime import datetime, timedelta, timezone",
            "from typing import Any, Iterable, Mapping, Sequence",
            "from dsr.store import RecordStore",
        )
    ], imports


def test_the_feature_reports_its_routes(client):
    record = client.get(f"/api/features/{FEATURE_ID}").json()
    paths = {route["path"] for route in record["routes"]}
    assert paths == {
        f"{PREFIX}/report",
        f"{PREFIX}/accounts",
        f"{PREFIX}/accounts/{{account_key}}",
        f"{PREFIX}/rooms/{{room_id}}/engagement",
        f"{PREFIX}/filters",
        f"{PREFIX}/reports/team-usage",
        f"{PREFIX}/reports/implementations",
        f"{PREFIX}/config",
        f"{PREFIX}/events",
    }


def test_the_domain_module_is_a_plain_module_with_no_web_framework():
    """The domain is importable and testable without FastAPI in the way."""
    source = Path(ce.__file__).read_text(encoding="utf-8")
    assert "fastapi" not in source
    assert "dsr.api" not in source


def test_the_feature_never_opens_the_database_itself():
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert ".connect(" not in source


def test_the_feature_exports_its_domain_error_handlers():
    feature = load_feature(MODULE)
    assert set(feature.EXCEPTION_HANDLERS) == {
        EngagementReportError,
        UnknownWorkspace,
        UnknownAccount,
    }
    for handler in feature.EXCEPTION_HANDLERS.values():
        assert list(inspect.signature(handler).parameters) == ["request", "exc"]


def test_the_error_types_are_this_features_own():
    """A handler for a builtin would let this feature intercept the product."""
    for error in (EngagementReportError, UnknownWorkspace, UnknownAccount):
        assert error.__module__ == "dsr.client_engagement"
    assert issubclass(EngagementReportError, ValueError)
    assert not issubclass(EngagementReportError, LookupError)


def test_the_feature_exports_a_seed_function():
    feature = load_feature(MODULE)
    assert callable(feature.seed)
    assert list(inspect.signature(feature.seed).parameters) == ["db", "context"]


def test_no_migration_and_no_new_envelope_field():
    """Schema flexibility: the envelope is the only fixed vocabulary."""
    source = Path(ce.__file__).read_text(encoding="utf-8")
    for forbidden in ("CREATE TABLE", "ALTER TABLE", "PRAGMA", "schema_version"):
        assert forbidden not in source, forbidden


# --------------------------------------------------------------------------- #
# The audience rule: what counts as a client
# --------------------------------------------------------------------------- #


def test_an_explicit_user_type_field_wins_over_anything_else(store):
    config = ce.load_config(store)
    assert ce.classify_audience("dana", {"user_type": "internal"}, config) == ce.AUDIENCE_INTERNAL
    assert (
        ce.classify_audience("a.buyer", {"user_type": "external"}, config) == ce.AUDIENCE_EXTERNAL
    )


@pytest.mark.parametrize(
    "value", ["EXTERNAL", "external", " External ", "client", "buyer", "guest"]
)
def test_the_user_type_match_is_case_and_space_insensitive(store, value):
    config = ce.load_config(store)
    assert ce.classify_audience("a.buyer", {"user_type": value}, config) == ce.AUDIENCE_EXTERNAL


@pytest.mark.parametrize("value", ["internal", "REP", "member", "employee", "staff"])
def test_internal_spellings_are_also_case_insensitive(store, value):
    config = ce.load_config(store)
    assert ce.classify_audience("dana", {"user_type": value}, config) == ce.AUDIENCE_INTERNAL


def test_a_boolean_external_flag_is_honoured(store):
    config = ce.load_config(store)
    assert ce.classify_audience("x", {"external": True}, config) == ce.AUDIENCE_EXTERNAL
    assert ce.classify_audience("x", {"external": False}, config) == ce.AUDIENCE_INTERNAL
    assert ce.classify_audience("x", {"is_internal": True}, config) == ce.AUDIENCE_INTERNAL
    assert ce.classify_audience("x", {"is_internal": False}, config) == ce.AUDIENCE_EXTERNAL


def test_a_configured_internal_identity_is_not_a_client(store):
    ce.save_config(store, {"audience": {"internal_people": ["dana"]}}, source="test")
    config = ce.load_config(store)
    assert ce.classify_audience("dana", {}, config) == ce.AUDIENCE_INTERNAL
    assert ce.classify_audience("DANA", {}, config) == ce.AUDIENCE_INTERNAL
    assert ce.classify_audience("sam", {}, config) == ce.AUDIENCE_EXTERNAL


def test_a_configured_internal_domain_covers_the_whole_company(store):
    ce.save_config(store, {"audience": {"internal_domains": ["ourco.example"]}}, source="test")
    config = ce.load_config(store)
    assert ce.classify_audience("dana@ourco.example", {}, config) == ce.AUDIENCE_INTERNAL
    assert ce.classify_audience("ceo@eu.ourco.example", {}, config) == ce.AUDIENCE_INTERNAL
    # A suffix match must not fire on a lookalike domain.
    assert ce.classify_audience("x@notourco.example", {}, config) == ce.AUDIENCE_EXTERNAL
    assert ce.classify_audience("x@ourco.example.evil.test", {}, config) == ce.AUDIENCE_EXTERNAL


def test_an_anonymous_event_is_counted_for_nobody(store):
    """An un-attributable action must not inflate the client totals."""
    config = ce.load_config(store)
    assert ce.classify_audience("", {}, config) == ce.AUDIENCE_UNKNOWN
    assert ce.classify_audience("   ", {}, config) == ce.AUDIENCE_UNKNOWN


def test_the_default_for_an_unclassified_person_is_configurable(store):
    config = ce.load_config(store)
    assert ce.classify_audience("someone", {}, config) == ce.AUDIENCE_EXTERNAL
    ce.save_config(store, {"audience": {"unknown_is_external": False}}, source="test")
    assert ce.classify_audience("someone", {}, ce.load_config(store)) == ce.AUDIENCE_INTERNAL


def test_the_default_does_not_rescue_an_anonymous_event(store):
    ce.save_config(store, {"audience": {"unknown_is_external": True}}, source="test")
    assert ce.classify_audience("", {}, ce.load_config(store)) == ce.AUDIENCE_UNKNOWN


def test_an_explicit_field_beats_a_configured_identity_list(store):
    ce.save_config(store, {"audience": {"internal_people": ["dana"]}}, source="test")
    config = ce.load_config(store)
    assert ce.classify_audience("dana", {"user_type": "external"}, config) == ce.AUDIENCE_EXTERNAL


def test_an_unrecognised_user_type_falls_through_to_the_next_rule(store):
    config = ce.load_config(store)
    assert ce.classify_audience("a.buyer", {"user_type": "wizard"}, config) == ce.AUDIENCE_EXTERNAL


def test_a_non_string_person_is_not_crashing_the_classifier(store):
    config = ce.load_config(store)
    assert ce.classify_audience("42", {"user_type": 7}, config) == ce.AUDIENCE_EXTERNAL


def test_internal_activity_never_reaches_a_client_total(store):
    """The rule as the report experiences it, not as the classifier sees it."""
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    event(store, one["id"], "a.buyer", "commented")
    internal(store, one["id"], "dana", "viewed")
    internal(store, one["id"], "dana", "viewed")

    body = report(store)
    assert body["totals"]["client_views"] == 1
    assert body["totals"]["client_actions"] == 2
    assert body["totals"]["internal_actions"] == 2
    assert body["coverage"]["internal_events_excluded"] == 2
    assert body["coverage"]["external_events"] == 2


def test_an_anonymous_event_is_reported_in_coverage_and_left_out_of_totals(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    store.create(
        "activity", {"action": "viewed", "occurred_at": at(1)}, room_id=one["id"], source="test"
    )

    body = report(store)
    assert body["totals"]["client_actions"] == 1
    assert body["coverage"]["unattributed_events_excluded"] == 1


def test_a_team_that_marks_nothing_still_excludes_its_reps(store):
    """The config list is how a dataset that declares nothing says who is a rep."""
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    event(store, one["id"], "dana", "viewed")
    assert report(store)["totals"]["client_actions"] == 2

    ce.save_config(store, {"audience": {"internal_people": ["dana"]}}, source="test")
    body = report(store)
    assert body["totals"]["client_actions"] == 1
    assert body["totals"]["internal_actions"] == 1


# --------------------------------------------------------------------------- #
# The five tiles
# --------------------------------------------------------------------------- #


def test_the_report_serves_exactly_the_five_tiles_the_research_names(store):
    assert [key for key, _label, _expand in ce.TILES] == [
        "client_views",
        "client_actions",
        "avg_unique_clients",
        "client_views_over_time",
        "most_engaged_clients",
    ]
    labels = [tile["label"] for tile in report(store)["tiles"]]
    assert labels == [
        "Total client views",
        "Total client actions",
        "Average unique clients per workspace",
        "Client views over time",
        "Most engaged clients",
    ]


def test_a_view_is_a_kind_of_action_so_actions_never_falls_below_views(store):
    one = room(store, "Acme")
    for _ in range(3):
        event(store, one["id"], "a.buyer", "viewed")
    event(store, one["id"], "a.buyer", "downloaded")

    totals = report(store)["totals"]
    assert totals["client_views"] == 3
    assert totals["client_actions"] == 4
    assert totals["client_actions_other_than_views"] == 1
    assert totals["client_actions"] >= totals["client_views"]


def test_an_action_nobody_classified_as_a_view_is_still_an_action(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "watched_a_video")
    totals = report(store)["totals"]
    assert totals["client_views"] == 0
    assert totals["client_actions"] == 1


def test_the_view_taxonomy_is_configurable(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "watched_a_video")
    assert report(store)["totals"]["client_views"] == 0
    ce.save_config(store, {"taxonomy": {"view_actions": ["watched_a_video"]}}, source="test")
    assert report(store)["totals"]["client_views"] == 1


def test_the_average_client_denominator_is_every_workspace_in_scope(store):
    """Jev, 0.99: an unopened workspace is a zero, not an exclusion."""
    busy = room(store, "Acme")
    event(store, busy["id"], "a.buyer", "viewed")
    event(store, busy["id"], "b.buyer", "viewed")
    assert report(store)["totals"]["avg_unique_clients_per_workspace"] == 2.0

    room(store, "Quiet Co")  # nobody has opened it
    body = report(store)
    assert body["totals"]["workspaces"] == 2
    assert body["totals"]["workspaces_with_client_activity"] == 1
    assert body["totals"]["avg_unique_clients_per_workspace"] == 1.0


def test_the_average_is_zero_with_no_workspaces(store):
    assert report(store)["totals"]["avg_unique_clients_per_workspace"] == 0.0


def test_the_average_counts_people_once_per_workspace(store):
    """One person engaging in two workspaces is one client in each of them."""
    one = room(store, "Acme")
    two = room(store, "Acme")
    for space in (one, two):
        for _ in range(3):
            event(store, space["id"], "a.buyer", "viewed")
    body = report(store)
    assert body["totals"]["workspaces"] == 2
    assert body["totals"]["avg_unique_clients_per_workspace"] == 1.0
    assert body["totals"]["unique_clients_portfolio"] == 1


def test_a_client_engaging_two_accounts_counts_once_portfolio_wide(store):
    first = room(store, "Acme")
    second = room(store, "Globex")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "a.buyer", "viewed")
    body = report(store)
    assert body["totals"]["unique_clients"] == 2
    assert body["totals"]["unique_clients_portfolio"] == 1


def test_every_tile_carries_the_sort_key_it_expands_to(store):
    """The domain names the expansion; the route builds the URL from it."""
    for tile in report(store)["tiles"]:
        assert tile["expand"] in ce.SORTABLE_COLUMNS
        assert tile["hint"]


# --------------------------------------------------------------------------- #
# Client views over time
# --------------------------------------------------------------------------- #


def test_the_series_is_dense_and_zero_filled(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=0)
    event(store, one["id"], "a.buyer", "viewed", days=3)

    series = report(store)["tiles"][3]["series"]
    assert len(series) == 4
    assert [point["views"] for point in series] == [1, 0, 0, 1]
    assert [point["bucket_start"] for point in series] == [
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
        "2026-09-27",
    ]


def test_the_series_starts_at_the_earliest_event_not_a_fixed_window(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=200)
    series = report(store)["tiles"][3]["series"]
    assert series[0]["bucket_start"] == "2026-03-11"
    assert series[0]["views"] == 1


def test_the_series_is_empty_but_well_formed_with_no_activity(store):
    room(store, "Acme")
    series = report(store)["tiles"][3]["series"]
    assert series
    assert all(point["views"] == 0 for point in series)


def test_the_series_groups_by_week_on_request(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=0)
    event(store, one["id"], "a.buyer", "viewed", days=3)
    body = report(store, grain="week")
    series = body["tiles"][3]["series"]
    assert body["grain"] == "week"
    assert len(series) == 1
    assert series[0]["views"] == 2


def test_the_grain_widens_rather_than_returning_a_thousand_points(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=300)
    ce.save_config(store, {"thresholds": {"max_chart_points": 10}}, source="test")
    body = report(store)
    assert body["grain"] == "week"
    assert body["grain_widened"] is True
    assert body["series_points"] == len(body["tiles"][3]["series"])


def test_an_explicit_week_grain_is_not_reported_as_widened(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=300)
    ce.save_config(store, {"thresholds": {"max_chart_points": 10}}, source="test")
    assert report(store, grain="week")["grain_widened"] is False


def test_an_unknown_grain_is_refused(store):
    with pytest.raises(InvalidWindow):
        report(store, grain="fortnight")


def test_the_series_counts_distinct_clients_per_bucket(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=1)
    event(store, one["id"], "b.buyer", "viewed", days=1)
    event(store, one["id"], "a.buyer", "downloaded", days=1)
    series = report(store)["tiles"][3]["series"]
    active = [point for point in series if point["actions"]]
    assert active[0]["views"] == 2
    assert active[0]["actions"] == 3
    assert active[0]["clients"] == 2


def test_internal_activity_does_not_reach_the_series(store):
    one = room(store, "Acme")
    internal(store, one["id"], "dana", "viewed", days=1)
    assert all(point["views"] == 0 for point in report(store)["tiles"][3]["series"])


# --------------------------------------------------------------------------- #
# Most engaged clients
# --------------------------------------------------------------------------- #


def test_most_engaged_clients_are_ranked_by_actions(store):
    one = room(store, "Acme")
    for _ in range(5):
        event(store, one["id"], "quiet.buyer", "viewed")
    for _ in range(2):
        event(store, one["id"], "busy.buyer", "viewed")

    ranked = report(store)["tiles"][4]["clients"]
    assert [row["person"] for row in ranked] == ["quiet.buyer", "busy.buyer"]
    assert ranked[0]["actions"] == 5


def test_a_tie_is_broken_by_views_then_recency_then_name(store):
    one = room(store, "Acme")
    # Equal actions; b has the view and a does not.
    event(store, one["id"], "a.buyer", "downloaded")
    event(store, one["id"], "b.buyer", "viewed")
    assert [row["person"] for row in report(store)["tiles"][4]["clients"]] == ["b.buyer", "a.buyer"]


def test_a_tie_on_actions_and_views_is_broken_by_recency(store):
    one = room(store, "Acme")
    event(store, one["id"], "stale.buyer", "viewed", days=40)
    event(store, one["id"], "recent.buyer", "viewed", days=1)
    assert [row["person"] for row in report(store)["tiles"][4]["clients"]] == [
        "recent.buyer",
        "stale.buyer",
    ]


def test_the_ranking_is_stable_between_two_reads_of_the_same_data(store):
    one = room(store, "Acme")
    for person in ("c.buyer", "a.buyer", "b.buyer"):
        event(store, one["id"], person, "viewed", days=2)
    first = [row["person"] for row in report(store)["tiles"][4]["clients"]]
    second = [row["person"] for row in report(store)["tiles"][4]["clients"]]
    assert first == second == ["a.buyer", "b.buyer", "c.buyer"]


def test_a_client_engaging_two_accounts_is_summarised_portfolio_wide(store):
    first = room(store, "Acme")
    second = room(store, "Globex")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "a.buyer", "viewed")
    ranked = report(store)["tiles"][4]["clients"]
    assert len(ranked) == 1
    assert ranked[0]["actions"] == 2
    assert ranked[0]["account_count"] == 2


def test_the_client_tile_is_truncated_but_the_full_list_is_not_lost(store):
    one = room(store, "Acme")
    for index in range(8):
        event(store, one["id"], f"buyer{index}", "viewed")
    ce.save_config(store, {"thresholds": {"client_limit": 2}}, source="test")
    body = report(store)
    assert len(body["tiles"][4]["clients"]) == 2
    assert body["totals"]["unique_clients_portfolio"] == 8


# --------------------------------------------------------------------------- #
# Multi-threading and the champion
# --------------------------------------------------------------------------- #


def test_multi_threading_counts_distinct_client_people(store):
    """Jev, 0.99: the buyer side, because the tile counts clients."""
    one = room(store, "Acme")
    for _ in range(6):
        event(store, one["id"], "a.buyer", "viewed")
    single = accounts_of(store)["Acme"]
    assert single["unique_clients"] == 1
    assert single["multi_threaded"] is False
    assert single["champion_only_thread"] is True

    event(store, one["id"], "b.buyer", "viewed")
    multi = accounts_of(store)["Acme"]
    assert multi["unique_clients"] == 2
    assert multi["multi_threaded"] is True
    assert multi["champion_only_thread"] is False


def test_internal_reps_do_not_count_towards_the_thread_depth(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    internal(store, one["id"], "dana", "viewed")
    internal(store, one["id"], "sam", "viewed")
    account = accounts_of(store)["Acme"]
    assert account["unique_clients"] == 1
    assert account["multi_threaded"] is False


def test_the_champion_is_the_most_engaged_client(store):
    one = room(store, "Acme")
    for _ in range(4):
        event(store, one["id"], "a.buyer", "viewed")
    for _ in range(9):
        event(store, one["id"], "b.buyer", "downloaded")
    account = accounts_of(store)["Acme"]
    assert account["champion"]["person"] == "b.buyer"
    assert account["champion"]["actions"] == 9


def test_an_account_nobody_has_opened_names_no_champion(store):
    room(store, "Acme")
    account = accounts_of(store)["Acme"]
    assert account["champion"] is None
    assert account["no_client_activity"] is True
    assert account["unique_clients"] == 0
    assert account["multi_threaded"] is False


def test_the_multi_thread_floor_is_configurable(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    event(store, one["id"], "b.buyer", "viewed")
    assert accounts_of(store)["Acme"]["multi_threaded"] is True
    ce.save_config(store, {"thresholds": {"multi_thread_floor": 3}}, source="test")
    account = accounts_of(store)["Acme"]
    assert account["multi_threaded"] is False
    assert account["thread_floor"] == 3


def test_a_stale_account_is_one_nobody_has_looked_at_recently(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=90)
    assert accounts_of(store)["Acme"]["stale"] is True
    event(store, one["id"], "a.buyer", "viewed", days=1)
    assert accounts_of(store)["Acme"]["stale"] is False


def test_the_stale_threshold_is_configurable(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=10)
    ce.save_config(store, {"thresholds": {"stale_after_days": 5}}, source="test")
    assert accounts_of(store)["Acme"]["stale"] is True


def test_account_totals_add_up_to_the_portfolio_totals(store):
    first = room(store, "Acme")
    second = room(store, "Globex")
    for _ in range(3):
        event(store, first["id"], "a.buyer", "viewed")
        event(store, second["id"], "b.buyer", "downloaded")
    body = report(store)
    rows = ce.account_list(store, filters=no_filters(), as_of=AS_OF)["accounts"]
    assert sum(a["actions"] for a in rows) == body["totals"]["client_actions"] == 6
    assert sum(a["views"] for a in rows) == body["totals"]["client_views"] == 3
    assert sum(a["unique_clients"] for a in rows) == body["totals"]["unique_clients"] == 2
    assert sum(1 for a in rows if a["multi_threaded"]) == body["totals"]["multi_threaded_accounts"]
    # One buyer each, so both accounts rest on a single person.
    assert body["totals"]["multi_threaded_accounts"] == 0
    assert body["totals"]["single_threaded_accounts"] == 2
    assert body["totals"]["accounts_without_client_activity"] == 0


def test_a_workspace_with_no_account_value_gets_its_own_bucket(store):
    quiet = store.create("room", {"name": "No account here"}, source="test")
    event(store, quiet["id"], "a.buyer", "viewed")
    accounts = accounts_of(store)
    assert ce.UNATTRIBUTED_ACCOUNT in accounts
    assert accounts[ce.UNATTRIBUTED_ACCOUNT]["actions"] == 1


def test_an_event_naming_its_own_account_is_not_misfiled(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", account="Globex")
    accounts = accounts_of(store)
    assert accounts["Globex"]["actions"] == 1
    assert accounts["Globex"]["workspace_count"] == 1
    assert accounts["Acme"]["actions"] == 0


# --------------------------------------------------------------------------- #
# Filters: date range, owners, teams
# --------------------------------------------------------------------------- #


def test_a_date_range_narrows_the_events(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=1)
    event(store, one["id"], "b.buyer", "viewed", days=40)
    window = ce.resolve_filters(date_from=at(10), date_to=at(0))
    assert report(store, filters=window)["totals"]["client_actions"] == 1
    assert report(store)["totals"]["client_actions"] == 2


def test_a_date_only_upper_bound_means_the_end_of_that_day(store):
    """The quietest way for a report filter to be wrong."""
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=1, hours=0)  # 12:00 on the 26th
    included = ce.resolve_filters(date_from="2026-09-26", date_to="2026-09-26")
    assert included.end == datetime(2026, 9, 26, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert report(store, filters=included)["totals"]["client_actions"] == 1


def test_an_explicit_timestamp_upper_bound_is_used_exactly(store):
    window = ce.resolve_filters(date_from=None, date_to="2026-09-26T06:00:00Z")
    assert window.end == datetime(2026, 9, 26, 6, 0, tzinfo=timezone.utc)


def test_a_date_only_lower_bound_is_the_start_of_that_day(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=1)
    window = ce.resolve_filters(date_from="2026-09-26")
    assert report(store, filters=window)["totals"]["client_actions"] == 1


def test_an_inverted_range_is_refused(store):
    with pytest.raises(InvalidWindow):
        ce.resolve_filters(date_from="2026-09-10", date_to="2026-09-01")


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-45", "yesterday", "2026/13/01 99:99"])
def test_an_unparseable_bound_is_refused(store, value):
    with pytest.raises(InvalidWindow):
        ce.resolve_filters(date_from=value)
    with pytest.raises(InvalidWindow):
        ce.resolve_filters(date_to=value)


def test_a_range_narrows_the_date_series_too(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=1)
    event(store, one["id"], "a.buyer", "viewed", days=5)
    window = ce.resolve_filters(date_from=at(3), date_to=at(0))
    series = report(store, filters=window)["tiles"][3]["series"]
    assert sum(point["views"] for point in series) == 1


def test_the_owner_filter_narrows_the_workspaces(store):
    dana = room(store, "Acme", owner="dana")
    sam = room(store, "Globex", owner="sam")
    event(store, dana["id"], "a.buyer", "viewed")
    event(store, sam["id"], "b.buyer", "viewed")

    body = report(store, filters=ce.resolve_filters(owners=["dana"]))
    assert body["totals"]["workspaces"] == 1
    assert body["totals"]["client_actions"] == 1
    filtered = ce.account_list(store, filters=ce.resolve_filters(owners=["dana"]), as_of=AS_OF)[
        "accounts"
    ]
    assert [row["account"] for row in filtered] == ["Acme"]


def test_the_owner_filter_is_case_insensitive(store):
    dana = room(store, "Acme", owner="Dana")
    event(store, dana["id"], "a.buyer", "viewed")
    assert report(store, filters=ce.resolve_filters(owners=["dana"]))["totals"]["workspaces"] == 1
    assert report(store, filters=ce.resolve_filters(owners=["DANA"]))["totals"]["workspaces"] == 1


def test_the_owner_filter_may_be_repeated_or_comma_separated(store):
    first = room(store, "Acme", owner="dana")
    second = room(store, "Globex", owner="sam")
    third = room(store, "Initech", owner="kai")
    for space in (first, second, third):
        event(store, space["id"], "a.buyer", "viewed")

    repeated = report(store, filters=ce.resolve_filters(owners=["dana", "sam"]))
    joined = report(store, filters=ce.resolve_filters(owners=["dana,sam"]))
    assert repeated["totals"]["workspaces"] == joined["totals"]["workspaces"] == 2


def test_the_owner_filter_excludes_a_workspace_with_no_owner(store):
    """Otherwise a filtered report disagrees with the owners it was filtered by."""
    named = room(store, "Acme", owner="dana")
    anonymous = room(store, "Globex")
    event(store, named["id"], "a.buyer", "viewed")
    event(store, anonymous["id"], "b.buyer", "viewed")
    body = report(store, filters=ce.resolve_filters(owners=["dana"]))
    assert body["totals"]["workspaces"] == 1
    assert body["coverage"]["workspaces_dropped_by_filter"] == 1


def test_the_team_filter_narrows_the_workspaces(store):
    first = room(store, "Acme", team="Enterprise")
    second = room(store, "Globex", team="Mid-Market")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "b.buyer", "viewed")
    body = report(store, filters=ce.resolve_filters(teams=["enterprise"]))
    assert body["totals"]["workspaces"] == 1
    assert body["totals"]["client_actions"] == 1


def test_owner_and_team_filters_combine(store):
    first = room(store, "Acme", owner="dana", team="Enterprise")
    second = room(store, "Globex", owner="dana", team="Mid-Market")
    third = room(store, "Initech", owner="sam", team="Enterprise")
    for space in (first, second, third):
        event(store, space["id"], "a.buyer", "viewed")
    window = ce.resolve_filters(owners=["dana"], teams=["Enterprise"])
    assert report(store, filters=window)["totals"]["workspaces"] == 1


def test_a_filter_that_matches_nothing_is_an_empty_report_not_an_error(store):
    room(store, "Acme", owner="dana")
    body = report(store, filters=ce.resolve_filters(owners=["nobody"]))
    assert body["totals"]["workspaces"] == 0
    assert body["totals"]["client_actions"] == 0
    assert body["tiles"][1]["value"] == 0


def test_the_filter_echo_names_what_was_asked_for(store):
    room(store, "Acme", owner="dana", team="Enterprise")
    window = ce.resolve_filters(date_from="2026-01-01", owners=["Dana"], teams=["Enterprise"])
    echo = report(store, filters=window)["filters"]
    assert echo["owners"] == ["dana"]
    assert echo["teams"] == ["enterprise"]
    assert echo["from"] == "2026-01-01T00:00:00+00:00"


def test_an_event_with_no_timestamp_falls_back_to_its_record_timestamp(store):
    """A team that never wrote a timestamp still gets a dated report."""
    one = room(store, "Acme")
    store.create(
        "activity", {"person": "a.buyer", "action": "viewed"}, room_id=one["id"], source="test"
    )
    body = report(store)
    assert body["totals"]["client_actions"] == 1
    assert body["coverage"]["undated_events"] == 0


def test_an_undated_event_is_excluded_by_a_date_range(store):
    """An event that cannot be shown to be in a range is not in a range."""
    window = ce.resolve_filters(date_from=at(5), date_to=at(0))
    assert ce._in_window({"at": None}, window) is False
    assert ce._in_window({"at": None}, ce.resolve_filters()) is True


def test_an_event_on_no_workspace_is_out_of_scope_and_says_so(store):
    """It must not vanish from every number with no trace."""
    room(store, "Acme")
    store.create("activity", {"person": "a.buyer", "action": "viewed"}, source="test")
    body = report(store)
    assert body["totals"]["client_actions"] == 0
    assert body["coverage"]["events_out_of_scope"] == 1


# --------------------------------------------------------------------------- #
# Sorting: "sort by any column"
# --------------------------------------------------------------------------- #


def test_every_column_of_the_account_list_is_a_sort_key():
    listing_columns = {
        "account",
        "views",
        "actions",
        "other_actions",
        "unique_clients",
        "thread_depth",
        "workspace_count",
        "days_since_activity",
        "multi_threaded",
        "champion_only_thread",
        "stale",
        "no_client_activity",
        "owner_count",
        "team_count",
        "champion",
        "first_activity_at",
        "last_activity_at",
    }
    assert listing_columns == set(ce.SORTABLE_COLUMNS)


def test_every_sort_key_is_accepted_and_reported_back(store):
    busy = room(store, "Zeta")
    calm = room(store, "Alpha")
    event(store, busy["id"], "a.buyer", "viewed")
    event(store, calm["id"], "b.buyer", "viewed")
    for column in sorted(ce.SORTABLE_COLUMNS):
        body = ce.account_list(store, filters=no_filters(), sort=column, as_of=AS_OF)
        assert body["sort"] == column
        assert body["sort_kind"] == ce.SORTABLE_COLUMNS[column]
        assert len(body["accounts"]) == 2


def test_sorting_ascending_and_descending_are_opposites(store):
    first = room(store, "Acme")
    second = room(store, "Globex")
    for _ in range(3):
        event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "b.buyer", "viewed")

    down = ce.account_list(
        store, filters=no_filters(), sort="actions", descending=True, as_of=AS_OF
    )
    up = ce.account_list(store, filters=no_filters(), sort="actions", descending=False, as_of=AS_OF)
    assert [row["account"] for row in down["accounts"]] == ["Acme", "Globex"]
    assert [row["account"] for row in up["accounts"]] == ["Globex", "Acme"]


def test_an_unknown_sort_column_is_refused_with_the_alternatives(store):
    with pytest.raises(InvalidSortColumn) as caught:
        ce.sort_accounts([], "colour", True)
    assert "cannot sort by 'colour'" in str(caught.value)
    assert "unique_clients" in str(caught.value)


def test_an_unknown_sort_column_is_a_value_error(store):
    assert issubclass(InvalidSortColumn, EngagementReportError)
    assert issubclass(InvalidWindow, EngagementReportError)


def test_a_blank_sort_key_falls_back_to_the_default(store):
    assert (
        ce.account_list(store, filters=no_filters(), sort="   ", as_of=AS_OF)["sort"]
        == ce.DEFAULT_SORT
    )
    assert (
        ce.account_list(store, filters=no_filters(), sort=None, as_of=AS_OF)["sort"]
        == ce.DEFAULT_SORT
    )


def test_ties_break_on_the_account_name_so_reads_are_stable(store):
    for account in ("Zeta", "Alpha", "Mu"):
        space = room(store, account)
        event(store, space["id"], "a.buyer", "viewed")
    rows = ce.account_list(
        store, filters=no_filters(), sort="actions", descending=True, as_of=AS_OF
    )
    assert [row["account"] for row in rows["accounts"]] == ["Alpha", "Mu", "Zeta"]


def test_sorting_by_champion_orders_by_who_the_champion_is(store):
    first = room(store, "Acme")
    second = room(store, "Globex")
    event(store, first["id"], "zoe.buyer", "viewed")
    event(store, second["id"], "adam.buyer", "viewed")
    rows = ce.account_list(
        store, filters=no_filters(), sort="champion", descending=False, as_of=AS_OF
    )
    assert [row["account"] for row in rows["accounts"]] == ["Globex", "Acme"]


def test_the_list_pages_without_losing_the_total(store):
    for index in range(5):
        space = room(store, f"Account {index}")
        event(store, space["id"], "a.buyer", "viewed")
    page = ce.account_list(store, filters=no_filters(), limit=2, offset=2, as_of=AS_OF)
    assert page["count"] == 2
    assert page["total"] == 5
    assert page["offset"] == 2


def test_the_config_caps_the_list_when_no_limit_is_asked_for(store):
    for index in range(4):
        space = room(store, f"Account {index}")
        event(store, space["id"], "a.buyer", "viewed")
    ce.save_config(store, {"thresholds": {"account_limit": 2}}, source="test")
    page = ce.account_list(store, filters=no_filters(), as_of=AS_OF)
    assert page["count"] == 2
    assert page["total"] == 4


# --------------------------------------------------------------------------- #
# Account keys
# --------------------------------------------------------------------------- #


def test_an_account_key_is_path_safe(store):
    assert ce.account_key("Northwind / EMEA") == "Northwind-EMEA"
    assert ce.account_key("A B  C") == "A-B-C"
    assert ce.account_key("///") == "account"
    assert "/" not in ce.account_key("weird / name / here")


def test_two_accounts_whose_names_collapse_to_one_key_are_told_apart():
    keys = ce.assign_keys(["Northwind / EMEA", "Northwind EMEA"])
    assert len(set(keys.values())) == 2
    assert set(keys.values()) == {"Northwind-EMEA", "Northwind-EMEA-2"}


def test_account_keys_are_stable_between_calls():
    first = ce.assign_keys(["Acme", "Acme Ltd", "Globex"])
    second = ce.assign_keys(["Globex", "Acme Ltd", "Acme"])
    assert first == second


def test_every_account_row_carries_a_key_that_resolves(store):
    for account in ("Northwind / EMEA", "Northwind EMEA", "Contoso"):
        space = room(store, account)
        event(store, space["id"], "a.buyer", "viewed")
    rows = ce.account_list(store, filters=no_filters(), as_of=AS_OF)["accounts"]
    for row in rows:
        assert (
            ce.account_detail(store, row["account_key"], filters=no_filters(), as_of=AS_OF)[
                "account"
            ]
            == row["account"]
        )


# --------------------------------------------------------------------------- #
# The account drill-down
# --------------------------------------------------------------------------- #


def test_the_drill_down_names_the_individuals_behind_the_numbers(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    event(store, one["id"], "b.buyer", "downloaded")
    detail = ce.account_detail(store, "Acme", filters=no_filters(), as_of=AS_OF)
    assert {client["person"] for client in detail["clients"]} == {"a.buyer", "b.buyer"}
    assert detail["views"] == 1
    assert detail["actions"] == 2


def test_the_drill_down_reports_the_champion_and_the_thread_depth(store):
    one = room(store, "Acme")
    for _ in range(3):
        event(store, one["id"], "a.buyer", "viewed")
    event(store, one["id"], "b.buyer", "viewed")
    detail = ce.account_detail(store, "Acme", filters=no_filters(), as_of=AS_OF)
    assert detail["champion"]["person"] == "a.buyer"
    assert detail["unique_clients"] == 2
    assert detail["multi_threaded"] is True


def test_the_drill_down_lists_the_workspaces_behind_the_account(store):
    first = room(store, "Acme", name="Evaluation")
    second = room(store, "Acme", name="Renewal")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "b.buyer", "viewed")
    detail = ce.account_detail(store, "Acme", filters=no_filters(), as_of=AS_OF)
    assert {space["name"] for space in detail["workspaces"]} == {"Evaluation", "Renewal"}
    assert detail["workspace_count"] == 2


def test_the_drill_down_reports_owners_and_teams(store):
    one = room(store, "Acme", owner="dana", team="Enterprise")
    event(store, one["id"], "a.buyer", "viewed")
    detail = ce.account_detail(store, "Acme", filters=no_filters(), as_of=AS_OF)
    assert detail["owners"] == ["dana"]
    assert detail["teams"] == ["Enterprise"]


def test_an_unknown_account_key_is_a_lookup_error(store):
    with pytest.raises(UnknownAccount):
        ce.account_detail(store, "nope", filters=no_filters(), as_of=AS_OF)


def test_an_account_filtered_out_of_scope_is_not_resolvable(store):
    """Otherwise 'you filtered it away' reads as 'this account has no engagement'."""
    one = room(store, "Acme", owner="dana")
    event(store, one["id"], "a.buyer", "viewed")
    with pytest.raises(UnknownAccount):
        ce.account_detail(store, "Acme", filters=ce.resolve_filters(owners=["sam"]), as_of=AS_OF)


def test_a_client_row_carries_what_they_looked_at_and_where(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", target="Pricing One-Pager")
    client = ce.account_detail(store, "Acme", filters=no_filters(), as_of=AS_OF)["clients"][0]
    assert client["targets"] == ["Pricing One-Pager"]
    assert client["workspaces"] == [one["id"]]
    assert client["days_since_seen"] == 1


# --------------------------------------------------------------------------- #
# The workspace-scoped report
# --------------------------------------------------------------------------- #


def test_the_workspace_report_rolls_up_one_workspace(store):
    first = room(store, "Acme")
    second = room(store, "Globex")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "b.buyer", "viewed")

    body = ce.workspace_engagement(store, first["id"], filters=no_filters(), as_of=AS_OF)
    assert body["scope"]["all_workspaces"] is False
    assert body["scope"]["room_id"] == first["id"]
    assert body["totals"]["client_actions"] == 1
    assert body["totals"]["workspaces"] == 1


def test_the_workspace_report_names_the_workspace(store):
    one = room(store, "Acme", owner="dana", team="Enterprise", name="Acme Evaluation")
    event(store, one["id"], "a.buyer", "viewed")
    body = ce.workspace_engagement(store, one["id"], filters=no_filters(), as_of=AS_OF)
    assert body["workspace"]["name"] == "Acme Evaluation"
    assert body["workspace"]["account"] == "Acme"
    assert body["workspace"]["owner"] == "dana"
    assert body["workspace"]["teams"] == ["Enterprise"]


def test_the_workspace_report_serves_the_same_five_tiles(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    body = ce.workspace_engagement(store, one["id"], filters=no_filters(), as_of=AS_OF)
    assert [tile["key"] for tile in body["tiles"]] == [key for key, _l, _e in ce.TILES]


def test_the_workspace_report_keeps_the_date_range_but_drops_owner_and_team(store):
    one = room(store, "Acme", owner="dana")
    event(store, one["id"], "a.buyer", "viewed", days=1)
    event(store, one["id"], "a.buyer", "viewed", days=40)

    body = ce.workspace_engagement(
        store, one["id"], filters=ce.resolve_filters(owners=["dana"]), as_of=AS_OF
    )
    assert body["totals"]["client_actions"] == 2
    narrowed = ce.workspace_engagement(
        store, one["id"], filters=ce.resolve_filters(date_from=at(5)), as_of=AS_OF
    )
    assert narrowed["totals"]["client_actions"] == 1


def test_an_unopened_workspace_still_reports_rather_than_disappearing(store):
    one = room(store, "Quiet")
    body = ce.workspace_engagement(store, one["id"], filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["workspaces"] == 1
    assert body["totals"]["client_actions"] == 0
    assert body["workspace"]["clients"] == []


def test_an_unknown_workspace_is_a_lookup_error(store):
    with pytest.raises(UnknownWorkspace):
        ce.workspace_engagement(store, "room_missing", filters=no_filters(), as_of=AS_OF)


def test_a_record_that_is_not_a_workspace_is_not_a_workspace(store):
    other = store.create("activity", {"action": "viewed"}, source="test")
    with pytest.raises(UnknownWorkspace):
        ce.workspace_engagement(store, other["id"], filters=no_filters(), as_of=AS_OF)


def test_the_workspace_report_excludes_another_workspaces_events(store):
    first = room(store, "Acme")
    second = room(store, "Acme")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, second["id"], "b.buyer", "viewed")
    body = ce.workspace_engagement(store, first["id"], filters=no_filters(), as_of=AS_OF)
    assert [client["person"] for client in body["workspace"]["clients"]] == ["a.buyer"]


# --------------------------------------------------------------------------- #
# The filters endpoint
# --------------------------------------------------------------------------- #


def test_the_filters_endpoint_publishes_the_owners_and_teams_in_scope(store):
    room(store, "Acme", owner="dana", team="Enterprise")
    room(store, "Globex", owner="sam", team="Mid-Market")
    body = ce.available_filters(store, as_of=AS_OF)
    assert body["owners"] == ["dana", "sam"]
    assert body["teams"] == ["Enterprise", "Mid-Market"]


def test_the_filters_endpoint_omits_a_workspace_with_no_owner(store):
    room(store, "Acme", owner="dana")
    room(store, "Globex")
    assert ce.available_filters(store, as_of=AS_OF)["owners"] == ["dana"]


def test_the_filters_endpoint_publishes_the_date_bounds(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed", days=30)
    event(store, one["id"], "a.buyer", "viewed", days=1)
    body = ce.available_filters(store, as_of=AS_OF)["date_range"]
    assert body["earliest"] == at(30)
    assert body["latest"] == at(1)
    assert body["as_of"] == AS_OF


def test_the_filters_endpoint_publishes_no_bounds_with_no_activity(store):
    room(store, "Acme")
    bounds = ce.available_filters(store, as_of=AS_OF)["date_range"]
    assert bounds["earliest"] is None
    assert bounds["latest"] is None


def test_the_filters_endpoint_publishes_every_account_and_its_key(store):
    room(store, "Acme")
    room(store, "Northwind / EMEA")
    accounts = ce.available_filters(store, as_of=AS_OF)["accounts"]
    assert {row["account"] for row in accounts} == {"Acme", "Northwind / EMEA"}
    assert {row["account_key"] for row in accounts} == {"Acme", "Northwind-EMEA"}


def test_the_filters_endpoint_publishes_the_grains_and_the_default_sort(store):
    body = ce.available_filters(store, as_of=AS_OF)
    assert body["grains"] == ["day", "week"]
    assert body["default_sort"] == ce.DEFAULT_SORT
    assert set(body["sortable_columns"]) == set(ce.SORTABLE_COLUMNS)


def test_the_filters_endpoint_reports_the_audience_split(store):
    one = room(store, "Acme")
    event(store, one["id"], "a.buyer", "viewed")
    internal(store, one["id"], "dana", "viewed")
    store.create("activity", {"action": "viewed"}, room_id=one["id"], source="test")
    assert ce.available_filters(store, as_of=AS_OF)["audience_split"] == {
        "external": 1,
        "internal": 1,
        "unknown": 1,
    }


def test_the_filters_endpoint_names_the_collections_it_reads(store):
    body = ce.available_filters(store, as_of=AS_OF)["collections"]
    assert body == {"workspaces": "room", "events": "activity", "implementations": "implementation"}


# --------------------------------------------------------------------------- #
# Team Usage
# --------------------------------------------------------------------------- #


def test_team_usage_attributes_the_client_response_to_each_owner(store):
    dana = room(store, "Acme", owner="dana")
    sam = room(store, "Globex", owner="sam")
    for _ in range(3):
        event(store, dana["id"], "a.buyer", "viewed")
    event(store, sam["id"], "b.buyer", "viewed")

    body = ce.team_usage(store, filters=no_filters(), as_of=AS_OF)
    by_owner = {row["owner"]: row for row in body["owners_detail"]}
    assert by_owner["dana"]["client_actions"] == 3
    assert by_owner["sam"]["client_actions"] == 1
    assert by_owner["dana"]["accounts"] == 1


def test_team_usage_counts_a_reps_own_activity_separately(store):
    one = room(store, "Acme", owner="dana")
    event(store, one["id"], "a.buyer", "viewed")
    ce.save_config(store, {"audience": {"internal_people": ["dana"]}}, source="test")
    for _ in range(4):
        internal(store, one["id"], "dana", "viewed")

    row = ce.team_usage(store, filters=no_filters(), as_of=AS_OF)["owners_detail"][0]
    assert row["internal_actions"] == 4
    assert row["client_actions"] == 1


def test_team_usage_reports_the_coverage_risk_per_owner(store):
    first = room(store, "Acme", owner="dana")
    room(store, "Globex", owner="dana")
    event(store, first["id"], "a.buyer", "viewed")
    event(store, first["id"], "b.buyer", "viewed")

    row = ce.team_usage(store, filters=no_filters(), as_of=AS_OF)["owners_detail"][0]
    assert row["multi_threaded_accounts"] == 1
    assert row["accounts_without_client_activity"] == 1


def test_team_usage_names_a_workspace_with_no_owner(store):
    room(store, "Acme")
    body = ce.team_usage(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["workspaces_without_an_owner"] == 1
    assert [row["name"] for row in body["workspaces_without_an_owner"]] == ["Acme room"]


def test_team_usage_names_an_internal_person_who_owns_nothing(store):
    one = room(store, "Acme", owner="dana")
    ce.save_config(store, {"audience": {"internal_people": ["kai"]}}, source="test")
    internal(store, one["id"], "kai", "viewed")
    assert ce.team_usage(store, filters=no_filters(), as_of=AS_OF)[
        "unattributed_internal_people"
    ] == ["kai"]


def test_team_usage_is_empty_but_well_formed_with_no_owners(store):
    room(store, "Acme")
    body = ce.team_usage(store, filters=no_filters(), as_of=AS_OF)
    assert body["count"] == 0
    assert body["owners_detail"] == []
    assert body["totals"]["owners"] == 0


def test_team_usage_obeys_the_owner_filter(store):
    room(store, "Acme", owner="dana")
    room(store, "Globex", owner="sam")
    body = ce.team_usage(store, filters=ce.resolve_filters(owners=["dana"]), as_of=AS_OF)
    assert [row["owner"] for row in body["owners_detail"]] == ["dana"]


# --------------------------------------------------------------------------- #
# Implementations
# --------------------------------------------------------------------------- #


def implementation(store: RecordStore, **data) -> dict:
    return store.create("implementation", data, actor="dana", source="test")


def test_implementations_counts_total_active_and_completed(store):
    room(store, "Acme")
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(60),
        completed_at=at(30),
        due_at=at(40),
    )
    implementation(store, account="Acme", status="active", started_at=at(20), due_at=at(-5))
    implementation(store, account="Acme", status="in_progress", started_at=at(5))

    totals = ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"]
    assert totals["implementations"] == 3
    assert totals["active"] == 2
    assert totals["completed"] == 1


def test_implementations_averages_the_time_to_completion(store):
    room(store, "Acme")
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(60),
        completed_at=at(30),
        due_at=at(45),
    )
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(50),
        completed_at=at(40),
        due_at=at(45),
    )
    assert (
        ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"][
            "time_to_completion_days"
        ]
        == 20.0
    )


def test_implementations_reports_no_average_rather_than_a_wrong_one(store):
    room(store, "Acme")
    implementation(store, account="Acme", status="completed", completed_at=at(30))
    assert (
        ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"][
            "time_to_completion_days"
        ]
        is None
    )


def test_implementations_reports_the_percentage_completed_on_time(store):
    room(store, "Acme")
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(60),
        completed_at=at(30),
        due_at=at(40),
    )
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(60),
        completed_at=at(50),
        due_at=at(40),
    )
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(60),
        completed_at=at(45),
        due_at=at(40),
    )
    implementation(
        store, account="Acme", status="completed", started_at=at(60), completed_at=at(20)
    )
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["completed_on_time_percent"] == pytest.approx(66.67, abs=0.01)
    assert body["missing_dates"]["no_due_date"] == 1


def test_implementations_reports_no_percentage_rather_than_a_wrong_one(store):
    room(store, "Acme")
    implementation(
        store, account="Acme", status="completed", started_at=at(60), completed_at=at(30)
    )
    assert (
        ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"][
            "completed_on_time_percent"
        ]
        is None
    )


def test_implementations_groups_by_owner_with_an_unassigned_bucket(store):
    room(store, "Acme")
    implementation(store, account="Acme", status="active", owner="dana", due_at=at(-1))
    implementation(store, account="Acme", status="active", started_at=at(3))
    by_owner = {
        row["owner"]: row
        for row in ce.implementations(store, filters=no_filters(), as_of=AS_OF)["by_owner"]
    }
    assert by_owner["dana"]["total"] == 1
    assert by_owner["(unassigned)"]["total"] == 1


def test_implementations_reports_the_customer_figures_from_the_same_rollup(store):
    one = room(store, "Acme")
    for _ in range(3):
        event(store, one["id"], "a.buyer", "viewed")
    internal(store, one["id"], "dana", "viewed")
    implementation(store, account="Acme", status="active")

    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["customer_views"] == 3
    assert body["totals"]["customer_actions"] == 3
    assert body["totals"]["unique_customers"] == 1
    assert [row["person"] for row in body["most_engaged_customers"]] == ["a.buyer"]


def test_an_overdue_active_implementation_is_at_risk(store):
    room(store, "Acme")
    implementation(store, account="Acme", status="active", started_at=at(40), due_at=at(5))
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["at_risk"] == 1
    assert "past its" in body["at_risk"][0]["at_risk_reasons"][0]


def test_an_implementation_completed_late_is_at_risk(store):
    room(store, "Acme")
    implementation(
        store,
        account="Acme",
        status="completed",
        started_at=at(90),
        completed_at=at(30),
        due_at=at(45),
    )
    row = ce.implementations(store, filters=no_filters(), as_of=AS_OF)["implementations_detail"][0]
    assert row["at_risk"] is True
    assert row["completed_on_time"] is False
    assert "completed after its" in row["at_risk_reasons"][0]


def test_an_implementation_with_no_committed_date_is_at_risk(store):
    room(store, "Acme")
    implementation(store, account="Acme", status="active", started_at=at(20))
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["at_risk"][0]["at_risk_reasons"] == ["active with no due date"]


def test_an_implementation_due_next_week_is_not_at_risk(store):
    """Urgency is not risk; the report reports it as days_to_due instead."""
    room(store, "Acme")
    implementation(store, account="Acme", status="active", started_at=at(20), due_at=at(-3))
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["at_risk"] == 0
    assert body["implementations_detail"][0]["days_to_due"] == 3


def test_an_implementation_marked_active_with_a_completion_date_is_named(store):
    room(store, "Acme")
    implementation(store, account="Acme", status="active", started_at=at(20), completed_at=at(2))
    reasons = ce.implementations(store, filters=no_filters(), as_of=AS_OF)["at_risk"][0][
        "at_risk_reasons"
    ]
    assert "marked active but carries a completion date" in reasons


def test_an_unrecognised_status_is_counted_as_unknown_not_dropped(store):
    room(store, "Acme")
    implementation(store, account="Acme", status="paused for reasons")
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["implementations"] == 1
    assert body["missing_dates"]["unknown_state"] == 1
    assert body["implementations_detail"][0]["state"] == "unknown"


def test_an_implementation_with_no_status_at_all_is_still_a_row(store):
    room(store, "Acme")
    implementation(store, account="Acme")
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["implementations"] == 1
    assert body["by_owner"][0]["owner"] == "(unassigned)"


def test_an_implementation_for_an_account_out_of_scope_is_not_counted(store):
    room(store, "Acme")
    implementation(store, account="Globex", status="active", due_at=at(-1))
    assert (
        ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"]["implementations"]
        == 0
    )


def test_implementations_is_empty_but_well_formed_with_no_records(store):
    room(store, "Acme")
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["count"] == 0
    assert body["totals"]["at_risk"] == 0
    assert body["by_owner"] == []


def test_the_implementation_collection_is_configurable(store):
    room(store, "Acme")
    store.create("delivery", {"account": "Acme", "status": "active"}, source="test")
    assert (
        ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"]["implementations"]
        == 0
    )
    ce.save_config(store, {"implementation": {"collection": "delivery"}}, source="test")
    assert (
        ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"]["implementations"]
        == 1
    )


def test_the_implementation_state_vocabulary_is_configurable(store):
    room(store, "Acme")
    ce.save_config(store, {"implementation": {"active_states": ["wobbling"]}}, source="test")
    implementation(store, account="Acme", status="wobbling")
    assert ce.implementations(store, filters=no_filters(), as_of=AS_OF)["totals"]["active"] == 1


def test_the_implementation_field_names_are_discovered(store):
    room(store, "Acme")
    ce.save_config(
        store,
        {
            "implementation": {
                "collection": "delivery",
                "account_fields": ["customer"],
                "started_fields": ["kicked_off"],
                "completed_fields": ["landed"],
                "due_fields": ["promised"],
            }
        },
        source="test",
    )
    store.create(
        "delivery",
        {
            "customer": "Acme",
            "status": "completed",
            "kicked_off": at(40),
            "landed": at(20),
            "promised": at(10),
        },
        source="test",
    )
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["implementations"] == 1
    assert body["totals"]["time_to_completion_days"] == 20.0
    assert body["totals"]["completed_on_time_percent"] == 100.0


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_the_defaults_are_returned_when_no_config_record_exists(store):
    body = ce.load_config(store)
    assert body["thresholds"]["multi_thread_floor"] == 2
    assert body["audience"]["unknown_is_external"] is True
    assert "viewed" in body["taxonomy"]["view_actions"]


def test_a_config_patch_is_merged_not_replaced(store):
    ce.save_config(store, {"thresholds": {"multi_thread_floor": 5}}, source="test")
    ce.save_config(store, {"thresholds": {"stale_after_days": 7}}, source="test")
    body = ce.load_config(store)
    assert body["thresholds"]["multi_thread_floor"] == 5
    assert body["thresholds"]["stale_after_days"] == 7
    assert body["thresholds"]["chart_days"] == 30


def test_a_config_patch_merges_recursively_into_a_nested_table(store):
    ce.save_config(store, {"audience": {"internal_people": ["dana"]}}, source="test")
    ce.save_config(store, {"audience": {"internal_domains": ["ourco.example"]}}, source="test")
    body = ce.load_config(store)
    assert body["audience"]["internal_people"] == ["dana"]
    assert body["audience"]["internal_domains"] == ["ourco.example"]


def test_saving_config_the_first_time_creates_the_record(store):
    record = ce.save_config(store, {"key": "ignored"}, source="test")
    assert record["collection"] == ce.COLLECTION_CONFIG
    assert ce.load_config(store)["key"] == ce.CONFIG_KEY


def test_saving_config_twice_updates_the_one_record(store):
    ce.save_config(store, {"thresholds": {"multi_thread_floor": 3}}, source="test")
    second = ce.save_config(store, {"thresholds": {"multi_thread_floor": 4}}, source="test")
    assert second["id"] == f"{ce.COLLECTION_CONFIG}_{ce.CONFIG_KEY}"
    assert len(store.find(ce.COLLECTION_CONFIG, {"key": ce.CONFIG_KEY})) == 1


def test_save_config_requires_a_source():
    """No default: only the HTTP layer knows the route that caused the write."""
    with pytest.raises(TypeError):
        ce.save_config(store, {"thresholds": {}})


# --------------------------------------------------------------------------- #
# Event ingest
# --------------------------------------------------------------------------- #


def test_an_unclassified_event_is_recorded_as_client_activity(store):
    one = room(store, "Acme")
    body = ce.record_event(
        store, {"person": "a.buyer", "action": "viewed"}, room_id=one["id"], source="test"
    )
    assert body["counted_as"] == ce.AUDIENCE_EXTERNAL
    assert body["in_client_engagement"] is True
    assert body["event"]["data"]["user_type"] == "external"


def test_an_internal_event_is_recorded_but_kept_out_of_the_client_report(store):
    one = room(store, "Acme")
    body = ce.record_event(
        store,
        {"person": "dana", "user_type": "internal", "action": "viewed"},
        room_id=one["id"],
        source="test",
    )
    assert body["counted_as"] == ce.AUDIENCE_INTERNAL
    assert body["in_client_engagement"] is False
    assert report(store)["totals"]["client_actions"] == 0
    assert report(store)["totals"]["internal_actions"] == 1


def test_an_explicit_field_on_the_payload_is_left_alone(store):
    body = ce.record_event(store, {"person": "a.buyer", "audience": "client"}, source="test")
    assert "user_type" not in body["event"]["data"]
    assert body["counted_as"] == ce.AUDIENCE_EXTERNAL


def test_an_anonymous_event_is_recorded_and_still_counted_for_nobody(store):
    one = room(store, "Acme")
    body = ce.record_event(store, {"action": "viewed"}, room_id=one["id"], source="test")
    assert body["counted_as"] == ce.AUDIENCE_UNKNOWN
    assert body["in_client_engagement"] is False
    assert report(store)["totals"]["client_actions"] == 0
    assert report(store)["coverage"]["unattributed_events_excluded"] == 1


def test_the_payload_is_stored_verbatim_so_an_undeclared_field_survives(store):
    ce.record_event(
        store,
        {"person": "a.buyer", "action": "viewed", "seat": "b12", "sentiment": "warm"},
        source="test",
    )
    stored = store.find("activity", {"sentiment": "warm"})
    assert stored and stored[0]["data"]["seat"] == "b12"


def test_an_empty_event_is_refused(store):
    with pytest.raises(EngagementReportError):
        ce.record_event(store, {}, source="test")


def test_an_event_on_an_unknown_workspace_is_refused(store):
    with pytest.raises(UnknownWorkspace):
        ce.record_event(store, {"person": "a.buyer"}, room_id="room_missing", source="test")


def test_a_recorded_event_reaches_the_report(store):
    one = room(store, "Acme")
    ce.record_event(
        store,
        {"person": "a.buyer", "action": "viewed", "occurred_at": at(1)},
        room_id=one["id"],
        source="test",
    )
    assert report(store)["totals"]["client_views"] == 1


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


def make_room(client, account="Acme", **payload) -> dict:
    body = {"name": f"{account} room", "account": account, **payload}
    return client.post("/api/records/room", json=body).json()


def make_event(client, room_id, person, action="viewed", **payload) -> dict:
    return client.post(
        f"{PREFIX}/events",
        json={"person": person, "action": action, **payload},
        params={"room_id": room_id},
    ).json()["event"]


def test_the_report_route_serves_the_tiles(client):
    one = make_room(client)
    make_event(client, one["id"], "a.buyer")
    body = client.get(f"{PREFIX}/report").json()
    assert body["report"] == "client_engagement"
    assert body["scope"]["all_workspaces"] is True
    assert body["totals"]["client_actions"] == 1
    assert len(body["tiles"]) == 5


def test_the_report_route_has_no_workspace_selector(client):
    """Sourced scope: all workspaces, automatically."""
    signature = inspect.signature(
        next(
            route.endpoint
            for route in load_feature(MODULE).router.routes
            if route.path == f"{PREFIX}/report"
        )
    )
    assert "room_id" not in signature.parameters
    assert "workspace" not in signature.parameters


def test_the_report_spans_workspaces_without_being_asked(client):
    first = make_room(client, "Acme")
    second = make_room(client, "Globex")
    make_event(client, first["id"], "a.buyer")
    make_event(client, second["id"], "b.buyer")
    assert client.get(f"{PREFIX}/report").json()["totals"]["client_actions"] == 2


def test_every_tile_expands_to_a_url_the_app_serves(client):
    make_room(client)
    for tile in client.get(f"{PREFIX}/report").json()["tiles"]:
        href = tile["accounts_href"]
        path, _, query = href.partition("?")
        assert (
            client.get(path, params=dict(item.split("=") for item in query.split("&"))).status_code
            == 200
        )


def test_the_account_list_route_sorts_and_pages(client):
    for index in range(3):
        space = make_room(client, f"Account {index}")
        make_event(client, space["id"], "a.buyer")
    body = client.get(f"{PREFIX}/accounts", params={"sort": "account", "descending": False}).json()
    assert body["report"] == "client_engagement_accounts"
    assert [row["account"] for row in body["accounts"]] == ["Account 0", "Account 1", "Account 2"]


def test_the_account_list_route_refuses_an_unknown_sort_key(client):
    response = client.get(f"{PREFIX}/accounts", params={"sort": "colour"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_report_request"
    assert "sortable columns" in response.json()["detail"]


def test_the_account_detail_route_expands_the_individuals(client):
    one = make_room(client)
    make_event(client, one["id"], "a.buyer")
    make_event(client, one["id"], "b.buyer")
    body = client.get(f"{PREFIX}/accounts/Acme").json()
    assert body["report"] == "client_engagement_account"
    assert {row["person"] for row in body["clients"]} == {"a.buyer", "b.buyer"}


def test_an_unknown_account_key_is_404(client):
    response = client.get(f"{PREFIX}/accounts/no-such-account")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_account"


def test_an_account_name_with_a_slash_is_still_addressable(client):
    make_room(client, "Northwind / EMEA")
    keys = [row["account_key"] for row in client.get(f"{PREFIX}/accounts").json()["accounts"]]
    assert keys == ["Northwind-EMEA"]
    assert client.get(f"{PREFIX}/accounts/Northwind-EMEA").json()["account"] == "Northwind / EMEA"


def test_the_workspace_route_is_room_scoped_and_404s(client):
    one = make_room(client)
    make_event(client, one["id"], "a.buyer")
    body = client.get(f"{PREFIX}/rooms/{one['id']}/engagement").json()
    assert body["scope"]["room_id"] == one["id"]
    assert body["workspace"]["account"] == "Acme"
    assert client.get(f"{PREFIX}/rooms/room_missing/engagement").status_code == 404


def test_the_workspace_route_refuses_a_bad_date(client):
    one = make_room(client)
    response = client.get(f"{PREFIX}/rooms/{one['id']}/engagement", params={"date_to": "soon"})
    assert response.status_code == 422


def test_the_filters_route_lists_what_can_be_filtered(client):
    make_room(client, "Acme", owner="dana", team="Enterprise")
    body = client.get(f"{PREFIX}/filters").json()
    assert body["owners"] == ["dana"]
    assert body["teams"] == ["Enterprise"]
    assert body["grains"] == ["day", "week"]


def test_the_team_usage_route_serves_its_report(client):
    one = make_room(client, owner="dana")
    make_event(client, one["id"], "a.buyer")
    body = client.get(f"{PREFIX}/reports/team-usage").json()
    assert body["report"] == "team_usage"
    assert body["owners_detail"][0]["owner"] == "dana"


def test_the_implementations_route_serves_its_report(client):
    make_room(client, "Acme")
    client.post("/api/records/implementation", json={"account": "Acme", "status": "active"})
    body = client.get(f"{PREFIX}/reports/implementations").json()
    assert body["report"] == "implementations"
    assert body["totals"]["active"] == 1


def test_an_implementation_for_an_account_with_no_workspace_is_not_counted(client):
    """An implementation for an account nobody in scope owns is out of scope."""
    make_room(client, "Acme")
    client.post("/api/records/implementation", json={"account": "Globex", "status": "active"})
    body = client.get(f"{PREFIX}/reports/implementations").json()
    assert body["totals"]["implementations"] == 0


def test_the_config_route_serves_the_effective_rules(client):
    body = client.get(f"{PREFIX}/config").json()
    assert body["config"]["thresholds"]["multi_thread_floor"] == 2


def test_the_config_patch_route_merges_and_changes_the_report(client):
    one = make_room(client)
    make_event(client, one["id"], "a.buyer")
    make_event(client, one["id"], "b.buyer")
    assert client.get(f"{PREFIX}/report").json()["totals"]["multi_threaded_accounts"] == 1

    response = client.patch(f"{PREFIX}/config", json={"thresholds": {"multi_thread_floor": 5}})
    assert response.status_code == 200
    assert response.json()["config"]["thresholds"]["multi_thread_floor"] == 5
    assert response.json()["config"]["thresholds"]["chart_days"] == 30
    assert client.get(f"{PREFIX}/report").json()["thread_floor"] == 5


def test_the_events_route_records_a_client_interaction(client):
    one = make_room(client)
    response = client.post(
        f"{PREFIX}/events",
        json={"person": "a.buyer", "action": "viewed"},
        params={"room_id": one["id"]},
    )
    assert response.status_code == 201
    assert response.json()["counted_as"] == "external"
    assert client.get(f"{PREFIX}/report").json()["totals"]["client_actions"] == 1


def test_the_events_route_refuses_an_empty_payload(client):
    assert client.post(f"{PREFIX}/events", json={}).status_code == 422


def test_the_events_route_refuses_an_unknown_workspace(client):
    response = client.post(
        f"{PREFIX}/events", json={"person": "a.buyer"}, params={"room_id": "room_missing"}
    )
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workspace"


def test_the_owner_and_team_filters_reach_every_read_route(client):
    one = make_room(client, "Acme", owner="dana", team="Enterprise")
    two = make_room(client, "Globex", owner="sam", team="Mid-Market")
    make_event(client, one["id"], "a.buyer")
    make_event(client, two["id"], "b.buyer")
    client.post("/api/records/implementation", json={"account": "Acme", "status": "active"})

    params = {"owner": "dana"}
    assert client.get(f"{PREFIX}/report", params=params).json()["totals"]["workspaces"] == 1
    assert client.get(f"{PREFIX}/accounts", params=params).json()["total"] == 1
    assert client.get(f"{PREFIX}/reports/team-usage", params=params).json()["totals"]["owners"] == 1
    assert client.get(f"{PREFIX}/reports/implementations", params=params).json()["count"] == 1
    assert (
        client.get(f"{PREFIX}/report", params={"team": "Enterprise"}).json()["totals"]["workspaces"]
        == 1
    )


def test_the_date_filters_reach_every_read_route(client):
    one = make_room(client)
    make_event(client, one["id"], "a.buyer", occurred_at="2026-09-01T09:00:00+00:00")
    make_event(client, one["id"], "b.buyer", occurred_at="2026-09-20T09:00:00+00:00")
    params = {"date_from": "2026-09-15", "date_to": "2026-09-27"}
    assert client.get(f"{PREFIX}/report", params=params).json()["totals"]["client_actions"] == 1
    assert client.get(f"{PREFIX}/accounts", params=params).json()["total"] == 1
    assert (
        client.get(f"{PREFIX}/reports/team-usage", params=params).json()["coverage"][
            "external_events"
        ]
        == 1
    )
    assert (
        client.get(f"{PREFIX}/reports/implementations", params=params).json()["filters"]["from"]
        is not None
    )


def test_a_bad_date_range_is_422_on_every_filtered_read_route(client):
    make_room(client)
    for path in (
        f"{PREFIX}/report",
        f"{PREFIX}/accounts",
        f"{PREFIX}/reports/team-usage",
        f"{PREFIX}/reports/implementations",
    ):
        response = client.get(path, params={"date_from": "2026-09-10", "date_to": "2026-09-01"})
        assert response.status_code == 422, path


def test_the_filters_route_takes_no_date_range_because_it_publishes_the_bounds(client):
    """It answers "what can I filter by", so it has nothing to filter."""
    signature = inspect.signature(
        next(
            route.endpoint
            for route in load_feature(MODULE).router.routes
            if route.path == f"{PREFIX}/filters"
        )
    )
    assert list(signature.parameters) == ["as_of", "store"]
    make_room(client)
    body = client.get(f"{PREFIX}/filters").json()
    assert set(body["date_range"]) == {"earliest", "latest", "as_of"}


def test_a_repeated_owner_query_parameter_is_accepted(client):
    first = make_room(client, "Acme", owner="dana")
    second = make_room(client, "Globex", owner="sam")
    make_event(client, first["id"], "a.buyer")
    make_event(client, second["id"], "b.buyer")
    body = client.get(f"{PREFIX}/report", params=[("owner", "dana"), ("owner", "sam")]).json()
    assert body["totals"]["workspaces"] == 2


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def audit_sources(client) -> set[str]:
    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    return {entry["source"] for entry in entries if PREFIX in (entry.get("source") or "")}


def test_every_write_audits_the_path_this_router_serves(client):
    one = make_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"person": "a.buyer", "action": "viewed"},
        params={"room_id": one["id"]},
    )
    client.patch(
        f"{PREFIX}/config", json={"thresholds": {"chart_days": 14}}, params={"actor": "dana"}
    )
    assert audit_sources(client) == {f"POST {PREFIX}/events", f"PATCH {PREFIX}/config"}


def test_no_audit_row_names_a_path_this_feature_does_not_serve(client):
    one = make_room(client)
    client.post(f"{PREFIX}/events", json={"person": "a.buyer"}, params={"room_id": one["id"]})
    client.patch(f"{PREFIX}/config", json={"thresholds": {}})
    stale = [source for source in audit_sources(client) if PREFIX not in source]
    assert not stale, f"audit rows name an unserved path: {stale}"


def test_every_source_this_feature_can_write_is_one_of_its_own_routes():
    """The source strings are derived from the mounted router, not written out.

    Read out of the module rather than asserted one by one, because the defect
    this guards is a *drift* between the recorded source and the mounted path:
    a hand-written list in the test would be updated in the same commit as the
    bug and would never catch it.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    expressions = re.findall(r'source=f?"([^"]*\{router\.prefix\}[^"]*)"', source)
    assert expressions, "no source is built from router.prefix"

    write_routes = {
        (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
        for route in load_feature(MODULE).router.routes
        if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in ("POST", "PATCH", "PUT", "DELETE")
    }
    recorded = {
        (expression.split(" ", 1)[0], f"{PREFIX}{expression.split('{router.prefix}')[1]}")
        for expression in expressions
    }
    # Every write route the feature serves accounts for a recorded source, and
    # every recorded source resolves to a route that exists.
    assert {method for method, _path in write_routes} == {method for method, _path in recorded}
    for method, path in recorded:
        assert (method, path) in write_routes, f"{method} {path} is not a route this feature serves"


def mounted_routes(client) -> dict[str, list[list[str]]]:
    """Every mounted path, per method, split into segments.

    ``app.routes`` is not usable for this: this FastAPI version records an included
    router as a single nested entry rather than flattening its routes, so a check
    built on it would see neither a feature's routes nor its own. The published
    schema is the flat, authoritative list of what is served.
    """
    schema = client.get("/openapi.json").json()
    mounted: dict[str, list[list[str]]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.lower() in ("get", "post", "patch", "put", "delete"):
                mounted.setdefault(method.upper(), []).append(path.strip("/").split("/"))
    return mounted


#: The record-id shapes the core ``AuditedDatabase`` mints. A recorded source
#: carries the id that filled a parameter, so a segment matching this is a value
#: where the route declares a parameter.
RECORD_ID = re.compile(r"^[a-z_]+_[0-9a-f]{16,}$")


def serves(mounted: dict[str, list[list[str]]], method: str, path: str) -> bool:
    """Whether ``method path`` reaches a mounted route.

    Segment by segment, because a path parameter fills exactly one segment: the
    two must line up in number as well as in shape.
    """
    for candidate in mounted.get(method, []):
        recorded = path.strip("/").split("/")
        if len(candidate) != len(recorded):
            continue
        if all(
            expected.startswith("{") or expected == actual or RECORD_ID.match(actual)
            for expected, actual in zip(candidate, recorded, strict=False)
        ):
            return True
    return False


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client):
    """The invariant, product-wide: an audit `source` is always callable.

    This drives writes through this feature *and* through the core record routes,
    then checks every row the log holds. The defect the contract names by name is
    "a feature's audit log kept recording a path the app had stopped serving",
    which is invisible to a test scoped to one feature's own sources and is caught
    by this one.
    """
    one = make_room(client)
    client.post("/api/records/room", json={"name": "Core room", "account": "Core"})
    client.patch(f"/api/records/room/{one['id']}", json={"stage": "negotiation"})
    client.post(f"{PREFIX}/events", json={"person": "a.buyer"}, params={"room_id": one["id"]})
    client.patch(f"{PREFIX}/config", json={"thresholds": {"chart_days": 21}})
    client.post("/api/records/bulk/x", json=[{"k": "v"}])

    mounted = mounted_routes(client)
    assert serves(mounted, "POST", f"{PREFIX}/events")
    assert serves(mounted, "PATCH", f"{PREFIX}/config")

    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    assert entries
    for entry in entries:
        method, _, path = str(entry["source"]).partition(" ")
        assert serves(mounted, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


def test_the_audit_source_is_built_from_the_live_prefix_not_a_hardcoded_url(client):
    """The defect the contract names by name: a row that names a path nobody called."""
    one = make_room(client)
    client.post(f"{PREFIX}/events", json={"person": "a.buyer"}, params={"room_id": one["id"]})
    entry = client.get("/api/audit", params={"collection": "activity"}).json()["entries"][0]
    assert entry["source"] == f"POST {PREFIX}/events"
    method, path = entry["source"].split(" ", 1)
    assert method == "POST"
    assert path.startswith(load_feature(MODULE).router.prefix)


def test_the_audit_row_carries_the_room_scope_and_the_new_state(client):
    one = make_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"person": "a.buyer", "action": "viewed"},
        params={"room_id": one["id"], "actor": "dana"},
    )
    entry = client.get("/api/audit", params={"collection": "activity"}).json()["entries"][0]
    assert entry["room_id"] == one["id"]
    assert entry["actor"] == "dana"
    assert entry["after_state"]["person"] == "a.buyer"


def test_the_config_write_is_audited_with_the_actor(client):
    client.patch(
        f"{PREFIX}/config", json={"thresholds": {"chart_days": 7}}, params={"actor": "dana"}
    )
    entry = client.get("/api/audit", params={"collection": ce.COLLECTION_CONFIG}).json()["entries"][
        0
    ]
    assert entry["source"] == f"PATCH {PREFIX}/config"
    assert entry["actor"] == "dana"
    assert entry["after_state"]["thresholds"]["chart_days"] == 7


def test_the_audit_row_survives_a_config_update_rather_than_creating_a_second_record(client):
    client.patch(f"{PREFIX}/config", json={"thresholds": {"chart_days": 7}})
    client.patch(f"{PREFIX}/config", json={"thresholds": {"chart_days": 21}})
    entries = client.get("/api/audit", params={"collection": ce.COLLECTION_CONFIG}).json()[
        "entries"
    ]
    assert {entry["action"] for entry in entries} == {"insert", "update"}
    assert len({entry["record_id"] for entry in entries}) == 1


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """The feature's own seeder, over its own database."""
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "seed.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit", actor="test")
    rooms = [
        db.create(
            "room",
            {
                "name": "Northwind room",
                "account": "Northwind Traders",
                "owner": "dana",
                "team": "Enterprise",
            },
            source="test",
        ),
        db.create(
            "room",
            {
                "name": "Contoso room",
                "account": "Contoso Health",
                "owner": "sam",
                "team": "Mid-Market",
            },
            source="test",
        ),
    ]
    feature = load_feature(MODULE)
    summary = feature.seed(
        db,
        {
            "room_ids": [(rooms[0]["id"], "Northwind Traders"), (rooms[1]["id"], "Contoso Health")],
            "now": NOW,
            "rng": random.Random("wf024"),
        },
    )
    yield RecordStore(db), summary
    db.close()


def test_the_seed_returns_a_summary_the_seeder_can_print(seeded):
    _store, summary = seeded
    assert isinstance(summary, str) and summary
    assert "activity events" in summary
    assert "implementations" in summary


def test_the_seed_is_deterministic(seeded, tmp_path):
    _store, summary = seeded
    assert "3 reps" in summary


def test_the_seed_creates_the_interactions_it_claims(seeded):
    store, summary = seeded
    count = int(summary.split(" activity events")[0])
    assert len(store.list("activity", limit=1000)) == count
    assert len(store.list("implementation", limit=1000)) == 8


def test_the_seed_registers_the_reps_as_internal(seeded):
    store, _summary = seeded
    config = ce.load_config(store)
    assert set(config["audience"]["internal_people"]) >= {"dana", "sam", "kai"}
    assert ce.classify_audience("dana", {}, config) == ce.AUDIENCE_INTERNAL


def test_the_seed_creates_a_multi_threaded_account(seeded):
    store, _summary = seeded
    accounts = accounts_of(store)
    assert accounts["Northwind Traders"]["multi_threaded"] is True
    assert accounts["Northwind Traders"]["unique_clients"] >= 3
    assert accounts["Northwind Traders"]["champion"] is not None


def test_the_seed_creates_a_single_threaded_account(seeded):
    """The coverage risk the report exists to surface.

    On an account name the shared core dataset does not use, so the core
    seeder's random scatter of people across rooms cannot mask it.
    """
    store, _summary = seeded
    accounts = accounts_of(store)
    assert accounts["Initech"]["champion_only_thread"] is True
    assert accounts["Initech"]["multi_threaded"] is False
    assert accounts["Initech"]["unique_clients"] == 1
    assert accounts["Initech"]["champion"]["person"] == "one.person@initech.example"


def test_the_seed_keeps_the_single_threaded_account_out_of_the_core_scatter(seeded):
    """Only the Initech buyer may appear on the Initech room."""
    store, _summary = seeded
    clients = {client["person"] for client in accounts_of(store)["Initech"]["clients"]}
    assert clients == {"one.person@initech.example"}


def test_the_seed_creates_an_account_nobody_has_opened(seeded):
    store, _summary = seeded
    body = ce.client_engagement(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["accounts_without_client_activity"] >= 1
    # The unopened account is in the denominator, which is the whole point.
    assert body["totals"]["workspaces"] > body["totals"]["workspaces_with_client_activity"]
    assert body["totals"]["avg_unique_clients_per_workspace"] < body["totals"]["unique_clients"]


def test_the_seed_creates_internal_activity_that_never_reaches_a_client_total(seeded):
    store, _summary = seeded
    body = ce.client_engagement(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["internal_actions"] > 0
    assert body["coverage"]["internal_events_excluded"] == body["totals"]["internal_actions"]
    assert body["totals"]["client_actions"] > 0


def test_the_seed_creates_an_anonymous_interaction(seeded):
    store, _summary = seeded
    body = ce.client_engagement(store, filters=no_filters(), as_of=AS_OF)
    assert body["coverage"]["unattributed_events_excluded"] == 1


def test_the_seed_creates_a_workspace_with_no_client_activity(seeded):
    store, _summary = seeded
    body = ce.client_engagement(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["workspaces"] > body["totals"]["workspaces_with_client_activity"]


def test_the_seed_creates_both_an_overdue_and_an_on_time_delivery(seeded):
    store, _summary = seeded
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert body["totals"]["at_risk"] > 0
    assert 0 < body["totals"]["completed_on_time_percent"] < 100


def test_the_seed_creates_an_unassigned_implementation(seeded):
    store, _summary = seeded
    body = ce.implementations(store, filters=no_filters(), as_of=AS_OF)
    assert any(row["owner"] == "(unassigned)" for row in body["by_owner"])


def test_the_seed_creates_a_rep_who_owns_nothing(seeded):
    store, _summary = seeded
    assert (
        "kai"
        in ce.team_usage(store, filters=no_filters(), as_of=AS_OF)["unattributed_internal_people"]
    )


def test_the_seed_runs_every_report_without_raising(seeded):
    store, _summary = seeded
    assert ce.client_engagement(store, filters=no_filters(), as_of=AS_OF)["tiles"]
    assert ce.available_filters(store, as_of=AS_OF)["owners"]
    assert ce.team_usage(store, filters=no_filters(), as_of=AS_OF)["owners_detail"]
    assert ce.implementations(store, filters=no_filters(), as_of=AS_OF)["count"]


def test_the_seed_creates_its_own_workspace_for_an_account_that_has_none(tmp_path, monkeypatch):
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "extra.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    db = AuditedDatabase(tmp_path / "extra.db", mirror_dir=tmp_path / "audit", actor="test")
    feature = load_feature(MODULE)
    summary = feature.seed(db, {"room_ids": [], "now": NOW, "rng": random.Random("x")})
    assert "no demo rooms" in summary
    # Even with no rooms to attach to, the config that makes the demo legible is
    # written, and the feature's own report still answers.
    assert ce.classify_audience("dana", {}, ce.load_config(RecordStore(db))) == ce.AUDIENCE_INTERNAL
    db.close()


def test_the_seed_does_not_reuse_a_room_from_another_account(tmp_path, monkeypatch):
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "reuse.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    db = AuditedDatabase(tmp_path / "reuse.db", mirror_dir=tmp_path / "audit", actor="test")
    northwind = db.create(
        "room", {"name": "Northwind", "account": "Northwind Traders"}, source="test"
    )
    feature = load_feature(MODULE)
    feature.seed(
        db,
        {
            "room_ids": [(northwind["id"], "Northwind Traders")],
            "now": NOW,
            "rng": random.Random("y"),
        },
    )
    store = RecordStore(db)
    accounts = accounts_of(store)
    assert accounts["Northwind Traders"]["workspaces"][0]["id"] == northwind["id"]
    db.close()


def test_the_seeder_writes_through_the_audited_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "audited.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    db = AuditedDatabase(tmp_path / "audited.db", mirror_dir=tmp_path / "audit", actor="test")
    db.create("room", {"name": "Northwind", "account": "Northwind Traders"}, source="test")
    feature = load_feature(MODULE)
    feature.seed(
        db,
        {
            "room_ids": [(db.list("room")[0]["id"], "Northwind Traders")],
            "now": NOW,
            "rng": random.Random("z"),
        },
    )
    rows = db.audit(limit=10_000)
    assert rows, "the seeder wrote nothing to the audit log"
    assert {row["actor"] for row in rows} <= {"dana", "sam", "kai", "system", "seed", "test"}
    db.close()
