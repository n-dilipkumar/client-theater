"""Tests for WF-048: validate the connector against a sandbox or test account.

The domain is :mod:`dsr.connector_sandbox` and the HTTP surface is
``backend/dsr/features/wf048_validate_the_connector_against_a_sandb.py``. The
split mirrors the split in the code: the researched rules are tested against
the domain, the wiring through the feature's own router.

What is pinned, and why each one matters
----------------------------------------
* **The environment vocabulary is enforced, not displayed.** Production
  cannot be a test environment; a trial expires after 30 days and is
  limited to one per user; the default environment carries the sourced
  warning; a sandbox records what it was copied from and that it can be
  reset. Each is a quoted Power Platform fact, so a change has to be
  argued with here.
* **HubSpot's floors are refusals.** "platform version `2025.2` or later and
  CLI version `8.3.0` or later" - a create below either cannot succeed
  against the real platform, so it is refused rather than warned.
* **The four assertions are scored from the vendor's own answers.** A
  sandbox that reports ``created: true`` twice has created a second record,
  and the dedupe assertion fails on that fact - not on a status the room
  wrote by hand.
* **The quota assertion is about the run's conduct.** The run stops on the
  first 429 / exhausted signal, records the ``Retry-After``, and skips the
  rest; nothing may be sent after the signal.
* **Promotion is gated on green.** "Only after green does the admin switch
  the connection to production" - no run, or a failed run, is a 422, and
  the promotion names the green run it came from.
* **Revert touches only the test row.** The production row was never part
  of the test.
* **The audit trail names the route that served the write** - hard rule 4 of
  the build brief, and the defect the contract names by name.
* **A run and its write-back are one transaction.** The run record and the
  connection's ``last_run_*`` pointers land together or not at all.
"""

from __future__ import annotations

import inspect
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr import connector_sandbox as cs
from dsr.connector_sandbox import environments as envs
from dsr.connector_sandbox import runs as runs_mod
from dsr.connector_sandbox.errors import (
    CliTooOldError,
    NotValidatedError,
    PlatformTooOldError,
    ProductionEnvironmentRefused,
    SandboxError,
    TestSyncRefused,
    TrialLimitError,
    UnknownConnectionError,
    UnknownEnvironmentKind,
    UnknownRoomError,
    UnknownRunError,
)
from dsr.connector_sandbox.transport import SandboxResponse, ScriptedTransport, parse_quota
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

PREFIX = "/api/wf-048"
MODULE = "wf048_validate_the_connector_against_a_sandb"
FEATURE_ID = "wf-048-validate-the-connector-against-a-sandb"

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

#: The researched version floors, quoted once so a change has to be argued with.
HUBSPOT_MIN_PLATFORM = "2025.2"
HUBSPOT_MIN_CLI = "8.3.0"
TRIAL_DAYS = 30


def feature_module():
    return load_feature(MODULE)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    """A store over a throwaway audited database, for the domain tests."""
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(
        Path(tmp.name) / "wf048.db", mirror_dir=Path(tmp.name) / "audit", actor="test"
    )
    yield RecordStore(db)
    db.close()
    tmp.cleanup()


@pytest.fixture()
def client(monkeypatch):
    """A TestClient over a throwaway database, as test_features.py does it."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf048.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(_app()) as test_client:
        yield test_client
    tmp.cleanup()


def _app():
    from dsr.api import app

    return app


def make_room(client, name: str = "Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def make_connection(store, room_id, *, vendor="salesforce", **extra) -> dict:
    payload = {
        "vendor": vendor,
        "base_url": f"https://{vendor}.example/sandbox",
        "object_name": "Engagement__c",
        "key_field": "External_Engagement_Id__c",
        "field_map": {"event_type": "Event_Type__c"},
        "tenant": "acme",
    }
    payload.update(extra)
    return cs.create_connection(store, room_id, payload, actor="test", source="test")


def make_sandbox(store, production, *, env_type="sandbox", **extra) -> dict:
    spec = {
        "kind": "power_platform",
        "room_id": production["room_id"],
        "env_type": env_type,
    }
    spec.update(extra)
    return cs.create_test_environment(store, production["id"], spec, now=NOW, actor="test", source="test")


#: A green script: create honoured with a record id, the resend updating the
#: same record, and the poisoned allOrNone chunk rejected with nothing written.
GREEN_SCRIPT = [
    {"status": 201, "body": '{"id": "sbx-0001", "created": true, "success": true}',
     "headers": {"Sforce-Limit-Info": "api-usage=31/5000"}},
    {"status": 200, "body": '{"id": "sbx-0001", "created": false, "success": true}',
     "headers": {"Sforce-Limit-Info": "api-usage=32/5000"}},
    {"status": 400,
     "body": '{"records": [{"success": false, "errors": [{"message": "rolled back"}]}, {"success": false}]}'},
]


def scripted_transport(responses: list[dict]) -> ScriptedTransport:
    """A transport from a script of ``SandboxResponse``-shaped dicts."""
    return ScriptedTransport(
        [
            SandboxResponse(
                status=int(entry.get("status", 200)),
                body=str(entry.get("body", "")),
                headers=dict(entry.get("headers") or {}),
            )
            for entry in responses
        ]
    )


def demo_transport(name: str) -> ScriptedTransport:
    """A transport from one of the feature's demo scripts."""
    return scripted_transport(list(feature_module().DEMO_SCRIPTS[name]))


def run(store, connection_id, transport, *, source="test"):
    return cs.execute_run(store, connection_id, transport=transport, now=NOW, actor="test", source=source)


def by_kind(result: "cs.RunResult", kind: str) -> dict:
    return {entry["kind"]: entry for entry in result.assertions}[kind]


def green_sandbox(store, room_id):
    """A production row, a sandbox, and a green run behind them."""
    production = make_connection(store, room_id)
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], demo_transport("green"))
    return production, sandbox, result


def make_http_connection(client, room_id, *, vendor="salesforce", **extra) -> dict:
    """Register a connection through the HTTP route, as the page would."""
    payload = {
        "vendor": vendor,
        "base_url": f"https://{vendor}.example/prod",
        "object_name": "Engagement__c",
        "key_field": "External_Engagement_Id__c",
    }
    payload.update(extra)
    response = client.post(f"{PREFIX}/rooms/{room_id}/connections", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and the feature contract
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    body = client.get("/api/features").json()
    ids = {feature["id"] for feature in body["features"]}
    assert FEATURE_ID in ids
    assert body["failed_count"] == 0


def test_the_feature_reports_its_prefix_and_routes(client):
    feature = next(
        f for f in client.get("/api/features").json()["features"] if f["id"] == FEATURE_ID
    )
    assert feature["prefix"] == PREFIX
    assert feature["ticket"] == "WF-048"
    paths = {route["path"] for route in feature["routes"]}
    assert f"{PREFIX}/rooms/{{room_id}}/connections" in paths
    assert f"{PREFIX}/connections/{{connection_id}}/test-environment" in paths
    assert f"{PREFIX}/connections/{{connection_id}}/run-test-sync" in paths
    assert f"{PREFIX}/connections/{{connection_id}}/promote" in paths


def test_the_prefix_is_ticket_shaped():
    assert feature_module().router.prefix == PREFIX


def test_the_feature_does_not_import_the_app():
    source = Path(feature_module().__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source


def test_the_feature_takes_its_dependencies_from_deps():
    source = Path(feature_module().__file__).read_text(encoding="utf-8")
    assert "from dsr.deps import" in source


def test_the_domain_has_no_web_framework_dependency():
    """The domain is testable on its own, which is why it has no FastAPI."""
    for module in (runs_mod, cs.connections, cs.environments, cs.automation, cs.transport):
        source = Path(inspect.getmodule(module).__file__).read_text(encoding="utf-8")
        assert "fastapi" not in source, module
        assert "dsr.api" not in source, module


def test_the_domain_never_opens_the_database_itself():
    for module in (runs_mod, cs.connections, cs.environments, cs.automation, cs.transport):
        source = Path(inspect.getmodule(module).__file__).read_text(encoding="utf-8")
        assert "sqlite3" not in source, module
        assert ".connect(" not in source, module


def test_the_domain_imports_no_other_feature():
    """A feature must not depend on another feature, including its domain."""
    for module in (runs_mod, cs.connections, cs.environments, cs.automation):
        source = Path(inspect.getmodule(module).__file__).read_text(encoding="utf-8")
        assert "from dsr.crm" not in source, module
        assert "dsr.crm_upsert" not in source, module


def test_the_error_handlers_map_only_this_features_own_types():
    handlers = feature_module().EXCEPTION_HANDLERS
    for error_type in handlers:
        assert error_type.__module__.startswith("dsr.connector_sandbox"), error_type
    assert SandboxError in handlers
    assert NotValidatedError in handlers
    assert UnknownConnectionError in handlers
    assert UnknownRoomError in handlers
    assert UnknownRunError in handlers


def test_the_refusal_subclasses_are_not_registered_again():
    """The concrete refusals are subclasses of the base, so registering them
    again would collide with the host and be refused."""
    handlers = feature_module().EXCEPTION_HANDLERS
    for refusal in (
        TestSyncRefused,
        ProductionEnvironmentRefused,
        TrialLimitError,
        PlatformTooOldError,
        CliTooOldError,
    ):
        assert issubclass(refusal, SandboxError)
        assert refusal not in handlers


def test_no_migration_or_typed_column_was_added():
    """The feature adds files only: no schema statement, no table creation."""
    for module in (runs_mod, cs.connections, cs.environments, cs.automation):
        source = Path(inspect.getmodule(module).__file__).read_text(encoding="utf-8")
        assert "CREATE TABLE" not in source.upper(), module
        assert "ALTER TABLE" not in source.upper(), module


def test_room_scoped_routes_keep_the_room_in_the_path():
    """Brief rule 3: a room-scoped path stays room-scoped."""
    for route in feature_module().router.routes:
        path = route.path
        if "room_id" in path:
            assert path.startswith(f"{PREFIX}/rooms/{{room_id}}"), path
        else:
            assert "room_id" not in path, path


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_environment_types_are_the_researched_vocabulary():
    assert cs.describe_vocabulary()["environment_types"] == (
        "sandbox", "default", "trial", "developer", "production",
    )


def test_only_the_non_production_types_can_be_test_environments():
    assert cs.describe_vocabulary()["test_environment_types"] == (
        "sandbox", "default", "trial", "developer",
    )


def test_the_sandbox_fact_carries_the_copy_and_reset_quote():
    facts = cs.describe_vocabulary()["environment_facts"]["sandbox"]
    assert "copy and reset" in facts["quote"]
    assert facts["supports_copy"] is True
    assert facts["supports_reset"] is True


def test_the_default_fact_carries_the_no_backup_quote():
    facts = cs.describe_vocabulary()["environment_facts"]["default"]
    assert "doesn't provide any backup guarantees" in facts["quote"]
    assert facts["supports_reset"] is False


def test_the_trial_fact_carries_both_researched_limits():
    facts = cs.describe_vocabulary()["environment_facts"]["trial"]
    assert "30 days" in facts["quote"]
    assert "one per user" in facts["quote"]
    assert facts["lifetime_days"] == TRIAL_DAYS
    assert facts["per_user_limit"] == 1


def test_the_hubspot_facts_carry_the_sourced_floors_and_command():
    hubspot = cs.describe_vocabulary()["hubspot"]
    assert hubspot["min_platform"] == HUBSPOT_MIN_PLATFORM
    assert hubspot["min_cli"] == HUBSPOT_MIN_CLI
    assert hubspot["cli_command"] == "hs test-account create"


def test_the_automation_fact_is_the_documented_ci_path():
    automation = cs.describe_vocabulary()["automation"]
    assert "GitHub Actions creates the test account from a config file on every push" in automation["quote"]


def test_the_assertion_vocabulary_is_the_researched_four():
    assert set(cs.describe_vocabulary()["assertions"]) == {
        "object_created",
        "dedupe_key_honoured",
        "rollback_fired",
        "quota_headers_behaved",
    }


def test_the_research_gap_is_stated_not_hidden():
    gaps = cs.describe_vocabulary()["gaps"]
    assert "Salesforce sandbox types and scratch orgs could not be sourced" in gaps["salesforce_sandbox_types"]


def test_the_three_sources_are_the_researched_ones():
    sources = cs.describe_vocabulary()["sources"]
    assert len(sources) == 3
    assert "learn.microsoft.com" in sources[0]
    assert "developers.hubspot.com" in sources[1]
    assert "developers.hubspot.com" in sources[2]


def test_the_user_flow_says_what_the_run_asserts():
    flow = cs.describe_vocabulary()["user_flow"][0]
    assert "synthetic buyers" in flow
    assert "only after green" in flow


def test_the_data_flow_names_the_promotion_endpoints():
    flow = cs.describe_vocabulary()["data_flow"][0]
    assert "promote or revert" in flow


# --------------------------------------------------------------------------- #
# Version floors
# --------------------------------------------------------------------------- #


def test_the_floor_is_met_at_the_boundary():
    assert envs.meets_floor("2025.2", HUBSPOT_MIN_PLATFORM) is True
    assert envs.meets_floor("8.3.0", HUBSPOT_MIN_CLI) is True


def test_below_the_floor_is_refused():
    assert envs.meets_floor("2025.1", HUBSPOT_MIN_PLATFORM) is False
    assert envs.meets_floor("8.2.9", HUBSPOT_MIN_CLI) is False


def test_a_patch_release_above_the_floor_meets_it():
    assert envs.meets_floor("2025.2.1", HUBSPOT_MIN_PLATFORM) is True


def test_an_empty_version_meets_nothing():
    assert envs.meets_floor("", HUBSPOT_MIN_PLATFORM) is False


@pytest.mark.parametrize(
    "version,expected",
    [("2025.2", (2025, 2)), ("8.3.0", (8, 3, 0)), ("2025.2-beta1", (2025, 2))],
)
def test_version_tuple_parsing(version, expected):
    assert envs.version_tuple(version) == expected


# --------------------------------------------------------------------------- #
# Creating a test environment
# --------------------------------------------------------------------------- #


def test_a_sandbox_is_a_second_row_cloned_from_the_production_mapping(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    assert sandbox["id"] != production["id"]
    assert sandbox["data"]["environment"] == "test"
    # Same mapping: "same mapping + same code path as production".
    assert sandbox["data"]["object_name"] == production["data"]["object_name"]
    assert sandbox["data"]["key_field"] == production["data"]["key_field"]
    assert sandbox["data"]["field_map"] == production["data"]["field_map"]
    # Different coordinates: "sandbox base URL / separate OAuth client".
    assert sandbox["data"]["base_url"] != production["data"]["base_url"]
    assert sandbox["data"]["source_connection_id"] == production["id"]


def test_a_sandbox_records_what_it_was_copied_from_and_that_it_resets(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    assert sandbox["data"]["copied_from"] == production["data"]["base_url"]
    assert sandbox["data"]["resettable"] is True


def test_a_trial_expires_after_the_sourced_30_days(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production, env_type="trial", owner="sam")
    assert sandbox["data"]["expires_at"] == (NOW + timedelta(days=TRIAL_DAYS)).isoformat(timespec="seconds")


def test_a_second_live_trial_for_one_user_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    make_sandbox(store, production, env_type="trial", owner="sam")
    with pytest.raises(TrialLimitError) as caught:
        make_sandbox(store, make_connection(store, room["id"]), env_type="trial", owner="sam")
    assert "one per user" in str(caught.value)


def test_an_expired_trial_frees_the_slot(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    make_sandbox(store, production, env_type="trial", owner="sam")
    second = cs.create_test_environment(
        store,
        production["id"],
        {"kind": "power_platform", "env_type": "trial", "owner": "sam"},
        now=NOW + timedelta(days=TRIAL_DAYS + 1),
        actor="test",
        source="test",
    )
    assert second["data"]["env_type"] == "trial"


def test_a_trial_for_a_different_user_is_a_different_slot(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    make_sandbox(store, production, env_type="trial", owner="sam")
    second = make_sandbox(store, make_connection(store, room["id"]), env_type="trial", owner="kim")
    assert second["data"]["owner"] == "kim"


def test_production_cannot_be_a_test_environment(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(ProductionEnvironmentRefused) as caught:
        make_sandbox(store, production, env_type="production")
    assert "production cannot be a test environment" in str(caught.value)


def test_an_unknown_env_type_names_the_vocabulary(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(SandboxError) as caught:
        make_sandbox(store, production, env_type="telepathy")
    assert "sandbox" in str(caught.value)
    assert "trial" in str(caught.value)


def test_the_default_environment_warns_about_backups(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production, env_type="default")
    assert "shouldn't be used for production workloads" in sandbox["data"]["warning"]


def test_a_developer_environment_is_a_legitimate_slot(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production, env_type="developer")
    assert sandbox["data"]["env_type"] == "developer"
    assert sandbox["data"]["expires_at"] is None


def test_a_hubspot_account_needs_the_config_file(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"], vendor="hubspot")
    with pytest.raises(SandboxError) as caught:
        cs.create_test_environment(
            store,
            production["id"],
            {"kind": "hubspot", "platform_version": "2025.2", "cli_version": "8.3.0"},
            now=NOW,
            actor="test",
            source="test",
        )
    assert "config" in str(caught.value)


@pytest.mark.parametrize("platform", ["", "2025.1", "2024.12", "0.0"])
def test_a_hubspot_account_below_the_platform_floor_is_refused(platform, store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"], vendor="hubspot")
    with pytest.raises(PlatformTooOldError) as caught:
        cs.create_test_environment(
            store,
            production["id"],
            {
                "kind": "hubspot",
                "config": {"subscription": "Enterprise"},
                "platform_version": platform,
                "cli_version": "8.3.0",
            },
            now=NOW,
            actor="test",
            source="test",
        )
    assert HUBSPOT_MIN_PLATFORM in str(caught.value)


def test_a_hubspot_account_with_no_cli_version_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"], vendor="hubspot")
    with pytest.raises(CliTooOldError) as caught:
        cs.create_test_environment(
            store,
            production["id"],
            {
                "kind": "hubspot",
                "config": {"subscription": "Enterprise"},
                "platform_version": "2025.2",
            },
            now=NOW,
            actor="test",
            source="test",
        )
    assert HUBSPOT_MIN_CLI in str(caught.value)


def test_a_hubspot_account_on_cli_8_2_9_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"], vendor="hubspot")
    with pytest.raises(CliTooOldError) as caught:
        cs.create_test_environment(
            store,
            production["id"],
            {
                "kind": "hubspot",
                "config": {"subscription": "Enterprise"},
                "platform_version": "2025.2",
                "cli_version": "8.2.9",
            },
            now=NOW,
            actor="test",
            source="test",
        )
    assert HUBSPOT_MIN_CLI in str(caught.value)


def test_a_hubspot_account_at_the_floors_is_created(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"], vendor="hubspot")
    sandbox = cs.create_test_environment(
        store,
        production["id"],
        {
            "kind": "hubspot",
            "config": {"subscription": "Enterprise", "tier": "Professional"},
            "platform_version": "2025.2",
            "cli_version": "8.3.0",
        },
        now=NOW,
        actor="test",
        source="test",
    )
    assert sandbox["data"]["env_type"] == "hubspot_test_account"
    assert sandbox["data"]["cli_command"] == "hs test-account create"


def test_a_test_environment_for_an_unknown_connection_is_404(store):
    with pytest.raises(UnknownConnectionError):
        cs.create_test_environment(
            store, "nope", {"kind": "power_platform"}, now=NOW, actor="test", source="test"
        )


def test_a_test_environment_for_an_unknown_room_is_404(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(UnknownRoomError):
        cs.create_test_environment(
            store,
            production["id"],
            {"kind": "power_platform", "env_type": "sandbox", "room_id": "room-absent"},
            now=NOW,
            actor="test",
            source="test",
        )


def test_an_unknown_kind_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(UnknownEnvironmentKind):
        make_sandbox(store, production, kind="gopher")


def test_a_reverted_test_environment_leaves_the_listing(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    first = make_sandbox(store, production)
    cs.revert(store, first["id"], now=NOW, actor="test", source="test")
    make_sandbox(store, production)
    rows = envs.list_test_environment_rows(store, production["id"])
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# Conversion: production to sandbox cannot be blocked
# --------------------------------------------------------------------------- #


def test_converting_production_to_sandbox_is_allowed(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    converted = cs.convert_production_to_sandbox(
        store, production["id"], now=NOW, actor="test", source="test"
    )
    assert converted["data"]["environment"] == "converted_sandbox"
    assert converted["data"]["previous_environment"] == "production"


def test_the_conversion_records_who_and_when(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    converted = cs.convert_production_to_sandbox(
        store, production["id"], now=NOW, actor="test", source="test"
    )
    assert converted["data"]["converted_at"] == NOW.isoformat(timespec="seconds")


def test_converting_an_unknown_connection_is_404(store):
    with pytest.raises(UnknownConnectionError):
        cs.convert_production_to_sandbox(store, "nope", now=NOW, actor="test", source="test")


# --------------------------------------------------------------------------- #
# The four assertions
# --------------------------------------------------------------------------- #


def test_the_green_path_passes_all_four(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, _, result = green_sandbox(store, room["id"])
    assert result.passed is True
    assert result.status == "passed"
    assert {entry["outcome"] for entry in result.assertions} == {"passed"}


def test_the_create_assertion_names_the_record_id(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, _, result = green_sandbox(store, room["id"])
    assert result.assertions[0]["kind"] == "object_created"
    assert "sbx-0001" in result.assertions[0]["detail"]


def test_the_dedupe_assertion_holds_when_the_key_updated_the_same_record(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, _, result = green_sandbox(store, room["id"])
    entry = by_kind(result, "dedupe_key_honoured")
    assert entry["outcome"] == "passed"
    assert "sbx-0001" in entry["detail"]


def test_a_second_created_record_fails_the_dedupe_assertion(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], demo_transport("dedupe_broken"))
    entry = by_kind(result, "dedupe_key_honoured")
    assert entry["outcome"] == "failed"
    assert "second record" in entry["detail"]
    assert result.passed is False


def test_an_accepted_rollback_chunk_fails_the_rollback_assertion(store):
    """"rollback fired on an intentionally bad row" - a vendor that accepted
    the chunk wrote the good row beside the bad one, so the assertion fails."""
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], demo_transport("accepted_chunk"))
    entry = by_kind(result, "rollback_fired")
    assert entry["outcome"] == "failed"
    assert "written" in entry["detail"]
    assert result.passed is False


def test_a_400_that_still_wrote_a_row_fails_the_rollback_assertion(store):
    transport = scripted_transport(
        [
            {"status": 201, "body": '{"id": "a1", "created": true}'},
            {"status": 200, "body": '{"id": "a1", "created": false}'},
            {"status": 400,
             "body": '{"records": [{"id": "a2", "success": true, "created": true}, {"success": false}]}'},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert by_kind(result, "rollback_fired")["outcome"] == "failed"


def test_a_400_with_everything_rolled_back_passes_the_rollback_assertion(store):
    transport = scripted_transport(
        [
            {"status": 201, "body": '{"id": "a1", "created": true}'},
            {"status": 200, "body": '{"id": "a1", "created": false}'},
            {"status": 400,
             "body": '{"records": [{"success": false, "errors": [{"message": "rolled back"}]}, {"success": false}]}'},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert by_kind(result, "rollback_fired")["outcome"] == "passed"


def test_a_create_without_a_record_id_fails_the_object_assertion(store):
    transport = scripted_transport(
        [
            {"status": 200, "body": '{"created": true, "message": "accepted without an id"}'},
            {"status": 200, "body": '{"id": "a1", "created": false}'},
            {"status": 400, "body": '{"records": [{"success": false}, {"success": false}]}'},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert by_kind(result, "object_created")["outcome"] == "failed"
    # Nothing was proven about the object, so the dedupe probe has nothing to
    # dedupe against and is skipped rather than scored.
    assert by_kind(result, "dedupe_key_honoured")["outcome"] == "skipped"


def test_a_failed_create_skips_the_dedupe_but_still_probes_rollback(store):
    transport = scripted_transport(
        [
            {"status": 500, "body": '{"error": "boom"}'},
            {"status": 400, "body": '{"records": [{"success": false}, {"success": false}]}'},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert by_kind(result, "object_created")["outcome"] == "failed"
    assert by_kind(result, "dedupe_key_honoured")["outcome"] == "skipped"
    assert by_kind(result, "rollback_fired")["outcome"] == "passed"


def test_the_poisoned_row_is_marked_and_sent(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    fixture = cs.default_fixture(production)
    bad = fixture["rollback_chunk"][1]
    assert bad["poisoned"] is True
    assert bad["fields"]["event_type"] == cs.POISON_MARKER


def test_the_bulk_request_carries_all_or_none(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    fixture = cs.default_fixture(production)
    request = cs.build_bulk_request(production["data"], fixture, fixture["rollback_chunk"])
    assert request["method"] == "POST"
    assert request["url"].endswith("/Engagement__c/composite")
    assert request["query"] == {"allOrNone": "true"}
    assert len(request["body"]["records"]) == 2


def test_the_upsert_request_keys_on_the_path_not_the_body(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    fixture = cs.default_fixture(production)
    request = cs.build_upsert_request(
        production["data"], fixture, fixture["create_row"]["key"], fixture["create_row"]["fields"]
    )
    assert request["method"] == "PATCH"
    assert request["url"].endswith(
        f"/Engagement__c/External_Engagement_Id__c/{fixture['create_row']['key']}"
    )
    assert "id" not in request["body"]
    assert "Id" not in request["body"]


def test_the_fixture_is_deterministic_per_connection_and_distinct_across_them(store):
    room = store.create("room", {"name": "R"}, source="test")
    first = make_connection(store, room["id"])
    second = make_connection(store, room["id"])
    one = cs.default_fixture(first)["create_row"]["key"]
    again = cs.default_fixture(first)["create_row"]["key"]
    other = cs.default_fixture(second)["create_row"]["key"]
    assert one == again
    assert one != other


def test_the_fixture_carries_the_synthetic_buyers(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    fixture = cs.default_fixture(production)
    assert len(fixture["buyers"]) == 3
    assert all(buyer.startswith("synthetic.buyer-") for buyer in fixture["buyers"])


# --------------------------------------------------------------------------- #
# Quota behaviour
# --------------------------------------------------------------------------- #


def test_the_salesforce_limit_info_header_is_parsed():
    signal = parse_quota({"Sforce-Limit-Info": "api-usage=31/5000"})
    assert signal["used"] == 31
    assert signal["total"] == 5000


def test_a_remaining_balance_of_zero_is_exhausted():
    assert parse_quota({"X-RateLimit-Remaining": "0"}, 200)["exhausted"] is True


def test_a_429_is_the_signal_even_without_headers():
    signal = parse_quota({}, 429)
    assert signal["exhausted"] is True
    assert signal["hit_429"] is True


def test_retry_after_is_read_when_the_vendor_sends_it():
    assert parse_quota({"Retry-After": "45"}, 429)["retry_after"] == 45


def test_a_200_without_quota_headers_is_no_signal():
    signal = parse_quota({}, 200)
    assert signal["exhausted"] is False
    assert signal["remaining"] is None


def test_a_garbage_header_is_not_a_number():
    assert parse_quota({"X-RateLimit-Remaining": "many"}, 200)["remaining"] is None


def test_the_run_stops_on_the_first_quota_signal(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    transport = demo_transport("quota")
    result = run(store, sandbox["id"], transport)
    # Create was honoured, the dedupe probe drew the 429, and nothing was
    # sent after it.
    assert len(transport.calls) == 2
    assert [entry["step"] for entry in result.requests] == ["create", "dedupe"]
    assert by_kind(result, "rollback_fired")["outcome"] == "skipped"
    assert result.quota["backoff_seconds"] == 45.0
    assert result.aborted_reason == "quota_exhausted"


def test_a_quota_stopped_run_is_not_green(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], demo_transport("quota"))
    assert result.passed is False
    assert result.status == "failed"


def test_an_exhausted_remaining_header_stops_the_run_too(store):
    transport = scripted_transport(
        [
            {"status": 201, "body": '{"id": "a1", "created": true}'},
            {"status": 200, "body": '{"id": "a1", "created": false}',
             "headers": {"X-RateLimit-Remaining": "0"}},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert result.aborted_reason == "quota_exhausted"
    assert len(transport.calls) == 2


def test_the_default_backoff_is_used_when_the_vendor_says_nothing(store):
    transport = scripted_transport(
        [
            {"status": 201, "body": '{"id": "a1", "created": true}'},
            {"status": 429, "body": "{}"},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert result.quota["backoff_seconds"] == float(cs.DEFAULT_BACKOFF_SECONDS)


def test_a_quota_signal_on_the_first_call_skips_everything(store):
    transport = scripted_transport(
        [
            {"status": 429, "body": "{}", "headers": {"Retry-After": "30"}},
            {"status": 200, "body": '{"id": "a1"}'},
        ]
    )
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], transport)
    assert by_kind(result, "object_created")["outcome"] == "skipped"
    assert by_kind(result, "dedupe_key_honoured")["outcome"] == "skipped"
    assert by_kind(result, "rollback_fired")["outcome"] == "skipped"
    assert by_kind(result, "quota_headers_behaved")["outcome"] == "passed"
    assert len(transport.calls) == 1


# --------------------------------------------------------------------------- #
# Running against production is refused
# --------------------------------------------------------------------------- #


def test_a_test_sync_against_a_production_connection_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(TestSyncRefused) as caught:
        run(store, production["id"], demo_transport("green"))
    assert "production" in str(caught.value)
    assert "synthetic buyers" in str(caught.value)


def test_the_refusal_answers_400_over_http(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/connections/{production['id']}/run-test-sync", json={"scripted": GREEN_SCRIPT}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "sandbox_error"


# --------------------------------------------------------------------------- #
# The promotion gate, and the revert
# --------------------------------------------------------------------------- #


def test_promotion_without_a_run_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    with pytest.raises(NotValidatedError) as caught:
        cs.promote(store, sandbox["id"], now=NOW, actor="test", source="test")
    assert "no test sync behind it" in str(caught.value)


def test_promotion_after_a_failed_run_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], demo_transport("dedupe_broken"))
    assert result.passed is False
    with pytest.raises(NotValidatedError) as caught:
        cs.promote(store, sandbox["id"], now=NOW, actor="test", source="test")
    assert "green" in str(caught.value)


def test_promotion_after_green_names_the_run(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, sandbox, result = green_sandbox(store, room["id"])
    outcome = cs.promote(store, sandbox["id"], now=NOW, actor="test", source="test")
    assert outcome["promoted"] is True
    assert outcome["promoted_run_id"] == result.run_id


def test_the_promotion_lands_on_the_connection_row(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, sandbox, result = green_sandbox(store, room["id"])
    cs.promote(store, sandbox["id"], now=NOW, actor="test", source="test")
    fresh = cs.load_connection(store, sandbox["id"])
    assert fresh["data"]["promoted_run_id"] == result.run_id
    assert fresh["data"]["promoted_at"] == NOW.isoformat(timespec="seconds")


def test_a_second_green_run_updates_the_promotion_pointer(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, sandbox, _ = green_sandbox(store, room["id"])
    first = cs.promote(store, sandbox["id"], now=NOW, actor="test", source="test")
    second_run = run(store, sandbox["id"], demo_transport("green"))
    second = cs.promote(store, sandbox["id"], now=NOW, actor="test", source="test")
    assert second["promoted_run_id"] == second_run.run_id
    assert second_run.run_id != first["promoted_run_id"]


def test_promoting_a_production_connection_itself_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(SandboxError):
        cs.promote(store, production["id"], now=NOW, actor="test", source="test")


def test_revert_marks_only_the_test_row(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    outcome = cs.revert(store, sandbox["id"], now=NOW, actor="test", source="test")
    assert outcome["reverted"] is True
    assert cs.load_connection(store, sandbox["id"])["data"]["reverted_at"] == NOW.isoformat(timespec="seconds")
    assert cs.load_connection(store, production["id"])["data"].get("reverted_at") is None


def test_reverting_a_production_connection_is_refused(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    with pytest.raises(SandboxError):
        cs.revert(store, production["id"], now=NOW, actor="test", source="test")


def test_the_revert_is_audited(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    cs.revert(
        store,
        sandbox["id"],
        now=NOW,
        actor="test",
        source=f"POST {PREFIX}/connections/{sandbox['id']}/revert",
    )
    entries = store.audit(collection=cs.COLLECTION_CONNECTION, limit=100)
    assert any(
        entry["source"] == f"POST {PREFIX}/connections/{sandbox['id']}/revert" for entry in entries
    )


# --------------------------------------------------------------------------- #
# The run is atomic with its write-back
# --------------------------------------------------------------------------- #


def test_the_run_record_and_the_connection_pointer_land_together(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, sandbox, result = green_sandbox(store, room["id"])
    run_record = store.get(result.run_id)
    connection = cs.load_connection(store, sandbox["id"])
    assert run_record["collection"] == runs_mod.COLLECTION_RUN
    assert run_record["data"]["connection_id"] == sandbox["id"]
    assert connection["data"]["last_run_id"] == result.run_id
    assert connection["data"]["last_run_status"] == "passed"


def test_a_green_run_records_the_validation_timestamp(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, sandbox, _ = green_sandbox(store, room["id"])
    connection = cs.load_connection(store, sandbox["id"])
    assert connection["data"]["last_validated_at"] == NOW.isoformat(timespec="seconds")


def test_a_failed_run_does_not_claim_validation(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    run(store, sandbox["id"], demo_transport("dedupe_broken"))
    connection = cs.load_connection(store, sandbox["id"])
    assert connection["data"].get("last_validated_at") is None


def test_the_run_record_lives_in_the_room(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, _, result = green_sandbox(store, room["id"])
    assert store.get(result.run_id)["room_id"] == room["id"]


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_researched_contract(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-048"
    assert "production" in body["environment_types"]
    assert body["hubspot"]["min_platform"] == HUBSPOT_MIN_PLATFORM
    assert body["gaps"]["salesforce_sandbox_types"]


def test_the_inferences_route_serves_the_judgement_calls(client):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    assert "test_environment_is_a_second_connection_row" in ids
    assert "running_a_test_sync_against_production_is_refused" in ids


def test_the_summary_counts_the_states(client):
    body = client.get(f"{PREFIX}/summary").json()
    assert {"connections", "test_environments", "runs", "green_runs", "failed_runs"} <= set(body)


def test_registering_a_connection_answers_201_with_the_row(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/connections",
        json={
            "vendor": "salesforce",
            "base_url": "https://salesforce.example/prod",
            "object_name": "Engagement__c",
            "key_field": "External_Engagement_Id__c",
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["environment"] == "production"


def test_a_connection_without_its_coordinates_names_what_is_missing(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/connections", json={"vendor": "salesforce"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "base_url" in detail
    assert "object_name" in detail
    assert "key_field" in detail


def test_an_unknown_vendor_is_refused(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/connections",
        json={"vendor": "gopher", "base_url": "https://x", "object_name": "E__c", "key_field": "K__c"},
    )
    assert response.status_code == 400


def test_a_connection_for_an_unknown_room_is_404(client):
    response = client.post(
        f"{PREFIX}/rooms/nope/connections",
        json={"vendor": "salesforce", "base_url": "https://x", "object_name": "E__c", "key_field": "K__c"},
    )
    assert response.status_code == 404


def test_the_room_connections_route_scopes_to_the_room(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    body = client.get(f"{PREFIX}/rooms/{room['id']}/connections").json()
    assert body["room_id"] == room["id"]
    assert body["count"] == 1
    assert body["connections"][0]["id"] == production["id"]
    assert body["by_environment"]["production"] == 1


def test_the_room_connections_route_refuses_an_unknown_room(client):
    assert client.get(f"{PREFIX}/rooms/nope/connections").status_code == 404


def test_the_test_environment_route_answers_201(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform", "env_type": "sandbox"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["env_type"] == "sandbox"


def test_the_run_route_scores_the_assertions_over_http(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    response = client.post(
        f"{PREFIX}/connections/{sandbox['id']}/run-test-sync", json={"scripted": GREEN_SCRIPT}
    )
    assert response.status_code == 200
    run_payload = response.json()["run"]
    assert run_payload["status"] == "passed"
    assert [entry["kind"] for entry in run_payload["assertions"]] == [
        "object_created",
        "dedupe_key_honoured",
        "rollback_fired",
        "quota_headers_behaved",
    ]


def test_a_run_without_a_scripted_body_uses_the_simulated_vendor(client):
    """The page's own path: an empty body runs against the simulated sandbox
    and every assertion passes."""
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    response = client.post(f"{PREFIX}/connections/{sandbox['id']}/run-test-sync", json={})
    assert response.status_code == 200
    run_payload = response.json()["run"]
    assert run_payload["status"] == "passed"
    assert run_payload["transport"] == "simulated"


def test_the_promotion_gate_answers_422_over_http(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    response = client.post(f"{PREFIX}/connections/{sandbox['id']}/promote")
    assert response.status_code == 422
    assert response.json()["error"] == "not_validated"


def test_the_promotion_happy_path_over_http(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    client.post(f"{PREFIX}/connections/{sandbox['id']}/run-test-sync", json={"scripted": GREEN_SCRIPT})
    response = client.post(f"{PREFIX}/connections/{sandbox['id']}/promote")
    assert response.status_code == 200
    assert response.json()["promoted"] is True
    assert response.json()["promoted_run_id"]


def test_the_revert_over_http(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    response = client.post(f"{PREFIX}/connections/{sandbox['id']}/revert")
    assert response.status_code == 200
    assert response.json()["reverted"] is True


def test_an_unknown_run_is_404(client):
    assert client.get(f"{PREFIX}/runs/nope").status_code == 404


def test_the_room_runs_route_lists_the_runs(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    client.post(f"{PREFIX}/connections/{sandbox['id']}/run-test-sync", json={"scripted": GREEN_SCRIPT})
    body = client.get(f"{PREFIX}/rooms/{room['id']}/runs").json()
    assert body["count"] == 1
    assert body["runs"][0]["connection_id"] == sandbox["id"]


def test_the_self_test_route_creates_both_rows_and_runs_the_fixture(client):
    room = make_room(client)
    payload = {
        "vendor": "salesforce",
        "object_name": "Engagement__c",
        "base_url": "https://salesforce.example/prod",
        "key_field": "External_Engagement_Id__c",
        "test_environment": {"kind": "power_platform", "env_type": "sandbox"},
        "scripted": GREEN_SCRIPT,
    }
    response = client.post(f"{PREFIX}/rooms/{room['id']}/self-test", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["production"]["environment"] == "production"
    assert body["test_environment"]["environment"] == "test"
    assert body["run"]["status"] == "passed"


def test_the_self_test_reuses_the_production_row(client):
    room = make_room(client)
    payload = {
        "vendor": "salesforce",
        "object_name": "Engagement__c",
        "base_url": "https://salesforce.example/prod",
        "test_environment": {"kind": "power_platform"},
        "scripted": GREEN_SCRIPT,
    }
    first = client.post(f"{PREFIX}/rooms/{room['id']}/self-test", json=payload).json()
    second = client.post(f"{PREFIX}/rooms/{room['id']}/self-test", json=payload).json()
    assert second["production"]["id"] == first["production"]["id"]


def test_the_self_test_refuses_without_the_connector_coordinates(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/self-test", json={"scripted": GREEN_SCRIPT})
    assert response.status_code == 400


def test_the_ci_route_reports_for_a_job_step(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/ci-run",
        json={"connection_id": production["id"], "kind": "power_platform", "scripted": GREEN_SCRIPT},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ci"]["pass"] is True
    assert body["ci"]["run_id"]
    assert body["ci"]["summary"].startswith("test sync")
    assert body["run"]["status"] == "passed"


def test_the_ci_route_refuses_without_a_connection(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/ci-run", json={"kind": "power_platform"})
    assert response.status_code == 400
    assert "connection_id" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The audit trail names the route that served the write
# --------------------------------------------------------------------------- #


def _mounted_routes():
    """Every (method, path) the host actually serves, flattened.

    This FastAPI wraps each ``include_router`` as an ``_IncludedRouter``, so
    the walk recurses into ``original_router.routes`` to see the concrete
    paths a feature mounted - the same paths the registry reports.
    """
    routes = []

    def walk(candidates):
        for route in candidates:
            if type(route).__name__ == "_IncludedRouter":
                walk(route.original_router.routes)
                continue
            for method in (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
                routes.append((method, getattr(route, "path", "")))

    walk(_app().routes)
    return routes


def _source_matches_a_mounted_route(source: str) -> bool:
    method, _, path = source.partition(" ")
    parts = [part for part in path.strip("/").split("/") if part]
    for route_method, route_path in _mounted_routes():
        if route_method != method:
            continue
        pattern_parts = [part for part in route_path.strip("/").split("/") if part]
        if len(parts) != len(pattern_parts):
            continue
        if all(
            pattern_part.startswith("{") or part == pattern_part
            for part, pattern_part in zip(parts, pattern_parts)
        ):
            return True
    return False


def _audit_sources_for_the_feature(client) -> list[str]:
    sources = []
    for collection in (cs.COLLECTION_CONNECTION, runs_mod.COLLECTION_RUN):
        body = client.get(f"/api/audit", params={"collection": collection, "limit": 1000}).json()
        for entry in body["entries"]:
            if entry.get("source"):
                sources.append(str(entry["source"]))
    return sources


def test_every_audit_source_names_a_route_the_host_mounted(client):
    room = make_room(client)
    production = make_http_connection(client, room["id"])
    sandbox = client.post(
        f"{PREFIX}/connections/{production['id']}/test-environment",
        json={"kind": "power_platform"},
    ).json()
    client.post(f"{PREFIX}/connections/{sandbox['id']}/run-test-sync", json={"scripted": GREEN_SCRIPT})
    client.post(f"{PREFIX}/connections/{sandbox['id']}/promote")
    client.post(
        f"{PREFIX}/rooms/{room['id']}/ci-run",
        json={"connection_id": production["id"], "scripted": GREEN_SCRIPT},
    )
    sources = _audit_sources_for_the_feature(client)
    assert sources
    for source in sources:
        method, _, path = source.partition(" ")
        assert path.startswith(PREFIX), source
        assert _source_matches_a_mounted_route(source), source


def test_the_run_audit_row_names_its_route(store):
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    source = f"POST {PREFIX}/connections/{sandbox['id']}/run-test-sync"
    run(store, sandbox["id"], demo_transport("green"), source=source)
    entries = store.audit(collection=runs_mod.COLLECTION_RUN, limit=100)
    assert any(entry["source"] == source for entry in entries)


def test_the_promotion_audit_row_names_its_route(store):
    room = store.create("room", {"name": "R"}, source="test")
    _, sandbox, _ = green_sandbox(store, room["id"])
    source = f"POST {PREFIX}/connections/{sandbox['id']}/promote"
    cs.promote(store, sandbox["id"], now=NOW, actor="test", source=source)
    entries = store.audit(collection=cs.COLLECTION_CONNECTION, limit=100)
    assert any(entry["source"] == source for entry in entries)


# --------------------------------------------------------------------------- #
# Seed: the interesting states, not just the happy path
# --------------------------------------------------------------------------- #


def seed_into(store, room_id):
    return feature_module().seed(store.db, {"room_ids": [(room_id, "Acme")], "now": NOW})


def test_the_seed_creates_the_six_demo_states(store):
    room = store.create("room", {"name": "Demo"}, source="seed")
    summary = seed_into(store, room["id"])
    assert "6 connections" in summary
    assert "2 green runs" in summary
    assert "3 failed runs" in summary
    assert "1 promoted" in summary


def test_the_seed_runs_are_real_runs_with_all_four_assertions(store):
    room = store.create("room", {"name": "R"}, source="seed")
    seed_into(store, room["id"])
    runs = store.list(runs_mod.COLLECTION_RUN, limit=50)
    assert len(runs) == 5
    for run_record in runs:
        kinds = {entry["kind"] for entry in run_record["data"]["assertions"]}
        assert kinds == {
            "object_created",
            "dedupe_key_honoured",
            "rollback_fired",
            "quota_headers_behaved",
        }


def test_the_seed_promotion_names_its_green_run(store):
    room = store.create("room", {"name": "R"}, source="seed")
    seed_into(store, room["id"])
    promoted = [
        row
        for row in cs.list_connections(store)
        if row["data"].get("promoted_at") and row["data"].get("promoted_run_id")
    ]
    assert len(promoted) == 1
    assert store.get(promoted[0]["data"]["promoted_run_id"]) is not None


def test_the_seed_leaves_one_connection_without_a_test_environment(store):
    room = store.create("room", {"name": "R"}, source="seed")
    seed_into(store, room["id"])
    bare = [
        row
        for row in cs.list_connections(store, room["id"])
        if row["data"]["environment"] == "production"
        and row["data"].get("notes") == "a production connection with nothing to validate yet"
    ]
    assert len(bare) == 1
    assert not envs.list_test_environment_rows(store, bare[0]["id"])


def test_the_seed_with_no_rooms_says_so(store):
    summary = feature_module().seed(store.db, {"room_ids": [], "now": NOW})
    assert "no demo rooms" in summary


# --------------------------------------------------------------------------- #
# The transport seam
# --------------------------------------------------------------------------- #


def test_the_default_transport_is_the_simulated_one():
    """The route default is the simulated vendor, for the same reason WF-038's
    is: the research cites no credentials for a real org."""
    assert isinstance(feature_module()._transport_for({}), cs.SimulatedTransport)


def test_an_env_var_selects_the_real_transport(monkeypatch):
    monkeypatch.setenv("DSR_WF048_TRANSPORT", "real")
    assert isinstance(feature_module()._transport_for({}), cs.UrllibTransport)


def test_a_scripted_payload_selects_the_scripted_transport():
    assert isinstance(
        feature_module()._transport_for({"scripted": GREEN_SCRIPT}), cs.ScriptedTransport
    )


def test_a_simulated_run_passes_and_records_its_transport(store):
    """The demo page's path: run against the simulated sandbox and the four
    assertions pass, and the run record says so."""
    room = store.create("room", {"name": "R"}, source="test")
    production = make_connection(store, room["id"])
    sandbox = make_sandbox(store, production)
    result = run(store, sandbox["id"], cs.SimulatedTransport())
    assert result.passed is True
    run_record = store.get(result.run_id)
    assert run_record["data"]["transport"] == "simulated"


def test_a_scripted_transport_replays_the_script():
    transport = scripted_transport(
        [{"status": 200, "body": "one"}, {"status": 201, "body": "two"}]
    )
    first = transport.request("PATCH", "https://x/a", body={})
    second = transport.request("PATCH", "https://x/y", body={})
    assert first.status == 200
    assert first.body == "one"
    assert second.status == 201


def test_a_scripted_transport_records_its_calls():
    transport = scripted_transport([{"status": 200}])
    transport.request("PATCH", "https://x/y", body={"a": 1})
    assert transport.calls[0]["method"] == "PATCH"
    assert transport.calls[0]["body"] == {"a": 1}


def test_an_exhausted_script_answers_599_rather_than_hanging():
    assert scripted_transport([]).request("GET", "https://x").status == 599
