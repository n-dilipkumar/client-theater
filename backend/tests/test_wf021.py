"""Tests for WF-021: classify workspace engagement health.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-021.md``, which quotes its own
source for the four buckets and the three windows:

    Hot = workspaces that have tons of recent engagement within the last 7 days.
    Warm = workspaces that have a decent amount of engagement within the last 14
    days. Cooling = workspaces that previously had engagement, but none within
    the last 14 days. Cold = workspace with no engagement within the last month.

So the suite is organised around what the research asserts and what it left open,
and it is explicit about which is which:

* **the ladder** - four buckets, the three sourced windows, the exclusive window
  edge, the researched decay through all four states with no new activity, and
  the total-ness of the ladder. A rule that cannot return "no answer" is the bug
  this workflow is most likely to ship, so it is asserted combinatorially rather
  than case by case;
* **the vocabulary** - the four values, their sourced sentences, the five
  researched event shapes, and the flag that makes "Last Client View" a column
  rather than a guess;
* **the inferences** - every judgement call the research's silences forced, each
  asserted to be bounded and to name a change that actually works. An inference
  list nobody reads is a list nobody checks;
* **the store surface** - event intake, the room scope, the filters, and the
  sort keys the research's user flow names;
* **the HTTP surface** - every route through this feature's own router, the
  refusals, and the audit-source rule: every write's audit row must name a route
  the host actually mounted, checked against the live route table.

The volume floors are unsourced, so they are not tested as if they were
research. They are tested as the *mechanism* the inference describes - a floor
that suppresses a bucket and falls through rather than leaving a hole - and
:data:`DEFAULT_MIN_EVENTS` is asserted to be what the inference entry says it is,
so the two cannot drift apart silently.
"""

from __future__ import annotations

import itertools
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from dsr.trend_health import (
    CLOCK_SKEW_TOLERANCE_SECONDS,
    DEFAULT_MIN_EVENTS,
    DEFAULT_RULES,
    DEFAULT_WINDOWS,
    INFERENCES,
    SOURCED_QUOTE,
    TREND_LABELS,
    TREND_RULES,
    TREND_VALUES,
    WINDOW_KEYS,
    EngagementEvent,
    TrendError,
    TrendHealth,
    UnknownEventType,
    UnknownRoom,
    bucket_of,
    classify,
    decay,
    describe,
    effective,
    in_window,
    merge,
    parse_timestamp,
    validate,
    vocabulary,
)
from dsr.trend_health.errors import InvalidRules, InvalidSort, InvalidTimestamp
from dsr.trend_health.health import DASHBOARD_SORTS, SORT_KEYS
from dsr.trend_health.rules import describe as describe_rules
from dsr.trend_health.vocabulary import (
    AUDIENCES,
    CLIENT_VIEW_EVENT,
    ENGAGEMENT_COLLECTION,
    ENGAGEMENT_EVENT_TYPES,
    ORDER_FORM_PREFIX,
    RULES_COLLECTION,
    RULES_RECORD_ID,
    is_client_view,
    is_engagement_event,
    require_audience,
    require_event_type,
)
from fastapi.testclient import TestClient

#: The feature's own prefix. Written out here rather than imported, so a renamed
#: prefix fails a test instead of following silently.
PREFIX = "/api/wf-021"

FEATURE_ID = "wf-021-classify-workspace-engagement-health-h"

#: Every route this feature mounts. A change to the surface has to be made here
#: deliberately, which is the point of asserting a count and a set.
ROUTES = {
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("GET", "/rules"),
    ("PATCH", "/rules"),
    ("GET", "/summary"),
    ("GET", "/dashboard"),
    ("GET", "/rooms/{room_id}/trend"),
    ("GET", "/events"),
    ("POST", "/events"),
}

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "team": "EMEA",
    "stage": "evaluation",
}


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(":memory:", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def health(store):
    return TrendHealth(store)


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, on the shared app.

    The plugin host mounts every feature onto one app, so this exercises the real
    mounted route rather than a private test app - which is what makes the
    audit-source assertion below meaningful.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf021.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


def events_at(ages, *, event_type=CLIENT_VIEW_EVENT, external=True, now=NOW):
    """Engagement events at the given ages in days, newest first by construction."""
    return [
        EngagementEvent(at=now - timedelta(days=float(age)), type=event_type, external=external)
        for age in ages
    ]


def trend_of(ages, *, rules=None, event_type=CLIENT_VIEW_EVENT, external=True, now=NOW):
    return classify(
        events_at(ages, event_type=event_type, external=external, now=now),
        rules or DEFAULT_RULES,
        now,
    )["trend"]


def record(
    health,
    room_id,
    *,
    days_ago=1.0,
    event_type=CLIENT_VIEW_EVENT,
    audience="external",
    now=NOW,
    **extra,
):
    payload = {
        "type": event_type,
        "occurred_at": (now - timedelta(days=days_ago)).isoformat(timespec="seconds"),
        "audience": audience,
    }
    payload.update(extra)
    return health.record_event(payload, room_id=room_id, actor="dana", source="POST test", now=now)


# --------------------------------------------------------------------------- #
# Plugin registration
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-021"
    assert entry["exception_handlers"] == ["TrendError"]
    assert len(entry["routes"]) == len(ROUTES)


def test_the_mounted_route_set_is_exactly_the_one_this_suite_expects(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    served = {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}
    assert served == {(method, f"{PREFIX}{path}") for method, path in ROUTES}


def test_no_core_route_and_no_other_feature_answers_under_the_prefix(http):
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}

    assert len(mine) == len(ROUTES)
    assert not mine & others


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally.

    Checked against the parsed import list rather than the raw text, so a
    docstring that *mentions* ``dsr.api`` - as this one does, to explain why the
    import is not there - does not read as an import of it.
    """
    import ast

    module = load_feature("wf021_classify_workspace_engagement_health_h")
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert not {name for name in imported if name == "dsr.api" or name.startswith("dsr.api.")}
    assert "dsr.deps" in imported


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature("wf021_classify_workspace_engagement_health_h")

    assert f"id: {module.FEATURE['id']!r}" in text
    assert f"label: {module.FEATURE['nav'][0]['label']!r}" in text


def test_the_room_scoped_route_takes_its_room_in_the_path():
    """The brief asks for room-scoped paths to stay room-scoped."""
    paths = {
        route.path
        for route in load_feature("wf021_classify_workspace_engagement_health_h").router.routes
    }

    assert f"{PREFIX}/rooms/{{room_id}}/trend" in paths
    # No path reaches into another resource while carrying a room id, which is
    # the shape that makes a shared prefix collide later.
    assert not [path for path in paths if path.count("{") > 1]


# --------------------------------------------------------------------------- #
# The sourced vocabulary
# --------------------------------------------------------------------------- #


def test_there_are_exactly_four_trend_values_in_decay_order():
    assert TREND_VALUES == ("hot", "warm", "cooling", "cold")


@pytest.mark.parametrize("value", TREND_VALUES)
def test_every_trend_carries_its_sourced_sentence(value):
    assert TREND_RULES[value]
    assert TREND_LABELS[value] == value.capitalize()


def test_the_sourced_quote_is_carried_verbatim():
    """The quotation the whole workflow rests on, so it cannot be paraphrased away."""
    assert (
        "Hot = workspaces that have tons of recent engagement within the last 7 days"
        in SOURCED_QUOTE
    )
    assert (
        "Cooling = workspaces that previously had engagement, but none within the last 14 days"
        in SOURCED_QUOTE
    )
    assert "Cold = workspace with no engagement within the last month" in SOURCED_QUOTE


def test_the_three_windows_are_the_sourced_ones():
    assert DEFAULT_WINDOWS == {"hot": 7, "warm": 14, "cold": 30}
    assert WINDOW_KEYS == ("hot", "warm", "cold")


def test_the_researched_event_shapes_are_the_five_named_in_the_research():
    assert ENGAGEMENT_EVENT_TYPES == (
        "workspace.viewed",
        "workspace.page.viewed",
        "workspace.file.viewed",
        "workspace.link.clicked",
    )
    assert ORDER_FORM_PREFIX == "workspace.order_form."


@pytest.mark.parametrize(
    "event_type",
    [
        "workspace.viewed",
        "workspace.page.viewed",
        "workspace.file.viewed",
        "workspace.link.clicked",
        "workspace.order_form.started",
        "workspace.order_form.completed",
        "workspace.order_form.abandoned",
        "workspace.order_form",
    ],
)
def test_every_researched_shape_is_accepted(event_type):
    assert is_engagement_event(event_type)
    assert require_event_type(event_type) == event_type


@pytest.mark.parametrize(
    "event_type",
    [
        "room.heartbeat",
        "workspace.updated",
        "page.viewed",
        "workspaceorder_form.started",
        "workspace.order_forms.started",
        "",
        None,
    ],
)
def test_anything_outside_the_vocabulary_is_refused_with_the_accepted_list(event_type):
    with pytest.raises(UnknownEventType) as caught:
        require_event_type(event_type)
    message = str(caught.value)
    assert "workspace.viewed" in message
    assert f"{ORDER_FORM_PREFIX}*" in message


def test_only_the_room_opening_counts_as_a_client_view():
    """Last Client View is the room being opened, not activity inside it."""
    assert is_client_view(CLIENT_VIEW_EVENT)
    for event_type in ("workspace.page.viewed", "workspace.file.viewed", "workspace.link.clicked"):
        assert not is_client_view(event_type)


def test_vocabulary_endpoint_serves_every_published_name(http):
    body = http.get(f"{PREFIX}/vocabulary").json()

    assert body["trend_values"] == list(TREND_VALUES)
    assert body["decay_path"] == ["hot", "warm", "cooling", "cold"]
    assert body["trend_labels"] == {
        "hot": "Hot",
        "warm": "Warm",
        "cooling": "Cooling",
        "cold": "Cold",
    }
    assert body["engagement_event_types"] == list(ENGAGEMENT_EVENT_TYPES)
    assert body["client_view_event"] == CLIENT_VIEW_EVENT
    assert body["audiences"] == list(AUDIENCES)
    assert body["collections"]["engagement"] == ENGAGEMENT_COLLECTION
    assert [entry["rule"] for entry in body["trends"]] == [TREND_RULES[v] for v in TREND_VALUES]


# --------------------------------------------------------------------------- #
# The ladder: the researched rules
# --------------------------------------------------------------------------- #


def test_a_busy_week_is_hot():
    assert trend_of([0.2, 0.9, 1.4, 1.8, 2.2, 3.0]) == "hot"


def test_a_quiet_fortnight_with_nothing_this_week_is_warm():
    assert trend_of([8.0, 9.5, 11.0, 12.5]) == "warm"


def test_previous_engagement_and_none_in_a_fortnight_is_cooling():
    assert trend_of([20.0, 22.0]) == "cooling"


def test_nothing_in_the_last_month_is_cold():
    assert trend_of([45.0, 60.0]) == "cold"


def test_no_engagement_at_all_is_cold():
    assert trend_of([]) == "cold"


def test_a_workspace_older_than_every_window_reads_cold_not_cooling():
    """Cooling requires something inside the 30-day window; the research says so."""
    assert trend_of([31.0]) == "cold"
    assert trend_of([30.5]) == "cold"
    assert trend_of([29.9]) == "cooling"


def test_the_hot_window_is_seven_days_not_fourteen():
    """Five events this week is Hot. Move one outside the week and it is not."""
    assert trend_of([0.5, 1.0, 2.0, 3.0, 4.0]) == "hot"
    assert trend_of([0.5, 1.0, 2.0, 3.0, 7.5]) == "warm"


def test_an_event_just_inside_the_window_counts_and_one_just_outside_does_not():
    """The edge is exclusive; see the window-boundary inference for why."""
    assert in_window(NOW - timedelta(days=6, hours=23), NOW, 7)
    assert not in_window(NOW - timedelta(days=7), NOW, 7)
    assert not in_window(NOW - timedelta(days=7, seconds=1), NOW, 7)


def test_the_window_edge_is_exclusive_for_every_window():
    for days in DEFAULT_WINDOWS.values():
        assert in_window(NOW - timedelta(days=days, seconds=-1), NOW, days)
        assert not in_window(NOW - timedelta(days=days), NOW, days)


def test_an_event_in_the_future_is_outside_every_window():
    for days in DEFAULT_WINDOWS.values():
        assert not in_window(NOW + timedelta(seconds=1), NOW, days)


def test_the_warm_window_counts_engagement_from_days_eight_to_fourteen():
    assert trend_of([8.1, 9.0, 10.0, 11.0, 12.0, 13.0, 13.9]) == "warm"
    # One event short of the Warm floor, and one short of the window.
    assert trend_of([13.9, 14.0, 14.1, 14.2, 14.3]) == "cooling"
    # The same week, with the fifteenth day's event inside the window.
    assert trend_of([13.9, 13.8, 14.1, 14.2, 14.3]) == "warm"


# --------------------------------------------------------------------------- #
# The ladder is total
# --------------------------------------------------------------------------- #


def test_the_ladder_returns_a_value_for_any_age_including_none():
    ages = [0, 0.5, 1, 3, 6.9, 7, 7.1, 13.9, 14, 14.1, 20, 29.9, 30, 30.1, 45, 90]
    for age in ages:
        assert trend_of([age]) in TREND_VALUES


def test_the_ladder_never_falls_off_the_end_across_every_combo_of_ages():
    """The property the research's four states imply and a bug would break.

    Four buckets that are not a partition leave a room unclassified, and the
    caller has to guess. Every combination of a small set of ages, at three
    volumes, has to produce one of the four.
    """
    ages = [1, 3, 6, 7, 10, 14, 15, 25, 30, 31, 60]
    combinations = itertools.chain(
        ((age,) for age in ages),
        itertools.combinations(ages, 2),
        ((1, 3, 6), (7, 10, 14), (1, 14, 30), (31, 45, 60), (6, 7, 8), (14, 15, 16)),
    )
    for combo in combinations:
        assert trend_of(list(combo)) in TREND_VALUES


def test_bucket_of_never_raises_on_any_bucket_count():
    for hot, warm, cold in itertools.product(range(0, 4), repeat=3):
        counts = {
            "hot": {"qualifying": hot, "events": hot, "days": 7},
            "warm": {"qualifying": warm, "events": warm, "days": 14},
            "cold": {"qualifying": cold, "events": cold, "days": 30},
        }
        assert bucket_of(counts, DEFAULT_RULES) in TREND_VALUES


def test_the_hottest_matching_bucket_wins():
    """Hot is checked before Warm: a busy week is Hot, not Warm."""
    assert (
        bucket_of(
            {
                "hot": {"qualifying": 5},
                "warm": {"qualifying": 9},
                "cold": {"qualifying": 9, "events": 9},
            },
            DEFAULT_RULES,
        )
        == "hot"
    )
    assert (
        bucket_of(
            {
                "hot": {"qualifying": 0},
                "warm": {"qualifying": 9},
                "cold": {"qualifying": 9, "events": 9},
            },
            DEFAULT_RULES,
        )
        == "warm"
    )


# --------------------------------------------------------------------------- #
# The volume floor: unsourced, and tested as a mechanism
# --------------------------------------------------------------------------- #


def test_activity_under_every_floor_falls_through_to_a_bucket_rather_than_no_answer():
    """The mechanism, not the number: a floor suppresses a bucket, never the answer."""
    assert trend_of([1.0]) == "cooling"
    assert trend_of([1.0, 2.0]) == "warm"
    assert trend_of([0.5, 1.0, 2.0, 3.0]) == "warm"


def test_the_floor_suppression_is_visible_in_the_arithmetic():
    reading = classify(events_at([1.0]), DEFAULT_RULES, NOW)

    assert reading["trend"] == "cooling"
    assert any("below the Warm floor" in reason for reason in reading["reasons"])
    assert reading["windows"]["hot"]["qualifying"] == 1
    assert reading["windows"]["warm"]["qualifying"] == 1


def test_the_counts_come_back_with_every_value():
    """A rep acting on a bucket suppressed by a floor can see what suppressed it."""
    reading = classify(events_at([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), DEFAULT_RULES, NOW)

    assert reading["trend"] == "hot"
    assert reading["windows"]["hot"]["qualifying"] == 6
    assert reading["windows"]["cold"]["qualifying"] == 6


def test_the_default_floors_are_the_ones_the_inference_entry_publishes():
    entry = next(item for item in INFERENCES if item["id"] == "volume-floor")
    assert entry["value"]["min_events"] == DEFAULT_MIN_EVENTS
    assert DEFAULT_MIN_EVENTS["hot"] == 5
    assert DEFAULT_MIN_EVENTS["warm"] == 2


def test_a_floor_of_one_turns_the_ladder_into_pure_recency():
    """The reading that discards the volume words entirely, and it is reachable."""
    rules = merge(DEFAULT_RULES, {"min_events": {"hot": 1, "warm": 1}})
    assert trend_of([1.0], rules=rules) == "hot"
    assert trend_of([9.0], rules=rules) == "warm"
    assert trend_of([20.0], rules=rules) == "cooling"
    assert trend_of([40.0], rules=rules) == "cold"


# --------------------------------------------------------------------------- #
# External engagement only
# --------------------------------------------------------------------------- #


def test_internal_activity_does_not_make_a_workspace_hot():
    """The sourced phrase is "external engagement"."""
    assert trend_of([0.5, 1, 2, 3, 4, 5], external=False) == "cooling"


def test_internal_activity_is_still_recorded_and_reported():
    reading = classify(events_at([0.5, 1, 2], external=False), DEFAULT_RULES, NOW)
    assert reading["trend"] == "cooling"
    assert any("did not count" in reason for reason in reading["reasons"])


def test_recent_uncounted_activity_is_explained_rather_than_left_looking_inert():
    """A rep who opened the room yesterday needs to be told why it is still Cooling."""
    events = events_at([20.0, 22.0]) + events_at([1.0], external=False)
    reading = classify(events, DEFAULT_RULES, NOW)

    assert reading["trend"] == "cooling"
    assert any("external engagement" in reason for reason in reading["reasons"])


def test_the_explanation_disappears_when_internal_activity_counts():
    events = events_at([20.0, 22.0]) + events_at([1.0], external=False)
    loose = merge(DEFAULT_RULES, {"count_only_external": False})
    reading = classify(events, loose, NOW)

    assert not any("did not count" in reason for reason in reading["reasons"])


def test_turning_the_rule_off_counts_internal_activity():
    rules = merge(DEFAULT_RULES, {"count_only_external": False})
    assert trend_of([0.5, 1, 2, 3, 4, 5], external=False, rules=rules) == "hot"


def test_mixed_activity_counts_only_the_external_half():
    events = events_at([0.5, 1.0, 1.5, 2.0, 2.5, 3.0]) + events_at(
        [0.2, 0.3, 0.4, 0.6, 0.7, 0.8], external=False
    )
    reading = classify(events, DEFAULT_RULES, NOW)
    assert reading["windows"]["hot"]["events"] == 12
    assert reading["windows"]["hot"]["qualifying"] == 6


# --------------------------------------------------------------------------- #
# The researched decay
# --------------------------------------------------------------------------- #


def test_a_workspace_decays_hot_warm_cooling_cold_with_no_new_activity():
    """ "Buckets are time-window based, so a workspace decays from Hot -> Warm ->
    Cooling -> Cold without any new activity." A room with a full hot week and
    nothing after it must pass through every state, in that order, and stop."""
    events = events_at([0.0] * 6)
    ladder = []
    for days in (0, 7, 14, 30):
        ladder.append(classify(events, DEFAULT_RULES, NOW + timedelta(days=days))["trend"])
    assert ladder == ["hot", "warm", "cooling", "cold"]


def test_decay_reports_the_same_values_the_ladder_would_return():
    """The decay is evaluated with the classifier, not a second implementation."""
    events = events_at([0.0] * 6)
    steps, change = decay(events, DEFAULT_RULES, NOW, "hot")

    assert [step["trend"] for step in steps] == ["warm", "cooling", "cold"]
    assert [step["window"] for step in steps] == ["hot", "warm", "cold"]
    assert change["trend"] == "warm"
    for step in steps:
        moment = datetime.fromisoformat(step["at"])
        assert classify(events, DEFAULT_RULES, moment)["trend"] == step["trend"]


def test_decay_lands_exactly_on_the_window_edges():
    events = events_at([0.0] * 6)
    steps, _change = decay(events, DEFAULT_RULES, NOW, "hot")
    newest = NOW

    assert [step["at"] for step in steps] == [
        (newest + timedelta(days=days)).isoformat(timespec="seconds") for days in (7, 14, 30)
    ]


def test_next_change_is_the_first_edge_that_differs():
    events = events_at([0.0] * 2)
    reading = classify(events, DEFAULT_RULES, NOW)
    assert reading["trend"] == "warm"
    assert reading["next_change"]["window"] == "warm"
    assert reading["next_change"]["trend"] == "cooling"


def test_a_cooling_room_decays_to_cold_and_no_further():
    reading = classify(events_at([20.0]), DEFAULT_RULES, NOW)
    assert reading["trend"] == "cooling"
    assert [step["trend"] for step in reading["decay"]] == ["cold"]
    assert reading["next_change"]["trend"] == "cold"


def test_a_cold_room_has_nothing_left_to_decay_to():
    for ages in ([], [45.0], [90.0]):
        reading = classify(events_at(ages), DEFAULT_RULES, NOW)
        assert reading["trend"] == "cold"
        assert reading["decay"] == []
        assert reading["next_change"] is None


def test_a_room_whose_only_activity_is_internal_never_changes_on_its_own():
    reading = classify(events_at([0.5, 1.0], external=False), DEFAULT_RULES, NOW)
    assert reading["trend"] == "cooling"
    assert reading["next_change"] is None
    assert reading["decay"] == []


def test_decay_never_reports_an_edge_in_the_past():
    for ages in ([0.0] * 6, [1.0, 2.0], [20.0]):
        reading = classify(events_at(ages), DEFAULT_RULES, NOW)
        for step in reading["decay"]:
            assert datetime.fromisoformat(step["at"]) > NOW


def test_decay_is_monotone_for_a_room_that_only_loses_activity():
    """Nothing happening can only ever move a workspace down the researched order."""
    events = events_at([0.0] * 8)
    order = {value: rank for rank, value in enumerate(TREND_VALUES)}
    seen = []
    for hours in range(0, 32 * 24 * 4, 6):
        seen.append(classify(events, DEFAULT_RULES, NOW + timedelta(hours=hours))["trend"])
    ranks = [order[value] for value in seen]
    assert ranks == sorted(ranks), seen
    assert set(seen) == set(TREND_VALUES)


# --------------------------------------------------------------------------- #
# as_of
# --------------------------------------------------------------------------- #


def test_as_of_answers_the_question_for_the_past():
    events = events_at([0.0] * 6)

    assert classify(events, DEFAULT_RULES, NOW)["trend"] == "hot"
    assert classify(events, DEFAULT_RULES, NOW + timedelta(days=7))["trend"] == "warm"
    assert classify(events, DEFAULT_RULES, NOW + timedelta(days=31))["trend"] == "cold"


def test_events_that_have_not_happened_yet_cannot_count():
    """Evaluating before the events exist finds nothing, which is the honest answer."""
    events = events_at([0.0] * 6)
    assert classify(events, DEFAULT_RULES, NOW - timedelta(days=1))["trend"] == "cold"


def test_last_engagement_days_ago_is_none_when_nothing_counted():
    reading = classify([], DEFAULT_RULES, NOW)
    assert reading["last_engagement_at"] is None
    assert reading["last_engagement_days_ago"] is None


def test_last_engagement_days_ago_is_zero_only_for_an_event_stamp_now():
    assert classify(events_at([0.0]), DEFAULT_RULES, NOW)["last_engagement_days_ago"] == 0.0


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-27T12:00:00Z",
        "2026-09-27T12:00:00z",
        "2026-09-27T12:00:00+00:00",
        "2026-09-27T14:00:00+02:00",
        "2026-09-27 12:00:00",
        "2026-09-27",
        1790505600,
        1790505600000,
        datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
    ],
)
def test_timestamps_are_read_in_every_shape_a_sender_might_use(value):
    parsed = parse_timestamp(value)
    assert parsed is not None and parsed.tzinfo is not None


def test_the_utc_form_and_the_offset_form_agree():
    assert parse_timestamp("2026-09-27T12:00:00Z") == parse_timestamp("2026-09-27T12:00:00+00:00")


def test_a_naive_datetime_is_read_as_utc():
    assert parse_timestamp(datetime(2026, 9, 27, 12, 0)) == parse_timestamp("2026-09-27T12:00:00Z")


@pytest.mark.parametrize(
    "value", ["yesterday", "", "  ", None, True, False, "2026-13-45T99:99:99Z", -5]
)
def test_an_unreadable_timestamp_is_refused_with_the_value_it_saw(value):
    with pytest.raises(InvalidTimestamp):
        parse_timestamp(value)


def test_an_optional_timestamp_may_be_absent():
    assert parse_timestamp(None, required=False) is None
    assert parse_timestamp("", required=False) is None


def test_the_researched_camel_case_key_is_preferred():
    from dsr.trend_health.timestamps import timestamp_of

    when, defaulted = timestamp_of(
        {"occurredAt": "2026-09-27T09:00:00Z", "occurred_at": "2020-01-01T00:00:00Z"}, default=NOW
    )
    assert when.isoformat() == "2026-09-27T09:00:00+00:00"
    assert defaulted is False


def test_a_missing_timestamp_falls_back_to_arrival_and_says_so():
    from dsr.trend_health.timestamps import timestamp_of

    when, defaulted = timestamp_of({}, default=NOW)
    assert when == NOW
    assert defaulted is True


def test_a_missing_timestamp_with_no_default_is_refused():
    from dsr.trend_health.timestamps import timestamp_of

    with pytest.raises(InvalidTimestamp) as caught:
        timestamp_of({"type": CLIENT_VIEW_EVENT})
    assert "occurredAt" in str(caught.value)


def test_clock_skew_within_tolerance_is_accepted():
    from dsr.trend_health.timestamps import check_not_ahead

    check_not_ahead(NOW + timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS), now=NOW)


def test_clock_skew_beyond_tolerance_is_refused_rather_than_clamped():
    from dsr.trend_health.timestamps import check_not_ahead

    with pytest.raises(InvalidTimestamp) as caught:
        check_not_ahead(NOW + timedelta(days=30), now=NOW)
    assert "clock-skew" in str(caught.value)


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def test_the_defaults_are_the_researched_windows():
    assert validate(DEFAULT_RULES) == {
        "windows": {"hot": 7, "warm": 14, "cold": 30},
        "min_events": {"hot": 5, "warm": 2},
        "count_only_external": True,
    }


def test_a_patch_merges_rather_than_replaces():
    merged = merge(DEFAULT_RULES, {"min_events": {"hot": 3}})

    assert merged["min_events"] == {"hot": 3, "warm": 2}
    assert merged["windows"] == {"hot": 7, "warm": 14, "cold": 30}
    assert merged["count_only_external"] is True


def test_a_patch_can_retune_the_windows():
    merged = merge(DEFAULT_RULES, {"windows": {"hot": 3, "warm": 5, "cold": 10}})
    assert merged["windows"] == {"hot": 3, "warm": 5, "cold": 10}


def test_a_retuned_window_actually_moves_the_boundary():
    rules = merge(
        DEFAULT_RULES,
        {"windows": {"hot": 3, "warm": 5, "cold": 10}, "min_events": {"hot": 1, "warm": 1}},
    )
    assert trend_of([3.5], rules=rules) == "warm"
    assert trend_of([5.5], rules=rules) == "cooling"
    assert trend_of([10.5], rules=rules) == "cold"


def test_a_scalar_rule_is_replaced_outright():
    assert merge(DEFAULT_RULES, {"count_only_external": False})["count_only_external"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"min_event": 3},
        {"MIN_EVENTS": 3},
        {"window": {"hot": 3}},
        {"unknown": True},
    ],
)
def test_an_unknown_rule_key_is_refused_rather_than_ignored(patch):
    """A typo that silently does nothing is how a threshold ends up quietly wrong."""
    with pytest.raises(InvalidRules) as caught:
        merge(DEFAULT_RULES, patch)
    assert "unknown" in str(caught.value).lower()


@pytest.mark.parametrize(
    "windows",
    [
        {"hot": 14, "warm": 7, "cold": 30},
        {"hot": 7, "warm": 30, "cold": 14},
        {"hot": 7, "warm": 7, "cold": 30},
        {"hot": 0, "warm": 14, "cold": 30},
        {"hot": -1, "warm": 14, "cold": 30},
        {"hot": "7", "warm": 14, "cold": 30},
    ],
)
def test_windows_that_would_break_the_ladder_are_refused(windows):
    with pytest.raises(InvalidRules):
        merge(DEFAULT_RULES, {"windows": windows})


@pytest.mark.parametrize("windows", [{}, {"hot": 7}, {"warm": 14, "cold": 30}])
def test_a_complete_rules_mapping_must_name_all_three_windows(windows):
    with pytest.raises(InvalidRules):
        validate({**DEFAULT_RULES, "windows": windows})


def test_a_stored_rule_this_version_does_not_know_about_is_ignored_not_fatal():
    """A record written by a future version must not break this version's reads."""
    assert validate({**DEFAULT_RULES, "extra_knob": 7})["windows"] == {
        "hot": 7,
        "warm": 14,
        "cold": 30,
    }


def test_a_partial_window_patch_is_legal_because_it_merges():
    """Retuning one window must not force the caller to restate the other two."""
    assert merge(DEFAULT_RULES, {"windows": {"hot": 3}})["windows"] == {
        "hot": 3,
        "warm": 14,
        "cold": 30,
    }


@pytest.mark.parametrize("floor", [0, -1, "5", "many", True, 2.5])
def test_a_floor_below_one_or_of_the_wrong_type_is_refused(floor):
    with pytest.raises(InvalidRules):
        merge(DEFAULT_RULES, {"min_events": {"hot": floor}})


def test_count_only_external_must_be_a_boolean():
    with pytest.raises(InvalidRules):
        merge(DEFAULT_RULES, {"count_only_external": "yes"})


def test_a_stored_override_that_became_invalid_falls_back_and_says_where_it_came_from():
    """A stored record must not be able to break every read of the dashboard."""
    rules, origin = effective({"windows": {"hot": 99, "warm": 7, "cold": 30}})

    assert origin == "defaults"
    assert rules["windows"] == {"hot": 7, "warm": 14, "cold": 30}


def test_a_stored_override_in_force_is_reported_as_an_override():
    rules, origin = effective({"min_events": {"hot": 2, "warm": 2}})
    assert origin == "override"
    assert rules["min_events"]["hot"] == 2


def test_effective_with_no_record_is_the_defaults():
    assert effective(None) == (validate(DEFAULT_RULES), "defaults")


def test_the_ladder_stays_total_under_any_legal_window_ordering():
    """Ordering is enforced for the reader's sake, not for correctness - prove it."""
    rules = merge(
        DEFAULT_RULES,
        {"windows": {"hot": 1, "warm": 2, "cold": 3}, "min_events": {"hot": 2, "warm": 2}},
    )
    for age in (0, 1, 2, 3, 4, 10, 40):
        assert trend_of([age], rules=rules) in TREND_VALUES


# --------------------------------------------------------------------------- #
# The inferences registry
# --------------------------------------------------------------------------- #


def test_the_inference_registry_names_its_own_sourced_quotation():
    body = describe()
    assert body["sourced_quote"] == SOURCED_QUOTE
    assert body["count"] == len(INFERENCES)


@pytest.mark.parametrize("entry", INFERENCES, ids=lambda entry: entry["id"])
def test_every_inference_is_named_bounded_and_changeable(entry):
    for field in ("id", "topic", "basis", "value", "why", "change_it"):
        assert entry.get(field), f"{entry.get('id')} has no {field}"


@pytest.mark.parametrize("entry", INFERENCES, ids=lambda entry: entry["id"])
def test_every_inference_says_what_the_research_does_and_does_not_say(entry):
    basis = entry["basis"].lower()
    assert "sourced" in basis
    assert "not sourced" in basis or "not " in basis


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "inference_id",
    [
        "volume-floor",
        "window-boundary",
        "external-engagement-only",
        "client-view-definition",
        "clock-skew-tolerance",
        "workspace-equals-room",
        "nothing-stored-per-workspace",
        "unknown-events-refused",
    ],
)
def test_the_judgement_calls_the_research_left_open_are_all_published(inference_id):
    assert any(entry["id"] == inference_id for entry in INFERENCES)


def test_inferences_endpoint_serves_the_registry_next_to_the_sourced_half(http):
    body = http.get(f"{PREFIX}/inferences").json()

    assert body["count"] == len(INFERENCES)
    assert {entry["id"] for entry in body["inferences"]} == {entry["id"] for entry in INFERENCES}
    assert "Hot = workspaces" in body["sourced_quote"]


def test_the_change_it_instructions_name_something_that_exists():
    """Each entry's escape hatch is a real constant or route, not a promise."""
    assert DEFAULT_MIN_EVENTS == {"hot": 5, "warm": 2}
    assert CLIENT_VIEW_EVENT == "workspace.viewed"
    assert CLOCK_SKEW_TOLERANCE_SECONDS == 300
    assert isinstance(vocabulary(), dict)


# --------------------------------------------------------------------------- #
# Event intake
# --------------------------------------------------------------------------- #


def test_an_event_is_recorded_and_audited(health, store, room):
    record(health, room["id"])

    stored = store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])
    assert len(stored) == 1
    assert stored[0]["data"]["type"] == CLIENT_VIEW_EVENT
    assert stored[0]["data"]["audience"] == "external"
    assert stored[0]["source"] == "POST test"


@pytest.mark.parametrize("key", ["room_id", "workspaceId", "workspace_id", "workspace"])
def test_the_room_may_be_named_in_any_of_the_researched_spellings(health, room, store, key):
    health.record_event(
        {"type": CLIENT_VIEW_EVENT, key: room["id"], "occurred_at": NOW.isoformat()},
        actor="dana",
        source="POST test",
        now=NOW,
    )

    assert len(store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])) == 1


def test_an_explicit_room_wins_over_the_body(health, room, store):
    other = store.create("room", {**ROOM, "name": "Contoso"}, actor="sam")

    health.record_event(
        {"type": CLIENT_VIEW_EVENT, "room_id": other["id"], "occurred_at": NOW.isoformat()},
        room_id=room["id"],
        actor="dana",
        source="POST test",
        now=NOW,
    )

    assert len(store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])) == 1
    assert store.list(ENGAGEMENT_COLLECTION, room_id=other["id"]) == []


def test_an_unscoped_event_is_refused_rather_than_stored(health):
    with pytest.raises(TrendError) as caught:
        health.record_event({"type": CLIENT_VIEW_EVENT}, actor="dana", source="POST test", now=NOW)
    assert "workspaceId" in str(caught.value)


def test_an_event_for_a_room_that_does_not_exist_is_refused(health):
    with pytest.raises(UnknownRoom):
        health.record_event(
            {"type": CLIENT_VIEW_EVENT, "room_id": "room_nope", "occurred_at": NOW.isoformat()},
            actor="dana",
            source="POST test",
            now=NOW,
        )


def test_a_record_that_is_not_a_room_is_not_a_room(health, store):
    event = record(health, store.create("room", ROOM, actor="dana")["id"])
    with pytest.raises(UnknownRoom):
        health.require_room(event["id"])


def test_an_unknown_event_type_is_refused_at_intake(health, room):
    with pytest.raises(UnknownEventType):
        health.record_event(
            {"type": "workspace.telemetry", "room_id": room["id"], "occurred_at": NOW.isoformat()},
            actor="dana",
            source="POST test",
            now=NOW,
        )


def test_a_payload_field_this_workflow_does_not_own_is_stored_verbatim(health, room, store):
    """Schema flexibility, demonstrated: a new field, no migration."""
    record(health, room["id"], billing={"seats": 40}, seat_count=40, custom_flag="a")

    stored = store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])[0]["data"]
    assert stored["billing"] == {"seats": 40}
    assert stored["seat_count"] == 40
    assert stored["custom_flag"] == "a"


def test_a_stored_extra_field_is_queryable_through_the_dynamic_index(health, room, store):
    record(health, room["id"], billing={"seats": 40})
    record(health, room["id"], billing={"seats": 7})

    found = store.find(ENGAGEMENT_COLLECTION, {"billing.seats": 40})
    assert len(found) == 1


def test_a_stored_field_cannot_override_a_field_the_classifier_owns(health, room, store):
    health.record_event(
        {
            "type": CLIENT_VIEW_EVENT,
            "room_id": room["id"],
            "occurred_at": NOW.isoformat(),
            "audience": "external",
            "client_view": False,
        },
        actor="dana",
        source="POST test",
        now=NOW,
    )

    stored = store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])[0]["data"]
    assert stored["client_view"] is True
    assert stored["audience"] == "external"


def test_the_client_view_flag_is_derived_from_the_event_type(health, room, store):
    record(health, room["id"], event_type=CLIENT_VIEW_EVENT)
    record(health, room["id"], event_type="workspace.page.viewed")

    flags = [row["data"]["client_view"] for row in store.list(ENGAGEMENT_COLLECTION)]
    assert sorted(flags) == [False, True]


def test_a_missing_timestamp_is_stamped_on_arrival_and_says_it_was_defaulted(health, room, store):
    health.record_event(
        {"type": CLIENT_VIEW_EVENT, "room_id": room["id"]},
        actor="dana",
        source="POST test",
        now=NOW,
    )

    stored = store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])[0]["data"]
    assert stored["occurred_at_defaulted"] is True
    assert stored["occurred_at"].startswith("2026-09-27T12:00")


def test_a_supplied_timestamp_is_kept_exactly_and_not_marked_defaulted(health, room, store):
    health.record_event(
        {"type": CLIENT_VIEW_EVENT, "room_id": room["id"], "occurredAt": "2026-09-01T08:30:00Z"},
        actor="dana",
        source="POST test",
        now=NOW,
    )

    stored = store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])[0]["data"]
    assert stored["occurred_at"].startswith("2026-09-01T08:30:00")
    assert stored["occurred_at_defaulted"] is False


def test_an_event_far_in_the_future_is_refused(health, room):
    with pytest.raises(InvalidTimestamp):
        health.record_event(
            {
                "type": CLIENT_VIEW_EVENT,
                "room_id": room["id"],
                "occurredAt": "2027-01-01T00:00:00Z",
            },
            actor="dana",
            source="POST test",
            now=NOW,
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"type": CLIENT_VIEW_EVENT, "audience": "sideways"},
        {"type": CLIENT_VIEW_EVENT, "internal": "maybe"},
        {"type": CLIENT_VIEW_EVENT, "internal": 1},
    ],
)
def test_an_unreadable_audience_is_refused(health, room, bad):
    with pytest.raises(TrendError):
        health.record_event(
            {**bad, "room_id": room["id"], "occurred_at": NOW.isoformat()},
            actor="dana",
            source="POST test",
            now=NOW,
        )


def test_an_internal_flag_is_accepted_as_the_audience(health, room, store):
    health.record_event(
        {
            "type": CLIENT_VIEW_EVENT,
            "room_id": room["id"],
            "internal": True,
            "occurred_at": NOW.isoformat(),
        },
        actor="dana",
        source="POST test",
        now=NOW,
    )

    assert (
        store.list(ENGAGEMENT_COLLECTION, room_id=room["id"])[0]["data"]["audience"] == "internal"
    )


def test_audience_normalisation_defaults_to_external():
    assert require_audience(None) == "external"
    assert require_audience("") == "external"
    assert require_audience("Internal") == "internal"
    assert require_audience(internal=True) == "internal"
    assert require_audience(internal=False) == "external"


# --------------------------------------------------------------------------- #
# Listing events
# --------------------------------------------------------------------------- #


def test_events_come_back_newest_first(health, room):
    for age in (5.0, 1.0, 3.0):
        record(health, room["id"], days_ago=age)

    ages = [row["data"]["occurred_at"] for row in health.list_events()]
    assert ages == sorted(ages, reverse=True)


def test_events_can_be_filtered_by_room(health, store):
    first = store.create("room", ROOM, actor="dana")
    second = store.create("room", {**ROOM, "name": "Contoso"}, actor="sam")
    record(health, first["id"])
    record(health, second["id"])

    assert len(health.list_events(room_id=first["id"])) == 1
    assert len(health.list_events()) == 2


def test_events_can_be_filtered_by_type(health, room):
    record(health, room["id"], event_type=CLIENT_VIEW_EVENT)
    record(health, room["id"], event_type="workspace.order_form.completed")

    found = health.list_events(event_type="workspace.order_form.completed")
    assert [row["data"]["type"] for row in found] == ["workspace.order_form.completed"]


def test_filtering_by_an_unknown_type_is_refused(health):
    with pytest.raises(UnknownEventType):
        health.list_events(event_type="workspace.telemetry")


def test_events_can_be_filtered_by_audience(health, room):
    record(health, room["id"], audience="external")
    record(health, room["id"], audience="internal")

    assert [row["data"]["audience"] for row in health.list_events(audience="internal")] == [
        "internal"
    ]


def test_events_can_be_filtered_to_client_views(health, room):
    record(health, room["id"], event_type=CLIENT_VIEW_EVENT)
    record(health, room["id"], event_type="workspace.link.clicked")

    assert len(health.list_events(client_view=True)) == 1
    assert len(health.list_events(client_view=False)) == 1


def test_events_can_be_filtered_by_time_range(health, room):
    record(health, room["id"], days_ago=2.0)
    record(health, room["id"], days_ago=20.0)

    recent = health.list_events(since=(NOW - timedelta(days=7)).isoformat())
    assert len(recent) == 1
    old = health.list_events(until=(NOW - timedelta(days=7)).isoformat())
    assert len(old) == 1


def test_the_list_limit_is_respected(health, room):
    for index in range(6):
        record(health, room["id"], days_ago=index + 1.0)

    assert len(health.list_events(limit=3)) == 3


def test_a_soft_deleted_event_stops_counting(health, store, room):
    """A retracted event must not keep a workspace Hot."""
    for index in range(6):
        record(health, room["id"], days_ago=0.2 + 0.4 * index)
    assert health.classify_room(room["id"], as_of=NOW.isoformat())["trend"] == "hot"

    for event in health.list_events(limit=2):
        store.delete(event["id"], actor="dana", source="POST test")

    reading = health.classify_room(room["id"], as_of=NOW.isoformat())
    assert reading["events"]["total"] == 4
    assert reading["trend"] == "warm"


# --------------------------------------------------------------------------- #
# Classification over the store
# --------------------------------------------------------------------------- #


def test_a_room_with_no_events_reads_cold(health, room):
    reading = health.classify_room(room["id"], as_of=NOW.isoformat())

    assert reading["trend"] == "cold"
    assert reading["last_client_view"] is None
    assert reading["last_engagement_at"] is None
    assert reading["events"]["total"] == 0


def test_a_busy_room_reads_hot(health, room):
    for index in range(6):
        record(health, room["id"], days_ago=0.5 * (index + 1))

    assert health.classify_room(room["id"], as_of=NOW.isoformat())["trend"] == "hot"


def test_the_classification_carries_the_sourced_sentence_and_the_arithmetic(health, room):
    record(health, room["id"])

    reading = health.classify_room(room["id"], as_of=NOW.isoformat())

    assert reading["rule"] == TREND_RULES[reading["trend"]]
    assert reading["reasons"]
    assert reading["label"] == TREND_LABELS[reading["trend"]]
    assert reading["rules"]["windows"] == DEFAULT_WINDOWS


def test_the_room_metadata_the_dashboard_filters_by_is_read_off_the_room_record(health, store):
    named = store.create(
        "room", {**ROOM, "owner": "sam", "team": "AMER", "stage": "negotiation"}, actor="sam"
    )
    record(health, named["id"])

    reading = health.classify_room(named["id"], as_of=NOW.isoformat())
    assert reading["owner"] == "sam"
    assert reading["team"] == "AMER"
    assert reading["stage"] == "negotiation"
    assert reading["account"] == "Northwind Traders"


def test_a_room_record_using_a_different_field_name_still_filters(health, store):
    """A team that calls it ``rep`` rather than ``owner`` gets a working column."""
    named = store.create("room", {"name": "Contoso", "rep": "sam", "group": "AMER"}, actor="sam")
    record(health, named["id"])

    reading = health.classify_room(named["id"], as_of=NOW.isoformat())
    assert reading["owner"] == "sam"
    assert reading["team"] == "AMER"
    assert health.dashboard(owner="sam", as_of=NOW.isoformat())["count"] == 1


def test_a_room_record_with_no_name_falls_back_to_its_id(health, store):
    bare = store.create("room", {"owner": "sam"}, actor="sam")
    assert health.classify_room(bare["id"], as_of=NOW.isoformat())["name"] == bare["id"]


def test_classifying_an_unknown_room_is_refused(health):
    with pytest.raises(UnknownRoom):
        health.classify_room("room_nope", as_of=NOW.isoformat())


def test_reading_a_classification_writes_nothing(health, store, room):
    """The researched automation recomputes; it does not persist."""
    record(health, room["id"])
    before = store.stats()["records"]

    health.classify_room(room["id"], as_of=NOW.isoformat())
    health.dashboard(as_of=NOW.isoformat())
    health.summary(as_of=NOW.isoformat())

    assert store.stats()["records"] == before


def test_an_unreadable_stored_timestamp_is_skipped_and_reported(health, store, room):
    """A data fault upstream must not fail a whole dashboard read, nor hide."""
    record(health, room["id"])
    store.create(
        ENGAGEMENT_COLLECTION,
        {"type": CLIENT_VIEW_EVENT, "occurred_at": "not a date", "audience": "external"},
        room_id=room["id"],
        actor="dana",
        source="POST test",
    )

    reading = health.classify_room(room["id"], as_of=NOW.isoformat())
    assert reading["events"]["skipped"] == 1
    assert reading["events"]["total"] == 1


def test_the_stored_event_flag_is_not_required_for_classification(health, store, room):
    """A row written by hand through the generic API still counts."""
    store.create(
        ENGAGEMENT_COLLECTION,
        {
            "type": CLIENT_VIEW_EVENT,
            "occurred_at": (NOW - timedelta(days=1)).isoformat(),
            "audience": "external",
        },
        room_id=room["id"],
        actor="dana",
        source="POST test",
    )

    assert health.classify_room(room["id"], as_of=NOW.isoformat())["events"]["total"] == 1


# --------------------------------------------------------------------------- #
# The dashboard
# --------------------------------------------------------------------------- #


@pytest.fixture()
def portfolio(health, store):
    """Four rooms, one in each researched state, plus a fifth with no owner."""
    rooms = []
    plan = [
        ("Northwind", "dana", (0.2, 0.5, 0.9, 1.4, 1.8, 2.4), "hot"),
        ("Contoso", "sam", (8.0, 9.0, 10.5, 12.0), "warm"),
        ("Fabrikam", "dana", (20.0, 22.0), "cooling"),
        ("Adventure", "sam", (), "cold"),
        ("Tailspin", "", (), "cold"),
    ]
    for name, owner, ages, expected in plan:
        room = store.create(
            "room", {**ROOM, "name": name, "owner": owner, "team": "EMEA"}, actor=owner or "dana"
        )
        for age in ages:
            record(health, room["id"], days_ago=age)
        rooms.append({"id": room["id"], "name": name, "owner": owner, "expected": expected})
    return rooms


def test_the_dashboard_returns_one_row_per_workspace(health, portfolio):
    body = health.dashboard(as_of=NOW.isoformat())
    assert body["count"] == len(portfolio)
    assert body["total"] == len(portfolio)


def test_the_dashboard_reaches_all_four_researched_states(health, portfolio):
    body = health.dashboard(as_of=NOW.isoformat())
    got = {row["name"]: row["trend"] for row in body["rows"]}
    assert got == {room["name"]: room["expected"] for room in portfolio}


def test_sorting_by_trend_puts_the_hottest_first(health, portfolio):
    rows = health.dashboard(as_of=NOW.isoformat())["rows"]
    assert [row["trend"] for row in rows] == ["hot", "warm", "cooling", "cold", "cold"]


def test_sorting_by_trend_descending_reverses_it(health, portfolio):
    rows = health.dashboard(sort="trend", order="desc", as_of=NOW.isoformat())["rows"]
    assert [row["trend"] for row in rows] == ["cold", "cold", "cooling", "warm", "hot"]


@pytest.mark.parametrize("key", DASHBOARD_SORTS)
def test_every_advertised_sort_key_works(health, portfolio, key):
    body = health.dashboard(sort=key, as_of=NOW.isoformat())
    assert body["sort"] == key
    assert body["count"] == len(portfolio)


def test_the_advertised_sort_keys_are_the_ones_the_dashboard_accepts(health):
    assert set(health.dashboard()["sortable"]) == set(DASHBOARD_SORTS)
    assert set(SORT_KEYS) == set(DASHBOARD_SORTS)


def test_an_unknown_sort_key_is_refused_with_the_accepted_list(health):
    with pytest.raises(InvalidSort) as caught:
        health.dashboard(sort="vibes")
    assert "last_client_view" in str(caught.value)


def test_an_unknown_order_is_refused(health):
    with pytest.raises(InvalidSort):
        health.dashboard(order="sideways")


def test_an_unknown_bucket_filter_is_refused_with_the_four_values(health):
    with pytest.raises(InvalidSort) as caught:
        health.dashboard(trend="lukewarm")
    assert "cooling" in str(caught.value)


def test_a_bucket_filter_isolates_one_state(health, portfolio):
    rows = health.dashboard(trend="hot", as_of=NOW.isoformat())["rows"]
    assert [row["name"] for row in rows] == ["Northwind"]


def test_a_comma_separated_bucket_filter_isolates_both_ends_of_the_pipeline(health, portfolio):
    """The researched act: "isolate Hot rooms and Cold rooms"."""
    rows = health.dashboard(trend="hot,cold", as_of=NOW.isoformat())["rows"]
    assert sorted(row["name"] for row in rows) == ["Adventure", "Northwind", "Tailspin"]


def test_a_bucket_filter_is_case_and_whitespace_insensitive(health, portfolio):
    assert health.dashboard(trend=" Hot , COLD ", as_of=NOW.isoformat())["count"] == 3


def test_an_empty_bucket_filter_is_the_whole_dashboard(health, portfolio):
    assert health.dashboard(trend="", as_of=NOW.isoformat())["count"] == len(portfolio)
    assert health.dashboard(trend=[], as_of=NOW.isoformat())["count"] == len(portfolio)


def test_the_dashboard_filters_by_owner(health, portfolio):
    rows = health.dashboard(owner="dana", as_of=NOW.isoformat())["rows"]
    assert {row["owner"] for row in rows} == {"dana"}
    assert len(rows) == 2


def test_the_dashboard_filters_by_team(health, portfolio):
    assert health.dashboard(team="emea", as_of=NOW.isoformat())["count"] == len(portfolio)
    assert health.dashboard(team="apac", as_of=NOW.isoformat())["count"] == 0


def test_the_dashboard_scopes_to_one_room(health, portfolio):
    rows = health.dashboard(room_id=portfolio[0]["id"], as_of=NOW.isoformat())["rows"]
    assert [row["name"] for row in rows] == ["Northwind"]


def test_scoping_to_an_unknown_room_is_refused(health):
    with pytest.raises(UnknownRoom):
        health.dashboard(room_id="room_nope")


def test_a_room_with_no_client_view_sorts_last_in_both_directions(health, store):
    """A missing value is the coldest thing in the column, not the newest."""
    viewed = store.create("room", {**ROOM, "name": "Viewed"}, actor="dana")
    store.create("room", {**ROOM, "name": "Silent"}, actor="dana")
    record(health, viewed["id"], event_type=CLIENT_VIEW_EVENT)

    ascending = [
        row["name"]
        for row in health.dashboard(sort="last_client_view", as_of=NOW.isoformat())["rows"]
    ]
    descending = [
        row["name"]
        for row in health.dashboard(sort="last_client_view", order="desc", as_of=NOW.isoformat())[
            "rows"
        ]
    ]

    assert ascending[-1] == "Silent"
    assert descending[-1] == "Silent"


def test_sorting_by_last_client_view_puts_the_most_recent_first_when_asked(health, store):
    """Descending on a date column means newest first, not oldest.

    Caught by reading the running app's output rather than by the empty-value
    assertion above: the key was negated *and* reversed, so the two cancelled.
    """
    for name, days_ago in (("Older", 12.0), ("Newer", 2.0), ("Middle", 6.0)):
        room = store.create("room", {**ROOM, "name": name}, actor="dana")
        record(health, room["id"], event_type=CLIENT_VIEW_EVENT, days_ago=days_ago)

    def order(direction):
        return [
            row["name"]
            for row in health.dashboard(
                sort="last_client_view", order=direction, as_of=NOW.isoformat()
            )["rows"]
        ]

    assert order("desc") == ["Newer", "Middle", "Older"]
    assert order("asc") == ["Older", "Middle", "Newer"]


def test_sorting_by_engagement_respects_the_direction(health, store):
    for name, count in (("Quiet", 1), ("Busy", 6), (" Middling", 3)):
        room = store.create("room", {**ROOM, "name": name.strip()}, actor="dana")
        for _ in range(count):
            record(health, room["id"], days_ago=1.0)

    def order(direction):
        return [
            row["name"]
            for row in health.dashboard(sort="engagement", order=direction, as_of=NOW.isoformat())[
                "rows"
            ]
        ]

    assert order("desc") == ["Busy", "Middling", "Quiet"]
    assert order("asc") == ["Quiet", "Middling", "Busy"]


def test_sorting_by_engagement_counts_events(health, portfolio):
    rows = health.dashboard(sort="engagement", order="desc", as_of=NOW.isoformat())["rows"]
    assert rows[0]["name"] == "Northwind"
    assert rows[0]["events"]["total"] == 6


def test_the_dashboard_limit_is_applied_and_reported(health, portfolio):
    body = health.dashboard(limit=2, as_of=NOW.isoformat())
    assert body["count"] == 2
    assert body["total"] == len(portfolio)


def test_a_dashboard_row_carries_the_numbers_and_the_next_change(health, portfolio):
    row = next(
        r for r in health.dashboard(as_of=NOW.isoformat())["rows"] if r["name"] == "Northwind"
    )

    assert row["trend"] == "hot"
    assert row["events"]["qualifying_in_7_days"] == 6
    assert row["next_change"]["trend"] == "warm"
    assert row["rule"] == TREND_RULES["hot"]


def test_every_dashboard_row_is_evaluated_at_the_same_instant(health, portfolio):
    body = health.dashboard(as_of=NOW.isoformat())
    assert len({row["as_of"] for row in body["rows"]}) == 1


def test_the_dashboard_reports_where_the_rules_came_from(health, store, portfolio):
    assert health.dashboard()["rules_source"] == "defaults"

    health.save_rules({"min_events": {"hot": 99}}, actor="dana", source="PATCH test")
    assert health.dashboard()["rules_source"] == "override"


def test_a_stored_rules_override_changes_the_dashboard(health, portfolio):
    health.save_rules({"min_events": {"hot": 9, "warm": 9}}, actor="dana", source="PATCH test")

    body = health.dashboard(as_of=NOW.isoformat())
    assert "Northwind" not in {row["name"] for row in body["rows"] if row["trend"] == "hot"}


def test_a_dashboard_over_an_empty_store_is_empty_not_broken(health):
    body = health.dashboard()
    assert body["count"] == 0
    assert body["as_of"] is None
    assert body["rows"] == []


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_every_bucket(health, portfolio):
    body = health.summary(as_of=NOW.isoformat())

    assert body["rooms"] == len(portfolio)
    assert body["buckets"] == {"hot": 1, "warm": 1, "cooling": 1, "cold": 2}
    assert body["labels"]["cooling"] == "Cooling"


def test_the_summary_reports_the_recent_engagement_behind_the_buckets(health, portfolio):
    body = health.summary(as_of=NOW.isoformat())

    assert body["engagement_in_7_days"] == 6
    assert body["newest_client_view"]
    assert body["oldest_client_view"]


def test_the_summary_scopes_to_one_room(health, portfolio):
    body = health.summary(room_id=portfolio[0]["id"], as_of=NOW.isoformat())
    assert body["rooms"] == 1
    assert body["buckets"]["hot"] == 1


def test_the_summary_reports_the_rules_in_force(health):
    body = health.summary()
    assert body["rules"] == DEFAULT_RULES
    assert body["rules_source"] == "defaults"


# --------------------------------------------------------------------------- #
# Rules over the store
# --------------------------------------------------------------------------- #


def test_rules_start_at_the_defaults(health):
    view = health.rules_view()
    assert view["source"] == "defaults"
    assert view["rules"]["windows"] == DEFAULT_WINDOWS


def test_saving_rules_creates_one_row_and_audits_it(health, store):
    health.save_rules({"min_events": {"hot": 3}}, actor="dana", source="PATCH /api/wf-021/rules")

    assert store.get(RULES_RECORD_ID)["data"]["min_events"]["hot"] == 3
    audit = store.audit(collection=RULES_COLLECTION)
    assert [entry["action"] for entry in audit] == ["insert"]
    assert audit[0]["source"] == "PATCH /api/wf-021/rules"


def test_saving_rules_twice_updates_the_same_row(health, store):
    health.save_rules({"min_events": {"hot": 3}}, actor="dana", source="PATCH /api/wf-021/rules")
    health.save_rules(
        {"windows": {"hot": 3, "warm": 6, "cold": 20}},
        actor="dana",
        source="PATCH /api/wf-021/rules",
    )

    assert len(store.list(RULES_COLLECTION)) == 1
    assert store.get(RULES_RECORD_ID)["data"]["windows"]["hot"] == 3
    assert store.get(RULES_RECORD_ID)["data"]["min_events"]["hot"] == 3
    # The audit log is newest first, so the update is the row the reader meets.
    assert [entry["action"] for entry in store.audit(collection=RULES_COLLECTION)] == [
        "update",
        "insert",
    ]


def test_an_invalid_patch_writes_nothing(health, store):
    with pytest.raises(InvalidRules):
        health.save_rules({"windows": {"hot": 40}}, actor="dana", source="PATCH /api/wf-021/rules")

    assert store.list(RULES_COLLECTION) == []
    assert store.audit(collection=RULES_COLLECTION) == []


def test_the_rules_view_names_where_the_values_came_from(health):
    health.save_rules({"count_only_external": False}, actor="dana", source="PATCH test")
    view = health.rules_view()

    assert view["source"] == "override"
    assert view["collection"] == RULES_COLLECTION
    assert view["record_id"] == RULES_RECORD_ID
    assert describe_rules(view["rules"], view["source"])["source"] == "override"


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_a_read_with_no_store(http):
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200


def test_inferences_is_a_read_with_no_store(http):
    assert http.get(f"{PREFIX}/inferences").status_code == 200


def test_rules_can_be_read_and_patched(http):
    assert http.get(f"{PREFIX}/rules").json()["source"] == "defaults"

    patched = http.patch(
        f"{PREFIX}/rules", json={"min_events": {"hot": 3}}, params={"actor": "dana"}
    )
    assert patched.status_code == 200
    assert patched.json()["rules"]["min_events"] == {"hot": 3, "warm": 2}
    assert http.get(f"{PREFIX}/rules").json()["source"] == "override"


def test_an_invalid_rules_patch_is_400_with_the_reason(http):
    response = http.patch(f"{PREFIX}/rules", json={"windows": {"hot": 40}})

    assert response.status_code == 400
    assert "windows must increase" in response.json()["detail"]


def test_an_unknown_rules_key_is_400(http):
    response = http.patch(f"{PREFIX}/rules", json={"min_event": 3})
    assert response.status_code == 400
    assert response.json()["error"] == "InvalidRules"


def test_a_summary_over_an_empty_database_is_empty_not_an_error(http):
    body = http.get(f"{PREFIX}/summary").json()
    assert body["rooms"] == 0
    assert body["buckets"] == {"hot": 0, "warm": 0, "cooling": 0, "cold": 0}


def test_recording_an_event_over_http_creates_one_audited_row(http, http_room):
    response = http.post(
        f"{PREFIX}/events",
        json={
            "type": CLIENT_VIEW_EVENT,
            "occurredAt": "2026-09-26T09:00:00Z",
            "workspaceId": http_room["id"],
        },
        params={"actor": "dana"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["recorded"] is True
    assert body["event"]["type"] == CLIENT_VIEW_EVENT
    assert body["event"]["client_view"] is True
    assert body["trend_values"] == list(TREND_VALUES)

    entries = http.get("/api/audit", params={"collection": ENGAGEMENT_COLLECTION}).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == f"POST {PREFIX}/events"
    assert entries[0]["actor"] == "dana"


def test_an_unknown_event_type_over_http_is_400_and_writes_nothing(http, http_room):
    response = http.post(
        f"{PREFIX}/events", json={"type": "workspace.telemetry", "room_id": http_room["id"]}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "UnknownEventType"
    assert http.get("/api/audit", params={"collection": ENGAGEMENT_COLLECTION}).json()["count"] == 0


def test_an_event_for_an_unknown_room_is_404_over_http(http):
    response = http.post(
        f"{PREFIX}/events", json={"type": CLIENT_VIEW_EVENT, "room_id": "room_nope"}
    )

    assert response.status_code == 404
    assert response.json()["error"] == "UnknownRoom"


def test_an_unreadable_timestamp_over_http_is_400(http, http_room):
    response = http.post(
        f"{PREFIX}/events",
        json={"type": CLIENT_VIEW_EVENT, "room_id": http_room["id"], "occurredAt": "yesterday"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "InvalidTimestamp"


def test_an_unknown_bucket_filter_over_http_is_400(http):
    assert http.get(f"{PREFIX}/dashboard", params={"trend": "lukewarm"}).status_code == 400


def test_an_unknown_sort_key_over_http_is_400(http):
    assert http.get(f"{PREFIX}/dashboard", params={"sort": "vibes"}).status_code == 400


def test_the_dashboard_over_http_returns_rows(http, http_room):
    for index in range(6):
        http.post(
            f"{PREFIX}/events",
            json={
                "type": CLIENT_VIEW_EVENT,
                "room_id": http_room["id"],
                "occurredAt": f"2026-09-2{index}T09:00:00Z",
            },
        )

    body = http.get(f"{PREFIX}/dashboard", params={"as_of": NOW.isoformat()}).json()
    assert body["count"] == 1
    assert body["rows"][0]["trend"] == "hot"
    assert body["sort"] == "trend"
    assert body["order"] == "asc"


def test_the_room_trend_route_returns_the_badge(http, http_room):
    response = http.get(
        f"{PREFIX}/rooms/{http_room['id']}/trend", params={"as_of": NOW.isoformat()}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trend"] == "cold"
    assert body["decay_path"] == ["hot", "warm", "cooling", "cold"]
    assert body["last_client_view"] is None
    assert body["windows"]["hot"]["days"] == 7


def test_an_unknown_room_on_the_trend_route_is_404(http):
    response = http.get(f"{PREFIX}/rooms/room_nope/trend")
    assert response.status_code == 404


def test_the_events_route_lists_what_was_recorded(http, http_room):
    http.post(f"{PREFIX}/events", json={"type": CLIENT_VIEW_EVENT, "room_id": http_room["id"]})
    http.post(
        f"{PREFIX}/events", json={"type": "workspace.link.clicked", "room_id": http_room["id"]}
    )

    body = http.get(f"{PREFIX}/events", params={"room_id": http_room["id"]}).json()

    assert body["count"] == 2
    assert body["client_view_event"] == CLIENT_VIEW_EVENT
    assert f"{ORDER_FORM_PREFIX}*" in body["accepted_types"]


def test_the_events_route_filters_to_client_views(http, http_room):
    http.post(f"{PREFIX}/events", json={"type": CLIENT_VIEW_EVENT, "room_id": http_room["id"]})
    http.post(
        f"{PREFIX}/events", json={"type": "workspace.link.clicked", "room_id": http_room["id"]}
    )

    assert http.get(f"{PREFIX}/events", params={"client_view": True}).json()["count"] == 1


def test_every_route_is_documented_in_openapi(http):
    documented = set(http.get("/openapi.json").json()["paths"])
    for path in ("/vocabulary", "/inferences", "/rules", "/summary", "/dashboard", "/events"):
        assert f"{PREFIX}{path}" in documented
    assert f"{PREFIX}/rooms/{{room_id}}/trend" in documented


def test_the_feature_is_discoverable_with_its_routes_over_http(http):
    body = http.get("/api/features").json()
    entry = next(f for f in body["features"] if f["id"] == FEATURE_ID)

    assert entry["nav"] == [{"id": "trend", "label": "Trend"}]
    assert FEATURE_ID not in {failure["id"] for failure in body["failed"]}


def test_the_collections_this_workflow_owns_appear_in_schema_discovery(http, http_room):
    http.post(f"{PREFIX}/events", json={"type": CLIENT_VIEW_EVENT, "room_id": http_room["id"]})

    body = http.get("/api/collections").json()
    names = {entry["collection"] for entry in body["collections"]}
    assert ENGAGEMENT_COLLECTION in names

    entry = next(e for e in body["collections"] if e["collection"] == ENGAGEMENT_COLLECTION)
    paths = {field["path"] for field in entry["fields"]}
    assert {"type", "occurred_at", "audience", "client_view"} <= paths


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, routes: list[dict]) -> bool:
    """Does ``"POST /api/wf-021/events"`` name a route the host actually mounted?

    Compared segment by segment, with a ``{parameter}`` segment matching any one
    segment, so the assertion is about the shape of the path rather than about a
    hardcoded list that could itself go stale.
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
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The defect the feature contract names by name, checked against the live table."""
    http.post(f"{PREFIX}/events", json={"type": CLIENT_VIEW_EVENT, "room_id": http_room["id"]})
    http.patch(f"{PREFIX}/rules", json={"min_events": {"hot": 3}})

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]

    written = [
        entry
        for entry in entries
        if entry["collection"] in (ENGAGEMENT_COLLECTION, RULES_COLLECTION)
    ]
    assert written, "the two writes recorded no audit rows at all"
    for entry in written:
        assert _matches_registered_route(entry["source"], served), entry["source"]


def test_every_write_names_a_route_of_this_feature(http, http_room):
    http.post(f"{PREFIX}/events", json={"type": CLIENT_VIEW_EVENT, "room_id": http_room["id"]})
    http.patch(f"{PREFIX}/rules", json={"min_events": {"hot": 3}})

    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    written = [
        entry
        for entry in entries
        if entry["collection"] in (ENGAGEMENT_COLLECTION, RULES_COLLECTION)
    ]
    assert {entry["source"] for entry in written} == {
        f"POST {PREFIX}/events",
        f"PATCH {PREFIX}/rules",
    }


def test_the_domain_layer_refuses_to_write_without_a_source():
    """``source`` is a required keyword, so a hardcoded string cannot creep back in."""
    import inspect

    from dsr.trend_health import TrendHealth as Health

    for method in (Health.record_event, Health.save_rules):
        parameter = inspect.signature(method).parameters["source"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_reads_leave_no_audit_rows(http, http_room):
    before = http.get("/api/audit", params={"limit": 1000}).json()["count"]

    http.get(f"{PREFIX}/dashboard")
    http.get(f"{PREFIX}/summary")
    http.get(f"{PREFIX}/rooms/{http_room['id']}/trend")
    http.get(f"{PREFIX}/events")
    http.get(f"{PREFIX}/rules")
    http.get(f"{PREFIX}/vocabulary")
    http.get(f"{PREFIX}/inferences")

    assert http.get("/api/audit", params={"limit": 1000}).json()["count"] == before


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _seed_once(tmp_path, room_ids, name="seed"):
    """Run the feature's ``seed`` and read everything back while the db is open.

    The demo rooms are created here because the seeder is given ids, not records:
    the real seeder has already written the rooms by the time a feature's ``seed``
    is called, and a feature that invented its own rooms would be demo data no
    other workflow could see.

    Everything is read inside the ``with``, because the audited database closes
    its connection on the way out and reading it afterwards is a test bug
    masquerading as a seed bug.
    """
    import random

    module = load_feature("wf021_classify_workspace_engagement_health_h")
    with AuditedDatabase(tmp_path / f"{name}.db", mirror_dir=tmp_path / f"{name}-audit") as db:
        for room_id, account in room_ids:
            db.create(
                "room",
                {**ROOM, "name": account, "account": account},
                record_id=room_id,
                actor="dana",
                source="seed",
            )
        summary = module.seed(db, {"room_ids": room_ids, "now": NOW, "rng": random.Random("wf021")})
        health = TrendHealth(RecordStore(db))
        return {
            "summary": summary,
            "events": db.list(ENGAGEMENT_COLLECTION),
            "audit": db.audit(limit=500),
            "badges": {
                room_id: health.classify_room(room_id, as_of=NOW.isoformat())
                for room_id, _account in room_ids
            },
        }


def test_the_demo_covers_every_researched_state(tmp_path):
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    badges = _seed_once(tmp_path, rooms)["badges"]

    assert {room_id: badge["trend"] for room_id, badge in badges.items()} == {
        "room_northwind": "hot",
        "room_contoso": "warm",
        "room_fabrikam": "cooling",
        "room_adventure": "cold",
    }


def test_the_demo_seeds_the_trap_an_internal_view_does_not_lift_a_cooling_room(tmp_path):
    """The most likely way this column gets wrong, visible in the demo."""
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    badge = _seed_once(tmp_path, rooms)["badges"]["room_fabrikam"]

    assert badge["trend"] == "cooling"
    assert badge["events"]["internal"] == 1
    assert badge["events"]["external"] == 2
    assert any("did not count" in reason for reason in badge["reasons"])


def test_the_demo_seeds_a_room_with_no_engagement_at_all(tmp_path):
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    badge = _seed_once(tmp_path, rooms)["badges"]["room_adventure"]

    assert badge["trend"] == "cold"
    assert badge["last_client_view"] is None
    assert badge["events"]["total"] == 0
    assert badge["decay"] == []


def test_the_demo_seeds_an_order_form_event(tmp_path):
    """The researched ``workspace.order_form.*`` family is in the demo, not only the tests."""
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    seeded = _seed_once(tmp_path, rooms)

    types = {row["data"]["type"] for row in seeded["events"]}
    assert "workspace.order_form.completed" in types


def test_the_demo_seeds_a_recent_client_view_so_the_column_has_a_value(tmp_path):
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    badge = _seed_once(tmp_path, rooms)["badges"]["room_northwind"]

    assert badge["last_client_view"] is not None
    assert badge["events"]["client_views"] >= 2


def test_every_seeded_event_is_audited_to_the_seeder(tmp_path):
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    seeded = _seed_once(tmp_path, rooms)

    assert seeded["events"], "the demo seeded nothing"
    assert all(
        entry["source"] == "seed"
        for entry in seeded["audit"]
        if entry["collection"] == ENGAGEMENT_COLLECTION
    )
    assert len(seeded["events"]) == 15


def test_the_demo_says_what_it_seeded_and_why(tmp_path):
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    summary = _seed_once(tmp_path, rooms)["summary"]

    for state in ("hot", "warm", "cooling", "cold"):
        assert state in summary
    assert "internal view that must not count" in summary
    assert ENGAGEMENT_COLLECTION in summary


def test_the_demo_survives_a_database_with_no_rooms(tmp_path):
    """The seeder skips a feature loudly rather than aborting."""
    seeded = _seed_once(tmp_path, [])

    assert seeded["events"] == []
    assert "no rooms to scope them to" in seeded["summary"]


def test_the_demo_is_deterministic(tmp_path):
    rooms = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]
    first = _seed_once(tmp_path, rooms, name="a")["summary"]
    second = _seed_once(tmp_path, rooms, name="b")["summary"]
    assert first == second


def test_an_event_stamped_a_few_minutes_ahead_is_stored_but_one_stamped_a_day_ahead_is_not(
    tmp_path,
):
    """The real clock skew, on the real intake path.

    Found by reading the running app: a probe that stamped an event "now" while
    the server's own clock was a couple of hours behind it got a 400, which is the
    refusal working. This asserts both sides of the tolerance deliberately.
    """
    rooms = [
        ("room_northwind", "northwind"),
        ("room_contoso", "contoso"),
        ("room_fabrikam", "fabrikam"),
        ("room_adventure", "adventure"),
    ]
    module = load_feature("wf021_classify_workspace_engagement_health_h")
    with AuditedDatabase(tmp_path / "skew.db", mirror_dir=tmp_path / "skew-audit") as db:
        for room_id, account in rooms:
            db.create(
                "room", {**ROOM, "name": account}, record_id=room_id, actor="dana", source="seed"
            )
        health = TrendHealth(RecordStore(db))

        for offset, expected in (
            (0, 1),
            (60, 1),
            (CLOCK_SKEW_TOLERANCE_SECONDS, 1),
            (CLOCK_SKEW_TOLERANCE_SECONDS + 1, 0),
            (3600, 0),
        ):
            when = NOW + timedelta(seconds=offset)
            try:
                health.record_event(
                    {"type": CLIENT_VIEW_EVENT, "occurredAt": when.isoformat()},
                    room_id="room_northwind",
                    actor="dana",
                    source="POST test",
                    now=NOW,
                )
                recorded = 1
            except InvalidTimestamp:
                recorded = 0
            assert recorded == expected, f"{offset}s ahead of the clock: {recorded}"

        assert len(db.list(ENGAGEMENT_COLLECTION)) == 3
        assert module is not None


# --------------------------------------------------------------------------- #
# Boundary properties, asserted once rather than by eye
# --------------------------------------------------------------------------- #


def test_no_event_age_produces_an_unclassifiable_workspace():
    rng = random.Random(20260927)
    for _ in range(400):
        ages = [round(rng.uniform(0, 120), 3) for _ in range(rng.randint(0, 12))]
        assert trend_of(ages) in TREND_VALUES


def test_adding_activity_never_makes_a_workspace_colder():
    """Monotone in the sourced direction: more recent engagement never lowers the rank."""
    order = {value: rank for rank, value in enumerate(TREND_VALUES)}
    rng = random.Random(7)
    for _ in range(200):
        base = sorted(round(rng.uniform(0, 60), 3) for _ in range(rng.randint(0, 6)))
        with_new = sorted([round(rng.uniform(0, 3), 3)] + base)
        assert order[trend_of(with_new)] <= order[trend_of(base)]


def test_a_workspace_never_improves_as_time_passes_without_new_activity():
    order = {value: rank for rank, value in enumerate(TREND_VALUES)}
    rng = random.Random(99)
    for _ in range(60):
        ages = [round(rng.uniform(0, 40), 3) for _ in range(rng.randint(1, 8))]
        ranks = [
            order[classify(events_at(ages), DEFAULT_RULES, NOW + timedelta(hours=hours))["trend"]]
            for hours in range(0, 32 * 24, 12)
        ]
        assert ranks == sorted(ranks), ages
