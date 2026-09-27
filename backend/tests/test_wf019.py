"""Tests for WF-019: rank content influence and associate revenue with assets.

Two levels, on purpose:

* domain tests over :mod:`dsr.influence` against a temporary database, which is
  where the researched rules live - the two rates, the five grains, the two
  independent time windows, the association rule, the dedupe on ``event_id``;
* HTTP tests over ``/api/wf-019/*``, which pin what a client depends on: the
  status codes, the error bodies, the shape of each researched surface, and the
  guarantee that every write names the route that served it.

Every timestamp is injected, so nothing here depends on the wall clock, and no
test reaches around the audited store to set up its data - ``2026-09-26`` is a
Saturday, which the week-grain assertions rely on.
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr import influence
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import REGISTRY
from dsr.influence import (
    EVENT_COLLECTION,
    Filters,
    InfluenceError,
    UnknownAsset,
    asset_detail,
    collections,
    compile_report,
    engagement,
    errors,
    ingest_activity,
    link_account,
    link_deal,
    load_events,
    normalize_action,
    portfolio,
    preconditions,
    record_event,
    sales_influence,
    summarise_links,
    summarise_vocabulary,
    top_content,
    vocabulary,
)
from dsr.store import RecordStore

#: This feature's own prefix. The HTTP tests go through the mounted app rather
#: than a throwaway router, so a collision with another feature fails loudly here.
PREFIX = "/api/wf-019"

FEATURE_ID = "wf-019-rank-content-influence-and-associate-r"

#: 2026-09-26 is a Saturday. 2026-09-21 is the Monday of its week and
#: 2026-07-01 opens Q3, which the bucket assertions depend on.
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SOURCE = "test"


def at(**kwargs) -> str:
    """ISO timestamp ``kwargs`` before NOW."""
    return (NOW - timedelta(**kwargs)).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    """An audited store over an in-memory database, closed after the test."""
    db = AuditedDatabase(":memory:")
    try:
        yield RecordStore(db)
    finally:
        db.close()


@pytest.fixture()
def client(monkeypatch):
    """The mounted app over a temporary database, the way a client sees it."""
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    # Static mounts are import-time, so point the module at a missing directory.
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


LIBRARY = {
    "deck": {"title": "Overview Deck", "kind": "pptx", "collections": ["Enterprise"]},
    "pack": {"title": "Security Pack", "kind": "pdf", "collections": ["Security"]},
    "guide": {"title": "API Guide", "kind": "pdf", "collections": ["Enterprise", "Reference"]},
    "orphan": {"title": "Never Touched", "kind": "pdf", "collections": ["Archive"]},
}


def seed_library(store: RecordStore) -> dict[str, str]:
    """Four library assets, one of which nothing has ever been done with."""
    return {
        key: store.create("document", spec, actor="dana", source=SOURCE)["id"]
        for key, spec in LIBRARY.items()
    }


def seed_rooms(store: RecordStore, count: int = 2) -> list[str]:
    return [
        store.create("room", {"name": f"Room {index}", "account": "Acme"}, actor="dana", source=SOURCE)["id"]
        for index in range(count)
    ]


def share(store: RecordStore, asset: str, room: str | None = None, **kwargs):
    return record_event(
        store,
        {"action": "asset.shared", "asset_id": asset, "occurred_at": kwargs.pop("when", at(days=1)), **kwargs},
        room_id=room,
        actor="dana",
        source=SOURCE,
    )


def view(store: RecordStore, asset: str, room: str | None = None, **kwargs):
    return record_event(
        store,
        {"action": "asset.viewed", "asset_id": asset, "occurred_at": kwargs.pop("when", at(days=1)), **kwargs},
        room_id=room,
        actor="dana",
        source=SOURCE,
    )


def download(store: RecordStore, asset: str, room: str | None = None, **kwargs):
    return record_event(
        store,
        {
            "action": "asset.downloaded",
            "asset_id": asset,
            "occurred_at": kwargs.pop("when", at(days=1)),
            **kwargs,
        },
        room_id=room,
        actor="dana",
        source=SOURCE,
    )


# --------------------------------------------------------------------------- #
# Registration - the structural assertions
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery():
    """No file in the host names this feature; discovery mounts it anyway."""
    record = REGISTRY.by_id(FEATURE_ID)
    assert record is not None, "the plugin host did not load WF-019"
    assert record.prefix == PREFIX
    assert record.ticket == "WF-019"
    assert record.loaded is True


def test_no_feature_failed_to_load():
    assert [failure.id for failure in REGISTRY.failed] == []


def test_the_prefix_is_unique_across_the_host():
    prefixes = [feature.prefix for feature in REGISTRY.features if feature.prefix]
    assert prefixes.count(PREFIX) == 1


def test_no_route_collides_with_another_feature():
    seen: set[tuple[str, str]] = set()
    for feature in REGISTRY.features:
        for route in feature.routes:
            for method in route["methods"]:
                key = (method, route["path"])
                assert key not in seen, f"{feature.id} duplicates {key}"
                seen.add(key)


def test_every_researched_surface_is_served():
    record = REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    paths = {route["path"] for route in record.routes}
    for expected in (
        f"{PREFIX}/portfolio",
        f"{PREFIX}/engagement",
        f"{PREFIX}/top-content",
        f"{PREFIX}/sales-influence",
        f"{PREFIX}/events",
        f"{PREFIX}/rooms/{{room_id}}/influence",
    ):
        assert expected in paths, f"{expected} is missing from the researched surface"


def test_the_feature_claims_only_its_own_error_types():
    record = REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    assert record.exception_handlers == [
        "CrmNotLinked",
        "InfluenceError",
        "InvalidWindow",
        "UnknownAsset",
        "UnknownRoom",
        "UnparseableTime",
    ]


# --------------------------------------------------------------------------- #
# Time, buckets, filters
# --------------------------------------------------------------------------- #


def test_an_iso_timestamp_with_a_z_suffix_parses():
    assert influence.parse_time("2026-09-26T12:00:00Z") == NOW


def test_a_date_only_bound_is_midnight_utc():
    assert influence.parse_time("2026-09-26") == datetime(2026, 9, 26, tzinfo=timezone.utc)


def test_a_timestamp_with_no_offset_is_read_as_utc():
    """Otherwise a hand-written row moves across a bucket boundary."""
    assert influence.parse_time("2026-09-26T12:00:00") == NOW


def test_an_offset_timestamp_is_converted_not_dropped():
    assert influence.parse_time("2026-09-26T14:00:00+02:00") == NOW


def test_an_absent_bound_is_not_an_error():
    assert influence.parse_time(None) is None
    assert influence.parse_time("") is None


def test_an_unparseable_bound_names_the_value_and_the_field():
    with pytest.raises(errors.UnparseableTime) as excinfo:
        influence.parse_time("last tuesday", "client activity from")
    assert "last tuesday" in str(excinfo.value)
    assert excinfo.value.what == "client activity from"


@pytest.mark.parametrize(
    ("grain", "moment", "expected"),
    [
        ("day", "2026-09-26T12:00:00Z", "2026-09-26"),
        # Saturday, so the week's label is the Monday five days earlier.
        ("week", "2026-09-26T12:00:00Z", "2026-09-21"),
        ("week", "2026-09-21T00:00:00Z", "2026-09-21"),
        ("month", "2026-09-26T12:00:00Z", "2026-09"),
        ("quarter", "2026-09-26T12:00:00Z", "2026-Q3"),
        ("quarter", "2026-01-05T00:00:00Z", "2026-Q1"),
        ("year", "2026-09-26T12:00:00Z", "2026"),
    ],
)
def test_buckets_are_utc_calendar_buckets_with_monday_weeks(grain, moment, expected):
    assert influence.bucket_key(influence.parse_time(moment), grain) == expected


def test_a_quarter_series_rolls_q4_into_the_next_year():
    labels = influence.bucket_range(
        influence.parse_time("2026-11-15T00:00:00Z"),
        influence.parse_time("2027-02-01T00:00:00Z"),
        "quarter",
    )
    assert labels == ["2026-Q4", "2027-Q1"]


def test_a_month_series_walks_calendar_months():
    labels = influence.bucket_range(
        influence.parse_time("2026-12-05T00:00:00Z"),
        influence.parse_time("2027-02-01T00:00:00Z"),
        "month",
    )
    assert labels == ["2026-12", "2027-01", "2027-02"]


def test_a_week_series_crosses_the_year_boundary():
    labels = influence.bucket_range(
        influence.parse_time("2026-12-28T00:00:00Z"),
        influence.parse_time("2027-01-04T00:00:00Z"),
        "week",
    )
    assert labels == ["2026-12-28", "2027-01-04"]


def test_a_single_bucket_range_is_one_label():
    labels = influence.bucket_range(NOW, NOW, "day")
    assert labels == ["2026-09-26"]


def test_the_two_windows_are_independent():
    """The research offers two ranges; neither bounds the other's events."""
    filters = Filters.build(shared_from="2026-01-01T00:00:00Z")
    assert filters.window_for("shared") == (influence.parse_time("2026-01-01T00:00:00Z"), None)
    assert filters.window_for("viewed") == (None, None)
    assert filters.window_for("downloaded") == (None, None)


def test_a_window_is_inclusive_at_both_ends():
    filters = Filters.build(activity_from="2026-09-20T00:00:00Z", activity_to="2026-09-20T23:59:59Z")
    assert filters.in_window("viewed", influence.parse_time("2026-09-20T00:00:00Z")) is True
    assert filters.in_window("viewed", influence.parse_time("2026-09-20T23:59:59Z")) is True
    assert filters.in_window("viewed", influence.parse_time("2026-09-19T23:59:59Z")) is False


def test_a_window_starting_after_it_ends_is_refused():
    with pytest.raises(errors.InvalidWindow) as excinfo:
        Filters.build(activity_from="2026-02-01", activity_to="2026-01-01")
    assert "client activity" in str(excinfo.value)


def test_the_shares_window_is_validated_too():
    with pytest.raises(errors.InvalidWindow):
        Filters.build(shared_from="2026-02-01", shared_to="2026-01-01")


def test_an_unbounded_filter_set_says_it_is_unbounded():
    assert Filters().bounded is False
    assert Filters.build(shared_to="2026-01-01").bounded is True


def test_a_blank_collection_filter_is_no_filter():
    assert Filters.build(collection="   ").collection is None
    assert Filters.build(collection="Security").collection == "Security"


def test_a_filter_set_round_trips_to_json():
    filters = Filters.build(
        collection="Security",
        activity_from="2026-09-01",
        shared_to="2026-09-30T12:00:00Z",
        room_id="room_1",
    )
    body = filters.as_dict()
    assert body["collection"] == "Security"
    assert body["activity_from"].startswith("2026-09-01")
    assert body["room_id"] == "room_1"
    assert body["shared_from"] is None


def test_filters_are_frozen_so_a_scope_cannot_be_widened_afterwards():
    filters = Filters.build(collection="Security")
    with pytest.raises(Exception):
        filters.collection = "Enterprise"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Collections and titles - the schema-flexible reads
# --------------------------------------------------------------------------- #


def test_collections_are_read_from_an_array_a_string_or_a_tag():
    assert influence.asset_collections({"data": {"collections": ["a", "b"]}}) == ["a", "b"]
    assert influence.asset_collections({"data": {"collection": "Enterprise"}}) == ["Enterprise"]
    assert influence.asset_collections({"data": {"tags": ["deck"]}}) == ["deck"]
    assert influence.asset_collections({"data": {}}) == []


def test_a_collection_name_is_not_repeated_across_fields():
    names = influence.asset_collections({"data": {"collections": ["a"], "tags": ["a"]}})
    assert names == ["a"]


def test_a_title_falls_back_through_the_names_a_deployment_might_use():
    assert influence.asset_title({"id": "d1", "data": {"title": "Deck"}}) == "Deck"
    assert influence.asset_title({"id": "d1", "data": {"name": "Deck"}}) == "Deck"
    assert influence.asset_title({"id": "d1", "data": {}}) == "d1"


# --------------------------------------------------------------------------- #
# Ingest: the researched webhooks
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("asset.viewed", "viewed"),
        ("asset.shared", "shared"),
        ("asset.downloaded", "downloaded"),
        ("viewed", "viewed"),
        ("shared", "shared"),
        ("downloaded", "downloaded"),
        ("view", "viewed"),
        ("SHARED", "shared"),
        # The room-analytics taxonomy, which names the same two facts.
        ("viewed_document", "viewed"),
        ("downloaded_document", "downloaded"),
    ],
)
def test_every_spelling_of_an_action_lands_on_the_same_three(spelling, canonical):
    assert normalize_action(spelling) == canonical


@pytest.mark.parametrize("action", [None, "", "liked", "commented", 42])
def test_anything_else_is_not_an_asset_occurrence(action):
    assert normalize_action(action) is None


def test_a_share_is_recorded_with_its_audience_and_timestamp(store):
    assets = seed_library(store)
    room = seed_rooms(store, 1)[0]
    stored = share(store, assets["deck"], room, shared_by="dana", when=at(days=2, hours=3))

    assert stored["data"]["action"] == "shared"
    assert stored["data"]["audience"] == "internal"
    assert stored["data"]["asset_id"] == assets["deck"]
    assert stored["data"]["shared_by"] == "dana"
    assert stored["data"]["at"].startswith("2026-09-24T09:00")
    assert stored["duplicate"] is False


def test_a_view_is_external_unless_the_payload_says_otherwise(store):
    assets = seed_library(store)
    external = view(store, assets["deck"])
    internal = view(store, assets["deck"], internal=True)

    assert external["data"]["audience"] == "external"
    assert internal["data"]["audience"] == "internal"


def test_a_declared_audience_beats_every_other_signal(store):
    assets = seed_library(store)
    stored = view(store, assets["deck"], audience="internal", internal=False)
    assert stored["data"]["audience"] == "internal"


def test_an_unknown_audience_is_refused(store):
    assets = seed_library(store)
    with pytest.raises(InfluenceError, match="audience"):
        view(store, assets["deck"], audience="everyone")


def test_an_asset_can_be_named_by_its_title(store):
    assets = seed_library(store)
    stored = record_event(
        store, {"action": "asset.viewed", "asset": "API Guide"}, room_id=None, actor=None, source=SOURCE
    )
    assert stored["data"]["asset_id"] == assets["guide"]


def test_two_assets_with_one_title_are_refused_rather_than_guessed(store):
    """Guessing would attribute revenue to the wrong document, silently."""
    seed_library(store)
    store.create("document", {"title": "Overview Deck"}, actor="dana", source=SOURCE)

    with pytest.raises(InfluenceError) as excinfo:
        record_event(
            store, {"action": "asset.viewed", "asset": "Overview Deck"}, room_id=None, actor=None, source=SOURCE
        )
    assert "asset_id" in str(excinfo.value)


def test_an_event_needs_to_name_an_asset(store):
    seed_library(store)
    with pytest.raises(InfluenceError, match="asset_id"):
        record_event(store, {"action": "asset.viewed"}, room_id=None, actor=None, source=SOURCE)


def test_an_unknown_asset_id_is_404_worth(store):
    with pytest.raises(UnknownAsset):
        record_event(
            store, {"action": "asset.viewed", "asset_id": "nope"}, room_id=None, actor=None, source=SOURCE
        )


def test_an_unknown_action_is_refused_with_the_accepted_set(store):
    assets = seed_library(store)
    with pytest.raises(InfluenceError) as excinfo:
        view(store, assets["deck"], action="liked")
    assert "asset.viewed" in str(excinfo.value)


def test_the_event_key_is_accepted_as_well_as_the_action(store):
    assets = seed_library(store)
    stored = record_event(
        store,
        {"event": "asset.viewed", "asset_id": assets["deck"]},
        room_id=None,
        actor=None,
        source=SOURCE,
    )
    assert stored["data"]["action"] == "viewed"


def test_an_unparseable_occurrence_time_is_refused(store):
    assets = seed_library(store)
    with pytest.raises(errors.UnparseableTime):
        view(store, assets["deck"], when="the other day")


def test_an_occurrence_defaults_to_now(store):
    assets = seed_library(store)
    stored = record_event(
        store, {"action": "asset.viewed", "asset_id": assets["deck"]}, room_id=None, actor=None, source=SOURCE
    )
    assert stored["data"]["at"]


@pytest.mark.parametrize("seconds", ["soon", True, [30], -5])
def test_dwell_time_that_is_not_a_non_negative_number_is_refused(store, seconds):
    assets = seed_library(store)
    with pytest.raises(InfluenceError, match="seconds"):
        view(store, assets["deck"], seconds=seconds)


@pytest.mark.parametrize(("raw", "expected"), [("90", 90), (90.4, 90), (90.6, 91), (None, 0)])
def test_dwell_time_is_read_whole_seconds(store, raw, expected):
    assets = seed_library(store)
    stored = view(store, assets["deck"], seconds=raw)
    assert stored["data"]["seconds"] == expected


def test_dwell_time_is_read_from_the_namespaces_the_product_writes(store):
    assets = seed_library(store)
    stored = view(store, assets["deck"], seconds_on_page=45)
    assert stored["data"]["seconds"] == 45


def test_an_occurrence_in_an_unknown_workspace_is_refused(store):
    assets = seed_library(store)
    with pytest.raises(errors.UnknownRoom):
        view(store, assets["deck"], "room_missing")


def test_an_occurrence_with_no_workspace_is_a_real_portfolio_wide_share(store):
    """The researched report spans 'all workspaces', so this is not a mistake."""
    assets = seed_library(store)
    stored = share(store, assets["deck"], None)
    assert stored["room_id"] is None
    assert portfolio(store)["metrics"]["content_shares"] == 1


def test_an_occurrence_lands_in_the_envelope_not_the_payload(store):
    """The store strips reserved keys, so the payload must not pretend."""
    assets = seed_library(store)
    room = seed_rooms(store, 1)[0]
    stored = share(store, assets["deck"], room)
    assert "room_id" not in stored["data"]
    assert stored["room_id"] == room
    assert load_events(store)[0][0]["room_id"] == room


def test_a_workspace_scoped_occurrence_inherits_the_asset_own_workspace(store):
    seed_library(store)
    room = seed_rooms(store, 1)[0]
    asset = store.create(
        "document", {"title": "Attached Deck"}, room_id=room, actor="dana", source=SOURCE
    )["id"]
    stored = record_event(
        store, {"action": "asset.viewed", "asset_id": asset}, room_id=None, actor=None, source=SOURCE
    )
    assert stored["room_id"] == room


# --------------------------------------------------------------------------- #
# Ingest: redelivery
# --------------------------------------------------------------------------- #


def test_a_repeated_event_id_is_the_same_occurrence(store):
    assets = seed_library(store)
    first = share(store, assets["deck"], event_id="hook-1")
    second = share(store, assets["deck"], event_id="hook-1")

    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert portfolio(store)["metrics"]["content_shares"] == 1
    assert store.audit(collection=EVENT_COLLECTION, limit=10) == store.audit(
        collection=EVENT_COLLECTION, limit=10
    )


def test_a_repeat_writes_nothing_at_all(store):
    assets = seed_library(store)
    share(store, assets["deck"], event_id="hook-1")
    before = len(store.audit(collection=EVENT_COLLECTION, limit=100))
    share(store, assets["deck"], event_id="hook-1")
    assert len(store.audit(collection=EVENT_COLLECTION, limit=100)) == before


def test_an_occurrence_with_no_event_id_is_never_deduplicated(store):
    """Guessing an identity the caller did not supply would merge two shares."""
    assets = seed_library(store)
    share(store, assets["deck"])
    share(store, assets["deck"])
    assert portfolio(store)["metrics"]["content_shares"] == 2


def test_two_event_ids_are_two_occurrences(store):
    assets = seed_library(store)
    share(store, assets["deck"], event_id="hook-1")
    share(store, assets["deck"], event_id="hook-2")
    assert portfolio(store)["metrics"]["content_shares"] == 2


# --------------------------------------------------------------------------- #
# Ingest: the activity backfill
# --------------------------------------------------------------------------- #


def test_the_backfill_projects_only_the_three_asset_occurrences(store):
    assets = seed_library(store)
    room = seed_rooms(store, 1)[0]
    for action in ("viewed", "shared", "downloaded"):
        store.create(
            "activity",
            {"action": action, "target": "Overview Deck", "occurred_at": at(days=1)},
            room_id=room,
            actor="system",
            source=SOURCE,
        )
    for action in ("commented", "opened_link", "completed_section", "watched_video"):
        store.create(
            "activity",
            {"action": action, "target": "Overview Deck", "occurred_at": at(days=1)},
            room_id=room,
            actor="system",
            source=SOURCE,
        )

    result = ingest_activity(store, actor="system", source=SOURCE)

    assert result["scanned"] == 7
    assert result["created"] == 3
    assert result["skipped"] == {
        "commented": 1,
        "opened_link": 1,
        "completed_section": 1,
        "watched_video": 1,
    }
    assert portfolio(store)["metrics"]["content_shares"] == 1


def test_the_backfill_reads_the_other_features_taxonomy(store):
    assets = seed_library(store)
    room = seed_rooms(store, 1)[0]
    store.create(
        "activity",
        {"action": "viewed_document", "target": "API Guide", "occurred_at": at(days=1)},
        room_id=room,
        actor="system",
        source=SOURCE,
    )

    result = ingest_activity(store, actor="system", source=SOURCE)

    assert result["created"] == 1
    assert top_content(store)["rows"][0]["asset_id"] == assets["guide"]


def test_the_backfill_prefers_an_asset_id_over_a_title(store):
    assets = seed_library(store)
    room = seed_rooms(store, 1)[0]
    store.create(
        "activity",
        {
            "action": "viewed",
            "target": "Never Touched",
            "target_id": assets["pack"],
            "occurred_at": at(days=1),
        },
        room_id=room,
        actor="system",
        source=SOURCE,
    )

    ingest_activity(store, actor="system", source=SOURCE)

    assert top_content(store)["rows"][0]["asset_id"] == assets["pack"]


def test_running_the_backfill_twice_changes_nothing(store):
    assets = seed_library(store)
    room = seed_rooms(store, 1)[0]
    store.create(
        "activity",
        {"action": "viewed", "target": "Overview Deck", "occurred_at": at(days=1)},
        room_id=room,
        actor="system",
        source=SOURCE,
    )

    first = ingest_activity(store, actor="system", source=SOURCE)
    second = ingest_activity(store, actor="system", source=SOURCE)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["already_recorded"] == 1


def test_an_activity_row_naming_a_missing_asset_is_counted_not_fatal(store):
    seed_library(store)
    room = seed_rooms(store, 1)[0]
    for target in ("Overview Deck", "A Deck That Was Deleted"):
        store.create(
            "activity",
            {"action": "viewed", "target": target, "occurred_at": at(days=1)},
            room_id=room,
            actor="system",
            source=SOURCE,
        )

    result = ingest_activity(store, actor="system", source=SOURCE)

    assert result["created"] == 1
    assert result["skipped"]["unmatched_target"] == 1


def test_an_activity_row_with_no_target_is_counted_under_its_own_name(store):
    seed_library(store)
    room = seed_rooms(store, 1)[0]
    store.create(
        "activity", {"action": "viewed", "occurred_at": at(days=1)}, room_id=room, actor="system", source=SOURCE
    )

    result = ingest_activity(store, actor="system", source=SOURCE)

    assert result["created"] == 0
    assert result["skipped"]["no_target"] == 1


def test_the_backfill_carries_the_occurrence_onto_the_envelope_workspace(store):
    seed_library(store)
    room = seed_rooms(store, 1)[0]
    store.create(
        "activity",
        {"action": "shared", "target": "Overview Deck", "occurred_at": at(days=1)},
        room_id=room,
        actor="system",
        source=SOURCE,
    )
    ingest_activity(store, actor="system", source=SOURCE)

    events, _ = load_events(store)
    assert events[0]["room_id"] == room


def test_a_recorded_occurrence_with_no_workspace_or_asset_is_skipped_not_defaulted(store):
    """Inventing any of the three would place an event that never happened."""
    seed_library(store)
    store.create(EVENT_COLLECTION, {"action": "viewed"}, actor="dana", source=SOURCE)
    store.create(
        EVENT_COLLECTION, {"asset_id": "document_x", "action": "viewed"}, actor="dana", source=SOURCE
    )
    store.create(
        EVENT_COLLECTION, {"asset_id": "document_x", "at": at(days=1)}, actor="dana", source=SOURCE
    )
    room = seed_rooms(store, 1)[0]
    store.create(
        EVENT_COLLECTION,
        {"asset_id": "document_x", "action": "viewed"},
        room_id=room,
        actor="dana",
        source=SOURCE,
    )

    events, _ = load_events(store)
    assert events == []


def test_a_row_whose_action_is_unrecognised_is_not_counted_as_a_view(store):
    """Everything downstream reads 'not a share and not a download' as a view."""
    assets = seed_library(store)
    store.create(
        EVENT_COLLECTION,
        {"asset_id": assets["deck"], "at": at(days=1), "audience": "external"},
        actor="dana",
        source=SOURCE,
    )
    store.create(
        EVENT_COLLECTION,
        {"asset_id": assets["deck"], "at": at(days=1), "action": "bookmarked"},
        actor="dana",
        source=SOURCE,
    )

    metrics = portfolio(store)["metrics"]
    assert metrics["content_client_views"] == 0
    assert metrics["internal_views"] == 0


def test_a_row_written_in_the_webhook_spelling_still_reads(store):
    """Normalised on the way out, not only on the way in."""
    assets = seed_library(store)
    store.create(
        EVENT_COLLECTION,
        {"asset_id": assets["deck"], "at": at(days=1), "action": "asset.downloaded"},
        actor="dana",
        source=SOURCE,
    )
    assert portfolio(store)["metrics"]["downloads"] == 1


# --------------------------------------------------------------------------- #
# The two researched rates
# --------------------------------------------------------------------------- #


def test_the_portfolio_reports_the_five_researched_metrics_by_name(store):
    seed_library(store)
    metrics = portfolio(store)["metrics"]
    for name in influence.PORTFOLIO_METRICS:
        assert name in metrics
    assert metrics["number_of_assets"] == 4


def test_utilization_is_the_share_of_content_shared_at_least_once(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    share(store, assets["deck"])

    metrics = portfolio(store)["metrics"]

    # One of four assets has been shared, however many times.
    assert metrics["utilization_rate"] == 25.0
    assert metrics["utilization_rate_numerator"] == 1
    assert metrics["assets_in_scope"] == 4


def test_engagement_is_the_share_of_content_viewed_at_least_once(store):
    assets = seed_library(store)
    view(store, assets["pack"])
    view(store, assets["pack"])
    view(store, assets["guide"])

    metrics = portfolio(store)["metrics"]

    assert metrics["engagement_rate"] == 50.0
    assert metrics["engagement_rate_numerator"] == 2


def test_a_download_alone_is_neither_utilization_nor_engagement(store):
    """The research defines both rates over shares and views, separately."""
    assets = seed_library(store)
    download(store, assets["deck"])

    metrics = portfolio(store)["metrics"]

    assert metrics["utilization_rate"] == 0.0
    assert metrics["engagement_rate"] == 0.0
    assert metrics["downloads"] == 1


def test_an_unshared_asset_stays_in_the_denominator(store):
    """Dropping it would make every library report a 100% library."""
    assets = seed_library(store)
    share(store, assets["deck"])

    assert portfolio(store)["metrics"]["number_of_assets"] == 4
    assert portfolio(store)["metrics"]["assets_in_scope"] == 4


def test_an_empty_library_reports_no_rate_at_all(store):
    """A rate over nothing is not zero, and 0% would read as a content failure."""
    metrics = portfolio(store)["metrics"]
    assert metrics["utilization_rate"] is None
    assert metrics["engagement_rate"] is None
    assert metrics["number_of_assets"] == 0
    assert metrics["library_built_out"] is False


def test_the_empty_library_is_called_out_on_the_response(store):
    body = portfolio(store)
    assert body["library_built_out"] is False
    assert body["metrics"]["library_size"] == 0


def test_a_built_out_library_is_reported_as_such(store):
    seed_library(store)
    assert portfolio(store)["library_built_out"] is True
    assert portfolio(store)["metrics"]["library_size"] == 4


def test_a_rate_is_rounded_to_two_places(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    for _ in range(2):
        store.create("document", {"title": f"Filler {len(store.list('document'))}"}, actor="dana", source=SOURCE)

    # 1 of 6 is 16.666...
    assert portfolio(store)["metrics"]["utilization_rate"] == 16.67


def test_content_client_views_count_external_views_only(store):
    assets = seed_library(store)
    view(store, assets["deck"])
    view(store, assets["deck"], internal=True)
    share(store, assets["deck"])

    metrics = portfolio(store)["metrics"]

    assert metrics["content_client_views"] == 1
    assert metrics["internal_views"] == 1
    assert metrics["content_shares"] == 1


def test_workspaces_engaged_counts_the_distinct_workspaces(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    share(store, assets["deck"], rooms[0])
    share(store, assets["deck"], rooms[1])
    share(store, assets["deck"], rooms[0])

    assert portfolio(store)["metrics"]["workspaces_engaged"] == 2


# --------------------------------------------------------------------------- #
# The researched filters
# --------------------------------------------------------------------------- #


def test_filtering_by_a_collection_narrows_the_asset_set(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    share(store, assets["pack"])

    metrics = portfolio(store, Filters.build(collection="Security"))["metrics"]

    assert metrics["number_of_assets"] == 1
    assert metrics["content_shares"] == 1
    assert metrics["utilization_rate"] == 100.0


def test_filtering_by_a_collection_nobody_uses_is_an_empty_report_not_an_error(store):
    """A reader exploring a picker should get a number, not a failure."""
    seed_library(store)
    body = portfolio(store, Filters.build(collection="Nonexistent"))
    assert body["metrics"]["number_of_assets"] == 0
    assert body["metrics"]["utilization_rate"] is None


def test_filtering_by_a_tag_reaches_the_asset_it_was_filed_under(store):
    assets = seed_library(store)
    store.update(assets["pack"], {"tags": ["compliance"]}, actor="dana", source=SOURCE)
    share(store, assets["pack"])

    assert portfolio(store, Filters.build(collection="compliance"))["metrics"]["content_shares"] == 1


def test_the_shares_window_bounds_shares_only(store):
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=40))
    view(store, assets["deck"], when=at(days=40))

    metrics = portfolio(store, Filters.build(shared_from=at(days=1)))["metrics"]

    assert metrics["content_shares"] == 0
    # The view is outside the *activity* window, which is unbounded.
    assert metrics["content_client_views"] == 1


def test_the_client_activity_window_bounds_views_and_downloads_only(store):
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=40))
    view(store, assets["deck"], when=at(days=40))

    metrics = portfolio(store, Filters.build(activity_from=at(days=1)))["metrics"]

    assert metrics["content_client_views"] == 0
    assert metrics["content_shares"] == 1


def test_the_two_windows_can_disagree_about_one_asset(store):
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=40))
    view(store, assets["deck"], when=at(days=2))

    metrics = portfolio(store, Filters.build(shared_from=at(days=1)))["metrics"]

    # Shared outside the window, viewed inside it: unutilized but engaged.
    assert metrics["utilization_rate"] == 0.0
    assert metrics["engagement_rate"] == 25.0


def test_a_narrow_window_shrinks_the_assets_in_scope_that_have_activity(store):
    """The denominator is the filtered set, so a window changes it."""
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=1))
    share(store, assets["pack"], when=at(days=60))

    metrics = portfolio(store, Filters.build(shared_from=at(days=1), shared_to=at(days=1)))["metrics"]

    assert metrics["content_shares"] == 1
    assert metrics["utilization_rate"] == 25.0


# --------------------------------------------------------------------------- #
# The researched trend graph
# --------------------------------------------------------------------------- #


def test_the_trend_has_the_five_researched_grains(store):
    seed_library(store)
    body = engagement(store, grain="month")
    assert body["grains"] == list(influence.GRAINS)
    assert body["grain"] == "month"


@pytest.mark.parametrize("grain", ["day", "week", "month", "quarter", "year"])
def test_every_researched_grain_produces_a_series(store, grain):
    assets = seed_library(store)
    share(store, assets["deck"])
    view(store, assets["deck"])

    body = engagement(store, grain=grain)

    assert body["count"] >= 1
    assert body["totals"]["shares"] == 1
    assert body["totals"]["views"] == 1


def test_an_unsupported_grain_is_refused_with_the_five_names(store):
    with pytest.raises(errors.UnknownChoice) as excinfo:
        engagement(store, grain="fortnight")
    assert "quarter" in str(excinfo.value)


def test_the_trend_totals_equal_the_portfolio_counts(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    view(store, assets["deck"])
    download(store, assets["pack"])

    metrics = portfolio(store)["metrics"]
    totals = engagement(store, grain="day")["totals"]

    assert totals["shares"] == metrics["content_shares"]
    assert totals["views"] == metrics["content_client_views"]
    assert totals["downloads"] == metrics["downloads"]


def test_a_quiet_bucket_is_a_zero_point_not_a_gap(store):
    """Omitting it draws a straight line through days that never happened."""
    assets = seed_library(store)
    share(store, assets["deck"], when="2026-09-01T09:00:00+00:00")
    share(store, assets["deck"], when="2026-09-10T09:00:00+00:00")

    points = engagement(store, grain="day")["points"]

    assert len(points) == 10
    assert [point["bucket"] for point in points][:2] == ["2026-09-01", "2026-09-02"]
    assert points[1] == {"bucket": "2026-09-02", "views": 0, "internal_views": 0,
                         "shares": 0, "downloads": 0, "assets": 0}


def test_the_trend_is_ascending(store):
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=10))
    share(store, assets["deck"], when=at(days=1))

    labels = [point["bucket"] for point in engagement(store, grain="day")["points"]]

    assert labels == sorted(labels)


def test_a_bucket_counts_the_distinct_assets_touched_in_it(store):
    assets = seed_library(store)
    view(store, assets["deck"], when=at(days=1, hours=1))
    view(store, assets["deck"], when=at(days=1, hours=2))
    view(store, assets["pack"], when=at(days=1, hours=3))

    point = engagement(store, grain="day")["points"][0]

    assert point["views"] == 3
    assert point["assets"] == 2


def test_the_trend_separates_an_internal_view_from_a_client_view(store):
    assets = seed_library(store)
    view(store, assets["deck"], when=at(days=1))
    view(store, assets["deck"], when=at(days=1), internal=True)

    point = engagement(store, grain="day")["points"][0]

    assert point["views"] == 1
    assert point["internal_views"] == 1


def test_a_bounded_window_draws_its_whole_span(store):
    assets = seed_library(store)
    share(store, assets["deck"], when="2026-09-15T09:00:00+00:00")

    body = engagement(
        store,
        grain="day",
        filters=Filters.build(shared_from="2026-09-01T00:00:00+00:00", shared_to="2026-09-30T00:00:00+00:00"),
    )

    assert body["count"] == 30
    assert body["totals"]["shares"] == 1


def test_a_window_with_no_activity_draws_the_window_and_no_points_of_activity(store):
    seed_library(store)
    body = engagement(
        store,
        grain="day",
        filters=Filters.build(shared_from="2026-08-01T00:00:00+00:00", shared_to="2026-08-05T00:00:00+00:00"),
    )
    assert body["count"] == 5
    assert body["totals"]["shares"] == 0


# --------------------------------------------------------------------------- #
# Top content
# --------------------------------------------------------------------------- #


def test_top_content_is_organised_by_most_viewed_by_default(store):
    assets = seed_library(store)
    view(store, assets["guide"])
    view(store, assets["deck"])
    view(store, assets["deck"])

    body = top_content(store)

    assert body["sort"] == "views"
    assert body["direction"] == "desc"
    assert body["rows"][0]["asset_id"] == assets["deck"]


def test_top_content_reports_every_researched_column(store):
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=10))
    view(store, assets["deck"], when=at(days=1), seconds=120)

    row = top_content(store)["rows"][0]

    for column in ("shares", "views", "downloads", "total_time_seconds",
                   "last_share_at", "last_view_at"):
        assert column in row
    assert row["total_time_seconds"] == 120
    assert row["last_share_at"].startswith("2026-09-16")
    assert row["last_view_at"].startswith("2026-09-25")


def test_every_researched_column_is_sortable_in_both_directions(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    view(store, assets["pack"])
    download(store, assets["guide"])

    for column in influence.TOP_CONTENT_COLUMNS:
        for direction in ("asc", "desc"):
            body = top_content(store, sort=column, direction=direction)
            assert body["count"] == 4
            assert body["sort"] == column


def test_an_unsortable_column_is_refused_with_the_list(store):
    with pytest.raises(errors.UnknownChoice) as excinfo:
        top_content(store, sort="revenue")
    assert "total_time_seconds" in str(excinfo.value)


def test_an_unknown_direction_is_refused(store):
    with pytest.raises(errors.UnknownChoice):
        top_content(store, direction="sideways")


def test_sorting_by_shares_puts_the_most_shared_first(store):
    assets = seed_library(store)
    view(store, assets["guide"])
    view(store, assets["guide"])
    share(store, assets["deck"])
    share(store, assets["deck"])
    share(store, assets["deck"])

    rows = top_content(store, sort="shares")["rows"]

    assert rows[0]["title"] == "Overview Deck"
    assert rows[0]["shares"] == 3
    assert [row["shares"] for row in rows] == sorted(
        (row["shares"] for row in rows), reverse=True
    )


def test_sorting_by_title_is_alphabetical(store):
    seed_library(store)
    titles = [row["title"] for row in top_content(store, sort="title", direction="asc")["rows"]]
    assert titles == sorted(titles)


def test_each_direction_is_monotonic_on_the_sort_column(store):
    """Ascending is *not* descending reversed, and that is deliberate.

    Ties break on asset id in the same order in both directions, which is what
    makes paging a tied table correct. Reversing the tiebreak with the sort would
    make a descending page show a row twice and skip another.
    """
    assets = seed_library(store)
    view(store, assets["guide"])
    view(store, assets["deck"])
    view(store, assets["deck"])

    ascending = [row["views"] for row in top_content(store, direction="asc")["rows"]]
    descending = [row["views"] for row in top_content(store, direction="desc")["rows"]]

    assert ascending == sorted(ascending)
    assert descending == sorted(descending, reverse=True)


def test_a_tie_breaks_on_asset_id_and_not_on_the_row_order_it_happened_in(store):
    assets = seed_library(store)
    # Three views on the deck, one on the guide: the two untouched assets tie on
    # zero, and they must come out in asset-id order rather than in the order the
    # library happened to be created in.
    for _ in range(3):
        view(store, assets["deck"])
    view(store, assets["guide"])

    rows = top_content(store, sort="views")["rows"]

    assert [row["title"] for row in rows[:2]] == ["Overview Deck", "API Guide"]
    tied = sorted(set(assets.values()) - {assets["deck"], assets["guide"]})
    assert [row["asset_id"] for row in rows[2:]] == tied
    assert [row["views"] for row in rows] == [3, 1, 0, 0]


def test_ties_break_on_asset_id_the_same_way_in_both_directions(store):
    """Otherwise a tied table shows a row twice and skips another when paged."""
    first = store.create("document", {"title": "Same"}, actor="dana", source=SOURCE)["id"]
    second = store.create("document", {"title": "Same"}, actor="dana", source=SOURCE)["id"]
    third = store.create("document", {"title": "Same"}, actor="dana", source=SOURCE)["id"]
    for asset in (first, second, third):
        view(store, asset)

    descending = [row["asset_id"] for row in top_content(store, direction="desc", sort="views")["rows"]]
    ascending = [row["asset_id"] for row in top_content(store, direction="asc", sort="views")["rows"]]

    assert descending == ascending == sorted((first, second, third))


def test_paging_a_tied_table_covers_every_row_exactly_once(store):
    ids = [store.create("document", {"title": "Same"}, actor="dana", source=SOURCE)["id"] for _ in range(5)]
    for asset in ids:
        view(store, asset)

    seen: list[str] = []
    for offset in range(0, 5, 2):
        seen.extend(row["asset_id"] for row in top_content(store, sort="views", limit=2, offset=offset)["rows"])

    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen))


def test_paging_reports_the_whole_result_size(store):
    assets = seed_library(store)
    view(store, assets["deck"])
    body = top_content(store, limit=1)
    assert body["count"] == 1
    assert body["total"] == 4
    assert body["offset"] == 0


def test_an_asset_nothing_touched_is_still_in_the_table(store):
    """Otherwise the table cannot show what is not being used."""
    assets = seed_library(store)
    view(store, assets["deck"])

    titles = [row["title"] for row in top_content(store)["rows"]]

    assert "Never Touched" in titles


def test_the_last_view_is_the_latest_one_not_the_first_recorded(store):
    assets = seed_library(store)
    view(store, assets["deck"], when=at(days=5))
    view(store, assets["deck"], when=at(days=1))
    view(store, assets["deck"], when=at(days=9))

    assert top_content(store)["rows"][0]["last_view_at"].startswith("2026-09-25")


def test_an_asset_never_shared_sorts_without_a_timestamp_in_the_way(store):
    assets = seed_library(store)
    share(store, assets["deck"], when=at(days=2))
    view(store, assets["pack"])
    body = top_content(store, sort="last_share_at", direction="asc")
    assert body["rows"][0]["last_share_at"] is None
    assert body["rows"][-1]["last_share_at"] is not None


def test_top_content_reports_the_workspaces_an_asset_reached(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    share(store, assets["deck"], rooms[0])
    share(store, assets["deck"], rooms[1])

    row = next(r for r in top_content(store)["rows"] if r["asset_id"] == assets["deck"])
    assert row["workspaces"] == 2


def test_top_content_reports_utilized_and_engaged_flags(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    rows = {row["asset_id"]: row for row in top_content(store)["rows"]}
    assert rows[assets["deck"]]["utilized"] is True
    assert rows[assets["deck"]]["engaged"] is False
    assert rows[assets["guide"]]["utilized"] is False


# --------------------------------------------------------------------------- #
# One asset, with its evidence
# --------------------------------------------------------------------------- #


def test_an_asset_card_carries_every_event_behind_its_numbers(store):
    assets = seed_library(store)
    view(store, assets["deck"], when=at(days=3), seconds=90, person="buyer@example")
    view(store, assets["deck"], when=at(days=1), seconds=60, person="other@example")

    body = asset_detail(store, assets["deck"])

    assert body["count"] == 2
    assert body["asset"]["views"] == 2
    assert body["asset"]["total_time_seconds"] == 150
    assert [event["person"] for event in body["events"]] == ["buyer@example", "other@example"]


def test_an_asset_card_is_ascending_by_time(store):
    assets = seed_library(store)
    view(store, assets["deck"], when=at(days=5))
    view(store, assets["deck"], when=at(days=1))

    stamps = [event["at"] for event in asset_detail(store, assets["deck"])["events"]]

    assert stamps == sorted(stamps)


def test_an_unknown_asset_card_is_404_worth(store):
    with pytest.raises(UnknownAsset):
        asset_detail(store, "document_missing")


def test_an_asset_filtered_out_says_so_rather_than_saying_not_found(store):
    assets = seed_library(store)
    share(store, assets["pack"])

    with pytest.raises(errors.AssetOutOfScope) as excinfo:
        asset_detail(store, assets["pack"], filters=Filters.build(collection="Enterprise"))

    assert "out of scope" in str(excinfo.value)
    assert "collection" in str(excinfo.value)


def test_a_room_filter_on_an_asset_card_also_says_out_of_scope(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    share(store, assets["deck"], rooms[0])

    with pytest.raises(errors.AssetOutOfScope) as excinfo:
        asset_detail(store, assets["deck"], filters=Filters(room_id=rooms[1]))

    assert "room_id" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Collections endpoint
# --------------------------------------------------------------------------- #


def test_the_collections_endpoint_counts_each_name(store):
    seed_library(store)
    body = collections(store)
    by_name = {entry["collection"]: entry["assets"] for entry in body["collections"]}
    assert by_name == {"Enterprise": 2, "Security": 1, "Reference": 1, "Archive": 1}
    assert body["assets"] == 4
    assert body["unfiled_assets"] == 0


def test_an_asset_filed_under_nothing_is_counted_as_unfiled(store):
    store.create("document", {"title": "Loose"}, actor="dana", source=SOURCE)
    seed_library(store)
    assert collections(store)["unfiled_assets"] == 1


def test_the_collections_endpoint_is_empty_for_an_empty_library(store):
    body = collections(store)
    assert body["collections"] == []
    assert body["assets"] == 0


# --------------------------------------------------------------------------- #
# The vocabulary and the inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_names_every_choice_the_report_accepts():
    body = vocabulary()
    assert body["grains"] == ["day", "week", "month", "quarter", "year"]
    assert body["metrics"] == list(influence.PORTFOLIO_METRICS)
    assert body["sort_columns"] == list(influence.TOP_CONTENT_COLUMNS)
    assert body["default_sort"] == "views"
    assert body["action_spellings"] == ["asset.viewed", "asset.shared", "asset.downloaded"]
    assert set(body["windows"]) == {"activity", "shares"}


def test_the_event_vocabulary_lists_the_accepted_spellings():
    body = summarise_vocabulary()
    viewed = next(entry for entry in body["actions"] if entry["action"] == "viewed")
    assert "asset.viewed" in viewed["spellings"]
    assert "viewed_document" in viewed["spellings"]
    assert body["audiences"] == ["internal", "external"]


def test_every_inference_is_named_and_says_what_would_change_it():
    body = influence.describe_inferences()
    assert body["count"] == len(body["inferences"])
    seen = set()
    for entry in body["inferences"]:
        assert entry["id"] not in seen, f"duplicate inference id {entry['id']}"
        seen.add(entry["id"])
        for key in ("question", "decision", "basis", "changeable_by"):
            assert entry[key], f"{entry['id']} has no {key}"


def test_the_inferences_that_had_to_be_chosen_say_so():
    """A reviewer's first question is which of these the sources actually said."""
    body = influence.describe_inferences()
    unsourced = [entry for entry in body["inferences"] if entry["basis"].startswith("inference")]
    assert unsourced, "every inference claims a source, which is not credible"
    ids = {entry["id"] for entry in unsourced}
    assert "revenue_is_associated_not_caused" in {e["id"] for e in body["inferences"]}
    assert "revenue_is_never_summed_across_currencies" in ids
    assert "two_independent_windows" in {e["id"] for e in body["inferences"]}


# --------------------------------------------------------------------------- #
# Content & Sales Influence
# --------------------------------------------------------------------------- #


def linked(store: RecordStore, *, assets, room, amount=10000, name="Deal", **kwargs):
    link_account(store, {"name": "Acme"}, actor="dana", source=SOURCE)
    return link_deal(
        store,
        {"name": name, "account": "Acme", "room_id": room, "amount": amount, "assets": list(assets), **kwargs},
        actor="dana",
        source=SOURCE,
    )


def test_the_report_is_shown_rather_than_raised_when_the_crm_is_not_linked(store):
    """A screen that errors because a CRM is off is worse than one that says so."""
    seed_library(store)
    body = sales_influence(store)

    assert body["available"] is False
    assert body["totals"]["revenue"] == 0
    codes = {blocker["code"] for blocker in body["blockers"]}
    assert "crm_not_integrated" in codes
    assert "deals_not_linked" in codes


def test_every_blocker_says_what_to_do_about_it(store):
    body = sales_influence(store)
    for blocker in body["blockers"]:
        assert blocker["remedy"], f"{blocker['code']} gives no next step"
        assert blocker["detail"]


def test_strict_mode_turns_a_missing_precondition_into_a_428(store):
    seed_library(store)
    with pytest.raises(errors.CrmNotLinked) as excinfo:
        sales_influence(store, strict=True)
    assert excinfo.value.blockers


def test_a_deal_whose_account_is_not_registered_is_a_named_blocker(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    link_deal(
        store,
        {"name": "Deal", "account": "Nobody", "room_id": rooms[0], "amount": 100, "assets": [assets["deck"]]},
        actor="dana",
        source=SOURCE,
    )

    codes = {blocker["code"] for blocker in preconditions(store)["blockers"]}
    assert codes == {"crm_not_integrated"}


def test_a_deal_connected_to_a_missing_workspace_is_a_named_blocker(store):
    assets = seed_library(store)
    link_account(store, {"name": "Acme"}, actor="dana", source=SOURCE)
    # The link route refuses a missing workspace, so the row is written directly
    # to reach the state a half-completed CRM sync would leave behind.
    store.create(
        influence.DEAL_COLLECTION,
        {"name": "Orphan", "account": "Acme", "amount": 100, "assets": [assets["deck"]]},
        room_id="room_gone",
        actor="dana",
        source=SOURCE,
    )

    codes = {blocker["code"] for blocker in preconditions(store)["blockers"]}
    assert "deals_not_linked" in codes


def test_a_deal_naming_no_asset_is_a_named_blocker(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    link_account(store, {"name": "Acme"}, actor="dana", source=SOURCE)
    link_deal(
        store,
        {"name": "Deal", "account": "Acme", "room_id": rooms[0], "amount": 100},
        actor="dana",
        source=SOURCE,
    )

    codes = {blocker["code"] for blocker in preconditions(store)["blockers"]}
    assert "assets_not_linked" in codes


def test_a_fully_linked_dataset_says_the_report_is_available(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0])

    status = preconditions(store)
    assert status["available"] is True
    assert status["blockers"] == []
    assert status["checked"] == {
        "crm_accounts": 1,
        "deals": 1,
        "deals_linked_to_workspaces": 1,
        "deals_naming_a_known_account": 1,
        "deals_naming_an_asset": 1,
    }


def test_revenue_is_attributed_to_the_asset_the_deal_names(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=42000)

    body = sales_influence(store)

    assert body["available"] is True
    assert [entry["title"] for entry in body["assets"]] == ["Overview Deck"]
    assert body["assets"][0]["revenue"] == 42000
    assert body["totals"]["revenue"] == 42000
    assert body["currency"] == "USD"


def test_each_associated_row_carries_the_evidence_inside_that_workspace(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    share(store, assets["deck"], rooms[0])
    view(store, assets["deck"], rooms[0], seconds=100)
    view(store, assets["deck"], rooms[1], seconds=500)
    linked(store, assets=[assets["deck"]], room=rooms[0])

    evidence = sales_influence(store)["assets"][0]["deals"][0]["evidence"]

    # Only the first workspace's occurrences, because that is the workspace the
    # deal is attached to. The other room's 500 seconds are a different room.
    assert evidence["shares"] == 1
    assert evidence["views"] == 1
    assert evidence["seconds_on_content"] == 100
    assert evidence["viewers"] == 0


def test_evidence_counts_distinct_viewers(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0], person="one@example")
    view(store, assets["deck"], rooms[0], person="one@example")
    view(store, assets["deck"], rooms[0], person="two@example")
    linked(store, assets=[assets["deck"]], room=rooms[0])

    assert sales_influence(store)["assets"][0]["deals"][0]["evidence"]["viewers"] == 2


def test_a_deal_naming_an_asset_with_no_engagement_in_its_workspace_is_unassociated(store):
    """The research says association is a breakdown, not a model."""
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    view(store, assets["deck"], rooms[0])
    linked(store, assets=[assets["pack"]], room=rooms[0], name="Security deal")

    body = sales_influence(store)

    assert body["assets"] == []
    assert body["totals"]["revenue"] == 0
    assert body["totals"]["deal_revenue"] == 10000
    entry = body["unassociated_links"][0]
    assert entry["asset_title"] == "Security Pack"
    assert "does not infer influence" in entry["reason"]
    assert entry["room"] == "Room 0"


def test_a_workspace_with_engagement_but_no_asset_named_is_unattributed(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    linked(store, assets=[], room=rooms[0], name="Pilot")

    body = sales_influence(store)

    entry = body["unattributed_workspaces"][0]
    assert entry["deal"] == "Pilot"
    assert entry["assets_engaged"] == ["Overview Deck"]
    assert "names no asset" in entry["reason"]


def test_a_deal_on_two_assets_is_counted_once_in_the_totals(store):
    """Summing a shared deal per asset would report a pipeline larger than it is."""
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[0])
    linked(store, assets=[assets["deck"], assets["pack"]], room=rooms[0], amount=30000)

    body = sales_influence(store)

    assert body["totals"]["revenue"] == 30000
    assert body["totals"]["deal_revenue"] == 30000
    assert [entry["revenue"] for entry in body["assets"]] == [30000, 30000]
    assert body["totals"]["influenced_assets"] == 2


def test_won_and_open_revenue_are_reported_separately(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=10000, name="Won", stage="Closed Won")
    linked(store, assets=[assets["pack"]], room=rooms[0], amount=5000, name="Open", stage="Negotiation")

    totals = sales_influence(store)["totals"]

    assert totals["won_revenue"] == 10000
    assert totals["open_revenue"] == 5000
    assert totals["revenue"] == 15000


def test_a_deal_is_won_from_the_flag_or_the_stage(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=1, name="Flag", won=True)
    linked(store, assets=[assets["pack"]], room=rooms[0], amount=2, name="Stage", stage="closed won")

    assert sales_influence(store)["totals"]["won_revenue"] == 3


def test_revenue_in_two_currencies_is_never_summed(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=100, name="Dollars", currency="USD")
    linked(store, assets=[assets["pack"]], room=rooms[0], amount=90, name="Euros", currency="EUR")

    body = sales_influence(store)

    assert body["revenue_mixed_currencies"] is True
    assert body["totals"]["revenue"] is None
    assert body["currency"] is None
    assert body["revenue_by_currency"] == {"USD": 100.0, "EUR": 90.0}


def test_the_deal_list_names_the_workspaces_and_accounts_it_read(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=100, crm_id="crm-1")

    deal = sales_influence(store)["deals"][0]

    assert deal["room"] == "Room 0"
    assert deal["account"] == "Acme"
    assert deal["account_known"] is True
    assert deal["crm_id"] == "crm-1"
    assert deal["associated_assets"] == [assets["deck"]]


def test_a_deal_naming_an_asset_the_filters_hide_is_named_in_unknown_assets(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[0])
    linked(store, assets=[assets["deck"], assets["pack"]], room=rooms[0])

    body = sales_influence(store, filters=Filters.build(collection="Enterprise"))

    assert any("out of the active filter" in entry for entry in body["unknown_assets"])


def test_a_malformed_amount_on_a_synced_row_does_not_take_the_report_down(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    view(store, assets["deck"], rooms[0])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=100)
    # Written directly, as a half-validated CRM payload would arrive.
    store.create(
        influence.DEAL_COLLECTION,
        {"name": "Bad", "account": "Acme", "amount": "not a number", "assets": [assets["pack"]]},
        room_id=rooms[0],
        actor="dana",
        source=SOURCE,
    )

    body = sales_influence(store)

    assert body["assets"][0]["revenue"] == 100
    assert body["totals"]["revenue"] == 100


def test_the_sales_report_can_be_scoped_to_one_workspace(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    view(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[1])
    linked(store, assets=[assets["deck"]], room=rooms[0], amount=100, name="First")
    linked(store, assets=[assets["pack"]], room=rooms[1], amount=200, name="Second")

    scoped = sales_influence(store, filters=Filters(room_id=rooms[0]))

    assert scoped["room_id"] == rooms[0]
    assert [deal["deal"] for deal in scoped["deals"]] == ["First"]


# --------------------------------------------------------------------------- #
# The CRM links
# --------------------------------------------------------------------------- #


def test_a_deal_needs_a_name(store):
    rooms = seed_rooms(store, 1)
    with pytest.raises(InfluenceError, match="name"):
        link_deal(store, {"room_id": rooms[0], "amount": 1}, actor=None, source=SOURCE)


def test_a_deal_needs_a_workspace_and_says_why(store):
    with pytest.raises(InfluenceError, match="workspace"):
        link_deal(store, {"name": "Deal", "amount": 1}, actor=None, source=SOURCE)


def test_a_deal_naming_a_missing_workspace_is_404_worth(store):
    with pytest.raises(errors.UnknownRoom):
        link_deal(
            store, {"name": "Deal", "room_id": "room_gone", "amount": 1}, actor=None, source=SOURCE
        )


def test_a_deal_needs_an_amount_rather_than_defaulting_to_zero(store):
    rooms = seed_rooms(store, 1)
    with pytest.raises(InfluenceError, match="amount"):
        link_deal(store, {"name": "Deal", "room_id": rooms[0]}, actor=None, source=SOURCE)


@pytest.mark.parametrize("amount", ["free", -1, True, [1]])
def test_an_amount_that_is_not_a_non_negative_number_is_refused(store, amount):
    rooms = seed_rooms(store, 1)
    with pytest.raises(InfluenceError, match="amount"):
        link_deal(
            store,
            {"name": "Deal", "room_id": rooms[0], "amount": amount},
            actor=None,
            source=SOURCE,
        )


def test_a_link_naming_an_asset_that_is_not_in_the_library_is_404_worth(store):
    rooms = seed_rooms(store, 1)
    with pytest.raises(errors.InfluenceNotLinked) as excinfo:
        link_deal(
            store,
            {"name": "Deal", "room_id": rooms[0], "amount": 1, "assets": ["document_gone"]},
            actor=None,
            source=SOURCE,
        )
    assert "not in the library" in str(excinfo.value)


def test_a_link_cannot_name_a_room_as_though_it_were_an_asset(store):
    rooms = seed_rooms(store, 1)
    with pytest.raises(errors.UnknownAsset):
        link_deal(
            store,
            {"name": "Deal", "room_id": rooms[0], "amount": 1, "assets": [rooms[0]]},
            actor=None,
            source=SOURCE,
        )


def test_a_single_asset_id_may_be_sent_without_a_list(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    result = link_deal(
        store,
        {"name": "Deal", "room_id": rooms[0], "amount": 1, "assets": assets["deck"]},
        actor=None,
        source=SOURCE,
    )
    assert result["deal"]["data"]["assets"] == [assets["deck"]]


def test_a_duplicate_asset_id_on_one_link_is_collapsed(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    result = link_deal(
        store,
        {
            "name": "Deal",
            "room_id": rooms[0],
            "amount": 1,
            "assets": [assets["deck"], assets["deck"], assets["pack"]],
        },
        actor=None,
        source=SOURCE,
    )
    assert result["deal"]["data"]["assets"] == [assets["deck"], assets["pack"]]


def test_assets_must_be_a_list(store):
    rooms = seed_rooms(store, 1)
    with pytest.raises(InfluenceError, match="list"):
        link_deal(
            store,
            {"name": "Deal", "room_id": rooms[0], "amount": 1, "assets": {"id": 1}},
            actor=None,
            source=SOURCE,
        )


def test_a_repeat_crm_id_updates_the_one_deal_rather_than_adding_a_second(store):
    """Two rows for one deal would double its revenue in every breakdown."""
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    first = link_deal(
        store,
        {"name": "Deal", "crm_id": "crm-1", "room_id": rooms[0], "amount": 100, "assets": [assets["deck"]]},
        actor=None,
        source=SOURCE,
    )
    second = link_deal(
        store,
        {"name": "Deal renamed", "crm_id": "crm-1", "room_id": rooms[0], "amount": 250, "assets": [assets["deck"]]},
        actor=None,
        source=SOURCE,
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["deal"]["id"] == first["deal"]["id"]
    assert len(store.list(influence.DEAL_COLLECTION, limit=10)) == 1
    assert second["deal"]["data"]["amount"] == 250


def test_two_deals_with_no_crm_id_are_two_deals(store):
    """Without an identity from the CRM there is nothing to upsert on."""
    rooms = seed_rooms(store, 1)
    link_deal(store, {"name": "A", "room_id": rooms[0], "amount": 1}, actor=None, source=SOURCE)
    link_deal(store, {"name": "B", "room_id": rooms[0], "amount": 1}, actor=None, source=SOURCE)
    assert len(store.list(influence.DEAL_COLLECTION, limit=10)) == 2


def test_a_repeat_crm_id_updates_the_one_account(store):
    first = link_account(store, {"name": "Acme", "crm_id": "a-1"}, actor=None, source=SOURCE)
    second = link_account(
        store, {"name": "Acme Ltd", "crm_id": "a-1", "domain": "acme.example"}, actor=None, source=SOURCE
    )
    assert first["created"] is True
    assert second["created"] is False
    assert second["account"]["data"]["domain"] == "acme.example"
    assert len(store.list(influence.ACCOUNT_COLLECTION, limit=10)) == 1


def test_an_account_needs_a_name(store):
    with pytest.raises(InfluenceError, match="name"):
        link_account(store, {"crm_id": "a-1"}, actor=None, source=SOURCE)


def test_the_currency_is_upper_cased(store):
    rooms = seed_rooms(store, 1)
    result = link_deal(
        store, {"name": "D", "room_id": rooms[0], "amount": 1, "currency": "gbp"}, actor=None, source=SOURCE
    )
    assert result["deal"]["data"]["currency"] == "GBP"


def test_the_default_currency_can_be_set_for_the_deployment(store, monkeypatch):
    monkeypatch.setenv("DSR_CRM_CURRENCY", "nzd")
    rooms = seed_rooms(store, 1)
    result = link_deal(store, {"name": "D", "room_id": rooms[0], "amount": 1}, actor=None, source=SOURCE)
    assert result["deal"]["data"]["currency"] == "NZD"


def test_the_links_endpoint_returns_what_the_join_read(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    linked(store, assets=[assets["deck"]], room=rooms[0])

    body = summarise_links(store)

    assert body["counts"] == {"accounts": 1, "deals": 1}
    assert body["deals"][0]["assets"] == [assets["deck"]]
    assert body["collections"]["deals"] == "crm_deal"


def test_a_link_carries_arbitrary_extra_fields(store):
    """Schema flexibility: a team adds its own field the same day."""
    rooms = seed_rooms(store, 1)
    result = link_deal(
        store,
        {"name": "D", "room_id": rooms[0], "amount": 1, "owner": "dana", "hubspot_id": "hs-9"},
        actor=None,
        source=SOURCE,
    )
    assert result["deal"]["data"]["hubspot_id"] == "hs-9"


# --------------------------------------------------------------------------- #
# Workspace scoping
# --------------------------------------------------------------------------- #


def test_a_workspace_report_only_counts_that_workspace(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    share(store, assets["deck"], rooms[0])
    share(store, assets["pack"], rooms[1])

    report = compile_report(store, Filters(room_id=rooms[0]))

    assert [row.title for row in report.rows] == ["Overview Deck"]
    assert report.portfolio()["content_shares"] == 1


def test_a_workspace_report_divides_by_the_content_that_workspace_used(store):
    """Not by the whole library, which would read as 0% utilization."""
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    share(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[0])

    metrics = compile_report(store, Filters(room_id=rooms[0])).portfolio()

    assert metrics["number_of_assets"] == 2
    assert metrics["utilization_rate"] == 50.0
    assert metrics["library_size"] == 4


def test_a_workspace_report_excludes_a_share_made_straight_from_the_library(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 1)
    share(store, assets["deck"], rooms[0])
    share(store, assets["pack"], None)

    assert compile_report(store, Filters(room_id=rooms[0])).portfolio()["content_shares"] == 1
    assert portfolio(store)["metrics"]["content_shares"] == 2


def test_a_workspace_with_no_content_is_an_empty_scope_not_an_unbuilt_library(store):
    seed_library(store)
    rooms = seed_rooms(store, 1)

    metrics = compile_report(store, Filters(room_id=rooms[0])).portfolio()

    assert metrics["number_of_assets"] == 0
    assert metrics["utilization_rate"] is None
    assert metrics["library_built_out"] is True
    assert metrics["library_size"] == 4


def test_an_unknown_workspace_is_404_worth(store):
    seed_library(store)
    with pytest.raises(errors.UnknownRoom):
        compile_report(store, Filters(room_id="room_gone"))


def test_an_unknown_workspace_is_404_even_with_an_empty_library(store):
    with pytest.raises(errors.UnknownRoom):
        compile_report(store, Filters(room_id="room_gone"))


def test_a_room_that_is_not_a_room_is_not_a_room(store):
    assets = seed_library(store)
    with pytest.raises(errors.UnknownRoom):
        compile_report(store, Filters(room_id=assets["deck"]))


# --------------------------------------------------------------------------- #
# Every surface is one compilation
# --------------------------------------------------------------------------- #


def test_the_three_surfaces_agree_about_the_same_filter(store):
    assets = seed_library(store)
    share(store, assets["deck"])
    share(store, assets["pack"])
    view(store, assets["guide"])
    filters = Filters.build(collection="Enterprise")

    metrics = portfolio(store, filters)["metrics"]
    totals = engagement(store, grain="day", filters=filters)["totals"]
    rows = top_content(store, filters=filters)["rows"]

    assert totals["shares"] == metrics["content_shares"]
    assert totals["views"] == metrics["content_client_views"]
    assert sum(row["shares"] for row in rows) == metrics["content_shares"]
    assert sum(row["views"] for row in rows) == metrics["content_client_views"]
    assert sum(row["total_time_seconds"] for row in rows) == metrics["total_time_seconds"]


def test_the_three_surfaces_agree_about_a_workspace_scope(store):
    assets = seed_library(store)
    rooms = seed_rooms(store, 2)
    share(store, assets["deck"], rooms[0])
    view(store, assets["pack"], rooms[1])
    filters = Filters(room_id=rooms[0])

    metrics = portfolio(store, filters)["metrics"]
    totals = engagement(store, grain="day", filters=filters)["totals"]

    assert metrics["content_shares"] == totals["shares"] == 1
    assert metrics["content_client_views"] == totals["views"] == 0


# --------------------------------------------------------------------------- #
# HTTP: the surface a client sees
# --------------------------------------------------------------------------- #


def http_library(client) -> dict[str, str]:
    created = client.post(
        "/api/records/document/bulk",
        json=[
            {"title": "Overview Deck", "kind": "pptx", "collections": ["Enterprise"]},
            {"title": "Security Pack", "kind": "pdf", "collections": ["Security"]},
            {"title": "Never Touched", "kind": "pdf", "collections": ["Archive"]},
        ],
    ).json()
    return {record["data"]["title"]: record["id"] for record in created["records"]}


def http_room(client) -> str:
    return client.post("/api/records/room", json={"name": "Northwind"}).json()["id"]


def test_the_portfolio_endpoint_serves_the_researched_metrics(client):
    http_library(client)
    body = client.get(f"{PREFIX}/portfolio").json()

    assert body["metrics"]["number_of_assets"] == 3
    assert body["metric_names"] == list(influence.PORTFOLIO_METRICS)
    assert body["filters"]["collection"] is None


def test_the_vocabulary_endpoint_serves_both_halves(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["report"]["grains"] == list(influence.GRAINS)
    assert body["events"]["actions"][0]["action"] == "viewed"


def test_the_inferences_endpoint_needs_no_store_and_writes_nothing(client):
    before = client.get("/api/audit").json()["count"]
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 10
    assert client.get("/api/audit").json()["count"] == before


def test_the_engagement_endpoint_serves_a_grain(client):
    body = client.get(f"{PREFIX}/engagement", params={"grain": "quarter"}).json()
    assert body["grain"] == "quarter"
    assert body["grains"] == list(influence.GRAINS)


def test_an_unsupported_grain_is_a_400_with_the_list(client):
    response = client.get(f"{PREFIX}/engagement", params={"grain": "fortnight"})
    assert response.status_code == 400
    assert "quarter" in response.json()["detail"]


def test_an_unparseable_window_bound_is_a_400_naming_the_field(client):
    response = client.get(f"{PREFIX}/portfolio", params={"activity_from": "last tuesday"})
    body = response.json()
    assert response.status_code == 400
    assert body["error"] == "invalid_timestamp"
    assert body["field"] == "client activity from"
    assert body["value"] == "last tuesday"


def test_a_backwards_window_is_a_400(client):
    response = client.get(
        f"{PREFIX}/portfolio", params={"shared_from": "2026-02-01", "shared_to": "2026-01-01"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_window"


def test_the_top_content_endpoint_serves_a_sortable_table(client):
    http_library(client)
    body = client.get(f"{PREFIX}/top-content", params={"sort": "title", "direction": "asc"}).json()
    assert body["sort"] == "title"
    assert body["sort_columns"] == list(influence.TOP_CONTENT_COLUMNS)
    assert [row["title"] for row in body["rows"]] == [
        "Never Touched",
        "Overview Deck",
        "Security Pack",
    ]


def test_an_unsortable_column_is_a_400(client):
    response = client.get(f"{PREFIX}/top-content", params={"sort": "revenue"})
    assert response.status_code == 400
    assert "sort column" in response.json()["detail"]


def test_the_collections_endpoint_serves_the_filter_picker(client):
    http_library(client)
    body = client.get(f"{PREFIX}/collections").json()
    assert {entry["collection"] for entry in body["collections"]} == {
        "Enterprise",
        "Security",
        "Archive",
    }


def test_recording_an_event_is_201_and_audited(client):
    assets = http_library(client)
    response = client.post(
        f"{PREFIX}/events",
        json={"action": "asset.shared", "asset_id": assets["Overview Deck"], "event_id": "hook-1"},
        params={"actor": "dana"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["action"] == "shared"
    assert body["duplicate"] is False
    entry = client.get("/api/audit", params={"collection": EVENT_COLLECTION}).json()["entries"][0]
    assert entry["actor"] == "dana"
    assert entry["source"] == f"POST {PREFIX}/events"


def test_recording_an_event_into_a_workspace_records_it_on_the_envelope(client):
    assets = http_library(client)
    room = http_room(client)
    response = client.post(
        f"{PREFIX}/events",
        json={"action": "asset.viewed", "asset_id": assets["Overview Deck"]},
        params={"room_id": room},
    )
    assert response.json()["room_id"] == room
    scoped = client.get(f"{PREFIX}/rooms/{room}/influence").json()
    assert scoped["metrics"]["content_client_views"] == 1


def test_a_repeated_event_id_is_201_and_writes_nothing(client):
    assets = http_library(client)
    payload = {"action": "asset.shared", "asset_id": assets["Overview Deck"], "event_id": "hook-1"}
    client.post(f"{PREFIX}/events", json=payload)
    before = client.get("/api/audit").json()["count"]

    response = client.post(f"{PREFIX}/events", json=payload)

    assert response.json()["duplicate"] is True
    assert client.get("/api/audit").json()["count"] == before


def test_an_unknown_action_is_a_400(client):
    assets = http_library(client)
    response = client.post(
        f"{PREFIX}/events", json={"action": "liked", "asset_id": assets["Overview Deck"]}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_influence_request"


def test_recording_an_event_for_a_missing_asset_is_a_404(client):
    response = client.post(f"{PREFIX}/events", json={"action": "asset.viewed", "asset_id": "nope"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_asset"


def test_the_backfill_endpoint_projects_and_reports(client):
    room = http_room(client)
    client.post("/api/records/document", json={"title": "Overview Deck"})
    client.post(
        "/api/records/activity",
        json={"action": "viewed", "target": "Overview Deck", "occurred_at": "2026-09-20T10:00:00+00:00"},
        params={"room_id": room},
    )

    first = client.post(f"{PREFIX}/events/ingest-activity").json()
    second = client.post(f"{PREFIX}/events/ingest-activity").json()

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["already_recorded"] == 1


def test_the_asset_endpoint_serves_one_asset_with_its_events(client):
    assets = http_library(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.viewed", "asset_id": assets["Overview Deck"], "seconds": 90},
    )
    body = client.get(f"{PREFIX}/assets/{assets['Overview Deck']}").json()
    assert body["asset"]["title"] == "Overview Deck"
    assert body["asset"]["total_time_seconds"] == 90
    assert body["count"] == 1


def test_a_missing_asset_is_a_404(client):
    response = client.get(f"{PREFIX}/assets/document_gone")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_asset"


def test_a_filtered_out_asset_says_out_of_scope(client):
    assets = http_library(client)
    response = client.get(
        f"{PREFIX}/assets/{assets['Security Pack']}", params={"collection": "Enterprise"}
    )
    assert response.status_code == 404
    assert response.json()["error"] == "asset_out_of_scope"


def test_an_unknown_workspace_is_a_404(client):
    response = client.get(f"{PREFIX}/rooms/room_gone/influence")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_room"


def test_the_workspace_report_is_scoped_and_says_so(client):
    assets = http_library(client)
    room = http_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.shared", "asset_id": assets["Overview Deck"]},
        params={"room_id": room},
    )

    body = client.get(f"{PREFIX}/rooms/{room}/influence").json()

    assert body["room_id"] == room
    assert body["metrics"]["number_of_assets"] == 1
    assert body["metrics"]["content_shares"] == 1


def test_a_workspace_report_keeps_the_other_filters(client):
    assets = http_library(client)
    room = http_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.shared", "asset_id": assets["Overview Deck"]},
        params={"room_id": room},
    )
    body = client.get(
        f"{PREFIX}/rooms/{room}/influence", params={"collection": "Security"}
    ).json()
    assert body["filters"]["collection"] == "Security"
    assert body["metrics"]["number_of_assets"] == 0


def test_the_sales_endpoint_is_200_when_the_crm_is_not_linked(client):
    http_library(client)
    response = client.get(f"{PREFIX}/sales-influence")
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_the_sales_endpoint_is_428_in_strict_mode(client):
    http_library(client)
    response = client.get(f"{PREFIX}/sales-influence", params={"strict": "true"})
    assert response.status_code == 428
    body = response.json()
    assert body["error"] == "crm_not_linked"
    assert body["blockers"]


def test_the_preconditions_endpoint_serves_the_blockers_on_their_own(client):
    body = client.get(f"{PREFIX}/sales-influence/preconditions").json()
    assert body["available"] is False
    assert body["checked"]["crm_accounts"] == 0


def test_the_links_endpoints_write_and_turn_the_report_on(client):
    assets = http_library(client)
    room = http_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.viewed", "asset_id": assets["Overview Deck"]},
        params={"room_id": room},
    )

    account = client.post(f"{PREFIX}/links/accounts", json={"name": "Acme", "crm_id": "a-1"})
    assert account.status_code == 200
    assert account.json()["created"] is True

    deal = client.post(
        f"{PREFIX}/links/deals",
        json={
            "name": "Acme Platform",
            "crm_id": "d-1",
            "account": "Acme",
            "room_id": room,
            "amount": 25000,
            "assets": [assets["Overview Deck"]],
        },
        params={"actor": "dana"},
    )
    assert deal.status_code == 200
    assert deal.json()["created"] is True

    body = client.get(f"{PREFIX}/sales-influence").json()
    assert body["available"] is True
    assert body["assets"][0]["revenue"] == 25000
    assert body["assets"][0]["deals"][0]["evidence"]["views"] == 1


def test_re_registering_an_account_updates_it_over_http(client):
    first = client.post(f"{PREFIX}/links/accounts", json={"name": "Acme", "crm_id": "a-1"}).json()
    second = client.post(
        f"{PREFIX}/links/accounts", json={"name": "Acme Ltd", "crm_id": "a-1"}
    ).json()
    assert first["created"] is True
    assert second["created"] is False
    assert second["account"]["id"] == first["account"]["id"]


def test_a_deal_without_a_workspace_is_a_400_over_http(client):
    response = client.post(f"{PREFIX}/links/deals", json={"name": "D", "amount": 1})
    assert response.status_code == 400
    assert "workspace" in response.json()["detail"]


def test_a_deal_naming_a_missing_asset_is_a_404_over_http(client):
    room = http_room(client)
    response = client.post(
        f"{PREFIX}/links/deals",
        json={"name": "D", "room_id": room, "amount": 1, "assets": ["document_gone"]},
    )
    assert response.status_code == 404
    assert "not in the library" in response.json()["detail"]


def test_the_links_endpoint_serves_the_join_inputs(client):
    room = http_room(client)
    client.post(f"{PREFIX}/links/accounts", json={"name": "Acme"})
    client.post(
        f"{PREFIX}/links/deals", json={"name": "D", "account": "Acme", "room_id": room, "amount": 5}
    )
    body = client.get(f"{PREFIX}/links").json()
    assert body["counts"] == {"accounts": 1, "deals": 1}


# --------------------------------------------------------------------------- #
# The audit rules
# --------------------------------------------------------------------------- #


def test_every_source_this_feature_records_names_a_route_the_host_mounted(client):
    """The hard rule, asserted mechanically rather than by reading the diff.

    Every write route is exercised, the audit log is read back, and each source
    is matched against the routes the plugin host actually mounted for this
    feature. A hard-coded path that the app has stopped serving fails here.
    """
    assets = http_library(client)
    room = http_room(client)
    deck = assets["Overview Deck"]

    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.shared", "asset_id": deck, "event_id": "hook-1"},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.viewed", "asset_id": deck},
        params={"room_id": room},
    )
    client.post(f"{PREFIX}/events/ingest-activity")
    client.post(f"{PREFIX}/links/accounts", json={"name": "Acme", "crm_id": "a-1"}, params={"actor": "dana"})
    client.post(
        f"{PREFIX}/links/deals",
        json={"name": "D", "crm_id": "d-1", "account": "Acme", "room_id": room,
              "amount": 100, "assets": [deck]},
        params={"actor": "dana"},
    )

    record = REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    mounted = {
        (method, route["path"])
        for route in record.routes
        for method in route["methods"]
    }

    entries = client.get("/api/audit", params={"limit": 500}).json()["entries"]
    # The source is "<METHOD> <path>", so the prefix is *inside* the string, not
    # at the start of it.
    mine = [entry for entry in entries if PREFIX in (entry["source"] or "")]
    assert mine, "no write recorded a source naming this feature's prefix"

    pattern = re.compile(r"^(?P<method>[A-Z]+) (?P<path>/api/wf-019\S*)$")
    for entry in mine:
        match = pattern.match(entry["source"])
        assert match, f"source {entry['source']!r} is not '<METHOD> <path>'"
        method, path = match.group("method"), match.group("path")
        # A path with a value in it matches the mounted route with a parameter.
        candidates = {
            (mounted_method, mounted_path)
            for mounted_method, mounted_path in mounted
            if _path_matches(mounted_path, path)
        }
        assert (method, path) in candidates or method in {
            candidate[0] for candidate in candidates
        }, f"source {entry['source']!r} names a route the host does not mount: {sorted(candidates)}"


def _path_matches(mounted: str, actual: str) -> bool:
    """Whether a recorded path is an instance of a mounted route path."""
    mounted_parts = mounted.split("/")
    actual_parts = actual.split("/")
    if len(mounted_parts) != len(actual_parts):
        return False
    for expected, given in zip(mounted_parts, actual_parts):
        if expected.startswith("{") and expected.endswith("}"):
            continue
        if expected != given:
            return False
    return True


def test_the_four_write_routes_name_themselves_exactly(client):
    """The exact strings, so a rename of a path is a failing test not a surprise."""
    assets = http_library(client)
    room = http_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.shared", "asset_id": assets["Overview Deck"]},
    )
    client.post(f"{PREFIX}/events/ingest-activity")
    client.post(f"{PREFIX}/links/accounts", json={"name": "Acme"})
    client.post(
        f"{PREFIX}/links/deals",
        json={"name": "D", "room_id": room, "amount": 1, "assets": [assets["Overview Deck"]]},
    )

    sources = {
        entry["collection"]: entry["source"]
        for entry in client.get("/api/audit", params={"limit": 500}).json()["entries"]
        if PREFIX in (entry["source"] or "")
    }
    assert sources[EVENT_COLLECTION] == f"POST {PREFIX}/events"
    assert sources["crm_account"] == f"POST {PREFIX}/links/accounts"
    assert sources["crm_deal"] == f"POST {PREFIX}/links/deals"


def test_the_backfill_audit_row_names_its_own_route(client):
    http_library(client)
    room = http_room(client)
    client.post(
        "/api/records/activity",
        json={"action": "viewed", "target": "Overview Deck", "occurred_at": "2026-09-20T10:00:00+00:00"},
        params={"room_id": room},
    )
    client.post(f"{PREFIX}/events/ingest-activity")
    sources = [
        entry["source"]
        for entry in client.get("/api/audit", params={"collection": EVENT_COLLECTION}).json()["entries"]
    ]
    assert sources == [f"POST {PREFIX}/events/ingest-activity"]


def test_a_workspace_occurrence_records_the_workspace_on_its_audit_row(client):
    assets = http_library(client)
    room = http_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.viewed", "asset_id": assets["Overview Deck"]},
        params={"room_id": room},
    )
    entry = client.get("/api/audit", params={"collection": EVENT_COLLECTION}).json()["entries"][0]
    assert entry["room_id"] == room


def test_reading_the_report_never_writes_to_the_audit_log(client):
    assets = http_library(client)
    room = http_room(client)
    client.post(
        f"{PREFIX}/events",
        json={"action": "asset.viewed", "asset_id": assets["Overview Deck"]},
        params={"room_id": room},
    )
    before = client.get("/api/audit").json()["count"]

    client.get(f"{PREFIX}/portfolio")
    client.get(f"{PREFIX}/engagement")
    client.get(f"{PREFIX}/top-content")
    client.get(f"{PREFIX}/sales-influence")
    client.get(f"{PREFIX}/sales-influence/preconditions")
    client.get(f"{PREFIX}/collections")
    client.get(f"{PREFIX}/links")
    client.get(f"{PREFIX}/vocabulary")
    client.get(f"{PREFIX}/inferences")
    client.get(f"{PREFIX}/rooms/{room}/influence")
    client.get(f"{PREFIX}/assets/{assets['Overview Deck']}")

    assert client.get("/api/audit").json()["count"] == before


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded_db():
    from datetime import datetime as _dt

    db = AuditedDatabase(":memory:")
    try:
        yield db
    finally:
        db.close()


def seed_context(db: AuditedDatabase, rooms: int = 4):
    import random

    now = NOW
    room_ids = []
    for index in range(rooms):
        room_ids.append(
            (
                db.create(
                    "room",
                    {"name": f"Demo {index}", "account": f"Acme {index}"},
                    actor="dana",
                    source="seed",
                )["id"],
                f"Acme {index}",
            )
        )
    return {"room_ids": room_ids, "now": now, "rng": random.Random("wf019")}


def test_the_seed_describes_what_it_added(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    summary = seed(seeded_db, seed_context(seeded_db))
    assert "5 library assets" in summary
    assert "content events" in summary
    assert "deals" in summary


def test_the_seed_produces_a_report_with_rooms_below_full_utilization(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    seed(seeded_db, seed_context(seeded_db))
    store = RecordStore(seeded_db)

    metrics = portfolio(store)["metrics"]

    assert metrics["number_of_assets"] == 5
    assert metrics["library_built_out"] is True
    # The seeded Migration Guide is never shared and never viewed, which is the
    # whole point of seeding it: a library where everything is used cannot show a
    # utilization rate.
    assert 0 < metrics["utilization_rate"] < 100
    assert 0 < metrics["engagement_rate"] < 100
    assert metrics["content_shares"] > 0
    assert metrics["content_client_views"] > 0


def test_the_seed_attributes_revenue_to_two_assets_with_evidence(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    seed(seeded_db, seed_context(seeded_db))
    store = RecordStore(seeded_db)

    body = sales_influence(store)

    assert body["available"] is True
    assert body["totals"]["revenue"] > 0
    assert body["totals"]["won_revenue"] > 0
    assert body["totals"]["open_revenue"] > 0
    assert len(body["assets"]) == 3
    for entry in body["assets"]:
        assert entry["deals"][0]["evidence"]["shares"] + entry["deals"][0]["evidence"]["views"] > 0


def test_the_seed_produces_the_deal_that_cannot_be_associated(seeded_db):
    """The researched precondition is a human linking task, and the demo shows it."""
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    seed(seeded_db, seed_context(seeded_db))
    store = RecordStore(seeded_db)

    body = sales_influence(store)

    assert len(body["unassociated_links"]) == 1
    assert body["unassociated_links"][0]["asset_title"] == "Legacy Migration Guide"
    assert "does not infer influence" in body["unassociated_links"][0]["reason"]


def test_the_seed_produces_the_workspace_with_no_asset_named(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    seed(seeded_db, seed_context(seeded_db))
    store = RecordStore(seeded_db)

    body = sales_influence(store)

    assert len(body["unattributed_workspaces"]) == 1
    assert body["unattributed_workspaces"][0]["assets_engaged"]


def test_the_seed_produces_a_trend_with_more_than_one_bucket(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    seed(seeded_db, seed_context(seeded_db))
    store = RecordStore(seeded_db)

    assert engagement(store, grain="month")["count"] >= 2
    assert top_content(store)["rows"]


def test_the_seed_names_its_own_source_and_not_a_route(seeded_db):
    """No route served these writes, so the audit row must not claim one."""
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    seed(seeded_db, seed_context(seeded_db))
    sources = {
        entry["source"] for entry in seeded_db.audit(limit=500) if entry["source"]
    }
    assert sources == {"seed"}
    assert not any(source.startswith(PREFIX) for source in sources)


def test_the_seed_copes_with_no_rooms_to_attach_to(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    summary = seed(seeded_db, {"room_ids": [], "now": NOW, "rng": None})
    assert "5 library assets" in summary
    assert "no rooms to scope them to" in summary


def test_the_seed_survives_a_context_with_no_clock(seeded_db):
    from dsr.features.wf019_rank_content_influence_and_associate_r import seed

    summary = seed(seeded_db, {"room_ids": [], "now": None, "rng": None})
    assert "no clock" in summary


def test_the_seeder_runs_wf019_without_failing(tmp_path, monkeypatch):
    """The whole seeder, as `backend/seed.py` runs it, on a fresh database."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    python = root.parent / ".venv" / "Scripts" / "python.exe"
    interpreter = python if python.exists() else Path(sys.executable)
    env = dict(os.environ)
    env["DSR_DB_PATH"] = str(tmp_path / "demo.db")
    env["DSR_AUDIT_DIR"] = str(tmp_path / "audit")

    result = subprocess.run(
        [str(interpreter), str(root / "backend" / "seed.py")],
        capture_output=True, text=True, env=env, timeout=300,
    )

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "wf019" in result.stdout
    assert "SKIPPED" not in result.stdout
    assert "seed FAILED" not in result.stdout
