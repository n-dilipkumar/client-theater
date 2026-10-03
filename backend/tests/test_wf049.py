"""Tests for WF-049: monitor integration health and remaining API quota.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-049.md``, whose flow is four
steps::

    1. Operator opens the room's Integrations → <connection> → Monitoring
       dashboard.
    2. Dashboard shows remaining daily + burst quota, sync success rate, mean
       latency, error-class breakdown (validation / throttle / auth /
       vendor-5xx), and the live change-stream lag.
    3. Quota is read from the vendor's own surfaces.
    4. If a connector is starved, the operator lowers its concurrency or
       pauses it from the same page.

So the suite is organised around the researched surfaces and around the
transformation the research describes - "heterogeneous vendor quota models
(daily+burst, per-10s, per-app, per-account) normalise into one 'remaining
today / remaining this window' pair" - and it is explicit about which
assertions are sourced and which are this build's reading:

* **the surfaces, per vendor** - Salesforce's ``api-usage=<used>/<total>``
  header and its ``/limits/`` table with the connector's declared row, the
  HubSpot rate-limit headers with the sourced OAuth absence of the daily half,
  the account-information endpoint's two readable shapes, and the Dataverse
  refusal that quotes the research's own gap;
* **the transformation** - every observation carries the pair, unknown is
  never zero, and a lying surface (remaining > max) is refused;
* **the telemetry** - success rate, mean latency and the four researched
  error classes over one window, with the connector's own class word winning
  over the status-derived one;
* **the lag** - direct or computed, clamped at zero, latest wins;
* **the automations** - budget rules fire below, lag rules exceed, the
  cooldown suppresses the second poll, and the fire is recorded on the rule;
* **the controls** - pause and concurrency from the same page, and a paused
  connector drops out of evaluation;
* **the HTTP surface** - the mounted route set, the audit-source rule, and
  the room scoping the brief asks for.

The inferences are not tested as if they were research. They are tested as the
*mechanism* each entry describes - a default a parameter reverses, a bound
that refuses, a fallback the caller can name - and ``INFERENCES`` is asserted
against what the code actually does, so the list cannot drift away from the
behaviour silently.
"""

from __future__ import annotations

import inspect
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.integ_monitor import (
    CHANGE_COLLECTION,
    CONNECTOR_COLLECTION,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_POLICIES,
    DEFAULT_WINDOW_SECONDS,
    FIRE_HISTORY_LIMIT,
    INFERENCES,
    QUOTA_COLLECTION,
    RULE_COLLECTION,
    STREAM_COLLECTION,
    TELEMETRY_COLLECTION,
    IntegrationMonitor,
    InvalidPayload,
    InvalidQuotaSurface,
    InvalidRule,
    InvalidTelemetry,
    UnknownConnector,
    UnknownRoom,
    UnknownRule,
    UnknownVendor,
    aggregate,
    check_lag,
    check_rule,
    check_samples,
    class_from_status,
    evaluate_rules,
    metric_value,
    normalise,
    record_fire,
    vocabulary,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Written out here rather than imported, so a renamed
#: prefix fails a test instead of following silently.
PREFIX = "/api/wf-049"

FEATURE_ID = "wf-049-monitor-integration-health-and-remaini"
MODULE = "wf049_monitor_integration_health_and_remaini"

#: Every route this feature mounts. A change to the surface has to be made here
#: deliberately, which is the point of asserting a set.
ROUTES = {
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("GET", "/connectors"),
    ("POST", "/connectors"),
    ("GET", "/connectors/{connector_id}"),
    ("PATCH", "/connectors/{connector_id}"),
    ("DELETE", "/connectors/{connector_id}"),
    ("POST", "/connectors/{connector_id}/quota"),
    ("GET", "/connectors/{connector_id}/quota"),
    ("POST", "/connectors/{connector_id}/telemetry"),
    ("GET", "/connectors/{connector_id}/telemetry"),
    ("POST", "/connectors/{connector_id}/stream"),
    ("GET", "/rooms/{room_id}/dashboard"),
    ("POST", "/rooms/{room_id}/change-tracking"),
    ("GET", "/rooms/{room_id}/change-tracking"),
    ("GET", "/rooms/{room_id}/alerts"),
    ("POST", "/rooms/{room_id}/alerts/rules"),
    ("PATCH", "/alerts/rules/{rule_id}"),
    ("DELETE", "/alerts/rules/{rule_id}"),
    ("POST", "/rooms/{room_id}/alerts/evaluate"),
}

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
}


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


class Clock:
    """A clock the tests can move, so cooldowns and windows are testable."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def iso(self) -> str:
        return self.now.isoformat(timespec="milliseconds")

    def advance(self, **kwargs: timedelta) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf049.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def clock():
    return Clock(NOW)


@pytest.fixture()
def monitor(store, clock):
    return IntegrationMonitor(store, now=clock.iso)


@pytest.fixture()
def connector(monitor, room, clock):
    """A registered, unpaused Salesforce connector, the healthy demo shape."""
    return monitor.register_connector(
        {"vendor": "salesforce", "label": "Northwind — Salesforce", "limit_name": "API Requests"},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/connectors",
    )


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, on the shared app.

    The plugin host mounts every feature onto one app, so this exercises the
    real mounted route rather than a private test app - which is what makes the
    audit-source assertion below meaningful.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf049.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


# --------------------------------------------------------------------------- #
# The sourced vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_names_the_researched_error_classes():
    assert vocabulary()["error_classes"] == ["validation", "throttle", "auth", "vendor_5xx"]


def test_the_vocabulary_names_the_researched_channels():
    assert vocabulary()["channels"] == ["slack", "email", "webhook"]


def test_the_vocabulary_names_the_researched_gaps():
    """The research's own gaps are published, not hidden."""
    gaps = vocabulary()["quotas"]["gaps"]
    assert any("Service Protection" in gap for gap in gaps)
    assert any("not enumerated" in gap for gap in gaps)


def test_the_vocabulary_serves_a_surface_with_the_sentence_behind_it():
    surfaces = {s["id"]: s for s in vocabulary()["quotas"]["surfaces"]["salesforce"]}
    assert "limits_resource" in surfaces
    assert "remaining allocation" in surfaces["limits_resource"]["sourced"]


def test_every_vendor_publishes_a_model_note():
    notes = vocabulary()["quotas"]["model_note"]
    assert notes["dataverse"] == "no numeric limits sourced - see the research's own gap"


# --------------------------------------------------------------------------- #
# The transformation: Salesforce
# --------------------------------------------------------------------------- #


def test_a_sforce_limit_info_header_normalises_into_the_daily_half():
    reading = normalise("salesforce", "limit_info_header", {"header": "api-usage=123/500000"})
    assert reading["daily"]["known"] is True
    assert reading["daily"]["used"] == 123
    assert reading["daily"]["remaining"] == 499877
    assert reading["daily"]["remaining_pct"] == round(100 * 499877 / 500000, 2)
    assert reading["window"]["known"] is False


def test_the_header_ignores_segments_the_source_set_does_not_document():
    reading = normalise(
        "salesforce", "limit_info_header", {"header": "api-usage=10/500; callouts=1/10"}
    )
    assert reading["daily"]["remaining"] == 490
    assert any("callouts=1/10" in note for note in reading["daily"]["notes"])


def test_a_header_without_api_usage_is_refused_with_the_expected_shape():
    with pytest.raises(InvalidQuotaSurface) as raised:
        normalise("salesforce", "limit_info_header", {"header": "garbage=1/2"})
    assert "api-usage=<used>/<total>" in str(raised.value)


def test_the_limits_resource_picks_the_declared_row():
    body = {
        "limits": [
            {"name": "API Requests", "max": 100000, "remaining": 91240},
            {"name": "DataStorageMB", "max": 1024, "remaining": 890},
        ]
    }
    reading = normalise("salesforce", "limits_resource", body, limit_name="API Requests")
    assert reading["daily"]["remaining"] == 91240
    assert reading["daily"]["max"] == 100000


def test_the_limits_resource_keeps_the_whole_table_verbatim():
    body = {
        "limits": [
            {"name": "API Requests", "max": 100000, "remaining": 91240},
            {"name": "DataStorageMB", "max": 1024, "remaining": 890},
        ]
    }
    reading = normalise("salesforce", "limits_resource", body, limit_name="API Requests")
    assert reading["daily"]["remaining"] == 91240
    # The whole table is published, not only the row that forms the pair, so the
    # rows a connector could have read stay auditable after the fact. The second
    # row is the assertion that carries the weight: a version that published only
    # the matched row would still pass every other test in this file.
    #
    # This assertion used to be absent. The test kept its name, asserted only the
    # pair, and carried a comment saying the verbatim table was "checked in the
    # engine test for the stored shape". No engine test checked it. The branch in
    # quota.py computed these rows and dropped them, and nothing noticed until
    # ruff reported F841 on the assignment.
    assert reading["daily"]["verbatim"] == [
        {"name": "API Requests", "max": 100000, "remaining": 91240},
        {"name": "DataStorageMB", "max": 1024, "remaining": 890},
    ]


def test_an_unmatched_limit_name_is_unknown_with_the_rows_named():
    body = {"limits": [{"name": "DailyApiRequests", "max": 100, "remaining": 90}]}
    reading = normalise("salesforce", "limits_resource", body, limit_name="API Requests")
    assert reading["daily"]["known"] is False
    assert "DailyApiRequests" in reading["daily"]["notes"][0]


def test_the_limits_resource_refuses_a_body_without_rows():
    with pytest.raises(InvalidQuotaSurface):
        normalise("salesforce", "limits_resource", {"limits": []}, limit_name="API Requests")


def test_the_event_usage_metric_rows_are_kept_verbatim_and_answer_unknown():
    """The research names the object and quotes no record shape, so no pair."""
    reading = normalise(
        "salesforce",
        "event_usage_metric",
        {"body": {"records": [{"usage_type": "API_EVENT_DELIVERY", "count": 42}]}},
    )
    assert reading["daily"]["known"] is False
    assert reading["daily"]["verbatim"][0]["count"] == 42


# --------------------------------------------------------------------------- #
# The transformation: HubSpot
# --------------------------------------------------------------------------- #


def test_the_hubspot_headers_normalise_both_halves():
    reading = normalise(
        "hubspot",
        "rate_limit_headers",
        {
            "headers": {
                "X-HubSpot-RateLimit-Max": "190",
                "X-HubSpot-RateLimit-Remaining": "95",
                "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
                "X-HubSpot-RateLimit-Daily": "250000",
                "X-HubSpot-RateLimit-Daily-Remaining": "1820",
            }
        },
    )
    assert reading["window"]["known"] is True
    assert reading["window"]["remaining"] == 95
    assert reading["window"]["window_seconds"] == 10
    assert reading["daily"]["remaining"] == 1820


def test_hubspot_headers_are_read_case_insensitively():
    reading = normalise(
        "hubspot",
        "rate_limit_headers",
        {
            "headers": {
                "x-hubspot-ratelimit-max": "190",
                "x-hubspot-ratelimit-remaining": "189",
            }
        },
    )
    assert reading["window"]["remaining"] == 189


def test_an_oauth_response_reads_unknown_daily_not_zero():
    """Sourced: the Daily headers are not included for OAuth. Unknown, never 0."""
    reading = normalise(
        "hubspot",
        "rate_limit_headers",
        {
            "headers": {
                "X-HubSpot-RateLimit-Max": "190",
                "X-HubSpot-RateLimit-Remaining": "95",
                "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            }
        },
    )
    assert reading["daily"]["known"] is False
    assert reading["daily"]["remaining"] is None
    assert reading["daily"]["remaining_pct"] is None
    assert "OAuth" in reading["daily"]["notes"][0]


def test_a_window_header_without_an_interval_keeps_the_pair_and_notes_the_gap():
    reading = normalise(
        "hubspot",
        "rate_limit_headers",
        {
            "headers": {
                "X-HubSpot-RateLimit-Max": "190",
                "X-HubSpot-RateLimit-Remaining": "95",
            }
        },
    )
    assert reading["window"]["known"] is True
    assert reading["window"]["window_seconds"] is None
    assert any("Interval" in note for note in reading["window"]["notes"])


def test_one_daily_header_alone_is_a_partial_half_that_says_so():
    reading = normalise(
        "hubspot",
        "rate_limit_headers",
        {"headers": {"X-HubSpot-RateLimit-Daily": "250000"}},
    )
    assert reading["daily"]["known"] is True
    assert reading["daily"]["max"] == 250000
    assert reading["daily"]["remaining"] is None
    assert any("partial" in note for note in reading["daily"]["notes"])


def test_the_account_information_endpoint_reads_a_daily_object():
    reading = normalise(
        "hubspot", "account_information", {"body": {"daily": {"limit": 250000, "used": 48180}}}
    )
    assert reading["daily"]["remaining"] == 201820


def test_the_account_information_endpoint_answers_unknown_with_the_gap_quoted():
    """The research's gap: the usage fields were not enumerated in the page read."""
    reading = normalise("hubspot", "account_information", {"body": {"portalId": 123}})
    assert reading["daily"]["known"] is False
    assert "not enumerated" in reading["daily"]["notes"][0]
    assert reading["daily"]["verbatim"] == {"portalId": 123}


# --------------------------------------------------------------------------- #
# The transformation: Dataverse, and the refusals both vendors share
# --------------------------------------------------------------------------- #


def test_a_dataverse_quota_reading_is_refused_with_the_gap_quoted():
    with pytest.raises(InvalidQuotaSurface) as raised:
        normalise("dataverse", "service_protection_limits", {})
    assert "not found at a readable URL" in str(raised.value)


def test_an_undocumented_surface_is_refused_naming_the_documented_ones():
    with pytest.raises(InvalidQuotaSurface) as raised:
        normalise("salesforce", "some_other_header", {"header": "api-usage=1/2"})
    assert "limit_info_header" in str(raised.value)


def test_an_unknown_vendor_is_refused_with_the_extension_point_named():
    with pytest.raises(UnknownVendor) as raised:
        normalise("zendesk", "limits_resource", {})
    assert "DEFAULT_POLICIES" in str(raised.value) or "one of" in str(raised.value)


def test_a_lying_surface_is_refused_not_recorded():
    """Remaining above max, and remaining below zero, are both refused."""
    with pytest.raises(InvalidQuotaSurface) as raised:
        normalise("salesforce", "limit_info_header", {"header": "api-usage=600000/500000"})
    assert "negative" in str(raised.value)
    with pytest.raises(InvalidQuotaSurface) as raised:
        normalise(
            "hubspot",
            "rate_limit_headers",
            {"headers": {"X-HubSpot-RateLimit-Max": "190", "X-HubSpot-RateLimit-Remaining": "200"}},
        )
    assert "greater than max" in str(raised.value)


def test_every_reading_carries_the_pair_and_the_instant():
    reading = normalise(
        "hubspot",
        "rate_limit_headers",
        {"headers": {"X-HubSpot-RateLimit-Max": "190", "X-HubSpot-RateLimit-Remaining": "95"}},
    )
    assert set(reading) == {"vendor", "surface", "observed_at", "daily", "window", "notes"}
    assert set(reading["daily"]) == {
        "known",
        "max",
        "used",
        "remaining",
        "remaining_pct",
        "window_seconds",
        "notes",
    }
    assert reading["observed_at"]


# --------------------------------------------------------------------------- #
# The telemetry: classes, rates, latency
# --------------------------------------------------------------------------- #


def test_the_status_classes_fall_in_the_researched_four():
    assert class_from_status(401) == "auth"
    assert class_from_status(429) == "throttle"
    assert class_from_status(503) == "vendor_5xx"
    assert class_from_status(400) == "validation"
    assert class_from_status(200) is None
    assert class_from_status(403) == "validation"


def test_an_explicit_class_must_be_one_of_the_researched_four():
    with pytest.raises(InvalidTelemetry) as raised:
        check_samples({"calls": [{"ok": False, "status": 403, "error_class": "rate_limit"}]})
    assert "validation" in str(raised.value)


def test_an_explicit_class_wins_over_the_status_derived_one():
    """A Salesforce 403 REQUEST_LIMIT_EXCEEDED is a throttle, and the connector says so."""
    samples = check_samples({"calls": [{"ok": False, "status": 403, "error_class": "throttle"}]})
    assert samples[0]["error_class"] == "throttle"


def test_a_sample_needs_ok_or_a_status():
    with pytest.raises(InvalidTelemetry):
        check_samples({"calls": [{}]})


def test_a_failed_sample_needs_a_class_the_room_can_count():
    with pytest.raises(InvalidTelemetry) as raised:
        check_samples({"calls": [{"ok": False}]})
    assert "classify" in str(raised.value)


def test_status_alone_is_a_sample():
    samples = check_samples({"calls": [{"status": 200}, {"status": 429}]})
    assert samples[0]["ok"] is True and samples[0]["error_class"] is None
    assert samples[1]["ok"] is False and samples[1]["error_class"] == "throttle"


def test_an_impossible_latency_is_refused_not_averaged():
    with pytest.raises(InvalidTelemetry):
        check_samples({"calls": [{"ok": True, "latency_ms": -5}]})
    with pytest.raises(InvalidTelemetry):
        check_samples({"calls": [{"ok": True, "latency_ms": 10**9}]})


def test_the_aggregate_computes_the_researched_numbers():
    samples = check_samples(
        {
            "calls": [
                {"ok": True, "status": 200, "latency_ms": 200},
                {"ok": True, "status": 200, "latency_ms": 100},
                {"ok": True, "status": 204, "latency_ms": 300},
                {"ok": False, "status": 429, "latency_ms": 600},
                {"ok": False, "status": 401},
                {"ok": False, "status": 400},
                {"ok": False, "status": 503, "latency_ms": 1200},
            ]
        }
    )
    result = aggregate(samples, now=NOW)
    assert result["known"] is True
    assert result["calls"] == 7
    assert result["succeeded"] == 3
    assert result["success_rate"] == round(3 / 7, 4)
    assert result["mean_latency_ms"] == round((200 + 100 + 300 + 600 + 1200) / 5, 1)
    assert result["error_breakdown"] == {
        "validation": 1,
        "throttle": 1,
        "auth": 1,
        "vendor_5xx": 1,
    }


def test_the_aggregate_excludes_samples_outside_its_window():
    samples = check_samples(
        {
            "calls": [
                {"ok": True, "status": 200, "at": (NOW - timedelta(hours=48)).isoformat()},
                {"ok": False, "status": 429, "at": (NOW - timedelta(hours=1)).isoformat()},
            ]
        }
    )
    result = aggregate(samples, now=NOW)
    assert result["calls"] == 1
    assert result["failed"] == 1
    assert result["outside_window"] == 1


def test_an_empty_aggregate_is_known_false_not_a_perfect_rate():
    old = (NOW - timedelta(hours=48)).isoformat()
    samples = check_samples({"calls": [{"ok": True, "status": 200, "at": old}]})
    result = aggregate(samples, now=NOW)
    assert result["known"] is False
    assert result["success_rate"] is None
    assert result["calls"] == 0
    assert result["outside_window"] == 1


# --------------------------------------------------------------------------- #
# The change-stream lag
# --------------------------------------------------------------------------- #


def test_a_direct_lag_is_taken_as_given():
    assert check_lag({"lag_seconds": 42}, now=NOW) == 42.0


def test_a_lag_is_computed_from_the_two_instants():
    lag = check_lag(
        {
            "source_event_at": (NOW - timedelta(seconds=90)).isoformat(),
            "observed_at": NOW.isoformat(),
        },
        now=NOW,
    )
    assert lag == 90.0


def test_a_computed_lag_clamps_at_zero():
    """A receiver ahead of its source is clock skew, not negative lag."""
    lag = check_lag(
        {
            "source_event_at": (NOW + timedelta(seconds=5)).isoformat(),
            "observed_at": NOW.isoformat(),
        },
        now=NOW,
    )
    assert lag == 0.0


def test_a_lag_needs_something_to_read():
    with pytest.raises(InvalidTelemetry):
        check_lag({}, now=NOW)
    with pytest.raises(InvalidTelemetry):
        check_lag({"observed_at": NOW.isoformat()}, now=NOW)


def test_a_negative_lag_is_refused():
    with pytest.raises(InvalidTelemetry):
        check_lag({"lag_seconds": -4}, now=NOW)


# --------------------------------------------------------------------------- #
# The alert rules
# --------------------------------------------------------------------------- #


def test_a_rule_validates_every_field_the_evaluator_reads():
    rule = check_rule({"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]})
    assert rule["comparison"] == "below"
    assert rule["cooldown_minutes"] == DEFAULT_COOLDOWN_MINUTES
    assert rule["enabled"] is True


def test_a_lag_rule_exceeds_and_a_budget_rule_fires_below():
    assert (
        check_rule({"metric": "stream_lag", "threshold": 300, "channels": ["webhook"]})[
            "comparison"
        ]
        == "exceeds"
    )
    assert (
        check_rule({"metric": "window_remaining", "threshold": 10, "channels": ["email"]})[
            "comparison"
        ]
        == "below"
    )


def test_an_unknown_metric_is_refused_not_stored():
    with pytest.raises(InvalidRule) as raised:
        check_rule({"metric": "success_rate", "threshold": 90, "channels": ["slack"]})
    assert "daily_remaining" in str(raised.value)


def test_a_rule_without_a_channel_fires_into_nowhere_and_is_refused():
    with pytest.raises(InvalidRule):
        check_rule({"metric": "stream_lag", "threshold": 300, "channels": []})
    with pytest.raises(InvalidRule):
        check_rule({"metric": "stream_lag", "threshold": 300, "channels": ["sms"]})


def test_a_rule_without_a_threshold_is_refused():
    with pytest.raises(InvalidRule):
        check_rule({"metric": "stream_lag", "channels": ["slack"]})


def test_a_partial_patch_is_merged_then_revalidated():
    from dsr.integ_monitor import merge_rule

    current = check_rule({"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]})
    merged = merge_rule(current, {"threshold": 10})
    assert merged["threshold"] == 10
    assert merged["channels"] == ["slack"]


def test_the_evaluation_fires_a_budget_rule_that_is_below_its_threshold():
    values = [
        {
            "connector_id": "c1",
            "label": "Starved",
            "vendor": "hubspot",
            "metric": "daily_remaining",
            "value": 0.7,
            "known": True,
        },
        {
            "connector_id": "c2",
            "label": "Healthy",
            "vendor": "salesforce",
            "metric": "daily_remaining",
            "value": 91.2,
            "known": True,
        },
    ]
    rules = [
        {
            "id": "r1",
            "data": check_rule(
                {"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]}
            ),
        }
    ]
    result = evaluate_rules(rules, values, now=NOW)
    assert len(result["fired"]) == 1
    entry = result["fired"][0]
    assert entry["rule_id"] == "r1"
    assert entry["worst_connector"] == "Starved"
    assert entry["worst_value"] == 0.7
    assert entry["fired_at"]


def test_the_evaluation_names_the_rule_that_did_not_fire_and_why():
    values = [
        {
            "connector_id": "c2",
            "label": "Healthy",
            "vendor": "salesforce",
            "metric": "daily_remaining",
            "value": 91.2,
            "known": True,
        },
    ]
    rules = [
        {
            "id": "r1",
            "data": check_rule(
                {"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]}
            ),
        }
    ]
    entry = evaluate_rules(rules, values, now=NOW)["rules"][0]
    assert entry["fired"] is False
    assert "below 20" in entry["reason"]


def test_a_rule_with_no_readable_reading_says_so_rather_than_firing_a_zero():
    rules = [
        {
            "id": "r1",
            "data": check_rule(
                {"metric": "window_remaining", "threshold": 10, "channels": ["slack"]}
            ),
        }
    ]
    entry = evaluate_rules(rules, [], now=NOW)["rules"][0]
    assert entry["fired"] is False
    assert "no window_remaining reading is available" in entry["reason"]


def test_the_cooldown_suppresses_the_second_poll():
    """Sourced silence, coded: a crossing fires once, not on every poll."""
    rule_data = check_rule({"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]})
    values = [
        {
            "connector_id": "c1",
            "label": "Starved",
            "vendor": "hubspot",
            "metric": "daily_remaining",
            "value": 5.0,
            "known": True,
        },
    ]
    first = evaluate_rules([{"id": "r1", "data": rule_data}], values, now=NOW)
    assert first["fired"]

    after_fire = dict(rule_data)
    after_fire.update(record_fire(rule_data, first["fired"][0], moment=NOW))
    second = evaluate_rules(
        [{"id": "r1", "data": after_fire}], values, now=NOW + timedelta(minutes=5)
    )
    assert second["rules"][0]["suppressed"] is True
    assert second["fired"] == []

    third = evaluate_rules(
        [{"id": "r1", "data": after_fire}],
        values,
        now=NOW + timedelta(minutes=DEFAULT_COOLDOWN_MINUTES + 1),
    )
    assert third["fired"]


def test_a_disabled_rule_is_reported_and_never_fires():
    rule_data = check_rule({"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]})
    rule_data["enabled"] = False
    values = [
        {
            "connector_id": "c1",
            "label": "Starved",
            "vendor": "hubspot",
            "metric": "daily_remaining",
            "value": 1.0,
            "known": True,
        },
    ]
    result = evaluate_rules([{"id": "r1", "data": rule_data}], values, now=NOW)
    assert result["rules"][0]["reason"] == "disabled"
    assert result["fired"] == []


def test_a_vendor_scoped_rule_ignores_the_other_vendors():
    rule_data = check_rule(
        {
            "metric": "daily_remaining",
            "threshold": 20,
            "channels": ["slack"],
            "vendor": "salesforce",
        }
    )
    values = [
        {
            "connector_id": "c1",
            "label": "HubSpot",
            "vendor": "hubspot",
            "metric": "daily_remaining",
            "value": 1.0,
            "known": True,
        },
    ]
    result = evaluate_rules([{"id": "r1", "data": rule_data}], values, now=NOW)
    assert result["fired"] == []
    assert result["rules"][0]["in_scope"] == 0


def test_a_fire_is_recorded_with_its_connector_and_channels():
    data = check_rule(
        {"metric": "daily_remaining", "threshold": 20, "channels": ["slack", "email"]}
    )
    entry = {"worst_connector_id": "c1", "worst_connector": "Starved", "worst_value": 3.0}
    after = record_fire(data, entry, moment=NOW)
    assert after["last_fired_at"]
    assert after["fire_count"] == 1
    assert after["fires"][0]["channels"] == ["slack", "email"]
    assert after["fires"][0]["connector_id"] == "c1"


def test_the_fire_history_is_bounded_and_says_when_it_truncated():
    data = check_rule({"metric": "stream_lag", "threshold": 1, "channels": ["webhook"]})
    data["cooldown_minutes"] = 0
    entry = {"worst_connector_id": "c1", "worst_connector": "Lagging", "worst_value": 9.0}
    for index in range(FIRE_HISTORY_LIMIT + 5):
        data = record_fire(data, entry, moment=NOW + timedelta(seconds=index))
    assert len(data["fires"]) == FIRE_HISTORY_LIMIT
    assert data["fires_truncated"] is True
    assert data["fire_count"] == FIRE_HISTORY_LIMIT + 5


def test_the_metric_reads_a_percentage_when_the_maximum_is_known():
    reading = {"known": True, "max": 100, "remaining": 25, "remaining_pct": 25.0}
    assert metric_value("daily_remaining", daily=reading, window=None) == 25.0


def test_the_metric_falls_back_to_a_count_when_the_maximum_is_not_known():
    reading = {"known": True, "max": None, "remaining": 12, "remaining_pct": None}
    assert metric_value("window_remaining", daily=None, window=reading) == 12.0


def test_an_unknown_half_reads_none_and_the_rule_says_so():
    reading = {"known": False, "max": None, "remaining": None, "remaining_pct": None}
    assert metric_value("daily_remaining", daily=reading, window=None) is None


# --------------------------------------------------------------------------- #
# The engine over the store
# --------------------------------------------------------------------------- #


def test_a_connector_is_registered_with_its_vendors_declared_policy(monitor, room, connector):
    assert connector["vendor"] == "salesforce"
    assert connector["quota_policy"] == DEFAULT_POLICIES["salesforce"]
    assert connector["concurrency"] == 4
    assert connector["paused"] is False


def test_an_unregistered_vendor_is_refused_at_registration(monitor, room):
    with pytest.raises(UnknownVendor):
        monitor.register_connector(
            {"vendor": "zendesk", "label": "Zen"},
            room_id=room["id"],
            actor="dana",
            source=f"POST {PREFIX}/connectors",
        )


def test_a_connector_outside_the_concurrency_bounds_is_refused(monitor, room, connector):
    with pytest.raises(InvalidPayload):
        monitor.update_connector(
            connector["id"], {"concurrency": 0}, actor="dana", source=f"PATCH {PREFIX}/connectors/x"
        )
    with pytest.raises(InvalidPayload):
        monitor.update_connector(
            connector["id"],
            {"concurrency": 100},
            actor="dana",
            source=f"PATCH {PREFIX}/connectors/x",
        )


def test_pause_and_resume_come_from_the_same_patch(monitor, room, connector, clock):
    """The researched step 4: "lowers its concurrency or pauses it from the same page"."""
    paused = monitor.update_connector(
        connector["id"],
        {"paused": True, "concurrency": 2},
        actor="dana",
        source=f"PATCH {PREFIX}/connectors/x",
    )
    assert paused["paused"] is True
    assert paused["paused_at"]
    assert paused["concurrency"] == 2
    assert paused["changed"] == ["paused", "concurrency"]

    resumed = monitor.update_connector(
        connector["id"], {"paused": False}, actor="dana", source=f"PATCH {PREFIX}/connectors/x"
    )
    assert resumed["paused"] is False
    assert resumed["paused_at"] is None


def test_patching_an_unknown_field_is_refused_with_the_tunables_named(monitor, connector):
    with pytest.raises(InvalidPayload) as raised:
        monitor.update_connector(
            connector["id"], {"limit": 5}, actor="dana", source=f"PATCH {PREFIX}/connectors/x"
        )
    assert "paused, concurrency" in str(raised.value)


def test_a_removed_connector_is_soft_deleted_and_its_observations_outlive_it(
    monitor, room, connector, clock
):
    monitor.record_quota(
        connector["id"],
        {"surface": "limit_info_header", "header": "api-usage=10/500000"},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    monitor.remove_connector(connector["id"], actor="dana", source=f"DELETE {PREFIX}/connectors/x")
    with pytest.raises(UnknownConnector):
        monitor.connector(connector["id"])
    rows = monitor.list_connectors(room_id=room["id"])
    assert rows == []
    records, _truncated = monitor._scan(QUOTA_COLLECTION)
    assert len(records) == 1


def test_a_quota_reading_is_stored_with_the_pair_it_parsed(monitor, connector, clock):
    result = monitor.record_quota(
        connector["id"],
        {"surface": "limit_info_header", "header": "api-usage=123/500000"},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    stored = result["observation"]
    assert stored["vendor"] == "salesforce"
    assert stored["daily"]["remaining"] == 499877
    assert stored["window"]["known"] is False


def test_the_quota_view_reports_the_latest_pair_and_the_delta(monitor, connector, clock):
    monitor.record_quota(
        connector["id"],
        {
            "surface": "limits_resource",
            "body": {"limits": [{"name": "API Requests", "max": 100000, "remaining": 91240}]},
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    clock.advance(minutes=10)
    monitor.record_quota(
        connector["id"],
        {"surface": "limit_info_header", "header": "api-usage=10310/100000"},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    view = monitor.quota_view(connector["id"])
    assert view["count"] == 2
    assert view["by_surface"] == {"limits_resource": 1, "limit_info_header": 1}
    assert view["latest"]["daily"]["remaining"] == 89690
    assert view["delta"]["daily_remaining"] == 89690 - 91240
    assert view["delta"]["window_remaining"] is None


def test_the_delta_is_null_when_either_side_is_unknown(monitor, connector):
    monitor.record_quota(
        connector["id"],
        {"surface": "limit_info_header", "header": "api-usage=123/500000"},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    view = monitor.quota_view(connector["id"])
    assert view["delta"] is None


def test_a_quota_reading_for_a_dataverse_connector_is_refused_with_the_gap(monitor, room, clock):
    contoso = monitor.register_connector(
        {"vendor": "dataverse", "label": "Contoso — Dataverse"},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/connectors",
    )
    with pytest.raises(InvalidQuotaSurface) as raised:
        monitor.record_quota(
            contoso["id"],
            {"surface": "limits_resource"},
            actor="dana",
            source=f"POST {PREFIX}/connectors/x/quota",
        )
    assert "not found at a readable URL" in str(raised.value)


def test_telemetry_is_stored_one_record_per_call_and_aggregated(monitor, connector, clock):
    result = monitor.record_telemetry(
        connector["id"],
        {
            "calls": [
                {"ok": True, "status": 200, "latency_ms": 200},
                {"ok": False, "status": 429, "latency_ms": 600},
            ]
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/telemetry",
    )
    assert result["recorded"] == 2
    assert result["aggregate"]["success_rate"] == 0.5
    assert result["aggregate"]["error_breakdown"]["throttle"] == 1

    records, _truncated = monitor._scan(TELEMETRY_COLLECTION)
    assert len(records) == 2


def test_the_telemetry_view_is_a_read(monitor, connector):
    monitor.record_telemetry(
        connector["id"],
        {"calls": [{"ok": True, "status": 200, "latency_ms": 100}]},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/telemetry",
    )
    view = monitor.telemetry_view(connector["id"])
    assert view["known"] is True
    assert view["calls"] == 1
    assert view["mean_latency_ms"] == 100


def test_a_stream_observation_is_stored_with_how_it_was_computed(monitor, connector, clock):
    result = monitor.record_stream(
        connector["id"],
        {
            "source_event_at": (NOW - timedelta(seconds=90)).isoformat(),
            "observed_at": NOW.isoformat(),
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/stream",
    )
    assert result["observation"]["lag_seconds"] == 90
    assert result["observation"]["source_event_at"] is not None
    assert result["previous_lag_seconds"] is None

    clock.advance(seconds=60)
    again = monitor.record_stream(
        connector["id"],
        {"lag_seconds": 8},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/stream",
    )
    assert again["previous_lag_seconds"] == 90


# --------------------------------------------------------------------------- #
# The change-tracking audit and the drift signal
# --------------------------------------------------------------------------- #


def test_the_change_tracking_audit_records_the_tables_being_tracked(monitor, room, clock):
    view = monitor.record_change_tracking(
        {
            "vendor": "dataverse",
            "globalmetadataversion": "61950421",
            "entities": [
                {"schema_name": "opportunity", "change_tracking_enabled": True},
                {"schema_name": "quote", "change_tracking_enabled": False},
            ],
        },
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    assert view["tracked"] == 1
    assert view["drift"] is False
    assert view["previous_version"] is None


def test_a_moved_schema_version_is_a_drift_signal_not_a_failure(monitor, room, clock):
    """Sourced: 'you might need to refresh any schema data that your application cached'."""
    monitor.record_change_tracking(
        {"globalmetadataversion": "100", "entities": [{"schema_name": "opportunity"}]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    clock.advance(minutes=5)
    view = monitor.record_change_tracking(
        {"globalmetadataversion": "167", "entities": [{"schema_name": "opportunity"}]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    assert view["drift"] is True
    assert view["previous_version"] == "100"
    assert "might need to refresh" in view["note"]


def test_an_unchanged_version_is_not_drift(monitor, room, clock):
    monitor.record_change_tracking(
        {"globalmetadataversion": "100", "entities": [{"schema_name": "opportunity"}]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    clock.advance(minutes=5)
    view = monitor.record_change_tracking(
        {"globalmetadataversion": "100", "entities": [{"schema_name": "opportunity"}]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    assert view["drift"] is False


def test_the_change_tracking_audit_is_dataverses_surface(monitor, room):
    with pytest.raises(InvalidPayload) as raised:
        monitor.record_change_tracking(
            {
                "vendor": "salesforce",
                "globalmetadataversion": "1",
                "entities": [{"schema_name": "x"}],
            },
            room_id=room["id"],
            actor="dana",
            source=f"POST {PREFIX}/rooms/x/change-tracking",
        )
    assert "EntityDefinitions" in str(raised.value)


def test_an_audit_without_tables_is_refused(monitor, room):
    with pytest.raises(InvalidPayload):
        monitor.record_change_tracking(
            {"globalmetadataversion": "1"},
            room_id=room["id"],
            actor="dana",
            source=f"POST {PREFIX}/rooms/x/change-tracking",
        )


def test_the_change_tracking_view_counts_drifts(monitor, room, clock):
    monitor.record_change_tracking(
        {"globalmetadataversion": "100", "entities": [{"schema_name": "opportunity"}]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    clock.advance(minutes=1)
    monitor.record_change_tracking(
        {"globalmetadataversion": "101", "entities": [{"schema_name": "opportunity"}]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/change-tracking",
    )
    view = monitor.change_tracking_view(room["id"])
    assert view["count"] == 2
    assert view["drift_count"] == 1
    assert view["latest"]["drift"] is True


# --------------------------------------------------------------------------- #
# The dashboard
# --------------------------------------------------------------------------- #


def test_the_dashboard_shows_the_researched_numbers_per_connector(monitor, room, connector, clock):
    monitor.record_quota(
        connector["id"],
        {
            "surface": "limits_resource",
            "body": {"limits": [{"name": "API Requests", "max": 100000, "remaining": 91240}]},
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    monitor.record_telemetry(
        connector["id"],
        {
            "calls": [
                {"ok": True, "status": 200, "latency_ms": 200},
                {"ok": False, "status": 401, "latency_ms": 60},
            ]
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/telemetry",
    )
    monitor.record_stream(
        connector["id"],
        {"lag_seconds": 4},
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/stream",
    )

    board = monitor.dashboard(room["id"])
    row = board["connectors"][0]
    assert row["vendor"] == "salesforce"
    assert board["by_vendor"] == {"salesforce": 1}
    assert board["window_seconds"] == DEFAULT_WINDOW_SECONDS
    assert row["concurrency"] == 4

    values = {(v["connector_id"], v["metric"]): v for v in monitor._metric_values(room["id"])}
    daily = values[(connector["id"], "daily_remaining")]
    assert daily["known"] is True
    assert daily["value"] == 91.24
    window = values[(connector["id"], "window_remaining")]
    assert window["known"] is False
    lag = values[(connector["id"], "stream_lag")]
    assert lag["known"] is True and lag["value"] == 4.0


def test_the_dashboard_preview_never_fires_a_rule(monitor, room, clock):
    """A GET that starts cooldowns would be a read that lies about having read."""
    hub = monitor.register_connector(
        {"vendor": "hubspot", "label": "Starved"},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/connectors",
    )
    monitor.create_rule(
        {"metric": "daily_remaining", "threshold": 99, "channels": ["slack"]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/alerts/rules",
    )
    monitor.record_quota(
        hub["id"],
        {
            "surface": "rate_limit_headers",
            "headers": {
                "X-HubSpot-RateLimit-Max": "190",
                "X-HubSpot-RateLimit-Remaining": "2",
                "X-HubSpot-RateLimit-Daily": "250000",
                "X-HubSpot-RateLimit-Daily-Remaining": "300",
            },
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    board = monitor.dashboard(room["id"])
    assert board["alert_preview"]["fired"], "the preview should show what would fire"
    rule = monitor.list_rules(room["id"])[0]
    assert rule["last_fired_at"] is None

    fired = monitor.evaluate_alerts(
        room["id"], actor="dana", source=f"POST {PREFIX}/rooms/x/alerts/evaluate"
    )
    assert fired["fired"]
    rule = monitor.list_rules(room["id"])[0]
    assert rule["last_fired_at"] is not None


def test_a_paused_connector_drops_out_of_evaluation(monitor, room, clock):
    hub = monitor.register_connector(
        {"vendor": "hubspot", "label": "Paused"},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/connectors",
    )
    monitor.record_quota(
        hub["id"],
        {
            "surface": "rate_limit_headers",
            "headers": {"X-HubSpot-RateLimit-Max": "190", "X-HubSpot-RateLimit-Remaining": "1"},
        },
        actor="dana",
        source=f"POST {PREFIX}/connectors/x/quota",
    )
    monitor.create_rule(
        {"metric": "window_remaining", "threshold": 10, "channels": ["slack"]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/alerts/rules",
    )
    before = monitor.evaluate_alerts(
        room["id"], actor="dana", source=f"POST {PREFIX}/rooms/x/alerts/evaluate"
    )
    assert before["fired"]

    monitor.update_connector(
        hub["id"], {"paused": True}, actor="dana", source=f"PATCH {PREFIX}/connectors/x"
    )
    after = monitor.evaluate_alerts(
        room["id"], actor="dana", source=f"POST {PREFIX}/rooms/x/alerts/evaluate"
    )
    assert after["rules"][0]["in_scope"] == 0
    assert after["fired"] == []


def test_the_dashboard_on_an_unknown_room_is_a_404(monitor):
    with pytest.raises(UnknownRoom):
        monitor.dashboard("no-such-room")


# --------------------------------------------------------------------------- #
# Rules over the engine
# --------------------------------------------------------------------------- #


def test_a_rule_is_created_listed_and_deleted(monitor, room):
    rule = monitor.create_rule(
        {"metric": "stream_lag", "threshold": 300, "channels": ["webhook"]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/alerts/rules",
    )
    assert rule["comparison"] == "exceeds"
    assert monitor.list_rules(room["id"])[0]["id"] == rule["id"]

    monitor.delete_rule(rule["id"], actor="dana", source=f"DELETE {PREFIX}/alerts/rules/x")
    assert monitor.list_rules(room["id"]) == []
    with pytest.raises(UnknownRule):
        monitor.update_rule(
            rule["id"], {"threshold": 1}, actor="dana", source=f"PATCH {PREFIX}/alerts/rules/x"
        )


def test_rules_are_room_scoped(monitor, room):
    other_room = monitor.store.create("room", {**ROOM, "name": "Other"}, actor="dana")
    monitor.create_rule(
        {"metric": "stream_lag", "threshold": 300, "channels": ["webhook"]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/alerts/rules",
    )
    assert len(monitor.list_rules(other_room["id"])) == 0
    with pytest.raises(UnknownRule):
        monitor.update_rule(
            monitor.list_rules(room["id"])[0]["id"],
            {"threshold": 1},
            room_id=other_room["id"],
            actor="dana",
            source=f"PATCH {PREFIX}/alerts/rules/x",
        )


def test_a_patch_preserves_the_fire_history(monitor, room, clock):
    rule = monitor.create_rule(
        {"metric": "stream_lag", "threshold": 300, "channels": ["webhook"]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/alerts/rules",
    )
    hub = monitor.register_connector(
        {"vendor": "hubspot", "label": "Lagging"},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/connectors",
    )
    monitor.record_stream(
        hub["id"], {"lag_seconds": 999}, actor="dana", source=f"POST {PREFIX}/connectors/x/stream"
    )
    monitor.evaluate_alerts(
        room["id"], actor="dana", source=f"POST {PREFIX}/rooms/x/alerts/evaluate"
    )
    after = monitor.list_rules(room["id"])[0]
    assert after["fire_count"] == 1

    retuned = monitor.update_rule(
        rule["id"], {"threshold": 120}, actor="dana", source=f"PATCH {PREFIX}/alerts/rules/x"
    )
    assert retuned["threshold"] == 120
    assert retuned["fire_count"] == 1
    assert len(retuned["fires"]) == 1


def test_the_evaluation_on_a_room_with_no_readings_says_so(monitor, room):
    monitor.create_rule(
        {"metric": "daily_remaining", "threshold": 20, "channels": ["slack"]},
        room_id=room["id"],
        actor="dana",
        source=f"POST {PREFIX}/rooms/x/alerts/rules",
    )
    result = monitor.evaluate_alerts(
        room["id"], actor="dana", source=f"POST {PREFIX}/rooms/x/alerts/evaluate"
    )
    assert result["fired"] == []
    assert "no daily_remaining reading is available" in result["rules"][0]["reason"]


# --------------------------------------------------------------------------- #
# Plugin registration
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-049"
    assert entry["exception_handlers"] == ["MonitorError"]
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
    import ast

    module = load_feature(MODULE)
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
    module = load_feature(MODULE)

    assert f"id: {module.FEATURE['id']!r}" in text
    assert f"label: {module.FEATURE['nav'][0]['label']!r}" in text


def test_the_frontend_only_calls_its_own_prefix():
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    others = sorted(
        path.name for path in folder.parent.iterdir() if path.is_dir() and path.name != FEATURE_ID
    )

    for name in ("api.js", "index.jsx", "IntegrationMonitor.jsx", "icons.js", "primitives.jsx"):
        path = folder / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        borrowed = [
            line.strip()
            for line in text.splitlines()
            if "apiRequest('" in line and "/wf-049" not in line
        ]
        assert borrowed == [], f"{name} calls {borrowed} instead of its own prefix"
        for other in others:
            assert f"{other}/" not in text, f"{name} references {other}"


def test_the_room_scoped_routes_take_their_room_in_the_path():
    paths = {route.path for route in load_feature(MODULE).router.routes}

    assert f"{PREFIX}/rooms/{{room_id}}/dashboard" in paths
    assert f"{PREFIX}/rooms/{{room_id}}/alerts/evaluate" in paths
    assert not [path for path in paths if path.count("{") > 2]
    allowed = {"{room_id}", "{connector_id}", "{rule_id}"}
    stray = [
        segment
        for path in paths
        for segment in path.split("/")
        if segment.startswith("{") and segment not in allowed
    ]
    assert stray == []


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_and_inferences_answer_over_http(http):
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert "oauth-daily-is-unknown-not-zero" in body["ids"]


def test_a_connector_lifecycle_over_http(http, http_room):
    room_id = http_room["id"]

    created = http.post(
        f"{PREFIX}/connectors",
        params={"room_id": room_id},
        json={"vendor": "hubspot", "label": "Northwind — HubSpot"},
    )
    assert created.status_code == 201
    connector_id = created.json()["id"]

    listed = http.get(f"{PREFIX}/connectors", params={"room_id": room_id}).json()
    assert listed["count"] == 1

    paused = http.patch(f"{PREFIX}/connectors/{connector_id}", json={"paused": True})
    assert paused.status_code == 200
    assert paused.json()["paused"] is True

    quota = http.post(
        f"{PREFIX}/connectors/{connector_id}/quota",
        json={
            "surface": "rate_limit_headers",
            "headers": {
                "X-HubSpot-RateLimit-Max": "190",
                "X-HubSpot-RateLimit-Remaining": "95",
                "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
            },
        },
    )
    assert quota.status_code == 200
    assert quota.json()["observation"]["window"]["remaining"] == 95

    read = http.get(f"{PREFIX}/connectors/{connector_id}/quota").json()
    assert read["latest"]["window"]["known"] is True

    telemetry = http.post(
        f"{PREFIX}/connectors/{connector_id}/telemetry",
        json={"calls": [{"status": 200, "latency_ms": 100}]},
    )
    assert telemetry.status_code == 200
    assert telemetry.json()["aggregate"]["success_rate"] == 1.0

    stream = http.post(f"{PREFIX}/connectors/{connector_id}/stream", json={"lag_seconds": 12})
    assert stream.status_code == 200

    removed = http.delete(f"{PREFIX}/connectors/{connector_id}")
    assert removed.status_code == 204
    assert http.get(f"{PREFIX}/connectors/{connector_id}").status_code == 404


def test_the_dashboard_over_http(http, http_room):
    room_id = http_room["id"]
    http.post(
        f"{PREFIX}/connectors",
        params={"room_id": room_id},
        json={
            "vendor": "salesforce",
            "label": "Northwind — Salesforce",
            "limit_name": "API Requests",
        },
    )
    board = http.get(f"{PREFIX}/rooms/{room_id}/dashboard")
    assert board.status_code == 200
    body = board.json()
    assert body["connectors"][0]["vendor"] == "salesforce"
    assert body["alert_preview"]["evaluated_at"]


def test_an_unknown_vendor_over_http_is_a_400_naming_the_documented_ones(http, http_room):
    response = http.post(
        f"{PREFIX}/connectors", params={"room_id": http_room["id"]}, json={"vendor": "pipedrive"}
    )
    assert response.status_code == 400
    assert "salesforce" in response.json()["detail"]


def test_an_unknown_room_over_http_is_a_404(http):
    assert http.get(f"{PREFIX}/rooms/nope/dashboard").status_code == 404
    assert http.post(f"{PREFIX}/rooms/nope/alerts/evaluate").status_code == 404


def test_an_unparseable_quota_surface_is_a_400_with_a_hint(http, http_room):
    created = http.post(
        f"{PREFIX}/connectors",
        params={"room_id": http_room["id"]},
        json={"vendor": "salesforce"},
    ).json()
    response = http.post(
        f"{PREFIX}/connectors/{created['id']}/quota",
        json={"surface": "limit_info_header", "header": "nonsense"},
    )
    assert response.status_code == 400
    assert response.json()["hint"].endswith("/vocabulary")


def test_an_alert_rule_lifecycle_over_http(http, http_room):
    room_id = http_room["id"]
    created = http.post(
        f"{PREFIX}/rooms/{room_id}/alerts/rules",
        json={"metric": "stream_lag", "threshold": 300, "channels": ["webhook"]},
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    listed = http.get(f"{PREFIX}/rooms/{room_id}/alerts").json()
    assert listed["count"] == 1

    evaluation = http.post(f"{PREFIX}/rooms/{room_id}/alerts/evaluate")
    assert evaluation.status_code == 200
    assert evaluation.json()["rules"][0]["fired"] is False

    retuned = http.patch(
        f"{PREFIX}/alerts/rules/{rule_id}", json={"threshold": 5, "channels": ["slack"]}
    )
    assert retuned.status_code == 200
    assert retuned.json()["threshold"] == 5

    removed = http.delete(f"{PREFIX}/alerts/rules/{rule_id}")
    assert removed.status_code == 204
    assert http.get(f"{PREFIX}/rooms/{room_id}/alerts").json()["count"] == 0


def test_an_invalid_rule_over_http_is_a_400(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/alerts/rules",
        json={"metric": "success_rate", "threshold": 90, "channels": ["slack"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "InvalidRule"


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def _matches_registered_route(source, routes):
    """Does ``"POST /api/wf-049/connectors"`` name a route the host actually mounted?

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
            for expected, found in zip(template, actual, strict=True)
        ):
            return True
    return False


def _exercise_every_write(http, room_id):
    created = http.post(
        f"{PREFIX}/connectors",
        params={"room_id": room_id},
        json={"vendor": "hubspot", "label": "Northwind — HubSpot"},
    ).json()
    http.patch(f"{PREFIX}/connectors/{created['id']}", json={"concurrency": 2})
    http.post(
        f"{PREFIX}/connectors/{created['id']}/quota",
        json={
            "surface": "rate_limit_headers",
            "headers": {"X-HubSpot-RateLimit-Max": "190", "X-HubSpot-RateLimit-Remaining": "95"},
        },
    )
    http.post(
        f"{PREFIX}/connectors/{created['id']}/telemetry",
        json={"calls": [{"status": 200, "latency_ms": 100}]},
    )
    http.post(f"{PREFIX}/connectors/{created['id']}/stream", json={"lag_seconds": 5})
    http.post(
        f"{PREFIX}/rooms/{room_id}/change-tracking",
        json={"globalmetadataversion": "1", "entities": [{"schema_name": "opportunity"}]},
    )
    rule = http.post(
        f"{PREFIX}/rooms/{room_id}/alerts/rules",
        json={"metric": "stream_lag", "threshold": 1, "channels": ["slack"]},
    ).json()
    http.post(f"{PREFIX}/rooms/{room_id}/alerts/evaluate")
    http.patch(f"{PREFIX}/alerts/rules/{rule['id']}", json={"threshold": 2})
    http.delete(f"{PREFIX}/connectors/{created['id']}")


MY_COLLECTIONS = {
    CONNECTOR_COLLECTION,
    QUOTA_COLLECTION,
    TELEMETRY_COLLECTION,
    STREAM_COLLECTION,
    CHANGE_COLLECTION,
    RULE_COLLECTION,
}


def test_every_audit_row_names_a_route_the_host_actually_mounted(http, http_room):
    """The rule the brief names by name, asserted against the real host."""
    _exercise_every_write(http, http_room["id"])

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]

    written = [entry for entry in entries if entry["collection"] in MY_COLLECTIONS]
    assert written, "the writes recorded no audit rows at all"
    for entry in written:
        assert _matches_registered_route(entry["source"], served), entry["source"]


def test_every_audit_row_names_a_route_of_this_feature(http, http_room):
    _exercise_every_write(http, http_room["id"])

    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    written = [entry for entry in entries if entry["collection"] in MY_COLLECTIONS]
    assert written
    for entry in written:
        method, _, path = entry["source"].partition(" ")
        assert method in ("POST", "PATCH", "DELETE"), entry["source"]
        assert path.startswith(f"{PREFIX}/"), entry["source"]


def test_the_domain_layer_refuses_to_write_without_a_source():
    """``source`` is a required keyword, so a hardcoded string cannot creep back in."""
    write_methods = (
        IntegrationMonitor.register_connector,
        IntegrationMonitor.update_connector,
        IntegrationMonitor.remove_connector,
        IntegrationMonitor.record_quota,
        IntegrationMonitor.record_telemetry,
        IntegrationMonitor.record_stream,
        IntegrationMonitor.record_change_tracking,
        IntegrationMonitor.create_rule,
        IntegrationMonitor.update_rule,
        IntegrationMonitor.delete_rule,
        IntegrationMonitor.evaluate_alerts,
    )
    for method in write_methods:
        parameter = inspect.signature(method).parameters["source"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_no_domain_module_hardcodes_a_literal_string_as_an_audit_source():
    import ast

    package = Path(IntegrationMonitor.__module__.replace(".", "/")).parent
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "source":
                    assert not isinstance(keyword.value, ast.Constant), (
                        f"{module} passes a literal string as source="
                    )


def test_no_domain_module_touches_sqlite_directly():
    import ast

    package = Path(IntegrationMonitor.__module__.replace(".", "/")).parent
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                assert "sqlite3" not in names, f"{module} imports sqlite3"


# --------------------------------------------------------------------------- #
# The inferences, asserted against the behaviour
# --------------------------------------------------------------------------- #


def test_the_inference_registry_covers_the_mechanisms_the_suite_asserts():
    ids = {entry["id"] for entry in INFERENCES}
    assert {
        "oauth-daily-is-unknown-not-zero",
        "salesforce-limits-are-daily",
        "the-room-holds-no-vendor-credentials",
        "throttle-is-429-and-the-vendors-word-wins",
        "success-window-is-24h",
        "alert-cooldown-30-minutes",
        "concurrency-default-4",
        "lag-is-observed-minus-source",
        "schema-drift-is-a-signal-not-a-failure",
        "dataverse-has-no-sourced-numbers",
        "unreachable-vendor-entries-are-not-zeros",
        "evaluation-runs-on-read",
    } <= ids


def test_every_inference_is_bounded_and_changeable():
    for entry in INFERENCES:
        assert entry["basis"], entry["id"]
        assert "value" in entry
        assert entry["why"], entry["id"]
        assert entry["change_it"], entry["id"]


def test_the_default_concurrency_the_inference_names_is_the_code_s_default():
    concurrency = next(entry for entry in INFERENCES if entry["id"] == "concurrency-default-4")
    assert concurrency["value"]["default"] == 4


def test_the_cooldown_the_inference_names_is_the_code_s_default():
    cooldown = next(entry for entry in INFERENCES if entry["id"] == "alert-cooldown-30-minutes")
    assert cooldown["value"]["cooldown_minutes"] == DEFAULT_COOLDOWN_MINUTES


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def test_the_seed_registers_the_researched_states(store, tmp_path):
    from dsr.features.wf049_monitor_integration_health_and_remaini import seed

    db = store.db
    room = db.create("room", ROOM, actor="dana")
    other = db.create("room", {**ROOM, "name": "Second"}, actor="dana")

    summary = seed(
        db,
        {
            "room_ids": [(room["id"], ROOM["account"]), (other["id"], "Second")],
            "now": datetime.now(timezone.utc),
            "rng": None,
        },
    )
    assert "monitored connectors" in summary

    monitor = IntegrationMonitor(store)
    rows = monitor.list_connectors(room_id=room["id"])
    assert len(rows) >= 5

    labels = {row["label"] for row in rows}
    assert any("starved" in label for label in labels)
    assert any("auth failing" in label for label in labels)
    assert any("no sourced numbers" in label for label in labels)

    assert [row for row in rows if row["paused"]], "the demo pauses one connector"

    board = monitor.dashboard(room["id"])
    assert board["alert_rules"], "the demo seeds alert rules"

    starved = next(row for row in rows if "starved" in row["label"])
    reading = monitor.quota_view(starved["id"])
    assert reading["latest"]["window"]["remaining"] == 7
    assert reading["latest"]["daily"]["remaining"] == 1820

    tracking = monitor.change_tracking_view(room["id"])
    assert tracking["drift_count"] == 1
    assert tracking["latest"]["tracked"] == 3
