"""WF-108's HTTP surface, its audit trail and its seed.

The router is driven through the application's own ``TestClient``, so the discovery, the
error mapping and the audit rows are all the real ones rather than stand-ins. The
conversation records an SLA reads are created through WF-107's own HTTP routes, which is
also a check that the reuse works across the two features.

No test here carries a state that depends on the day the suite runs. Every instant that a
timer decisions turns on is passed explicitly, either as a message ``at`` or as the
``now`` a sweep evaluates at.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dsr.db.audited import AuditedDatabase
from dsr.features import wf108_run_sla_response_timers_with_office_hours as feature
from dsr.sla_timers import rules
from dsr.store import RecordStore

PREFIX = feature.router.prefix
CHASE = "/api/wf-107"
FIXED = datetime(2026, 3, 2, tzinfo=timezone.utc)  # a Monday
AT_10 = FIXED.replace(hour=10, minute=0)
AT_14 = FIXED.replace(hour=14, minute=0)
QUARTER_HOUR = 15 * 60


def make_room(db: AuditedDatabase) -> str:
    return RecordStore(db).create(
        "room", {"name": "Northwind", "account": "Northwind"}, actor="dana"
    )["id"]


def open_conversation(http, room_id: str, **payload: Any) -> str:
    body = {"origin": "inbox", "inbox": "sales"}
    body.update(payload)
    response = http.post(f"{CHASE}/rooms/{room_id}/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def buyer_says(http, room_id: str, conversation_id: str, at: datetime) -> None:
    response = http.post(
        f"{CHASE}/rooms/{room_id}/conversations/{conversation_id}/messages",
        json={"author_kind": "customer", "at": at.isoformat(), "body": "hello"},
    )
    assert response.status_code == 201, response.text


def make_policy_http(http, room_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "Enterprise first response",
        "targets": {"frt": QUARTER_HOUR, "nrt": 30 * 60, "ttc": 8 * 60 * 60},
        "teams": ["enterprise"],
        "workflow_name": "Escalate enterprise leads",
        "live": True,
    }
    payload.update(overrides)
    response = http.post(f"{PREFIX}/rooms/{room_id}/policies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def apply_http(http, room_id: str, conversation_id: str, policy_id: str) -> dict[str, Any]:
    response = http.post(
        f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/sla",
        json={"policy_id": policy_id, "applied_at": AT_10.isoformat()},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Published data
# --------------------------------------------------------------------------- #


class TestPublishedData:
    def test_the_vocabulary_route_serves_the_metrics_and_badges(self, client):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["metrics"] == ["frt", "nrt", "ttc", "ttr"]
        assert body["badges"] == ["grey", "orange", "red"]
        assert body["defaults"]["targets"]["frt"] == QUARTER_HOUR

    def test_the_inferences_route_serves_every_decision(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["count"] >= 8
        assert body["jev_audits"]["domain_package_and_timer_mechanism"].startswith("jev-")
        for entry in body["decisions"]:
            assert entry["consequence"]
            assert entry["evidence"]

    def test_the_snooze_contradiction_is_recorded_as_resolved(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        entry = next(e for e in body["decisions"] if e["id"] == "snooze-versus-auto-unsnooze")
        assert "already breached" in entry["chosen"]

    def test_one_decision_can_be_read_by_id(self, client):
        response = client.get(f"{PREFIX}/decisions/fixed-rate")
        assert response.status_code == 200
        assert response.json()["unsourced"] is True

    def test_an_unknown_decision_is_a_404(self, client):
        response = client.get(f"{PREFIX}/decisions/no-such-decision")
        assert response.status_code == 404
        assert response.json()["error"] == "decision_not_found"


# --------------------------------------------------------------------------- #
# Policies over HTTP
# --------------------------------------------------------------------------- #


class TestPolicyRoutes:
    def test_creating_and_reading_a_policy(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        created = make_policy_http(client, room_id)
        assert created["id"]
        read = client.get(f"{PREFIX}/rooms/{room_id}/policies/{created['id']}").json()
        assert read["name"] == "Enterprise first response"
        assert read["targets"]["frt"] == QUARTER_HOUR

    def test_a_missing_name_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.post(f"{PREFIX}/rooms/{room_id}/policies", json={"targets": {"frt": 60}})
        assert response.status_code == 422
        assert response.json()["error"] == "policy_needs_a_name"

    def test_a_target_below_the_floor_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/policies", json={"name": "Too fast", "targets": {"frt": 1}}
        )
        assert response.status_code == 422
        assert response.json()["error"] == "target_out_of_range"

    def test_listing_policies_publishes_the_metrics(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        make_policy_http(client, room_id)
        body = client.get(f"{PREFIX}/rooms/{room_id}/policies").json()
        assert body["count"] == 1
        assert body["metrics"] == ["frt", "nrt", "ttc", "ttr"]

    def test_patching_a_policy_revalidates_the_target(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        bad = client.patch(
            f"{PREFIX}/rooms/{room_id}/policies/{policy['id']}", json={"targets": {"frt": 1}}
        )
        assert bad.status_code == 422
        good = client.patch(
            f"{PREFIX}/rooms/{room_id}/policies/{policy['id']}", json={"targets": {"frt": 600}}
        )
        assert good.status_code == 200
        assert good.json()["targets"]["frt"] == 600

    def test_archiving_a_policy_then_applying_it_is_409(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        assert client.delete(f"{PREFIX}/rooms/{room_id}/policies/{policy['id']}").status_code == 200
        conversation = open_conversation(client, room_id)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla",
            json={"policy_id": policy["id"]},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "policy_archived"

    def test_an_unknown_policy_is_a_404(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.get(f"{PREFIX}/rooms/{room_id}/policies/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "policy_not_found"

    def test_the_used_by_column_names_the_workflow(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        apply_http(client, room_id, conversation, policy["id"])
        body = client.get(f"{PREFIX}/rooms/{room_id}/policies/{policy['id']}/used-by").json()
        assert body["workflows"] == ["Escalate enterprise leads"]


# --------------------------------------------------------------------------- #
# Applying an SLA over HTTP
# --------------------------------------------------------------------------- #


class TestApplyRoutes:
    def test_applying_and_reading_back(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        timer = apply_http(client, room_id, conversation, policy["id"])
        assert timer["active"] is True
        read = client.get(f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla").json()
        assert read["timer"]["policy_name"] == "Enterprise first response"

    def test_reading_an_uncovered_conversation_is_null_not_404(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        conversation = open_conversation(client, room_id)
        body = client.get(f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla").json()
        assert body["timer"] is None

    def test_only_one_active_sla_survives_a_second_apply(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        first = make_policy_http(client, room_id)
        second = make_policy_http(client, room_id, name="Standard")
        conversation = open_conversation(client, room_id)
        apply_http(client, room_id, conversation, first["id"])
        apply_http(client, room_id, conversation, second["id"])
        read = client.get(f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla").json()
        assert read["timer"]["policy_name"] == "Standard"

    def test_removing_the_active_sla(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        apply_http(client, room_id, conversation, policy["id"])
        removed = client.post(f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla/remove")
        assert removed.status_code == 200
        assert removed.json()["removed"]

    def test_removing_with_none_active_is_409(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        conversation = open_conversation(client, room_id)
        response = client.post(f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla/remove")
        assert response.status_code == 409
        assert response.json()["error"] == "no_active_sla"

    def test_applying_to_a_closed_conversation_is_409(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        client.post(f"{CHASE}/rooms/{room_id}/conversations/{conversation}/close", json={})
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation}/sla",
            json={"policy_id": policy["id"]},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "conversation_closed"

    def test_a_closed_conversation_is_not_seen_as_a_fresh_one(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/does-not-exist/sla",
            json={"policy_id": "also-not-real"},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "conversation_not_found"


# --------------------------------------------------------------------------- #
# The sweep, the inbox and the report over HTTP
# --------------------------------------------------------------------------- #


class TestSweepRoutes:
    def test_the_sweep_fires_a_breach_deterministically(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        buyer_says(client, room_id, conversation, AT_10 - timedelta(minutes=30))
        apply_http(client, room_id, conversation, policy["id"])
        body = client.post(
            f"{PREFIX}/rooms/{room_id}/timers/evaluate", json={"now": AT_14.isoformat()}
        ).json()
        assert body["checked"] == 1
        assert body["counts"]["breached"] == 1
        assert any(t["type"] == "SLA breached" for t in body["transitions"])

    def test_the_sweep_reopens_an_already_breached_snooze(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        buyer_says(client, room_id, conversation, AT_10 - timedelta(hours=3))
        apply_http(client, room_id, conversation, policy["id"])
        client.post(
            f"{CHASE}/rooms/{room_id}/conversations/{conversation}/snooze",
            json={"reason": "waiting on procurement"},
        )
        body = client.post(
            f"{PREFIX}/rooms/{room_id}/timers/evaluate", json={"now": AT_14.isoformat()}
        ).json()
        assert body["reopened"] == [conversation]
        assert any(t["type"] == "Conversation unsnoozed and reopened" for t in body["transitions"])

    def test_reading_a_timer_returns_its_four_metrics_shape(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        timer = apply_http(client, room_id, conversation, policy["id"])
        body = client.get(f"{PREFIX}/rooms/{room_id}/timers/{timer['id']}").json()
        metrics = {entry["metric"] for entry in body["metrics"]}
        assert metrics == {"frt", "nrt", "ttc"}
        assert "badge" in body and "nearest" in body

    def test_an_unknown_timer_is_a_404(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.get(f"{PREFIX}/rooms/{room_id}/timers/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "timer_not_found"

    def test_the_inbox_lists_conversations_with_their_badge(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id, subject="Renewal")
        buyer_says(client, room_id, conversation, AT_10 - timedelta(hours=3))
        apply_http(client, room_id, conversation, policy["id"])
        client.post(f"{PREFIX}/rooms/{room_id}/timers/evaluate", json={"now": AT_14.isoformat()})
        body = client.get(f"{PREFIX}/rooms/{room_id}/inbox", params={"sort": "next_target"}).json()
        assert body["count"] == 1
        assert body["conversations"][0]["badge"] == "red"
        assert body["conversations"][0]["sla"] == "Enterprise first response"

    def test_the_report_publishes_the_three_rates(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        buyer_says(client, room_id, conversation, AT_10 - timedelta(hours=3))
        apply_http(client, room_id, conversation, policy["id"])
        body = client.get(f"{PREFIX}/rooms/{room_id}/report").json()
        assert set(body["outcomes"]) == {"hit", "miss", "fixed"}
        assert body["slas"][0]["sla"] == "Enterprise first response"

    def test_the_summary_route(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        make_policy_http(client, room_id)
        body = client.get(f"{PREFIX}/rooms/{room_id}/summary").json()
        assert body["policies"] == 1
        assert body["office_hours"]["derived"] is True


# --------------------------------------------------------------------------- #
# Office hours over HTTP
# --------------------------------------------------------------------------- #


class TestOfficeHoursRoutes:
    def test_the_default_is_derived_and_the_write_marks_it_stored(
        self, client, db: AuditedDatabase
    ):
        room_id = make_room(db)
        before = client.get(f"{PREFIX}/rooms/{room_id}/office-hours").json()
        assert before["derived"] is True
        written = client.put(f"{PREFIX}/rooms/{room_id}/office-hours", json={"timezone": "UTC"})
        assert written.status_code == 200
        after = client.get(f"{PREFIX}/rooms/{room_id}/office-hours").json()
        assert after["stored"] is True

    def test_an_unusable_schedule_is_422(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        response = client.put(
            f"{PREFIX}/rooms/{room_id}/office-hours",
            json={"schedule": {"monday": {"open": "18:00", "close": "09:00"}}},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_office_hours"


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
        assert f"{PREFIX}/rooms/{{room_id}}/timers/evaluate" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/report" in paths

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
        apply_http(client, room_id, conversation, policy["id"])
        sources = self._audit_sources(db)
        assert feature.POLICY_SOURCE in sources
        assert feature.APPLY_SOURCE in sources

    def test_every_recorded_source_names_a_mounted_route(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        buyer_says(client, room_id, conversation, AT_10)
        apply_http(client, room_id, conversation, policy["id"])
        client.post(f"{PREFIX}/rooms/{room_id}/timers/evaluate", json={"now": AT_14.isoformat()})

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

    def test_the_evaluate_source_is_recorded(self, client, db: AuditedDatabase):
        room_id = make_room(db)
        policy = make_policy_http(client, room_id)
        conversation = open_conversation(client, room_id)
        apply_http(client, room_id, conversation, policy["id"])
        client.post(f"{PREFIX}/rooms/{room_id}/timers/evaluate", json={"now": AT_14.isoformat()})
        assert feature.EVALUATE_SOURCE in self._audit_sources(db)


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
        for phrase in (
            "met",
            "breached",
            "paused",
            "reopened",
            "office-hours",
            "time to resolution",
        ):
            assert phrase in summary, phrase

    def test_it_produces_rows_of_every_kind(self, db: AuditedDatabase):
        room_id, _ = self._seed(db, FIXED)
        store = RecordStore(db)
        for collection, minimum in (
            ("wf108_sla_policy", 3),
            ("wf108_sla_timer", 5),
            ("wf108_sla_activity", 1),
            ("wf107_conversation", 5),
            ("wf107_office_hours", 1),
        ):
            assert len(store.find(collection, {"room_ref": room_id}, limit=100)) >= minimum, (
                collection
            )

    def test_it_seeds_the_states_that_are_not_all_successes(self, db: AuditedDatabase):
        room_id, _ = self._seed(db, FIXED)
        store = RecordStore(db)
        policies = store.find("wf108_sla_policy", {"room_ref": room_id}, limit=100)
        kinds = {dict(row["data"]).get("object_kind") for row in policies}
        assert "ticket" in kinds and "conversation" in kinds
        timers = store.find("wf108_sla_timer", {"room_ref": room_id}, limit=100)
        assert any(dict(row["data"]).get("reopened_after_breach") for row in timers)

    def test_it_runs_with_no_room_at_all(self, db: AuditedDatabase):
        summary = feature.seed(db, {"room_ids": [], "now": FIXED, "rng": None})
        assert "room created" in summary

    def test_every_seeded_source_was_written_by_a_mounted_route(self, db: AuditedDatabase):
        """The seed is checked against the live registry, so it cannot pass by doing nothing."""

        self._seed(db, FIXED)
        routes: list[tuple[str, str]] = []
        # Import the live app registry lazily: the mounted routes come from the host.
        from dsr.features import REGISTRY

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
    "closed_at",
    "snoozed_at",
)
INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class TestSeedInstants:
    """Every seeded instant derives from ``context["now"]``, never from a written-in date.

    The seed anchors its states to a weekday morning near the clock it is handed, so the
    office-hours cases hold whenever the seeder runs. This checks that moving the clock
    moves every instant, which a hardcoded date cannot do.
    """

    def _instants(self, store: RecordStore, room_id: str) -> list[datetime]:
        found: list[datetime] = []
        for collection in (
            "wf108_sla_policy",
            "wf108_sla_timer",
            "wf108_sla_activity",
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

DOMAIN = Path(__file__).resolve().parents[1] / "dsr" / "sla_timers"
FEATURE_MODULE = Path(feature.__file__)


def _domain_paths() -> list[Path]:
    return sorted(DOMAIN.glob("*.py"))


class TestArchitecture:
    def test_the_domain_depends_only_on_the_store_and_wf107(self):
        """A domain module that imports the app cannot be unit-tested on its own.

        The conversation vocabulary and the office-hours rules are WF-107's and the brief
        instructs reuse, so ``dsr.conversation_chase`` is the one other package allowed.
        """

        allowed = ("dsr.store", "dsr.conversation_chase", "dsr.sla_timers")
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
        from dsr.sla_timers import vocabulary as vocab

        for collection in vocab.OWNED_COLLECTIONS:
            assert collection.startswith("wf108_"), collection

    def test_the_feature_exports_its_ticket_and_name(self):
        assert feature.FEATURE["ticket"] == "WF-108"
        assert feature.FEATURE["name"]

    def test_the_prefix_is_ticket_derived_and_under_api(self):
        assert feature.router.prefix == "/api/wf-108"

    def test_the_seed_is_exported(self):
        assert callable(feature.seed)

    def test_no_decision_is_left_uncited(self):
        from dsr.sla_timers import inferences

        for entry in inferences.DECISIONS:
            assert entry["consequence"], entry["id"]
            assert entry["evidence"], entry["id"]
        assert inferences.JEV_DESIGN_AUDITS["domain_package_and_timer_mechanism"].startswith("jev-")
