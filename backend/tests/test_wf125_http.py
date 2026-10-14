"""WF-125 over HTTP: the routes the host actually mounted.

``test_wf125.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that a rule creates a room and an engagement event writes back to
standard and custom objects, and that every audit row a write left behind names
a concrete route the app serves.

Each test file must pass on its own, because the suite runs under
``pytest-xdist`` and a test that only passes in one order fails intermittently.
Nothing here depends on another test's rows: every test builds its own records.
"""

from __future__ import annotations

import pytest
from dsr.competitive_baseline import room_sync as sync
from dsr.competitive_baseline.room_sync import RoomSyncRefused
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf125_crm_room_sync"
PREFIX = "/api/wf-125"
FEATURE_ID = "wf-125-room-sync"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves."""
    return RecordStore(db)


def _rule(client: TestClient, **extra: object) -> dict:
    payload = {
        "name": "Open opportunity over 50k",
        "crm": "salesforce",
        "object": "deal",
        "conditions": [
            {"field": "stage", "op": "eq", "value": "Qualification"},
            {"field": "value", "op": "gte", "value": 50000},
        ],
        **extra,
    }
    response = client.post(f"{PREFIX}/rules", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _deal(client: TestClient, external_id: str = "006-A", **extra: object) -> dict:
    response = client.post(
        f"{PREFIX}/records",
        json={"crm": "salesforce", "object": "deal", "external_id": external_id, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _target(client: TestClient, **extra: object) -> dict:
    payload = {
        "object": "deal",
        "fields": {"Room_Views__c": "view_count", "Room_Last_Event__c": "kind"},
        "create_mirror": True,
        **extra,
    }
    response = client.post(f"{PREFIX}/targets", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    """The router was mounted by discovery alone. No shared file was edited."""
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    """A broken feature is reported and skipped, so a failure here would be silent."""
    listed = client.get("/api/features").json()
    failures = [entry for entry in listed["failed"] if entry["module"].endswith(MODULE)]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    """A handler for a builtin type would intercept that exception product-wide."""
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.competitive_baseline.room_sync", error_type
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type
    assert len(feature.EXCEPTION_HANDLERS) == 3


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks.

    Every path parameter is a name the verifier knows (``id`` and ``deal_id``),
    because an unknown one is left in the URL and fails as a request that never
    answered rather than as a clean 404.
    """
    shapes = client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
    served = {f"{method} {shape['path']}" for shape in shapes for method in shape["methods"]}
    assert served == {
        f"GET {PREFIX}/summary",
        f"GET {PREFIX}/vocabulary",
        f"GET {PREFIX}/rules",
        f"POST {PREFIX}/rules",
        f"GET {PREFIX}/rules/{{id}}",
        f"PATCH {PREFIX}/rules/{{id}}",
        f"DELETE {PREFIX}/rules/{{id}}",
        f"POST {PREFIX}/evaluate",
        f"GET {PREFIX}/records",
        f"POST {PREFIX}/records",
        f"GET {PREFIX}/records/{{id}}",
        f"GET {PREFIX}/targets",
        f"POST {PREFIX}/targets",
        f"GET {PREFIX}/rooms",
        f"POST {PREFIX}/deals/{{deal_id}}/room",
        f"GET {PREFIX}/events",
        f"POST {PREFIX}/deals/{{deal_id}}/events",
        f"GET {PREFIX}/writebacks",
        f"POST {PREFIX}/writebacks/{{id}}/retry",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    """The enforced rule, restated so the reason is on the record for this workflow."""
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert source.count("_source(") >= 9  # every write derives its audit source


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_summary_route_reports_the_headline_numbers(client: TestClient):
    _rule(client)
    _deal(client, "006-A", stage="Qualification", value=120000)
    body = client.get(f"{PREFIX}/summary").json()
    assert body["rules"] == 1
    assert body["records"] == 1
    assert "generated_at" in body


def test_the_vocabulary_route_publishes_the_issue_s_open_decisions(client: TestClient):
    """A reviewer can see each derivation without reading the source."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["writeback_states"] == list(sync.WRITEBACK_STATES)
    assert body["roles"] == list(sync.ROLES)
    assert body["decisions"]["shape"]
    assert body["not_implemented"]
    # The merged WF-042 connector's vocabulary is reused, not restated.
    assert body["connector"]["systems"] == list(sync.CRM_SYSTEMS)


# --------------------------------------------------------------------------- #
# Errors become the status codes a client branches on
# --------------------------------------------------------------------------- #


def test_an_invalid_rule_is_400_with_a_field_keyed_map(client: TestClient):
    response = client.post(f"{PREFIX}/rules", json={"name": "no crm"})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "room_sync_invalid"
    assert "crm" in body["errors"]


def test_an_unknown_rule_or_record_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/rules/wf125_rule_absent").status_code == 404
    assert client.get(f"{PREFIX}/records/wf125_crm_record_absent").status_code == 404


def test_applying_a_disabled_rule_is_409_with_a_reason(client: TestClient):
    """409, because nothing is malformed: the rule's state refuses the change."""
    rule = _rule(client, enabled=False, conditions=[{"field": "stage", "op": "eq", "value": "x"}])
    refused = client.post(f"{PREFIX}/evaluate", json={"rule_id": rule["id"]})
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["error"] == "room_sync_conflict"
    assert body["reason"] == RoomSyncRefused.REASON_RULE_DISABLED


def test_a_write_that_does_not_read_back_is_409_not_a_success(client: TestClient, monkeypatch):
    """The seed for the release-bar claim that every clear change is verified."""
    rule = _rule(client)
    monkeypatch.setattr(client.app.state.store, "update", lambda *args, **kwargs: None)
    response = client.patch(f"{PREFIX}/rules/{rule['id']}", json={"name": "Renamed"})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == RoomSyncRefused.REASON_WRITE_NOT_VISIBLE


# --------------------------------------------------------------------------- #
# Rule-driven room creation, and the seller pick
# --------------------------------------------------------------------------- #


def test_a_rule_creates_a_room_over_http(client: TestClient):
    _rule(client)
    matching = _deal(client, "006-N", stage="Qualification", value=120000)
    _deal(client, "006-C", stage="Closed Lost", value=40000)

    applied = client.post(f"{PREFIX}/evaluate").json()
    assert applied["matched"] == 1
    assert applied["provisioned"] == 1

    rooms = client.get(f"{PREFIX}/rooms").json()["rooms"]
    assert [row["deal_id"] for row in rooms] == [matching["id"]]
    assert rooms[0]["created_from"] == sync.CREATED_FROM_RULE


def test_the_seller_pick_provisions_one_room_over_http(client: TestClient):
    deal = _deal(client, "006-S", stage="Closed Lost")
    first = client.post(f"{PREFIX}/deals/{deal['id']}/room")
    second = client.post(f"{PREFIX}/deals/{deal['id']}/room")
    assert first.status_code == 200, first.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["created_from"] == sync.CREATED_FROM_SELLER


def test_a_rule_driven_room_is_not_created_twice(client: TestClient):
    _rule(client)
    _deal(client, "006-N", stage="Qualification", value=120000)
    client.post(f"{PREFIX}/evaluate")
    second = client.post(f"{PREFIX}/evaluate").json()
    assert second["provisioned"] == 0


# --------------------------------------------------------------------------- #
# Engagement write-back
# --------------------------------------------------------------------------- #


def test_a_view_writes_back_to_standard_and_custom_objects_over_http(client: TestClient):
    deal = _deal(client, "006-N", stage="Qualification")
    _target(client)
    _target(client, object="Deal_Room__c", fields={"Last_Event__c": "kind"})

    made = client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "view"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["event"]["kind"] == "view"
    assert {row["object"] for row in body["writebacks"]} == {"deal", "Deal_Room__c"}
    assert {row["state"] for row in body["writebacks"]} == {sync.SYNCED_STATE}


def test_a_custom_object_target_receives_its_writeback_over_http(client: TestClient):
    deal = _deal(client, "006-N", stage="Qualification")
    _target(client, object="Deal_Room__c", fields={"Engagement_Count__c": "view_count"})
    client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "view"})
    mirrors = client.get(f"{PREFIX}/records", params={"object": "Deal_Room__c"}).json()["records"]
    assert len(mirrors) == 1
    assert mirrors[0]["Engagement_Count__c"] == 1


def test_a_writeback_without_a_mirror_is_failed_and_can_be_listed(client: TestClient):
    deal = _deal(client, "006-N", stage="Qualification")
    _target(client, object="Room_Engagement__c", fields={"Kind__c": "kind"}, create_mirror=False)
    client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "view"})

    failed = client.get(f"{PREFIX}/writebacks", params={"state": sync.FAILED_STATE}).json()
    assert failed["writebacks"], "the failed write-back was not listed"
    assert failed["writebacks"][0]["reason"] == RoomSyncRefused.REASON_MIRROR_ABSENT


def test_a_pending_writeback_is_settled_by_retry_over_http(client: TestClient):
    deal = _deal(client, "006-N", stage="Qualification")
    _target(client, object="Room_Task__c", fields={"Kind__c": "kind"}, auto_sync=False)
    made = client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "view"}).json()
    pending = made["writebacks"][0]
    assert pending["state"] == sync.PENDING_STATE

    settled = client.post(f"{PREFIX}/writebacks/{pending['id']}/retry")
    assert settled.status_code == 200, settled.text
    assert settled.json()["state"] == sync.SYNCED_STATE


def test_retrying_a_synced_writeback_is_409(client: TestClient):
    deal = _deal(client, "006-N", stage="Qualification")
    _target(client)
    made = client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "view"}).json()
    synced = made["writebacks"][0]
    refused = client.post(f"{PREFIX}/writebacks/{synced['id']}/retry")
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == RoomSyncRefused.REASON_ALREADY_SYNCED


def test_each_event_increments_the_deal_counters_over_http(client: TestClient):
    deal = _deal(client, "006-N", stage="Qualification")
    _target(client)
    for kind in ("view", "view", "download", "share"):
        client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": kind})
    fresh = client.get(f"{PREFIX}/records/{deal['id']}").json()
    assert fresh["room_view_count"] == 2
    assert fresh["room_download_count"] == 1
    assert fresh["room_share_count"] == 1


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore
):
    """The property the feature host exists to protect.

    Asserted against the mounted route table rather than a literal, because a
    literal in the test would agree with a typo in the module.
    """
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    # Drive one write of every kind this feature makes.
    rule = _rule(client)
    client.patch(f"{PREFIX}/rules/{rule['id']}", json={"name": "Renamed"})
    deal = _deal(client, "006-N", stage="Qualification", value=120000)
    client.post(
        f"{PREFIX}/evaluate",
        json={"rule_id": rule["id"]},
    )
    _target(client)
    client.post(f"{PREFIX}/deals/{deal['id']}/room")
    client.post(f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "view"})
    disabled = _rule(
        client, enabled=False, conditions=[{"field": "stage", "op": "eq", "value": "z"}]
    )
    client.delete(f"{PREFIX}/rules/{disabled['id']}")

    rows = [
        row for row in live_store.db.audit() if str(row.get("collection") or "").startswith("wf125")
    ]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "PUT ", "DELETE ")):
            # A write made by the seeder or by a test fixture. Not a route, and
            # correctly so: nothing served it.
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 6, f"only {checked} route-sourced audit rows were checked"
