"""WF-125: the CRM-driven room lifecycle rules, exercised with no server.

The rules in ``dsr.competitive_baseline.room_sync`` touch the store and nothing
else, so every rule below is reachable without a ``TestClient``.

What is under test, and why each one matters
--------------------------------------------

* **A rule is data.** It is a stored row with a condition set, evaluated against
  a stored JSON payload through dotted paths, so it is a rule the data drives
  rather than a UI trigger.
* **Custom objects are the acceptance bar.** A target's object name is data, and
  an unknown name is a custom object served by the same code path. There is no
  fixed column list.
* **The write-back is per engagement event.** One row per target object per
  view, download or share.
* **A write is verified before it reports success.** A write that does not read
  back is a conflict, not a success.
* **Records are plain JSON in ``data``.** A team adding a custom field needs no
  migration and no typed column.

Every test names the property it protects, because a test named ``test_flow``
tells a reviewer nothing about which rule they can stop worrying about.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.competitive_baseline import room_sync as sync
from dsr.competitive_baseline.room_sync import (
    RoomSyncEngine,
    RoomSyncError,
    RoomSyncNotFound,
    RoomSyncRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

MODULE = "wf125_crm_room_sync"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(store: RecordStore) -> RoomSyncEngine:
    """The engine, with the clock pinned so every timestamp is exact."""
    return RoomSyncEngine(store, now=lambda: NOW)


def _deal(engine: RoomSyncEngine, external_id: str = "006-A", **extra: object) -> dict:
    return engine.ingest_record(
        {"crm": "salesforce", "object": "deal", "external_id": external_id, **extra},
        source="test",
        actor="test",
    )


def _rule(engine: RoomSyncEngine, **extra: object) -> dict:
    payload = {
        "name": "Open opportunity over 50k",
        "crm": "salesforce",
        "object": "deal",
        "conditions": [{"field": "stage", "op": "eq", "value": "Qualification"}],
        **extra,
    }
    return engine.create_rule(payload, source="test", actor="test")


# --------------------------------------------------------------------------- #
# The condition rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("op", "expected", "actual", "result"),
    [
        ("eq", "Qualification", "Qualification", True),
        ("eq", "Qualification", "Prospecting", False),
        ("ne", "Qualification", "Prospecting", True),
        ("gt", 50000, 120000, True),
        ("gt", 120000, 50000, False),
        ("gte", 50000, 50000, True),
        ("lt", 50000, 120000, False),
        ("lte", 50000, 50000, True),
        ("in", ["Qualification", "Proposal"], "Proposal", True),
        ("in", ["Qualification", "Proposal"], "Closed Lost", False),
        ("contains", "North", "Northwind", True),
        ("contains", "East", "Northwind", False),
    ],
)
def test_every_condition_operator(op, expected, actual, result):
    condition = {
        "field": "value" if not isinstance(expected, str) else "stage",
        "op": op,
        "value": expected,
    }
    if not isinstance(expected, str):
        payload = {"value": actual}
    else:
        payload = {"stage": actual}
    assert sync.evaluate_condition(condition, payload) is result


def test_a_missing_field_matches_nothing_but_ne():
    """A record without the field is not equal, not greater and not containing."""
    for op in ("eq", "gt", "gte", "lt", "lte", "in", "contains", "exists"):
        assert sync.evaluate_condition({"field": "absent", "op": op, "value": 1}, {}) is False
    assert sync.evaluate_condition({"field": "absent", "op": "ne", "value": 1}, {}) is True


def test_exists_ignores_its_value_and_fires_on_presence():
    assert sync.evaluate_condition(
        {"field": "stage", "op": "exists", "value": "x"}, {"stage": "Qualification"}
    )
    assert not sync.evaluate_condition({"field": "stage", "op": "exists"}, {"stage": None})


def test_a_rule_matches_only_when_every_condition_holds():
    rule = {
        "conditions": [
            {"field": "stage", "op": "eq", "value": "Qualification"},
            {"field": "value", "op": "gte", "value": 50000},
        ]
    }
    assert sync.rule_matches(rule, {"stage": "Qualification", "value": 120000})
    assert not sync.rule_matches(rule, {"stage": "Qualification", "value": 40000})


def test_a_rule_with_no_conditions_matches_nothing():
    """A rule that matches everything is a rule nobody wrote on purpose."""
    assert sync.rule_matches({"conditions": []}, {"stage": "Qualification"}) is False


def test_dig_reads_a_dotted_path_and_a_list_index():
    payload = {"account": {"name": "Northwind"}, "products": [{"sku": "ENT"}]}
    assert sync.dig(payload, "account.name") == "Northwind"
    assert sync.dig(payload, "products.0.sku") == "ENT"
    assert sync.dig(payload, "account.absent", "default") == "default"


# --------------------------------------------------------------------------- #
# Custom objects are the acceptance bar
# --------------------------------------------------------------------------- #


def test_an_unknown_object_name_is_a_custom_object():
    assert sync.object_kind("deal") == "standard"
    assert sync.object_kind("opportunity") == "standard"
    assert sync.object_kind("Deal_Room__c") == "custom"
    assert sync.object_kind("Room_Engagement__c") == "custom"


def test_an_empty_object_name_is_refused():
    with pytest.raises(RoomSyncError) as caught:
        sync.object_kind("")
    assert "object" in caught.value.errors


def test_a_writeback_target_carries_an_open_field_map_for_a_custom_object():
    """The custom object's field list is data, not a typed column."""
    target = sync.validate_target(
        {
            "object": "Deal_Room__c",
            "fields": {"Engagement_Count__c": "view_count", "Last_Event__c": "kind"},
        }
    )
    assert target["object"] == "Deal_Room__c"
    assert target["fields"] == {"Engagement_Count__c": "view_count", "Last_Event__c": "kind"}
    assert target["enabled"] is True
    assert target["auto_sync"] is True
    assert target["create_mirror"] is False


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_rule_needs_a_name_a_crm_an_object_and_a_condition():
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_rule({"name": "x"})
    errors = caught.value.errors
    assert "crm" in errors and "object" in errors and "conditions" in errors
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_rule({"crm": "salesforce", "object": "deal", "conditions": []})
    assert "conditions" in caught.value.errors


def test_an_unknown_crm_or_condition_op_is_refused():
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_rule(
            {
                "name": "bad",
                "crm": "sugarcrm",
                "object": "deal",
                "conditions": [{"field": "stage", "op": "eq", "value": "x"}],
            }
        )
    assert "crm" in caught.value.errors
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_rule(
            {
                "name": "bad",
                "crm": "salesforce",
                "object": "deal",
                "conditions": [{"field": "stage", "op": "resembles", "value": "x"}],
            }
        )
    assert any(key.startswith("conditions") for key in caught.value.errors)


def test_a_condition_without_its_value_is_refused_but_exists_needs_none():
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_rule(
            {
                "name": "bad",
                "crm": "salesforce",
                "object": "deal",
                "conditions": [{"field": "stage", "op": "eq"}],
            }
        )
    assert any(key.startswith("conditions") for key in caught.value.errors)
    ok = sync.validate_rule(
        {
            "name": "present",
            "crm": "salesforce",
            "object": "deal",
            "conditions": [{"field": "stage", "op": "exists"}],
        }
    )
    assert ok["conditions"][0]["op"] == "exists"


def test_a_record_needs_a_crm_an_object_and_an_external_id():
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_record({"crm": "salesforce"})
    assert "object" in caught.value.errors and "external_id" in caught.value.errors


def test_an_event_needs_a_known_kind_and_a_readable_timestamp():
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_event({"kind": "click"})
    assert "kind" in caught.value.errors
    with pytest.raises(RoomSyncError) as caught:
        sync.validate_event({"kind": "view", "at": "someday"})
    assert "at" in caught.value.errors


def test_a_rule_keeps_a_field_this_workflow_has_never_heard_of():
    """Schema flexibility: a team adds a field without a migration."""
    normalised = sync.validate_rule(
        {
            "name": "Rule",
            "crm": "salesforce",
            "object": "deal",
            "conditions": [{"field": "stage", "op": "eq", "value": "x"}],
            "owner_team": "EMEA",
        }
    )
    assert normalised["owner_team"] == "EMEA"


# --------------------------------------------------------------------------- #
# Generation and projection
# --------------------------------------------------------------------------- #


def test_a_room_is_generated_from_the_crm_fields_the_user_flow_names():
    plan = sync.room_plan(
        {"template_id": "tpl-1"},
        {
            "name": "Northwind",
            "account": "Northwind Traders",
            "stage": "Qualification",
            "value": 120000,
            "currency": "USD",
            "contacts": ["a@b.example"],
            "products": ["Enterprise"],
        },
        deal_id="deal-1",
    )
    assert plan["name"] == "Northwind"
    assert plan["stage"] == "Qualification"
    assert plan["value"] == 120000
    assert plan["contacts"] == ["a@b.example"]
    assert plan["products"] == ["Enterprise"]
    assert plan["template_id"] == "tpl-1"
    assert plan["deal_id"] == "deal-1"


def test_a_room_falls_back_to_the_account_then_a_default_name():
    assert sync.room_plan(None, {"account": "Contoso"})["name"] == "Contoso"
    assert sync.room_plan(None, {})["name"] == "Deal room"


def test_project_fields_resolves_a_fact_path_and_tolerates_absence():
    facts = {"view_count": 3, "last_activity_at": "2026-05-04T09:00:00+00:00"}
    projected = sync.project_fields(
        {"Room_Views__c": "view_count", "Missing__c": "no.such.path"}, facts
    )
    assert projected == {"Room_Views__c": 3, "Missing__c": None}


def test_engagement_facts_expose_the_running_counters():
    deal = {
        "external_id": "006-A",
        "crm": "salesforce",
        "room_view_count": 2,
        "room_download_count": 1,
        "room_share_count": 0,
        "last_room_activity_at": NOW.isoformat(),
    }
    facts = sync.engagement_facts({"kind": "view", "at": NOW.isoformat()}, deal, deal_id="d1")
    assert facts["kind"] == "view"
    assert facts["view_count"] == 2
    assert facts["download_count"] == 1
    assert facts["deal_id"] == "d1"


def test_role_capability_maps_the_three_researched_roles_and_presents_an_unknown_one():
    assert sync.role_capability("admin")["provision"] is True
    assert sync.role_capability("editor")["provision"] is False
    assert sync.role_capability("presenter")["present"] is True
    assert sync.role_capability("stranger")["present"] is True
    assert sync.role_capability("stranger")["provision"] is False


# --------------------------------------------------------------------------- #
# The engine: read, rule and create
# --------------------------------------------------------------------------- #


def test_ingest_is_an_upsert_keyed_by_crm_object_and_external_id(engine):
    first = _deal(engine, "006-A", name="Northwind", stage="Qualification")
    second = _deal(engine, "006-A", name="Northwind renamed", stage="Proposal")
    assert first["id"] == second["id"]
    assert engine.read_record(first["id"])["name"] == "Northwind renamed"
    assert len(engine.list_records()) == 1


def test_a_rule_creates_a_room_only_for_the_record_it_matches(engine):
    northwind = _deal(engine, "006-N", stage="Qualification", value=120000)
    _deal(engine, "006-C", stage="Closed Lost", value=40000)
    _rule(
        engine,
        conditions=[
            {"field": "stage", "op": "eq", "value": "Qualification"},
            {"field": "value", "op": "gte", "value": 50000},
        ],
    )
    applied = engine.evaluate(source="test", actor="test")
    assert applied["matched"] == 1
    assert applied["provisioned"] == 1
    assert len(engine.list_rooms()) == 1
    assert engine.list_rooms()[0]["deal_id"] == northwind["id"]
    assert engine.list_rooms()[0]["created_from"] == sync.CREATED_FROM_RULE


def test_applying_the_same_rule_twice_makes_no_second_room(engine):
    _deal(engine, "006-N", stage="Qualification", value=120000)
    _rule(engine, conditions=[{"field": "stage", "op": "eq", "value": "Qualification"}])
    engine.evaluate(source="test", actor="test")
    second = engine.evaluate(source="test", actor="test")
    assert second["provisioned"] == 0
    assert len(engine.list_rooms()) == 1


def test_a_disabled_rule_provisions_nothing_and_refuses_when_named(engine):
    _deal(engine, "006-N", stage="Qualification")
    rule = _rule(
        engine, enabled=False, conditions=[{"field": "stage", "op": "eq", "value": "Qualification"}]
    )
    # Applying every enabled rule skips it silently.
    assert engine.evaluate(source="test", actor="test")["provisioned"] == 0
    # Applying it by id refuses, so a caller learns why nothing happened.
    with pytest.raises(RoomSyncRefused) as caught:
        engine.evaluate(rule["id"], source="test", actor="test")
    assert caught.value.reason == RoomSyncRefused.REASON_RULE_DISABLED


def test_the_seller_pick_creates_one_room_and_is_idempotent(engine):
    deal = _deal(engine, "006-S", stage="Closed Lost")
    first = engine.provision_deal(deal["id"], source="test", actor="test")
    second = engine.provision_deal(deal["id"], source="test", actor="test")
    assert first["id"] == second["id"]
    assert first["created_from"] == sync.CREATED_FROM_SELLER
    assert len(engine.list_rooms()) == 1


def test_updating_a_rule_changes_its_conditions(engine):
    rule = _rule(engine)
    engine.update_rule(
        rule["id"],
        {"conditions": [{"field": "stage", "op": "eq", "value": "Proposal"}]},
        source="test",
        actor="test",
    )
    assert engine.read_rule(rule["id"])["conditions"][0]["value"] == "Proposal"


def test_a_write_that_does_not_read_back_is_refused(engine):
    """The unlock read-back is the rule; the refusal is what a caller branches on."""
    rule = _rule(engine)
    engine.store.update = lambda *args, **kwargs: None
    with pytest.raises(RoomSyncRefused) as caught:
        engine.update_rule(rule["id"], {"name": "Renamed"}, source="test", actor="test")
    assert caught.value.reason == RoomSyncRefused.REASON_WRITE_NOT_VISIBLE


def test_an_unknown_rule_or_record_is_not_found(engine):
    with pytest.raises(RoomSyncNotFound):
        engine.read_rule("wf125_rule_absent")
    with pytest.raises(RoomSyncNotFound):
        engine.read_record("wf125_crm_record_absent")


# --------------------------------------------------------------------------- #
# The engine: engagement write-back
# --------------------------------------------------------------------------- #


def _targets(engine: RoomSyncEngine, **overrides: object) -> dict:
    payload = {
        "object": "deal",
        "fields": {"Room_Views__c": "view_count", "Room_Last_Event__c": "kind"},
        "create_mirror": True,
        **overrides,
    }
    return engine.create_target(payload, source="test", actor="test")


def test_recording_a_view_writes_back_one_row_per_target(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine)
    _targets(engine, object="Deal_Room__c", fields={"Last_Event__c": "kind"})
    result = engine.record_event(
        deal["id"], {"kind": "view", "at": NOW.isoformat()}, source="test", actor="test"
    )
    assert result["event"]["kind"] == "view"
    assert len(result["writebacks"]) == 2
    assert {row["state"] for row in result["writebacks"]} == {sync.SYNCED_STATE}
    assert {row["object"] for row in result["writebacks"]} == {"deal", "Deal_Room__c"}


def test_each_event_increments_the_deals_running_counters(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine)
    for kind in ("view", "view", "download", "share"):
        engine.record_event(deal["id"], {"kind": kind}, source="test", actor="test")
    fresh = engine.read_record(deal["id"])
    assert fresh["room_view_count"] == 2
    assert fresh["room_download_count"] == 1
    assert fresh["room_share_count"] == 1


def test_a_custom_object_target_creates_its_mirror_and_receives_the_writeback(engine):
    """Custom objects are the acceptance bar, served by the same code path."""
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(
        engine,
        object="Deal_Room__c",
        fields={"Engagement_Count__c": "view_count"},
        create_mirror=True,
    )
    engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    mirrors = engine.list_records(object_name="Deal_Room__c")
    assert len(mirrors) == 1
    assert mirrors[0]["Engagement_Count__c"] == 1


def test_a_target_without_a_mirror_fails_rather_than_inventing_one(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine, object="Room_Engagement__c", fields={"Kind__c": "kind"}, create_mirror=False)
    result = engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    row = result["writebacks"][0]
    assert row["state"] == sync.FAILED_STATE
    assert row["reason"] == RoomSyncRefused.REASON_MIRROR_ABSENT
    assert engine.list_records(object_name="Room_Engagement__c") == []


def test_a_target_with_auto_sync_off_stays_pending(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine, object="Room_Task__c", fields={"Kind__c": "kind"}, auto_sync=False)
    result = engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    assert result["writebacks"][0]["state"] == sync.PENDING_STATE


def test_retrying_a_pending_writeback_settles_it(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine, object="Room_Task__c", fields={"Kind__c": "kind"}, auto_sync=False)
    created = engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    pending = created["writebacks"][0]
    settled = engine.retry_writeback(pending["id"], source="test", actor="test")
    assert settled["state"] == sync.SYNCED_STATE
    assert settled["attempts"] == 1


def test_retrying_a_synced_writeback_refuses(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine)
    created = engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    synced = created["writebacks"][0]
    with pytest.raises(RoomSyncRefused) as caught:
        engine.retry_writeback(synced["id"], source="test", actor="test")
    assert caught.value.reason == RoomSyncRefused.REASON_ALREADY_SYNCED


def test_the_summary_reports_the_states_that_are_not_successes(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine, object="deal", create_mirror=True)
    _targets(engine, object="Room_Engagement__c", fields={"Kind__c": "kind"}, create_mirror=False)
    engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    counted = engine.summary()
    assert counted["synced"] == 1
    assert counted["failed"] == 1
    assert counted["pending"] == 0
    assert "generated_at" in counted


# --------------------------------------------------------------------------- #
# Schema flexibility and the audit trail
# --------------------------------------------------------------------------- #


def test_a_team_can_add_its_own_custom_field_with_no_migration(engine):
    engine.ingest_record(
        {
            "crm": "salesforce",
            "object": "Deal_Room__c",
            "external_id": "a1",
            "engagement_score": 42,
            "our_fields": {"reviewer": "sam"},
        },
        source="test",
        actor="test",
    )
    found = engine.list_records(object_name="Deal_Room__c")
    assert found[0]["engagement_score"] == 42
    assert found[0]["our_fields"] == {"reviewer": "sam"}


def test_every_record_this_workflow_writes_is_ordinary_json(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    _targets(engine)
    _rule(engine, conditions=[{"field": "stage", "op": "eq", "value": "Qualification"}])
    engine.evaluate(source="test", actor="test")
    engine.record_event(deal["id"], {"kind": "view"}, source="test", actor="test")
    for collection in (
        sync.RULE_COLLECTION,
        sync.RECORD_COLLECTION,
        sync.LINK_COLLECTION,
        sync.TARGET_COLLECTION,
        sync.EVENT_COLLECTION,
        sync.WRITEBACK_COLLECTION,
    ):
        for row in engine.store.find(collection, {}, limit=50):
            for key, value in row["data"].items():
                assert isinstance(key, str), (collection, key)
                assert isinstance(value, (str, int, float, bool, list, dict, type(None)))


def test_every_write_leaves_an_audit_row(engine):
    deal = _deal(engine, "006-N", stage="Qualification")
    rows = [
        row
        for row in engine.store.audit(collection=sync.RECORD_COLLECTION)
        if row["record_id"] == deal["id"]
    ]
    assert rows, "the ingest wrote no audit row"


# --------------------------------------------------------------------------- #
# The module's import surface
# --------------------------------------------------------------------------- #


def test_the_module_imports_nothing_but_the_store():
    """An enforced property: no framework, no ``dsr.api``, no bare SQLite."""
    import dsr.competitive_baseline as package

    source_file = Path(package.__file__).parent / "room_sync.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for name in imported:
        assert name != "sqlite3", name
        assert name != "fastapi", name
        assert not name.startswith("starlette"), name
        assert name != "dsr.api", name
        if name == "dsr" or name.startswith("dsr."):
            assert name == "dsr.store", name


def test_the_vocabulary_agrees_with_the_merged_connector():
    """Reuse, not a second list: the CRM systems match WF-042's connector."""
    from dsr.crm_integration import vocabulary as connector

    assert sync.CRM_SYSTEMS == tuple(connector.CRM_SYSTEMS)
    assert set(connector.CRM_OBJECTS) <= set(sync.STANDARD_OBJECTS)


# --------------------------------------------------------------------------- #
# The feature module's own contract
# --------------------------------------------------------------------------- #


def test_the_feature_declares_a_ticket_derived_prefix():
    feature = load_feature(MODULE)
    assert feature.router.prefix == "/api/wf-125"
    assert feature.FEATURE["ticket"] == "WF-125"
    assert feature.FEATURE["id"] == "wf-125-room-sync"


def test_every_route_sits_under_this_features_own_prefix():
    feature = load_feature(MODULE)
    for route in feature.router.routes:
        assert route.path.startswith("/api/wf-125"), route.path


def test_every_engine_write_requires_a_source_and_has_no_default():
    import inspect

    for name in (
        "create_rule",
        "update_rule",
        "delete_rule",
        "ingest_record",
        "create_target",
        "evaluate",
        "provision_deal",
        "record_event",
        "retry_writeback",
    ):
        signature = inspect.signature(getattr(RoomSyncEngine, name))
        assert "source" in signature.parameters, name
        assert signature.parameters["source"].default is inspect.Parameter.empty, name


def test_the_vocabulary_publishes_the_decisions_the_issue_left_open():
    served = sync.vocabulary()
    assert served["crm_systems"] == list(sync.CRM_SYSTEMS)
    assert served["writeback_states"] == list(sync.WRITEBACK_STATES)
    assert served["roles"] == list(sync.ROLES)
    assert "custom_objects" in served["decisions"]
    assert "shape" in served["decisions"]
    assert any("real CRM client" in note for note in served["not_implemented"])


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """The seeder prints it on a Windows console, so every character must encode."""
    feature = load_feature(MODULE)
    reported = feature.seed(db, {"room_ids": [], "now": NOW})
    assert reported
    reported.encode("cp1252")
    assert reported == reported.encode("cp1252").decode("cp1252")


def test_the_seed_creates_the_states_the_issue_asks_for(db: AuditedDatabase):
    """Both creation paths, custom objects, and write-backs that are not all
    successes."""
    feature = load_feature(MODULE)
    feature.seed(db, {"room_ids": [], "now": NOW})

    engine = RoomSyncEngine(RecordStore(db), now=lambda: NOW)
    counted = engine.summary()
    assert counted["rules"] >= 2
    assert counted["rules_enabled"] >= 1
    assert counted["custom_objects"] >= 1
    assert counted["custom_targets"] >= 1
    assert counted["rooms"] >= 2
    assert counted["events"] >= 3
    assert counted["pending"] >= 1
    assert counted["synced"] >= 1
    assert counted["failed"] >= 1

    paths = {row["created_from"] for row in engine.list_rooms()}
    assert paths == {sync.CREATED_FROM_RULE, sync.CREATED_FROM_SELLER}
