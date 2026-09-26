"""Tests for WF-006: buyer engagement review and follow-up prioritisation.

Two levels, deliberately:

* pure-function tests over the aggregation helpers, which is where the design
  inferences live (trend boundaries, session grouping, view-time formatting);
* HTTP tests over ``/api/wf-006/*``, which pin the researched behaviour a
  client depends on: the connection gate, "All Rooms" default scope, the
  widget vocabulary, and the guarantee that every write is audited.

All timestamps are injected through ``as_of`` so nothing here depends on the
wall clock, and no test reaches around the audited store to set up its data.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr import analytics
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore

# 2026-09-26 is a Saturday, which the week-grain assertions depend on.
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
AS_OF = NOW.isoformat(timespec="seconds")
TOKEN = {"connection": {"token": "ldp-environment-token", "environment": "prod"}}


def at(**kwargs) -> str:
    """ISO timestamp ``kwargs`` before NOW."""
    return (NOW - timedelta(**kwargs)).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_format_duration_matches_the_shape_the_research_quotes():
    # "View Time Viewed (e.g., 5h 32 min)"
    assert analytics.format_duration(5 * 3600 + 32 * 60) == "5h 32 min"
    assert analytics.format_duration(45 * 60) == "45 min"
    assert analytics.format_duration(12) == "12s"
    assert analytics.format_duration(None) == "0s"
    assert analytics.format_duration(-5) == "0s"


def test_parse_dt_handles_the_shapes_a_team_actually_sends():
    assert analytics._parse_dt("2026-09-26T12:00:00Z") == NOW
    assert analytics._parse_dt("2026-09-26T12:00:00+00:00") == NOW
    # A date-only deadline is midnight UTC, which is what a date field means.
    assert analytics._parse_dt("2026-09-26") == datetime(2026, 9, 26, tzinfo=timezone.utc)
    assert analytics._parse_dt("not a date") is None
    assert analytics._parse_dt(None) is None


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        ({"actions": 0, "days_since_activity": None}, "cold"),
        ({"actions": 60, "days_since_activity": 0}, "hot"),
        ({"actions": 60, "days_since_activity": 9}, "warm"),
        ({"actions": 3, "days_since_activity": 40}, "cold"),
        ({"actions": 20, "days_since_activity": 40}, "warm"),
    ],
)
def test_classify_trend_covers_all_three_states(stats, expected):
    result = analytics.classify_trend(stats, analytics.DEFAULT_CONFIG["thresholds"])
    assert result["classification"] == expected
    assert result["reasons"]


def test_classify_trend_never_returns_an_unknown_state():
    for actions in (0, 1, 5, 39, 40, 500):
        result = analytics.classify_trend(
            {"actions": actions, "days_since_activity": 0},
            analytics.DEFAULT_CONFIG["thresholds"],
        )
        assert result["classification"] in analytics.TREND_STATES


def test_count_visits_groups_by_gap_window():
    events = [
        {"room_id": "r1", "person": "a", "at": NOW - timedelta(minutes=10)},
        {"room_id": "r1", "person": "a", "at": NOW - timedelta(minutes=5)},  # same visit
        {"room_id": "r1", "person": "a", "at": NOW},  # same visit
        {"room_id": "r1", "person": "b", "at": NOW},  # different visitor
        {"room_id": "r1", "person": "a", "at": NOW - timedelta(days=1)},  # new visit
    ]
    assert len(analytics.count_visits(events, 30)) == 3


def test_count_visits_separates_rooms_for_the_same_visitor():
    events = [
        {"room_id": "r1", "person": "a", "at": NOW - timedelta(minutes=2)},
        {"room_id": "r2", "person": "a", "at": NOW},
    ]
    assert len(analytics.count_visits(events, 30)) == 2


def test_deep_merge_keeps_siblings_when_a_nested_key_is_patched():
    merged = analytics._deep_merge(
        {"thresholds": {"cold_floor": 3, "warm_actions": 15}, "other": 1},
        {"thresholds": {"cold_floor": 9}},
    )
    assert merged["thresholds"] == {"cold_floor": 9, "warm_actions": 15}
    assert merged["other"] == 1


# --------------------------------------------------------------------------- #
# Store-level fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    db = AuditedDatabase(":memory:")
    try:
        yield RecordStore(db)
    finally:
        db.close()


def connect(store, **overrides):
    return analytics.save_config(store, {**TOKEN, **overrides}, actor="dana")


def make_room(store, name, **fields):
    return store.create("room", {"name": name, **fields}, actor="dana")


def make_event(store, room_id, **fields):
    return analytics.record_event(store, fields, room_id=room_id, actor="system")


@pytest.fixture()
def wired(store):
    """A connected store: one engaged room with a deadline, one quiet room, one closed.

    The engaged room's seven actions fall into two 30-minute sessions, so the
    fixture distinguishes visits from actions rather than making them equal.
    """
    connect(store, thresholds={"alert_after_days": 0})
    hot = make_room(store, "Northwind", stage="evaluation", expires_at="2026-10-01")
    for minute in (45, 40, 35):
        make_event(
            store,
            hot["id"],
            person="buyer0@northwind.example",
            action="viewed",
            target="Pricing One-Pager",
            seconds_on_page=120,
            occurred_at=at(minutes=minute),
        )
    make_event(
        store,
        hot["id"],
        person="buyer0@northwind.example",
        action="downloaded",
        target="Enterprise Overview Deck",
        seconds_on_page=30,
        occurred_at=at(minutes=30),
    )
    for minute in (45, 40, 35):
        make_event(
            store,
            hot["id"],
            person="buyer1@northwind.example",
            action="viewed",
            target="Pricing One-Pager",
            seconds_on_page=120,
            occurred_at=at(minutes=minute),
        )
    quiet = make_room(store, "Contoso", stage="discovery")
    closed = make_room(store, "Adventure Works", stage="closed", won_at="2026-08-30")
    return {"store": store, "hot": hot, "quiet": quiet, "closed": closed}


# --------------------------------------------------------------------------- #
# Connection gate
# --------------------------------------------------------------------------- #


def test_overview_refuses_to_report_metrics_until_a_token_exists(store):
    make_room(store, "Northwind")
    with pytest.raises(PermissionError):
        analytics.overview(store, as_of=AS_OF)


def test_a_token_is_all_that_connects_the_instance(store):
    # Sourced: "if the token field already holds a value, your instance is connected."
    assert analytics.is_connected(analytics.DEFAULT_CONFIG) is False
    connect(store)
    assert analytics.is_connected(analytics.load_config(store)) is True


def test_room_engagement_is_gated_too(store):
    room = make_room(store, "Northwind")
    with pytest.raises(PermissionError):
        analytics.room_engagement(store, room["id"], as_of=AS_OF)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_config_defaults_apply_when_no_record_exists(store):
    assert analytics.load_config(store)["thresholds"]["cold_floor"] == 3


def test_config_patch_merges_rather_than_replaces(store):
    connect(store)
    analytics.save_config(store, {"thresholds": {"cold_floor": 7}}, actor="dana")
    config = analytics.load_config(store)
    assert config["thresholds"]["cold_floor"] == 7
    assert config["thresholds"]["warm_actions"] == 15  # sibling survived


def test_config_overrides_change_the_computed_outcome(wired):
    store = wired["store"]
    before = analytics.room_engagement(store, wired["hot"]["id"], as_of=AS_OF)["room_trend"]
    assert before["classification"] == "warm"  # 7 actions is short of the hot bar of 40

    analytics.save_config(store, {"thresholds": {"hot_actions": 3}}, actor="dana")
    after = analytics.room_engagement(store, wired["hot"]["id"], as_of=AS_OF)["room_trend"]
    assert after["classification"] == "hot"


def test_field_synonyms_are_configurable(wired):
    store = wired["store"]
    analytics.save_config(
        store,
        {"event": {"person_fields": ["who"], "action_fields": ["verb"], "seconds_fields": ["ms"]}},
        actor="dana",
    )
    make_event(
        store, wired["hot"]["id"], who="new@example", verb="clicked", ms=45, occurred_at=AS_OF
    )
    card = analytics.room_engagement(store, wired["hot"]["id"], as_of=AS_OF)
    assert "new@example" in [row["person"] for row in card["most_active_visitors"]]


# --------------------------------------------------------------------------- #
# Overview: active deals, alerts, prioritisation
# --------------------------------------------------------------------------- #


def test_total_active_deals_excludes_terminal_stages(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    assert payload["total_active_deals"] == 2
    assert {card["name"] for card in payload["rooms"] if card["active"]} == {"Northwind", "Contoso"}


def test_scope_defaults_to_all_rooms(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    assert payload["scope"] == {"room_id": None, "room_name": None, "label": "All Rooms"}
    assert len(payload["rooms"]) == 3


def test_room_scope_narrows_the_dashboard(wired):
    payload = analytics.overview(wired["store"], room_id=wired["hot"]["id"], as_of=AS_OF)
    assert payload["scope"]["label"] == "Northwind"
    assert [card["room_id"] for card in payload["rooms"]] == [wired["hot"]["id"]]


def test_alerts_cover_low_engagement_and_approaching_deadlines(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    kinds = {alert["kind"] for alert in payload["alerts"]}
    assert "low_engagement" in kinds  # Contoso: zero actions
    assert "deadline_approaching" in kinds  # Northwind expires 2026-10-01
    assert all(alert["room_id"] and alert["message"] for alert in payload["alerts"])


def test_the_low_engagement_alert_waits_for_the_room_to_be_old_enough(wired):
    store = wired["store"]
    fresh = make_room(store, "Initech", stage="discovery")

    impatient = {alert["room_id"] for alert in analytics.overview(store, as_of=AS_OF)["alerts"]}
    assert fresh["id"] in impatient

    analytics.save_config(store, {"thresholds": {"alert_after_days": 7}}, actor="dana")
    patient = {alert["room_id"] for alert in analytics.overview(store, as_of=AS_OF)["alerts"]}
    assert fresh["id"] not in patient


def test_deadline_alert_carries_the_field_and_remaining_days(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    alert = next(a for a in payload["alerts"] if a["kind"] == "deadline_approaching")
    assert alert["detail"]["field"] == "expires_at"
    assert alert["detail"]["days_remaining"] == 4


def test_a_deadline_that_has_passed_does_not_raise_an_alert(store):
    # "won_at" is in the default deadline vocabulary, and a won deal's date is
    # always in the past. Clamping that to zero would read as "due today".
    connect(store, thresholds={"alert_after_days": 0})
    room = make_room(store, "Adventure Works", stage="evaluation", won_at="2026-08-30")
    for index in range(4):
        make_event(store, room["id"], person="a@b.example", action="viewed", occurred_at=at(minutes=index))

    payload = analytics.overview(store, as_of=AS_OF)
    kinds = {alert["kind"] for alert in payload["alerts"] if alert["room_id"] == room["id"]}
    assert "deadline_approaching" not in kinds

    card = next(c for c in payload["rooms"] if c["room_id"] == room["id"])
    assert card["deadline"]["passed"] is True
    assert card["deadline"]["days_remaining"] == -28  # 27.5 days, floored
    assert "deadline passed 28 day(s) ago" in card["priority"]["reasons"]


def test_a_terminal_deal_raises_no_alerts(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    # Adventure Works is closed, so its silence is not a follow-up signal.
    assert wired["closed"]["id"] not in {alert["room_id"] for alert in payload["alerts"]}
    closed = next(card for card in payload["rooms"] if card["room_id"] == wired["closed"]["id"])
    assert closed["active"] is False
    assert closed["alerts"] == []


def test_rooms_are_ranked_and_the_rank_is_returned(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    scores = [card["priority"]["score"] for card in payload["rooms"]]
    assert scores == sorted(scores, reverse=True)
    assert [card["priority"]["rank"] for card in payload["rooms"]] == [1, 2, 3]


def test_priority_explains_itself(wired):
    card = analytics.overview(wired["store"], as_of=AS_OF)["rooms"][0]
    assert card["priority"]["reasons"]
    assert card["priority"]["suggested_action"]
    assert card["priority"]["urgency"] in ("normal", "high")


def test_an_engaged_room_outranks_a_quiet_one(wired):
    payload = analytics.overview(wired["store"], as_of=AS_OF)
    by_name = {card["name"]: card for card in payload["rooms"]}
    assert by_name["Northwind"]["priority"]["rank"] < by_name["Contoso"]["priority"]["rank"]


# --------------------------------------------------------------------------- #
# Per-room widgets
# --------------------------------------------------------------------------- #


def test_room_stats_cover_the_four_sourced_measures(wired):
    stats = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)["room_stats"]
    assert stats["actions"] == 7
    assert stats["visitors"] == 2
    assert stats["total_visits"] == 2  # seven actions collapsed into two sessions
    assert stats["view_seconds"] == 750.0
    assert stats["view_time"] == "12 min"
    assert stats["actions_by_kind"] == {"view": 6, "download": 1, "comment": 0, "other": 0}


def test_most_active_visitors_are_ranked_by_total_actions(wired):
    rows = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)[
        "most_active_visitors"
    ]
    assert rows[0]["person"] == "buyer0@northwind.example"
    assert rows[0]["actions"] == 4
    assert [row["actions"] for row in rows] == sorted((r["actions"] for r in rows), reverse=True)


def test_most_engaged_documents_carry_the_sourced_columns(wired):
    rows = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)[
        "most_engaged_documents"
    ]
    top = rows[0]
    assert top["title"] == "Pricing One-Pager"
    assert top["views"] == 6
    assert top["downloads"] == 0
    assert top["average_time"] == "2 min"
    assert top["users_involved"] == 2
    assert top["last_viewed_at"] == at(minutes=35)
    assert rows[1]["downloads"] == 1  # the deck is still listed despite no views


def test_latest_activity_is_newest_first_with_user_action_and_time(wired):
    rows = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)["latest_activity"]
    assert rows[0]["person"] == "buyer0@northwind.example"
    assert rows[0]["action"] == "downloaded"
    assert rows[0]["occurred_at"] == at(minutes=30)
    stamps = [row["occurred_at"] for row in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_room_trend_is_one_of_three_states_with_signals(wired):
    trend = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)["room_trend"]
    assert trend["classification"] in analytics.TREND_STATES
    assert trend["thresholds"]["hot_actions"] == 40


def test_recent_engagement_chart_is_dense_and_daily(wired):
    points = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)[
        "recent_engagement"
    ]
    assert len(points) == 15  # 14-day window inclusive of today
    assert points[0]["bucket_start"] == "2026-09-12"
    assert points[-1]["actions"] == 7
    assert points[-1]["visitors"] == 2
    assert points[0]["actions"] == 0  # zero-filled, not omitted


def test_visit_frequency_supports_day_and_week_grain(wired):
    daily = analytics.room_engagement(wired["store"], wired["hot"]["id"], as_of=AS_OF)[
        "visit_frequency"
    ]
    weekly = analytics.room_engagement(wired["store"], wired["hot"]["id"], grain="week", as_of=AS_OF)[
        "visit_frequency"
    ]
    assert daily[-1]["visits"] == 2
    assert [p["bucket_start"] for p in weekly] == ["2026-09-07", "2026-09-14", "2026-09-21"]
    assert weekly[-1]["visits"] == 2


def test_visit_gap_threshold_is_configurable(wired):
    store, room_id = wired["store"], wired["hot"]["id"]
    make_event(
        store,
        room_id,
        person="buyer0@northwind.example",
        action="viewed",
        target="Deck",
        occurred_at=at(hours=3),
    )
    assert analytics.room_engagement(store, room_id, as_of=AS_OF)["room_stats"]["total_visits"] == 3

    analytics.save_config(store, {"thresholds": {"visit_gap_minutes": 600}}, actor="dana")
    assert analytics.room_engagement(store, room_id, as_of=AS_OF)["room_stats"]["total_visits"] == 2


def test_unknown_room_is_reported_as_missing(wired):
    with pytest.raises(analytics.UnknownRoom):
        analytics.room_engagement(wired["store"], "room_nope", as_of=AS_OF)


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #


def test_timeline_merges_internal_notes_with_buyer_activity(wired):
    store, room_id = wired["store"], wired["hot"]["id"]
    analytics.add_note(
        store,
        room_id,
        {"summary": "Kickoff call done", "actor": "dana", "at": AS_OF},
        actor="dana",
    )
    entries = analytics.timeline(store, analytics.load_config(store), room_id=room_id, now=NOW)
    assert [entry["kind"] for entry in entries[:2]] == ["note", "buyer"]
    assert entries[0]["summary"] == "Kickoff call done"
    assert entries[1]["summary"].endswith("downloaded · Enterprise Overview Deck")
    stamps = [entry["at"] for entry in entries]
    assert stamps == sorted(stamps, reverse=True)


def test_timeline_preserves_fields_it_does_not_recognise(wired):
    store, room_id = wired["store"], wired["hot"]["id"]
    record = analytics.add_note(
        store,
        room_id,
        {"summary": "Called procurement", "channel": "phone", "duration_min": 12},
        actor="sam",
    )
    assert record["data"]["channel"] == "phone"
    entries = analytics.timeline(store, analytics.load_config(store), room_id=room_id, now=NOW)
    note = next(entry for entry in entries if entry["kind"] == "note")
    assert note["data"]["channel"] == "phone"


def test_timeline_is_scoped_to_one_room(wired):
    store = wired["store"]
    analytics.add_note(store, wired["quiet"]["id"], {"summary": "Chased twice"}, actor="sam")
    entries = analytics.timeline(store, analytics.load_config(store), room_id=wired["hot"]["id"], now=NOW)
    assert all(entry["room_id"] == wired["hot"]["id"] for entry in entries)


def test_empty_timeline_entry_is_rejected(wired):
    with pytest.raises(ValueError):
        analytics.add_note(wired["store"], wired["hot"]["id"], {})


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_new_action_kind_needs_no_configuration_change(wired):
    store, room_id = wired["store"], wired["hot"]["id"]
    make_event(
        store, room_id, person="bot@example", action="annotated", target="Deck", occurred_at=AS_OF
    )
    stats = analytics.room_engagement(store, room_id, as_of=AS_OF)["room_stats"]
    assert stats["actions"] == 8
    # Unrecognised kinds are still counted, just grouped as "other".
    assert stats["actions_by_kind"]["other"] == 1


def test_a_team_can_name_its_fields_anything_it_likes(store):
    connect(store)
    # "visitor"/"event"/"dwell_seconds"/"page" resolve through the default
    # synonym lists; the room's own vocabulary needs one line of config.
    analytics.save_config(
        store, {"deal": {"name_fields": ["headline"], "stage_fields": ["state"]}}, actor="dana"
    )
    room = store.create("room", {"headline": "Tailspin - Pilot", "state": "pilot"}, actor="dana")
    for index in range(5):
        make_event(
            store,
            room["id"],
            visitor=f"v{index}@tailspin.example",
            event="viewed",
            page="Overview",
            dwell_seconds=90,
            at=at(hours=index),
        )
    card = analytics.room_engagement(store, room["id"], as_of=AS_OF)
    assert card["room"]["name"] == "Tailspin - Pilot"
    assert card["room"]["stage"] == "pilot"
    assert card["room_stats"]["actions"] == 5
    assert card["room_stats"]["view_seconds"] == 450.0
    assert card["most_engaged_documents"][0]["title"] == "Overview"


def test_a_completely_unrecognisable_room_still_aggregates(store):
    connect(store)
    room = store.create("room", {"whatever": True}, actor="dana")
    make_event(store, room["id"], whatever_action="x", occurred_at=AS_OF)
    card = analytics.room_engagement(store, room["id"], as_of=AS_OF)
    assert card["room"]["name"] == room["id"]  # falls back to the id, never crashes
    assert card["room_stats"]["actions"] == 1
    assert card["room_trend"]["classification"] == "warm"


def test_event_payload_is_stored_verbatim_and_queryable(wired):
    record = make_event(
        wired["store"], wired["hot"]["id"], person="x@y.example", action="viewed", cohort="enterprise"
    )
    assert record["data"]["cohort"] == "enterprise"
    found = wired["store"].find("activity", {"cohort": "enterprise"})
    assert record["id"] in [row["id"] for row in found]


def test_events_without_a_recognised_timestamp_fall_back_to_the_envelope(store):
    connect(store)
    room = make_room(store, "Northwind")
    record = make_event(store, room["id"], person="a@b.example", action="viewed")
    assert record["created_at"]
    events = analytics.collect_events(store, room["id"], analytics.load_config(store))
    assert events[0]["at"] is not None


def test_events_for_an_unknown_room_are_rejected(store):
    with pytest.raises(analytics.UnknownRoom):
        analytics.record_event(store, {"action": "viewed"}, room_id="room_nope")


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


def seed(client):
    """One connected room with four events, via the public API only."""
    client.patch("/api/wf-006/config", json=TOKEN)
    room = client.post("/api/records/room", json={"name": "Northwind", "stage": "evaluation"}).json()
    for index in range(4):
        client.post(
            "/api/wf-006/events",
            params={"room_id": room["id"]},
            json={
                "person": f"b{index}@northwind.example",
                "action": "viewed",
                "target": "Deck",
                "seconds_on_page": 90,
                "occurred_at": at(hours=index),
            },
        )
    return room


def test_http_overview_defaults_to_all_rooms(client):
    seed(client)
    body = client.get("/api/wf-006/overview", params={"as_of": AS_OF}).json()
    assert body["scope"]["label"] == "All Rooms"
    assert body["total_active_deals"] == 1
    assert body["rooms"][0]["priority"]["rank"] == 1


def test_http_overview_is_gated_with_428(client):
    client.post("/api/records/room", json={"name": "Northwind"})
    response = client.get("/api/wf-006/overview")
    assert response.status_code == 428
    assert "token" in response.json()["detail"]


def test_http_room_drilldown_returns_every_widget(client):
    room = seed(client)
    body = client.get(f"/api/wf-006/rooms/{room['id']}", params={"as_of": AS_OF}).json()
    for key in (
        "room_stats",
        "room_trend",
        "most_active_visitors",
        "most_engaged_documents",
        "latest_activity",
        "recent_engagement",
        "visit_frequency",
        "timeline",
    ):
        assert key in body, key
    assert body["room_stats"]["actions"] == 4


def test_http_unknown_room_is_404(client):
    seed(client)
    assert client.get("/api/wf-006/rooms/room_nope").status_code == 404


def test_http_grain_is_validated(client):
    seed(client)
    assert client.get("/api/wf-006/overview", params={"grain": "hour"}).status_code == 422


def test_http_config_hides_the_token_but_reports_presence(client):
    seed(client)
    body = client.get("/api/wf-006/config").json()
    assert body["connected"] is True
    assert body["config"]["connection"]["token"] == "set"
    assert "ldp-environment-token" not in client.get("/api/wf-006/config").text


def test_http_config_patch_is_merged_and_audited(client):
    seed(client)
    response = client.patch("/api/wf-006/config", json={"thresholds": {"cold_floor": 11}})
    assert response.status_code == 200
    # The record holds only the override; the effective config layers it onto
    # the defaults, so neither the write nor the read loses the siblings.
    assert response.json()["record"]["data"]["thresholds"] == {"cold_floor": 11}
    effective = client.get("/api/wf-006/config").json()["config"]["thresholds"]
    assert effective["cold_floor"] == 11
    assert effective["warm_actions"] == 15
    assert client.get("/api/audit", params={"collection": "analytics_config"}).json()["count"] == 2


def test_http_timeline_round_trip_is_audited(client):
    room = seed(client)
    response = client.post(
        f"/api/wf-006/rooms/{room['id']}/timeline",
        json={"summary": "Called procurement", "at": AS_OF},
        params={"actor": "sam"},
    )
    assert response.status_code == 201

    body = client.get(f"/api/wf-006/rooms/{room['id']}/timeline").json()
    assert body["count"] == 5  # one note plus four buyer events
    assert body["entries"][0]["summary"] == "Called procurement"

    actions = client.get("/api/audit", params={"collection": "timeline"}).json()["entries"]
    assert [entry["action"] for entry in actions] == ["insert"]


def test_http_timeline_write_rejects_an_empty_payload(client):
    room = seed(client)
    assert client.post(f"/api/wf-006/rooms/{room['id']}/timeline", json={}).status_code == 400


def test_http_timeline_write_to_unknown_room_is_404(client):
    seed(client)
    response = client.post("/api/wf-006/rooms/room_nope/timeline", json={"summary": "x"})
    assert response.status_code == 404


def test_http_event_ingest_keeps_unknown_fields_and_is_audited(client):
    room = seed(client)
    response = client.post(
        "/api/wf-006/events",
        params={"room_id": room["id"]},
        json={"person": "new@example", "action": "viewed", "region": "apac"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["region"] == "apac"
    assert client.get("/api/audit", params={"collection": "activity"}).json()["count"] == 5


def test_http_event_ingest_to_unknown_room_is_404(client):
    seed(client)
    response = client.post(
        "/api/wf-006/events", params={"room_id": "room_nope"}, json={"action": "viewed"}
    )
    assert response.status_code == 404


def test_http_alerts_endpoint_scopes(client):
    room = seed(client)
    everything = client.get("/api/wf-006/alerts", params={"as_of": AS_OF}).json()
    scoped = client.get(
        "/api/wf-006/alerts", params={"room_id": room["id"], "as_of": AS_OF}
    ).json()
    assert scoped["count"] == len(scoped["alerts"])
    assert all(alert["room_id"] == room["id"] for alert in scoped["alerts"])
    assert everything["scope"]["label"] == "All Rooms"


def test_reading_analytics_writes_nothing_to_the_audit_log(client):
    room = seed(client)
    before = client.get("/api/audit").json()["count"]
    client.get("/api/wf-006/overview", params={"as_of": AS_OF})
    client.get(f"/api/wf-006/rooms/{room['id']}", params={"as_of": AS_OF})
    client.get("/api/wf-006/alerts", params={"as_of": AS_OF})
    client.get("/api/wf-006/config")
    assert client.get("/api/audit").json()["count"] == before


def test_analytics_records_appear_in_the_audit_trail_with_room_scope(client):
    room = seed(client)
    client.post(f"/api/wf-006/rooms/{room['id']}/timeline", json={"summary": "Kickoff"})
    entry = client.get("/api/audit", params={"collection": "timeline"}).json()["entries"][0]
    assert entry["room_id"] == room["id"]
    assert entry["source"] == f"POST /api/wf-006/rooms/{room['id']}/timeline"
    assert entry["after_state"]["summary"] == "Kickoff"


def test_audit_source_is_derived_from_the_live_prefix_not_a_hardcoded_url(client):
    """The audit trail must name the route that actually served the write.

    The ported domain module arrived with its own URLs baked into the write
    calls, which silently misreported provenance the moment the route moved:
    the route was served from /api/wf-006 while the audit row still claimed
    /api/analytics. The audit log is the product's guarantee, so a row that
    names a path nobody called is a defect, not a cosmetic mismatch.

    This asserts the general property rather than today's prefix, so the next
    route rename is caught here instead of by a reader of the audit log.
    """
    room = seed(client)
    client.post(f"/api/wf-006/rooms/{room['id']}/timeline", json={"summary": "Kickoff"})
    client.post("/api/wf-006/events", params={"room_id": room["id"]}, json={"action": "viewed"})
    client.patch("/api/wf-006/config", json={"thresholds": {"cold_floor": 12}})

    entries = client.get("/api/audit").json()["entries"]
    sources = [entry["source"] for entry in entries]
    for expected in (
        f"POST /api/wf-006/rooms/{room['id']}/timeline",
        "POST /api/wf-006/events",
        "PATCH /api/wf-006/config",
    ):
        assert expected in sources, f"{expected!r} missing from audit sources {sources}"

    # No row may name a path this feature does not serve. This is the assertion
    # that would have caught the stale /api/analytics entries.
    stale = [s for s in sources if "analytics" in s and "/api/wf-006" not in s]
    assert not stale, f"audit rows name an unserved path: {stale}"
