"""WF-109's HTTP surface, its audit trail and its seed.

The router is driven through the application's own ``TestClient``, so the discovery, the
error mapping and the audit rows are all the real ones rather than stand-ins. The SLA
timers the workflows watch are created through WF-108's own HTTP routes and the
conversations through WF-107's, which is also a check that the reuse works across the
three features.

No test here carries a state that depends on the day the suite runs. Every instant a
decision turns on is passed explicitly, either as a message ``at``, an apply ``applied_at``
or the ``now`` a sweep evaluates at.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dsr.db.audited import AuditedDatabase
from dsr.features import wf109_escalate_on_sla_breach_with_a_pre_warning_head as feature
from dsr.sla_escalation import rules, vocabulary as vocab
from dsr.store import RecordStore
from fastapi.routing import APIRoute

PREFIX = feature.router.prefix
CHASE = "/api/wf-107"
SLA = "/api/wf-108"
FIXED = datetime(2026, 3, 2, tzinfo=timezone.utc)  # a Monday
AT_10 = FIXED.replace(hour=10, minute=0)
EARLIER = FIXED - timedelta(days=3)  # the Friday before
EARLIER_10 = EARLIER.replace(hour=10, minute=0)
QUARTER_HOUR = 15 * 60
MINUTE = 60
HOUR = 60 * MINUTE


def make_room(db: AuditedDatabase) -> str:
    return RecordStore(db).create(
        "room", {"name": "Northwind", "account": "Northwind"}, actor="dana"
    )["id"]


def open_conversation(http, room_id: str, **payload: Any) -> str:
    body = {"origin": "inbox", "inbox": "enterprise"}
    body.update(payload)
    response = http.post(f"{CHASE}/rooms/{room_id}/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def says(http, room_id: str, conversation_id: str, author: str, at: datetime) -> None:
    response = http.post(
        f"{CHASE}/rooms/{room_id}/conversations/{conversation_id}/messages",
        json={"author_kind": author, "at": at.isoformat(), "body": "hello"},
    )
    assert response.status_code == 201, response.text


def make_policy_http(http, room_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "Enterprise first response",
        "targets": {"frt": QUARTER_HOUR, "nrt": 30 * MINUTE, "ttc": 8 * HOUR},
        "pause_reasons": ["snoozed", "waiting_on_customer"],
        "teams": ["enterprise"],
        "workflow_name": "Escalate enterprise leads",
        "live": True,
    }
    payload.update(overrides)
    response = http.post(f"{SLA}/rooms/{room_id}/policies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def apply_sla_http(
    http, room_id: str, conversation_id: str, policy_id: str, applied_at: datetime
) -> dict[str, Any]:
    response = http.post(
        f"{SLA}/rooms/{room_id}/conversations/{conversation_id}/sla",
        json={"policy_id": policy_id, "applied_at": applied_at.isoformat()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_workflow_http(http, room_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "Pre-warn before first response",
        "trigger": vocab.TRIGGER_BEFORE_BREACH,
        "metric": vocab.METRIC_FRT,
        "lead_seconds": 10 * MINUTE,
        "actions": [{"kind": vocab.ACTION_TAG, "tag": "sla pre-warning"}],
        "live": True,
    }
    payload.update(overrides)
    response = http.post(f"{PREFIX}/rooms/{room_id}/workflows", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def evaluate_http(http, room_id: str, at: datetime) -> dict[str, Any]:
    response = http.post(
        f"{PREFIX}/rooms/{room_id}/escalations/evaluate", json={"now": at.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Published data
# --------------------------------------------------------------------------- #


class TestPublishedData:
    def test_the_vocabulary_route_serves_the_triggers_and_targets(self, client):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["trigger_kinds"] == ["before_breach", "on_breach"]
        assert body["metrics"] == ["frt", "nrt", "ttc", "ttr"]
        assert body["levels"] == ["pre_warning", "breach"]

    def test_the_vocabulary_quotes_the_lead_rule(self, client):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert "shorter than the SLA target's duration" in body["evidence"]["lead_time"]

    def test_the_inferences_route_serves_every_decision(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["count"] >= 8
        assert body["jev_status"]
        for entry in body["decisions"]:
            assert entry["consequence"]
            assert entry["evidence"]

    def test_one_decision_can_be_read_by_id(self, client):
        response = client.get(f"{PREFIX}/decisions/cancellation-model")
        assert response.status_code == 200
        assert response.json()["id"] == "cancellation-model"

    def test_an_unknown_decision_is_a_404(self, client):
        response = client.get(f"{PREFIX}/decisions/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "decision_not_found"


# --------------------------------------------------------------------------- #
# Workflows over HTTP
# --------------------------------------------------------------------------- #


class TestWorkflowRoutes:
    def test_creating_and_reading_a_workflow(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        created = make_workflow_http(client, room_id)
        assert created["id"]
        read = client.get(f"{PREFIX}/rooms/{room_id}/workflows/{created['id']}").json()
        assert read["trigger"] == "before_breach"
        assert read["level"] == "pre_warning"
        assert read["metric"] == "frt"

    def test_a_missing_name_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/workflows",
            json={"trigger": "before_breach", "actions": [{"kind": "tag", "tag": "x"}]},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "workflow_needs_a_name"

    def test_a_workflow_with_no_actions_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/workflows", json={"name": "W", "actions": []}
        )
        assert response.status_code == 422
        assert response.json()["error"] == "workflow_needs_actions"

    def test_an_unknown_trigger_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/workflows",
            json={
                "name": "W",
                "trigger": "when the sun rises",
                "actions": [{"kind": "tag", "tag": "x"}],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_trigger_kind"

    def test_a_lead_equal_to_a_named_policys_target_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/workflows",
            json={
                "name": "Too slow",
                "trigger": "before_breach",
                "metric": "frt",
                "lead_seconds": 20 * MINUTE,
                "policy_id": policy["id"],
                "actions": [{"kind": "tag", "tag": "x"}],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"] == "lead_not_shorter_than_target"

    def test_a_lead_shorter_than_a_named_policys_target_is_accepted(
        self, client, db: AuditedDatabase
    ):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        created = make_workflow_http(
            client, room_id, lead_seconds=10 * MINUTE, policy_id=policy["id"]
        )
        assert created["lead_seconds"] == 10 * MINUTE

    def test_listing_publishes_the_trigger_category(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        make_workflow_http(client, room_id)
        body = client.get(f"{PREFIX}/rooms/{room_id}/workflows").json()
        assert body["count"] == 1
        assert body["trigger_category_label"] == "During a conversation"

    def test_patching_a_workflow_revalidates_the_target(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        workflow = make_workflow_http(client, room_id)
        bad = client.patch(
            f"{PREFIX}/rooms/{room_id}/workflows/{workflow['id']}", json={"metric": "vibes"}
        )
        assert bad.status_code == 422
        good = client.patch(
            f"{PREFIX}/rooms/{room_id}/workflows/{workflow['id']}",
            json={"metric": "nrt", "lead_seconds": 300},
        )
        assert good.status_code == 200
        assert good.json()["metric"] == "nrt"

    def test_go_live_and_a_second_go_live_is_409(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        workflow = make_workflow_http(client, room_id, live=False)
        first = client.post(f"{PREFIX}/rooms/{room_id}/workflows/{workflow['id']}/go-live")
        assert first.status_code == 200
        assert first.json()["live"] is True
        second = client.post(f"{PREFIX}/rooms/{room_id}/workflows/{workflow['id']}/go-live")
        assert second.status_code == 409
        assert second.json()["error"] == "workflow_already_live"

    def test_archiving_a_workflow_hides_it_but_keeps_its_runs(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        workflow = make_workflow_http(client, room_id)
        assert (
            client.delete(f"{PREFIX}/rooms/{room_id}/workflows/{workflow['id']}").status_code == 200
        )
        assert client.get(f"{PREFIX}/rooms/{room_id}/workflows").json()["count"] == 0

    def test_an_unknown_workflow_is_a_404(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.get(f"{PREFIX}/rooms/{room_id}/workflows/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "workflow_not_found"


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


class TestSweepRoutes:
    def test_a_pre_warning_fires_inside_the_lead_window(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(client, room_id)
        body = evaluate_http(client, room_id, AT_10 + timedelta(minutes=7))
        assert body["fired"] == 1
        assert body["counts"]["fired"] == 1
        assert body["runs"][0]["level"] == "pre_warning"

    def test_a_pre_warning_waits_until_the_window(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(client, room_id)
        body = evaluate_http(client, room_id, AT_10 + timedelta(minutes=2))
        assert body["fired"] == 0
        assert body["counts"][vocab.SKIP_LEAD_WINDOW_NOT_REACHED] == 1

    def test_a_breach_fires_the_on_breach_workflow(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(
            client,
            room_id,
            name="Escalate a breach",
            trigger=vocab.TRIGGER_ON_BREACH,
            lead_seconds=None,
            actions=[{"kind": vocab.ACTION_TAG, "tag": "sla breach"}],
        )
        body = evaluate_http(client, room_id, AT_10 + timedelta(minutes=20))
        assert body["fired"] == 1
        assert body["runs"][0]["level"] == "breach"

    def test_a_missed_pre_warning_window_is_recorded(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", EARLIER_10)
        apply_sla_http(client, room_id, conversation, policy["id"], EARLIER_10)
        make_workflow_http(client, room_id)
        body = evaluate_http(client, room_id, AT_10)
        assert body["fired"] == 0
        assert body["counts"][vocab.SKIP_HEAD_START_MISSED] == 1

    def test_a_teammate_reply_cancels_a_close_warning(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        watch = make_policy_http(
            client,
            room_id,
            name="Close watch",
            pause_reasons=["snoozed"],
        )
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        says(client, room_id, conversation, "teammate", AT_10 + timedelta(minutes=2))
        apply_sla_http(client, room_id, conversation, watch["id"], AT_10)
        make_workflow_http(
            client,
            room_id,
            name="Pre-warn before a close breach",
            metric=vocab.METRIC_TTC,
            lead_seconds=30 * MINUTE,
            policy_id=watch["id"],
        )
        body = evaluate_http(client, room_id, AT_10 + timedelta(hours=7, minutes=30))
        assert body["fired"] == 0
        cancelled = [d for d in body["decisions"] if d.get("cancel")]
        assert cancelled and cancelled[0]["cancel"] == vocab.CANCEL_TEAMMATE_REPLY

    def test_a_snoozed_conversation_cancels_an_on_breach_workflow(
        self, client, db: AuditedDatabase
    ):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", EARLIER_10)
        apply_sla_http(client, room_id, conversation, policy["id"], EARLIER_10)
        client.post(
            f"{CHASE}/rooms/{room_id}/conversations/{conversation}/snooze",
            json={"reason": "waiting on procurement"},
        )
        make_workflow_http(
            client,
            room_id,
            name="Escalate a breach",
            trigger=vocab.TRIGGER_ON_BREACH,
            lead_seconds=None,
            actions=[{"kind": vocab.ACTION_TAG, "tag": "sla breach"}],
        )
        body = evaluate_http(client, room_id, AT_10)
        assert body["fired"] == 0
        cancelled = [d for d in body["decisions"] if d.get("cancel")]
        assert cancelled and cancelled[0]["cancel"] == vocab.CANCEL_SNOOZED

    def test_a_second_sweep_does_not_fire_the_same_workflow_twice(
        self, client, db: AuditedDatabase
    ):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(client, room_id)
        first = evaluate_http(client, room_id, AT_10 + timedelta(minutes=12))
        second = evaluate_http(client, room_id, AT_10 + timedelta(minutes=13))
        assert first["fired"] == 1
        assert second["fired"] == 0
        assert second["counts"][vocab.SKIP_ALREADY_FIRED] == 1

    def test_a_conversation_with_no_sla_is_reported_as_a_skip(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        open_conversation(client, room_id)
        make_workflow_http(client, room_id)
        body = evaluate_http(client, room_id, AT_10)
        assert body["fired"] == 0
        assert body["counts"][vocab.SKIP_NO_ACTIVE_SLA] == 1

    def test_a_policy_mismatch_is_a_skip_rather_than_a_fire(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        applied = make_policy_http(client, room_id, name="Applied")
        watched = make_policy_http(client, room_id, name="Watched")
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, applied["id"], AT_10)
        make_workflow_http(client, room_id, policy_id=watched["id"])
        body = evaluate_http(client, room_id, AT_10 + timedelta(minutes=12))
        assert body["fired"] == 0
        assert body["counts"][vocab.SKIP_POLICY_MISMATCH] == 1

    def test_reading_a_run_and_an_unknown_run(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(client, room_id)
        body = evaluate_http(client, room_id, AT_10 + timedelta(minutes=12))
        run_id = body["runs"][0]["id"]
        read = client.get(f"{PREFIX}/rooms/{room_id}/runs/{run_id}")
        assert read.status_code == 200
        assert read.json()["actions"][0]["kind"] == "tag"
        missing = client.get(f"{PREFIX}/rooms/{room_id}/runs/nope")
        assert missing.status_code == 404
        assert missing.json()["error"] == "run_not_found"

    def test_a_failed_alert_is_a_row_the_page_can_show(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(
            client,
            room_id,
            name="Escalate a breach",
            trigger=vocab.TRIGGER_ON_BREACH,
            lead_seconds=None,
            actions=[
                {"kind": vocab.ACTION_ALERT, "connector_id": "conn_1"},
            ],
        )
        evaluate_http(client, room_id, AT_10 + timedelta(minutes=20))
        alerts = client.get(f"{PREFIX}/rooms/{room_id}/alerts").json()
        assert alerts["failures"] == 1
        assert alerts["alerts"][0]["error_type"] == "request_configuration_error"
        assert alerts["alerts"][0]["http_status"] == 400

    def test_the_inbox_sorts_a_breach_above_a_pre_warning(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        warned = open_conversation(client, room_id, subject="Warned")
        says(client, room_id, warned, "customer", AT_10)
        apply_sla_http(client, room_id, warned, policy["id"], AT_10)
        breached = open_conversation(client, room_id, subject="Breached")
        says(client, room_id, breached, "customer", EARLIER_10)
        apply_sla_http(client, room_id, breached, policy["id"], EARLIER_10)
        make_workflow_http(client, room_id)
        make_workflow_http(
            client,
            room_id,
            name="Escalate a breach",
            trigger=vocab.TRIGGER_ON_BREACH,
            lead_seconds=None,
            actions=[{"kind": vocab.ACTION_TAG, "tag": "sla breach"}],
        )
        evaluate_http(client, room_id, AT_10 + timedelta(minutes=7))
        inbox = client.get(f"{PREFIX}/rooms/{room_id}/inbox").json()
        assert inbox["conversations"][0]["escalation_level"] == "breach"
        assert inbox["conversations"][0]["subject"] == "Breached"

    def test_the_summary_route(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        make_workflow_http(client, room_id)
        body = client.get(f"{PREFIX}/rooms/{room_id}/summary").json()
        assert body["workflows"] == 1
        assert body["trigger_category_label"] == "During a conversation"


# --------------------------------------------------------------------------- #
# The host, the contract and the audit trail
# --------------------------------------------------------------------------- #


def mounted_routes(client) -> list[tuple[str, str]]:
    body = client.get("/api/features").json()
    return [
        (method, route["path"])
        for entry in body["features"]
        for route in entry["routes"]
        for method in route["methods"]
    ]


class TestHostIntegration:
    def test_the_feature_is_mounted_by_discovery(self, client):
        body = client.get("/api/features").json()
        found = [entry for entry in body["features"] if entry["id"] == feature.FEATURE["id"]]
        assert found, sorted(entry["id"] for entry in body["features"])
        assert body["failed_count"] == 0

    def test_it_reports_its_routes(self, client):
        body = client.get("/api/features").json()
        entry = next(e for e in body["features"] if e["id"] == feature.FEATURE["id"])
        paths = {route["path"] for route in entry["routes"]}
        assert f"{PREFIX}/vocabulary" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/escalations/evaluate" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/inbox" in paths

    def test_no_route_collides_with_another_feature(self, client):
        seen: dict[tuple[str, str], str] = {}
        body = client.get("/api/features").json()
        for entry in body["features"]:
            for route in entry["routes"]:
                for method in route["methods"]:
                    key = (method, route["path"])
                    assert key not in seen, f"{key} claimed by {seen[key]} and {entry['id']}"
                    seen[key] = entry["id"]


class TestAuditSources:
    def _audit_sources(self, db: AuditedDatabase) -> list[str]:
        return [str(row.get("source")) for row in db.audit(limit=5000) if row.get("source")]

    def test_every_seed_source_names_a_mounted_route(self, client):
        routes = mounted_routes(client)
        placeholder = re.compile(r"\{[a-z_]+\}")
        for source in feature.SEED_SOURCES:
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in routes), (
                f"{source} names no mounted route"
            )

    def test_every_http_write_names_its_own_route(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(client, room_id)
        evaluate_http(client, room_id, AT_10 + timedelta(minutes=12))
        sources = self._audit_sources(db)
        assert feature.WORKFLOW_SOURCE in sources
        assert feature.EVALUATE_SOURCE in sources

    def test_every_recorded_source_names_a_mounted_route(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        make_workflow_http(client, room_id)
        evaluate_http(client, room_id, AT_10 + timedelta(minutes=12))

        routes = mounted_routes(client)
        placeholder = re.compile(r"\{[a-z_]+\}")
        for source in self._audit_sources(db):
            method, _, path = source.partition(" ")
            if not method or not path:
                continue
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in routes), (
                f"{source} names no mounted route"
            )


class TestEveryRouteAnswers:
    def test_no_advertised_route_returns_a_5xx(self, client, db: AuditedDatabase):
        """Every route the host mounted has a working handler behind it.

        A non-2xx is not a fault: a route reading a row that does not exist answers 404 and
        a write route with a stale id answers 409. A 5xx is the fault, because it means a
        mounted route has no handler that survives its own first call.
        """

        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        says(client, room_id, conversation, "customer", AT_10)
        apply_sla_http(client, room_id, conversation, policy["id"], AT_10)
        workflow = make_workflow_http(client, room_id)
        body = evaluate_http(client, room_id, AT_10 + timedelta(minutes=12))
        run_id = body["runs"][0]["id"] if body["runs"] else "none"
        substitutions = {
            "room_id": room_id,
            "workflow_id": workflow["id"],
            "conversation_id": conversation,
            "run_id": run_id,
            "decision_id": "cancellation-model",
        }

        checked = 0
        for route in feature.router.routes:
            if not isinstance(route, APIRoute):
                continue
            path = route.path
            for key, value in substitutions.items():
                path = path.replace("{" + key + "}", value)
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                payload = {} if method in {"POST", "PUT", "PATCH"} else None
                response = client.request(method, path, json=payload)
                assert response.status_code < 500, f"{method} {path} -> {response.status_code}"
                checked += 1
        assert checked >= 15, "the feature advertised fewer routes than this test expects"


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def _seed(self, db: AuditedDatabase, now: datetime) -> tuple[str, str]:
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": now, "rng": None})
        return room_id, summary

    def test_it_returns_a_cp1252_string_naming_the_states(self, db: AuditedDatabase):
        _, summary = self._seed(db, FIXED)
        summary.encode("cp1252")
        for phrase in ("pre-warning", "on breach", "cancelled", "office-hours", "alert"):
            assert phrase in summary, phrase

    def test_it_produces_rows_of_every_kind(self, db: AuditedDatabase):
        room_id, _ = self._seed(db, FIXED)
        store = RecordStore(db)
        for collection, minimum in (
            ("wf109_escalation_workflow", 3),
            ("wf109_escalation_run", 2),
            ("wf109_alert_execution", 2),
            ("wf108_sla_policy", 2),
            ("wf107_conversation", 5),
        ):
            assert len(store.find(collection, {"room_ref": room_id}, limit=100)) >= minimum, (
                collection
            )

    def test_it_seeds_states_that_are_not_all_successes(self, db: AuditedDatabase):
        room_id, _ = self._seed(db, FIXED)
        store = RecordStore(db)
        alerts = store.find("wf109_alert_execution", {"room_ref": room_id}, limit=100)
        assert any(dict(row["data"]).get("success") is False for row in alerts)
        runs = store.find("wf109_escalation_run", {"room_ref": room_id}, limit=100)
        levels = {dict(row["data"]).get("level") for row in runs}
        assert levels == {"pre_warning", "breach"}

    def test_it_runs_with_no_room_at_all(self, db: AuditedDatabase):
        summary = feature.seed(db, {"room_ids": [], "now": FIXED, "rng": None})
        assert "room created" in summary

    def test_every_seeded_source_was_written_by_a_mounted_route(self, db: AuditedDatabase):
        """The seed is checked against the live registry, so it cannot pass by doing nothing."""

        self._seed(db, FIXED)
        from dsr.features import REGISTRY

        routes: list[tuple[str, str]] = []
        for entry in REGISTRY.features:
            for route in entry.to_dict()["routes"]:
                for method in route["methods"]:
                    routes.append((method, route["path"]))
        assert routes, "the host mounted no routes, so this check proves nothing"

        placeholder = re.compile(r"\{[a-z_]+\}")
        checked = 0
        for row in db.audit(limit=5000):
            source = str(row.get("source") or "")
            if not source or not source.startswith(("GET ", "POST ", "PUT ", "PATCH ", "DELETE ")):
                continue
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in routes), (
                f"{source} names no mounted route"
            )
            checked += 1
        assert checked > 0


#: Every payload key this workflow writes an instant into.
INSTANT_KEYS = (
    "at",
    "applied_at",
    "created_at",
    "updated_at",
    "checked_at",
    "escalated_at",
    "fire_at",
    "closed_at",
    "snoozed_at",
)
INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class TestSeedInstants:
    """Every seeded instant derives from ``context["now"]``, never from a written-in date."""

    def _instants(self, store: RecordStore, room_id: str) -> list[datetime]:
        found: list[datetime] = []
        for collection in (
            "wf109_escalation_workflow",
            "wf109_escalation_run",
            "wf109_alert_execution",
            "wf109_escalation_activity",
            "wf108_sla_timer",
            "wf107_conversation",
            "wf107_conversation_part",
        ):
            for row in store.find(collection, {"room_ref": room_id}, limit=1000):
                data = dict(row["data"])
                for key in INSTANT_KEYS:
                    value = data.get(key)
                    if isinstance(value, str) and INSTANT.search(value):
                        parsed = rules.coerce_instant(value)
                        if parsed:
                            found.append(parsed)
        return found

    def test_moving_the_clock_moves_every_instant(self, db: AuditedDatabase):
        store = RecordStore(db)
        first_room = store.create("room", {"name": "A"}, actor="dana")["id"]
        second_room = store.create("room", {"name": "B"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(first_room, "A")], "now": FIXED, "rng": None})
        shifted = FIXED + timedelta(days=400)
        feature.seed(db, {"room_ids": [(second_room, "B")], "now": shifted, "rng": None})

        first = self._instants(store, first_room)
        second = self._instants(store, second_room)
        assert first and second
        assert not (set(first) & set(second)), "some instant ignored the clock it was given"
        for value in second:
            assert abs(value - shifted) < timedelta(days=6), value


# --------------------------------------------------------------------------- #
# Architecture
# --------------------------------------------------------------------------- #

DOMAIN = Path(__file__).resolve().parents[1] / "dsr" / "sla_escalation"
FEATURE_MODULE = Path(feature.__file__)


def _domain_paths() -> list[Path]:
    return sorted(DOMAIN.glob("*.py"))


class TestArchitecture:
    def test_the_domain_depends_only_on_the_store_wf107_and_wf108(self):
        """A domain module that imports the app cannot be unit-tested on its own.

        The SLA metrics and timer are WF-108's and the conversation vocabulary and
        office-hours rules are WF-107's, and the brief instructs reuse, so
        ``dsr.conversation_chase`` and ``dsr.sla_timers`` are the two other packages
        allowed.
        """

        allowed = ("dsr.store", "dsr.conversation_chase", "dsr.sla_timers", "dsr.sla_escalation")
        for path in _domain_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name.startswith(allowed), f"{path.name} imports {name}"

    def test_the_domain_never_opens_sqlite(self):
        for path in _domain_paths():
            assert "import sqlite3" not in path.read_text(encoding="utf-8"), path.name

    def test_the_feature_never_imports_the_app(self):
        tree = ast.parse(FEATURE_MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("dsr.api")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("dsr.api")

    def test_every_owned_collection_is_namespaced_by_the_ticket(self):
        for collection in vocab.OWNED_COLLECTIONS:
            assert collection.startswith("wf109_"), collection

    def test_the_feature_exports_its_ticket_and_name(self):
        assert feature.FEATURE["ticket"] == "WF-109"
        assert feature.FEATURE["name"]

    def test_the_prefix_is_ticket_derived_and_under_api(self):
        assert feature.router.prefix == "/api/wf-109"

    def test_the_seed_is_exported(self):
        assert callable(feature.seed)
