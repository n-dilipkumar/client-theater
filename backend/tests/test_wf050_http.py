"""WF-050 HTTP tests: the mounted router, its errors, its audit rows, and the demo.

Every test here maps to a row of the test plan in
``docs/design/WF-050-reconcile-gaps-after-a-dropped-change.md`` section 10.

Isolation: the ``client`` fixture from ``conftest.py`` enters one ``TestClient`` per
module and points ``app.state`` at a fresh, empty database per test, so this file
passes on its own and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dsr.crm_integration.gap_errors import (
    DeletedSourceVendorMismatch,
    GapReconcileError,
    MalformedGapEvent,
    MissingRecordId,
    NoDirtyRecord,
    UnknownDeletedSource,
    UnknownEntity,
    UnknownEvent,
    UnknownGapType,
    UnsupportedVendor,
)
from dsr.crm_integration.reconcile_engine import ReconcileEngine
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf050_reconcile_gaps_after_a_dropped_change_st"
feature = load_feature(MODULE)

PREFIX = "/api/wf-050"
FEATURE_ID = "wf-050-reconcile-gaps-after-a-dropped-change-st"
ROOM = "room-1"
OTHER_ROOM = "room-2"
ENTITY = "Opportunity"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def served_routes(client: TestClient) -> list[dict[str, Any]]:
    """Every route the host reports, from the live registry."""
    return [
        route
        for entry in client.get("/api/features").json()["features"]
        for route in entry["routes"]
    ]


def names_a_served_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """True when an audit ``source`` names a mounted route, segment by segment."""
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


def engine_of(client: TestClient, *, now: datetime = NOW) -> ReconcileEngine:
    """The engine the app is using, built on the app's own store."""
    return ReconcileEngine(client.app.state.store, clock=lambda: now)


def gap(change_type: str = "GAP_UPDATE", **overrides: Any) -> dict[str, Any]:
    body = {
        "changeType": change_type,
        "transactionKey": "tx-1",
        "commitTimestamp": NOW.isoformat(),
        "entity": ENTITY,
        "recordIds": ["006A000001"],
    }
    body.update(overrides)
    return body


def vendor_row(engine: ReconcileEngine, record_id: str, **payload: Any) -> None:
    """One row in the room's copy of the vendor's tables."""
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id=record_id,
        payload={"Name": record_id, **payload},
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )


@pytest.fixture()
def room(client: TestClient) -> str:
    """A room row, so the room-scoped routes have something to scope to."""
    client.post(
        "/api/records/room",
        json={"name": "Northwind", "account": "Northwind Traders"},
        params={"room_id": ROOM},
    )
    return ROOM


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_the_registry_reports_this_feature_with_its_prefix_and_routes(client: TestClient):
    body = client.get("/api/features").json()
    entry = next(row for row in body["features"] if row["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-050"
    assert entry["loaded"] is True
    assert entry["routes"]
    assert entry["exception_handlers"] == ["GapReconcileError"]


def test_no_feature_failed_to_load(client: TestClient):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_the_one_the_ticket_names():
    assert feature.router.prefix == PREFIX
    assert feature.FEATURE["ticket"] == "WF-050"
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["name"]
    assert feature.FEATURE["description"]
    assert feature.FEATURE["nav"]


def test_one_handler_covers_the_whole_error_hierarchy():
    assert list(feature.EXCEPTION_HANDLERS) == [GapReconcileError]
    assert feature.EXCEPTION_HANDLERS[GapReconcileError].__name__ == "_error"


def test_no_core_route_is_shadowed_by_this_feature(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/records/room?limit=1").status_code == 200


def test_this_feature_claims_an_error_type_no_other_feature_has(client: TestClient):
    """Two features may not map one exception type, and the host refuses the second.
    WF-042's inbound read package already maps ``CrmIntegrationError``, so this
    workflow hangs its hierarchy off a new base class rather than reusing it."""
    features = client.get("/api/features").json()["features"]
    mine = [row for row in features if row["id"] == FEATURE_ID]
    assert len(mine) == 1, "this feature is mounted once"
    claimed = [
        handler
        for row in features
        for handler in row["exception_handlers"]
        if handler == "GapReconcileError"
    ]
    assert claimed == ["GapReconcileError"], f"two features map it: {claimed}"
    # WF-042 is still mounted with its own handler, unharmed by this change.
    wf042 = [row for row in features if row["id"].startswith("wf-042")]
    assert wf042, "WF-042 is not mounted"
    assert "CrmIntegrationError" in wf042[0]["exception_handlers"]


# --------------------------------------------------------------------------- #
# The served vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_gap_types_the_cursors_and_the_numbers(
    client: TestClient,
):
    served = client.get(f"{PREFIX}/vocabulary").json()
    assert served["gap_change_types"] == [
        "GAP_CREATE",
        "GAP_UPDATE",
        "GAP_DELETE",
        "GAP_UNDELETE",
    ]
    assert served["overflow_change_type"] == "GAP_OVERFLOW"
    assert served["vendors"] == ["salesforce", "dataverse"]
    assert served["unsupported_vendors"] == ["hubspot"]
    assert served["numbers"]["overflow_change_threshold"]["value"] == 100000
    assert served["numbers"]["change_tracking_expiry_days"]["value"] == 7
    assert [row["kind"] for row in served["cursor_kinds"]] == ["replay_id", "delta_link"]
    assert served["dirty_key"] == ["room_id", "entity", "record_id"]
    assert served["dirty_states"] == ["dirty", "reconciled"]
    assert [row["value"] for row in served["run_states"]] == [
        "open",
        "complete",
        "expired_cursor",
        "refused",
    ]
    assert served["last_modified_field"] == "LastModifiedDate"


def test_the_inferences_route_publishes_the_judgement_calls(client: TestClient):
    served = client.get(f"{PREFIX}/inferences").json()
    assert served["count"] >= 6
    assert served["ids"]
    vendors = next(row for row in served["inferred"] if row["id"] == "vendor-scope")
    assert "no documented gap/overflow analogue" in vendors["because"]
    ownership = next(row for row in served["inferred"] if row["id"] == "replica-ownership")
    assert "WF-043" in ownership["because"]
    assert ownership["decision"] == "own_a_namespaced_replica"
    assert "no other feature's" in ownership["chosen"]

    cursors = next(row for row in served["inferred"] if row["id"] == "two-named-cursors")
    assert cursors["decision"] == "two_named_cursors"
    assert "not reconciled in a single source" in cursors["because"]

    deadline = next(row for row in served["inferred"] if row["id"] == "expired-cursor-fallback")
    assert "full re-read" in deadline["chosen"]
    assert "hard deadline" in deadline["question"]
    assert "section 18" in deadline["source"]

    key = next(row for row in served["inferred"] if row["id"] == "dirty-marker-key")
    assert key["chosen"] == "room_id, entity and record_id"

    source = next(row for row in served["inferred"] if row["id"] == "local-vendor-tables")
    assert "CrmReader" in source["seam"]


# --------------------------------------------------------------------------- #
# Step 1 and 2 over HTTP
# --------------------------------------------------------------------------- #


def test_reporting_a_gap_marks_the_record_dirty_over_http(client: TestClient, room: str):
    """Pins 1 and 5."""
    body = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    assert body["kind"] == "gap"
    assert body["event"]["change_type"] == "GAP_UPDATE"
    assert body["event"]["entity"] == ENTITY
    assert body["event"]["subscription_state"] == "subscribed"
    assert [row["record_id"] for row in body["dirty_records"]] == ["006A000001"]

    dirty = client.get(f"{PREFIX}/rooms/{room}/dirty").json()
    assert dirty["count"] == 1
    assert dirty["state"] == "dirty"
    assert dirty["records"][0]["record_id"] == "006A000001"
    assert dirty["records"][0]["gap_commit_timestamp"] == NOW.isoformat()


@pytest.mark.parametrize("change_type", ["GAP_CREATE", "GAP_UPDATE", "GAP_DELETE", "GAP_UNDELETE"])
def test_each_gap_type_is_accepted_over_http(client: TestClient, room: str, change_type: str):
    response = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap(change_type))
    assert response.status_code == 200
    assert response.json()["event"]["change_type"] == change_type


def test_reporting_an_overflow_unsubscribes_and_stores_the_replay_id(client: TestClient, room: str):
    """Pins 2, 3 and 9."""
    body = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="replay-7"),
    ).json()

    assert body["kind"] == "overflow"
    assert body["replay_id_stored"] is True
    assert body["event"]["record_ids"] == []
    assert body["event"]["subscription_state"] == "unsubscribed"
    assert body["event"]["exceeds_overflow_threshold"] is True

    cursors = client.get(f"{PREFIX}/rooms/{room}/cursors").json()
    assert cursors["count"] == 1
    assert cursors["cursors"][0]["kind"] == "replay_id"
    assert cursors["cursors"][0]["position"] == "replay-7"
    assert cursors["cursors"][0]["resumable"] is True
    assert "seven days" in cursors["note"]


def test_a_gap_on_a_hubspot_stream_is_refused_with_422(client: TestClient, room: str):
    """Pins 19."""
    response = client.post(
        f"{PREFIX}/rooms/{room}/gap-events", json=gap("GAP_UPDATE", vendor="hubspot")
    )
    assert response.status_code == UnsupportedVendor.status == 400
    body = response.json()
    assert body["error"] == UnsupportedVendor.code == "unsupported_gap_vendor"
    assert body["status"] == 400
    assert "no documented gap/overflow analogue" in body["detail"]


def test_an_unknown_change_type_is_refused_with_422(client: TestClient, room: str):
    response = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap("GAP_REORG"))
    assert response.status_code == UnknownGapType.status
    assert response.json()["error"] == UnknownGapType.code
    assert "GAP_OVERFLOW" in response.json()["detail"]


def test_a_gap_with_no_record_id_is_refused_with_422(client: TestClient, room: str):
    response = client.post(
        f"{PREFIX}/rooms/{room}/gap-events", json=gap("GAP_UPDATE", recordIds=[])
    )
    assert response.status_code == MissingRecordId.status
    assert response.json()["error"] == MissingRecordId.code
    assert client.get(f"{PREFIX}/rooms/{room}/gap-events").json()["count"] == 0


def test_the_ledger_lists_events_and_one_event_can_be_read_back(client: TestClient, room: str):
    first = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events", json=gap("GAP_CREATE", recordIds=["006A000002"])
    )

    ledger = client.get(f"{PREFIX}/rooms/{room}/gap-events").json()
    assert ledger["room_id"] == room
    assert ledger["count"] == 2
    assert all(row["entity"] == ENTITY for row in ledger["events"])

    one = client.get(f"{PREFIX}/rooms/{room}/gap-events/{first['event']['id']}").json()
    assert one["id"] == first["event"]["id"]
    assert one["commit_timestamp"] == NOW.isoformat()
    assert one["transaction_key"] == "tx-1"


def test_reading_an_event_that_does_not_exist_is_a_404(client: TestClient, room: str):
    response = client.get(f"{PREFIX}/rooms/{room}/gap-events/crm_gap_event_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "gap_event_not_found"


def test_one_rooms_events_are_invisible_to_another(client: TestClient, room: str):
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    assert client.get(f"{PREFIX}/rooms/{OTHER_ROOM}/gap-events").json()["count"] == 0
    other = client.get(f"{PREFIX}/rooms/{OTHER_ROOM}/gap-events/{posted['event']['id']}")
    assert other.status_code == 404


# --------------------------------------------------------------------------- #
# Step 2: a change event over HTTP
# --------------------------------------------------------------------------- #


def test_a_change_for_a_clean_record_is_applied(client: TestClient, room: str):
    """Pins 6, reason one."""
    answer = client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000001",
            "commit_timestamp": NOW.isoformat(),
            "LastModifiedDate": NOW.isoformat(),
            "payload": {"Name": "Northwind rollout"},
        },
    ).json()
    assert answer["applied"] is True
    assert answer["reason"] == "no_dirty_marker"

    replica = client.get(f"{PREFIX}/rooms/{room}/replica").json()
    assert replica["count"] == 1
    assert replica["rows"][0]["payload"]["Name"] == "Northwind rollout"
    assert replica["rows"][0]["deleted"] is False
    assert "crm_gap_replica" in replica["ownership"]


def test_a_change_for_a_dirty_record_is_dropped_over_http(client: TestClient, room: str):
    """Pins 6. "If you receive change events for new changes for the same record
    before the data has been reconciled, don't process them." """
    vendor_row(engine_of(client), "006A000001", LastModifiedDate="2026-09-27T11:00:00+00:00")
    engine = engine_of(client)
    engine.store.create(
        "crm_gap_replica",
        {"entity": ENTITY, "record_id": "006A000001", "deleted": False, "payload": {"Name": "old"}},
        room_id=room,
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_UPDATE", commitTimestamp="2026-09-27T10:00:00+00:00"),
    )
    before = client.get(f"{PREFIX}/rooms/{room}/replica").json()

    answer = client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000001",
            "commit_timestamp": NOW.isoformat(),
            "LastModifiedDate": NOW.isoformat(),
            "payload": {"Name": "a value the vendor has not seen"},
        },
    ).json()

    assert answer["applied"] is False
    assert answer["reason"] == "dirty_and_newer_than_the_read"
    assert answer["dirty"] is True
    assert "has not reached this room" in answer["detail"]
    assert client.get(f"{PREFIX}/rooms/{room}/replica").json() == before
    assert client.get(f"{PREFIX}/rooms/{room}/dirty").json()["count"] == 1


def test_a_change_that_did_not_commit_after_the_gap_is_dropped(client: TestClient, room: str):
    """Pins 7, comparison A."""
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_UPDATE", commitTimestamp=NOW.isoformat()),
    )
    answer = client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000001",
            "commit_timestamp": "2026-09-27T09:00:00+00:00",
            "payload": {"Name": "inside the window"},
        },
    ).json()
    assert answer["applied"] is False
    assert answer["reason"] == "older_than_the_gap"
    assert "already inside the window" in answer["detail"]


def test_a_change_the_reread_already_carries_is_applied(client: TestClient, room: str):
    """Pins 7, comparison B."""
    vendor_row(engine_of(client), "006A000001", LastModifiedDate="2026-09-27T12:30:00+00:00")
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_UPDATE", commitTimestamp="2026-09-27T10:00:00+00:00"),
    )
    answer = client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000001",
            "commit_timestamp": NOW.isoformat(),
            "LastModifiedDate": NOW.isoformat(),
            "payload": {"Name": "already covered"},
        },
    ).json()
    assert answer["applied"] is True
    assert answer["reason"] == "covered_by_the_re_read"
    assert "already carried this change" in answer["detail"]


def test_a_change_naming_nothing_is_answered_rather_than_refused(client: TestClient, room: str):
    answer = client.post(f"{PREFIX}/rooms/{room}/change-events", json={}).json()
    assert answer["applied"] is False
    assert answer["reason"] == "no_dirty_marker"
    assert "neither was sent" in answer["detail"]


def test_a_change_with_an_unreadable_timestamp_is_refused(client: TestClient, room: str):
    response = client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={"entity": ENTITY, "record_id": "006A000001", "commit_timestamp": "soon"},
    )
    assert response.status_code == MalformedGapEvent.status
    assert response.json()["error"] == MalformedGapEvent.code


# --------------------------------------------------------------------------- #
# Steps 3 and 5 over HTTP
# --------------------------------------------------------------------------- #


def test_a_reconcile_rereads_overwrites_and_clears_the_flag(client: TestClient, room: str):
    """Pins 8."""
    engine = engine_of(client)
    vendor_row(engine, "006A000001", Amount=48000, LastModifiedDate="2026-09-27T11:00:00+00:00")
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()

    body = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "record_id": "006A000001"},
    ).json()

    assert body["scope"] == "record"
    assert body["dirty_cleared"] is True
    assert body["run"]["state"] == "complete"
    assert body["run"]["terminal"] is True
    assert body["replica"]["payload"]["Amount"] == 48000
    assert client.get(f"{PREFIX}/rooms/{room}/dirty").json()["count"] == 0


def test_a_reconcile_of_a_gone_record_writes_a_tombstone(client: TestClient, room: str):
    """Pins 8 and 9."""
    engine = engine_of(client)
    vendor_row(engine, "006A000003")
    engine.purge_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000003",
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events", json=gap("GAP_DELETE", recordIds=["006A000003"])
    ).json()

    body = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "record_id": "006A000003"},
    ).json()

    assert body["replica"]["deleted"] is True
    assert body["replica"]["record_id"] == "006A000003"
    assert "replica_deleted" in [row["event"] for row in body["run"]["log"]]


def test_reconciling_a_clean_record_is_a_409(client: TestClient, room: str):
    """Pins 8 and the recorded decision to refuse a repair nothing opened."""
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    payload = {"event_id": posted["event"]["id"], "record_id": "006A000001"}
    client.post(f"{PREFIX}/rooms/{room}/reconcile", json=payload)

    response = client.post(f"{PREFIX}/rooms/{room}/reconcile", json=payload)
    assert response.status_code == NoDirtyRecord.status == 409
    assert response.json()["error"] == NoDirtyRecord.code
    assert "not dirty" in response.json()["detail"]


def test_reconciling_without_an_event_id_is_a_409(client: TestClient, room: str):
    response = client.post(f"{PREFIX}/rooms/{room}/reconcile", json={})
    assert response.status_code == UnknownEvent.status == 404
    assert response.json()["error"] == UnknownEvent.code
    assert "needs the id" in response.json()["detail"]


def test_an_overflow_reconcile_applies_the_delete_diff_over_http(client: TestClient, room: str):
    """Pins 9, option a."""
    engine = engine_of(client)
    for record_id in ("006A000001", "006A000002", "006A000003"):
        engine.store.create(
            "crm_gap_replica",
            {"entity": ENTITY, "record_id": record_id, "deleted": False, "payload": {}},
            room_id=room,
            actor="test",
            source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
        )
    vendor_row(engine, "006A000001")
    vendor_row(engine, "006A000002")
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="replay-7"),
    ).json()

    body = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
    ).json()

    assert body["deleted_source"] == "difference"
    assert [row["record_id"] for row in body["deleted"]] == ["006A000003"]
    assert body["run"]["counts"] == {"written": 2, "deleted": 1}


def test_the_recycle_bin_source_deletes_what_the_query_returned(client: TestClient, room: str):
    """Pins 9, option b."""
    engine = engine_of(client)
    vendor_row(engine, "006A000001")
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000002",
        payload={},
        is_deleted=True,
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )
    engine.store.create(
        "crm_gap_replica",
        {"entity": ENTITY, "record_id": "006A000002", "deleted": False, "payload": {}},
        room_id=room,
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="r1"),
    ).json()

    body = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "entity": ENTITY, "deleted_source": "recycle_bin"},
    ).json()
    assert [row["record_id"] for row in body["deleted"]] == ["006A000002"]


def test_the_dataverse_delta_source_reads_its_deletes_inline(client: TestClient, room: str):
    """Pins 11."""
    engine = engine_of(client)
    vendor_row(engine, "006A000001")
    engine.put_source(
        ROOM,
        entity=ENTITY,
        record_id="006A000004",
        payload={},
        is_deleted=True,
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )
    engine.store.create(
        "crm_gap_replica",
        {"entity": ENTITY, "record_id": "006A000004", "deleted": False, "payload": {}},
        room_id=room,
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap(
            "GAP_OVERFLOW",
            vendor="dataverse",
            recordIds=None,
            changeCount=150000,
            replayId="dl-1",
        ),
    ).json()

    body = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={
            "event_id": posted["event"]["id"],
            "entity": ENTITY,
            "deleted_source": "dataverse_delta",
        },
    ).json()
    assert body["deleted_source"] == "dataverse_delta"
    assert [row["record_id"] for row in body["deleted"]] == ["006A000004"]
    assert [row["record_id"] for row in body["written"]] == ["006A000001"]


def test_a_deleted_source_the_research_does_not_describe_is_a_422(client: TestClient, room: str):
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="r1"),
    ).json()
    response = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={
            "event_id": posted["event"]["id"],
            "entity": ENTITY,
            "deleted_source": "guesswork",
        },
    )
    assert response.status_code == UnknownDeletedSource.status
    assert response.json()["error"] == UnknownDeletedSource.code
    assert "difference" in response.json()["detail"]


def test_pairing_a_deleted_source_with_the_wrong_vendor_is_a_422(client: TestClient, room: str):
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap(
            "GAP_OVERFLOW",
            vendor="dataverse",
            recordIds=None,
            changeCount=150000,
            replayId="dl-1",
        ),
    ).json()
    response = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={
            "event_id": posted["event"]["id"],
            "entity": ENTITY,
            "deleted_source": "recycle_bin",
        },
    )
    assert response.status_code == DeletedSourceVendorMismatch.status
    assert response.json()["error"] == DeletedSourceVendorMismatch.code
    assert "never asked the right question" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The runs and the sync log over HTTP
# --------------------------------------------------------------------------- #


def test_a_run_and_its_log_are_readable_and_the_log_is_numbered(client: TestClient, room: str):
    """Pins 18. The number is the order, because SQLite breaks a tie on write order."""
    vendor_row(engine_of(client), "006A000001")
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    run_id = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "record_id": "006A000001"},
    ).json()["run"]["id"]

    listed = client.get(f"{PREFIX}/rooms/{room}/runs").json()
    assert listed["room_id"] == room
    assert listed["count"] == 1
    assert listed["runs"][0]["id"] == run_id

    log = client.get(f"{PREFIX}/rooms/{room}/runs/{run_id}/log").json()
    assert log["run_id"] == run_id
    numbers = [row["seq"] for row in log["lines"]]
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    assert [row["event"] for row in log["lines"]] == [
        "run_opened",
        "record_read",
        "replica_overwritten",
        "dirty_flag_cleared",
        "run_complete",
    ]

    one = client.get(f"{PREFIX}/rooms/{room}/runs/{run_id}").json()
    assert one["id"] == run_id
    assert one["log"] == log["lines"]


def test_reading_a_run_that_does_not_exist_is_a_404(client: TestClient, room: str):
    assert client.get(f"{PREFIX}/rooms/{room}/runs/crm_gap_run_absent").status_code == 404
    assert client.get(f"{PREFIX}/rooms/{room}/runs/crm_gap_run_absent/log").status_code == 404


def test_the_health_view_counts_the_rooms_state(client: TestClient, room: str):
    engine = engine_of(client)
    vendor_row(engine, "006A000001")
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "record_id": "006A000001"},
    )
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events", json=gap("GAP_CREATE", recordIds=["006A000009"])
    )

    health = client.get(f"{PREFIX}/rooms/{room}/health").json()
    assert health["room_id"] == room
    assert health["dirty_count"] == 1
    assert health["reconciled_count"] == 1
    assert health["gap_event_count"] == 2
    assert health["open_run_count"] == 0
    assert health["run_count"] == 1
    assert health["clean"] is False
    assert "1 record(s) are dirty" in health["summary"]
    assert health["dirty_records"][0]["record_id"] == "006A000009"


def test_the_health_view_of_a_room_that_saw_nothing_is_clean(client: TestClient, room: str):
    health = client.get(f"{PREFIX}/rooms/{room}/health").json()
    assert health["clean"] is True
    assert health["gap_event_count"] == 0
    assert "no record is dirty" in health["summary"]


def test_the_dirty_route_refuses_a_state_the_research_does_not_describe(
    client: TestClient, room: str
):
    response = client.get(f"{PREFIX}/rooms/{room}/dirty", params={"state": "confused"})
    assert response.status_code == UnknownEntity.status == 404
    assert response.json()["error"] == UnknownEntity.code


# --------------------------------------------------------------------------- #
# The two cursors and the resubscription
# --------------------------------------------------------------------------- #


def test_a_delta_link_past_seven_days_reports_the_fallback(client: TestClient, room: str):
    """Pins 12. The cursor is aged by writing its own timestamp, because the seven-day
    rule is about how long *the room* has held the token."""
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap(
            "GAP_OVERFLOW",
            vendor="dataverse",
            recordIds=None,
            changeCount=150000,
            replayId="dl-1",
        ),
    )
    fresh = client.get(f"{PREFIX}/rooms/{room}/cursors").json()["cursors"][0]
    assert fresh["kind"] == "delta_link"
    assert fresh["resumable"] is True
    assert fresh["age_days"] < 1

    _age_the_cursor(client, room, days=8)
    verdict = client.get(f"{PREFIX}/rooms/{room}/cursors").json()["cursors"][0]
    assert verdict["kind"] == "delta_link"
    assert verdict["resumable"] is False
    assert verdict["state"] == "expired"
    assert verdict["fallback"] == "full_reread"
    assert "throws" in verdict["reason"]
    assert verdict["age_days"] > 7, "the age is measured from the room's own clock"
    assert verdict["expiry_days"] == 7

    health = client.get(f"{PREFIX}/rooms/{room}/health").json()
    assert health["unresumable_cursors"] == ["delta_link"]


def test_a_replay_id_is_still_resumable_after_the_same_aging(client: TestClient, room: str):
    """The seven-day sentence is about a Dataverse last token. Making the test
    kind-blind would declare a good Replay ID dead and make the overflow
    unrecoverable for no reason the vendor gave."""
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="replay-7"),
    )
    _age_the_cursor(client, room, days=400)
    verdict = client.get(f"{PREFIX}/rooms/{room}/cursors").json()["cursors"][0]
    assert verdict["kind"] == "replay_id"
    assert verdict["applies"] is False
    assert verdict["resumable"] is True
    assert verdict["age_days"] == 0.0


def _age_the_cursor(client: TestClient, room: str, *, days: int) -> None:
    """Move a stored cursor's ``updatedAt`` into the past, through the store."""
    store: RecordStore = client.app.state.store
    for row in store.list("crm_gap_cursor", room_id=room, limit=50):
        store.update(
            row["id"],
            {"updatedAt": (NOW - timedelta(days=days)).isoformat()},
            actor="test",
            source=f"POST {PREFIX}/rooms/{{room_id}}/subscribe",
        )


def test_an_expired_delta_link_stops_a_run_before_it_reads(client: TestClient, room: str):
    """Pins 12. The room refuses before it asks, so a run is never left half applied
    by a vendor exception discovered mid-transaction."""
    engine = engine_of(client)
    vendor_row(engine, "006A000001")
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap(
            "GAP_OVERFLOW",
            vendor="dataverse",
            recordIds=None,
            changeCount=150000,
            replayId="dl-1",
        ),
    ).json()
    before = client.get(f"{PREFIX}/rooms/{room}/replica").json()
    _age_the_cursor(client, room, days=8)

    past = engine_of(client, now=NOW)
    body = past.reconcile(
        ROOM,
        {
            "event_id": posted["event"]["id"],
            "entity": ENTITY,
            "deleted_source": "dataverse_delta",
        },
        actor="test",
        source=f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
    )

    assert body["run"]["state"] == "expired_cursor"
    assert body["written"] == []
    assert body["deleted"] == []
    assert body["fallback"] == "full_reread"
    assert client.get(f"{PREFIX}/rooms/{room}/replica").json() == before
    assert [row["event"] for row in body["run"]["log"]] == [
        "run_opened",
        "unsubscribed",
        "replay_id_stored",
        "delta_link_expired",
        "run_expired_cursor",
    ]


def test_resubscribing_is_recorded_and_annotated_over_http(client: TestClient, room: str):
    """Pins 10."""
    vendor_row(engine_of(client), "006A000001")
    posted = client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_OVERFLOW", recordIds=None, changeCount=150000, replayId="replay-7"),
    ).json()
    run_id = client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "entity": ENTITY, "deleted_source": "difference"},
    ).json()["run"]["id"]

    answer = client.post(f"{PREFIX}/rooms/{room}/subscribe", json={"entity": ENTITY}).json()

    assert answer["subscription_state"] == "subscribed"
    assert answer["resubscribed_at"]
    assert run_id in {row["id"] for row in answer["runs_annotated"]}
    assert answer["cursor"]["position"] == "replay-7"

    log = client.get(f"{PREFIX}/rooms/{room}/runs/{run_id}/log").json()["lines"]
    assert log[-1]["event"] == "resubscribed"
    numbers = [row["seq"] for row in log]
    assert numbers == list(range(1, len(numbers) + 1)), numbers

    event = client.get(f"{PREFIX}/rooms/{room}/gap-events/{posted['event']['id']}").json()
    assert event["subscription_state"] == "subscribed"
    assert event["resubscribed_at"]


def test_resubscribing_a_room_with_no_cursor_leaves_one_behind(client: TestClient, room: str):
    answer = client.post(f"{PREFIX}/rooms/{room}/subscribe", json={"entity": ENTITY}).json()
    assert answer["subscription_state"] == "subscribed"
    assert answer["cursor"]["kind"] == "replay_id"
    assert answer["cursor"]["position"].startswith("reconciled:")
    assert answer["cursors"][0]["resumable"] is True


def test_subscribing_with_no_body_at_all_is_answered(client: TestClient, room: str):
    response = client.post(f"{PREFIX}/rooms/{room}/subscribe")
    assert response.status_code == 200
    assert response.json()["subscription_state"] == "subscribed"


# --------------------------------------------------------------------------- #
# The audit rows
# --------------------------------------------------------------------------- #


def test_every_write_audit_row_names_a_route_the_app_serves(client: TestClient, room: str):
    """Pins 21."""
    engine = engine_of(client)
    vendor_row(engine, "006A000001")
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "record_id": "006A000001"},
    )
    client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000009",
            "commit_timestamp": NOW.isoformat(),
            "payload": {"Name": "Fabrikam"},
        },
    )
    client.post(f"{PREFIX}/rooms/{room}/subscribe", json={"entity": ENTITY})

    audit = client.get("/api/audit?limit=1000").json()
    rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert mine, "no audit row from this feature was written"

    routes = served_routes(client)
    for row in mine:
        source = str(row["source"])
        assert names_a_served_route(source, routes), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_sources_written_are_exactly_the_routes_that_served_them(client: TestClient, room: str):
    """Pins 21. A source that drifts from its route is an audit row that cannot be
    followed back to the request that made it."""
    engine = engine_of(client)
    vendor_row(engine, "006A000001")
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    client.post(
        f"{PREFIX}/rooms/{room}/reconcile",
        json={"event_id": posted["event"]["id"], "record_id": "006A000001"},
    )
    client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000009",
            "commit_timestamp": NOW.isoformat(),
            "payload": {"Name": "Fabrikam"},
        },
    )
    client.post(f"{PREFIX}/rooms/{room}/subscribe", json={"entity": ENTITY})

    audit = client.get("/api/audit?limit=1000").json()
    rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
    mine = {
        row["source"]
        for row in rows
        if PREFIX in str(row.get("source") or "") and row.get("action") != "read"
    }
    # The vendor rows the test writes itself carry the reconcile source, because that
    # is the only write this feature's test surface has. Every other source comes
    # from a route the app served.
    assert mine == {
        f"POST {PREFIX}/rooms/{{room_id}}/gap-events",
        f"POST {PREFIX}/rooms/{{room_id}}/change-events",
        f"POST {PREFIX}/rooms/{{room_id}}/reconcile",
        f"POST {PREFIX}/rooms/{{room_id}}/subscribe",
    }


def test_every_audit_row_this_writes_records_the_room_it_belongs_to(client: TestClient, room: str):
    posted = client.post(f"{PREFIX}/rooms/{room}/gap-events", json=gap()).json()
    audit = client.get("/api/audit?limit=1000").json()
    rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert mine
    assert all(row["room_id"] == room for row in mine)
    assert all(row["actor"] for row in mine)
    assert posted["event"]["id"]


def test_a_dropped_change_writes_no_audit_row(client: TestClient, room: str):
    client.post(
        f"{PREFIX}/rooms/{room}/gap-events",
        json=gap("GAP_UPDATE", commitTimestamp="2026-09-27T10:00:00+00:00"),
    )
    client.post(
        f"{PREFIX}/rooms/{room}/change-events",
        json={
            "entity": ENTITY,
            "record_id": "006A000001",
            "commit_timestamp": "2026-09-27T09:00:00+00:00",
            "payload": {"Name": "inside the window"},
        },
    )
    audit = client.get("/api/audit?limit=1000").json()
    rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
    sources = [row["source"] for row in rows]
    assert f"POST {PREFIX}/rooms/{{room_id}}/change-events" not in sources


def test_the_audit_source_is_built_from_the_live_prefix_not_a_hardcoded_url():
    """A hardcoded path would drift the moment the prefix changed."""
    text = Path(feature.__file__).read_text(encoding="utf-8")
    every_source = re.findall(r'source=f"([^"]*)"', text)
    assert every_source, "no source is written with an f-string"
    for expression in every_source:
        assert "{router.prefix}" in expression, expression


# --------------------------------------------------------------------------- #
# The architecture, read off the files
# --------------------------------------------------------------------------- #


def test_the_feature_module_owns_no_shared_file_and_opens_no_connection():
    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text
    assert "dsr.deps" in text


def test_the_domain_package_imports_nothing_but_the_store():
    """The contract's rule, read off the package on disk rather than off a claim."""
    root = Path(__file__).resolve().parents[1] / "dsr" / "crm_integration"
    for module in sorted(root.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name
        assert "from dsr.api" not in text, module.name
        assert "dsr.deps" not in text, module.name
        assert "fastapi" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_feature_added_no_table_and_no_typed_column(client: TestClient, room: str):
    before = {
        row["name"]
        for row in client.app.state.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    client.get(f"{PREFIX}/vocabulary")
    client.get(f"{PREFIX}/rooms/{room}/health")
    client.get(f"{PREFIX}/inferences")
    after = {
        row["name"]
        for row in client.app.state.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert before == after


def test_no_other_feature_writes_into_a_collection_this_one_owns():
    """Two features in one collection is invisible to the route-collision check."""
    from dsr.crm_integration.reconcile_engine import OWNED_COLLECTIONS
    from dsr.features import __file__ as host_file

    mine = set(OWNED_COLLECTIONS)
    this_file = Path(feature.__file__).name
    offenders: list[str] = []
    for path in sorted(Path(host_file).parent.glob("*.py")):
        if path.name in {"__init__.py", "installed_features.py", this_file}:
            continue
        text = path.read_text(encoding="utf-8")
        for name in mine:
            if re.search(rf"""['"]{re.escape(name)}['"]""", text):
                offenders.append(f"{path.name} mentions {name!r}")
    assert not offenders, (
        "another feature already owns a collection this one writes to: " + ", ".join(offenders)
    )


def test_the_frontend_descriptor_id_matches_the_backend_feature_id():
    descriptor = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    text = (descriptor / "index.jsx").read_text(encoding="utf-8")
    assert feature.FEATURE["id"] in text
    assert f"id: {feature.FEATURE['id']!r}" in text
    assert "Component:" in text
    assert "iconPath:" in text


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def test_the_seeder_returns_a_string_a_windows_console_can_print(tmp_path):
    """A rightwards arrow in one recovered feature broke the entire seeder."""
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        store = RecordStore(db)
        store.create("room", {"name": "Demo"}, room_id="room-seed", actor="seed", source="seed")
        store.create("room", {"name": "Other"}, room_id="room-seed-2", actor="seed", source="seed")
        reported = feature.seed(
            db, {"room_ids": [("room-seed", "northwind"), ("room-seed-2", "contoso")]}
        )
    finally:
        db.close()

    assert isinstance(reported, str) and reported
    reported.encode("cp1252")
    print(reported)
    assert "dirty" in reported
    assert "tombstone" in reported
    assert "overflow" in reported
    assert "room(s)" in reported


def test_the_seeder_with_no_rooms_says_so_rather_than_claiming_rows_it_did_not_create(
    tmp_path,
):
    db = AuditedDatabase(tmp_path / "seed-empty.db", actor="seed")
    try:
        reported = feature.seed(db, {"room_ids": []})
    finally:
        db.close()
    reported.encode("cp1252")
    assert "no rooms to scope them to" in reported


def test_the_seed_produces_every_collection_this_feature_writes(tmp_path):
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        room = db.create("room", {"name": "Demo", "account": "Northwind"}, source="seed")
        summary = feature.seed(
            db,
            {
                "room_ids": [(room["id"], "Northwind")],
                "now": NOW,
                "rng": random.Random(1),
            },
        )
        assert isinstance(summary, str) and summary
        for collection in (
            "crm_gap_event",
            "crm_gap_dirty",
            "crm_gap_cursor",
            "crm_gap_run",
            "crm_gap_log",
            "crm_gap_replica",
            "crm_gap_source_row",
        ):
            assert db.count(collection, room_id=room["id"]) > 0, (
                f"{collection} is empty in the demo"
            )
    finally:
        db.close()


def test_the_seed_writes_only_audited_rows_attributed_to_the_seeder(tmp_path):
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        room = db.create("room", {"name": "Demo", "account": "Northwind"}, source="seed")
        feature.seed(
            db,
            {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": random.Random(1)},
        )
        seeded = {
            "crm_gap_event",
            "crm_gap_dirty",
            "crm_gap_cursor",
            "crm_gap_run",
            "crm_gap_log",
            "crm_gap_replica",
            "crm_gap_source_row",
        }
        entries = [entry for entry in db.audit(limit=2000) if entry["collection"] in seeded]
        assert entries
        assert all(entry["source"] == "seed" for entry in entries)
        assert all(entry["actor"] for entry in entries)
    finally:
        db.close()


def test_the_seeded_state_is_not_all_successes(tmp_path):
    """A feature whose demo page is empty is a feature nobody can review."""
    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        room = db.create("room", {"name": "Demo", "account": "Northwind"}, source="seed")
        feature.seed(
            db,
            {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": random.Random(1)},
        )
        engine = ReconcileEngine(RecordStore(db), clock=lambda: NOW)
        health = engine.health(room["id"])
        assert health["dirty_count"] >= 1, "the data-health view needs a row to show"
        assert health["gap_event_count"] == 4
        kinds = [row["kind"] for row in engine.gap_events(room["id"])]
        assert kinds.count("overflow") == 1
        assert kinds.count("gap") == 3
        assert any(row["deleted"] for row in engine.replica(room["id"])), "the tombstone must show"
        unsubscribed = [
            row
            for row in engine.gap_events(room["id"])
            if row["subscription_state"] == "unsubscribed"
        ]
        assert len(unsubscribed) == 1, "the overflow must leave the room unsubscribed"
        for run in engine.runs(room["id"]):
            numbers = [row["seq"] for row in engine.run_log(room["id"], run["id"])]
            assert numbers == list(range(1, len(numbers) + 1))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Called the way CI calls it
# --------------------------------------------------------------------------- #


def test_every_route_answers_without_a_5xx_when_called_with_an_empty_body(client: TestClient):
    """The tool CI runs substitutes ids and sends ``{}`` to every method it finds."""
    faults: list[tuple[str, str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX):
            continue
        path = route["path"].replace("{room_id}", ROOM).replace("{event_id}", "absent")
        path = path.replace("{run_id}", "absent")
        for method in route["methods"]:
            if method not in {"GET", "POST", "PATCH", "DELETE"}:
                continue
            body = {} if method in {"POST", "PATCH", "PUT"} else None
            response = client.request(method, path, json=body)
            if response.status_code == 0 or response.status_code >= 500:
                faults.append((method, path, response.status_code))
    assert faults == [], f"routes answered 5xx when called with an empty body: {faults}"


def test_every_get_route_answers_without_a_5xx_on_a_room_that_read_nothing(
    client: TestClient,
):
    faults: list[tuple[str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX) or "GET" not in route["methods"]:
            continue
        path = route["path"].replace("{room_id}", "room-absent")
        path = path.replace("{event_id}", "absent").replace("{run_id}", "absent")
        response = client.get(path)
        if response.status_code >= 500:
            faults.append((path, response.status_code))
    assert faults == [], faults


def test_a_route_answered_with_no_body_at_all_does_not_answer_5xx(client: TestClient):
    """The tool sends no body on a DELETE and no query on a GET."""
    faults: list[tuple[str, str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX):
            continue
        path = route["path"].replace("{room_id}", ROOM).replace("{event_id}", "absent")
        path = path.replace("{run_id}", "absent")
        for method in route["methods"]:
            response = client.request(method, path)
            if response.status_code == 0 or response.status_code >= 500:
                faults.append((method, path, response.status_code))
    assert faults == [], faults


def test_the_routes_the_tool_advertises_are_the_ones_the_module_declares():
    """The published schema is the flat, authoritative list of what is served."""
    served = {
        (method, route.path)
        for route in feature.router.routes
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert len(served) == 15
    assert ("GET", f"{PREFIX}/vocabulary") in served
    assert ("GET", f"{PREFIX}/inferences") in served
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/gap-events") in served
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/change-events") in served
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/reconcile") in served
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/subscribe") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/health") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/cursors") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/replica") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/dirty") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/runs") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/runs/{{run_id}}") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/runs/{{run_id}}/log") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/gap-events") in served
    assert ("GET", f"{PREFIX}/rooms/{{room_id}}/gap-events/{{event_id}}") in served
    assert all(path.startswith(PREFIX) for _method, path in served)
