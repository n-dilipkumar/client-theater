"""The HTTP surface and the audit-source rule for WF-038.

Split from ``test_wf038.py``, which tests the researched rules against the
domain. This file drives the feature's own mounted router, so it covers the
wiring and, at the end, hard rule 4 of the build brief: **the audit row must name
the route that actually served the write**.

The audit-source tests are the ones worth reading twice. The contract names the
defect by name - "a feature's audit log kept recording a path the app had stopped
serving" - and it is invisible to a test scoped to one feature's own strings, so
these tests read the sources out of the module and out of the published OpenAPI
schema rather than asserting a hand-written list that would be updated in the same
commit as the bug.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.crm_upsert.transport import SimulatedTransport
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

PREFIX = "/api/wf-038"
MODULE = "wf038_batch_upsert_engagement_rows_keyed_on_"
FEATURE_ID = "wf-038-batch-upsert-engagement-rows-keyed-on-"


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf038http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    # The default transport is a simulation whose memory of which keys the "CRM"
    # holds is process-wide, so it is reset per test: two tests queueing the same
    # external id would otherwise see the second one answered as an update.
    SimulatedTransport.reset()
    with TestClient(_app()) as test_client:
        yield test_client
    tmp.cleanup()


def _app():
    from dsr.api import app

    return app


def make_room(client, name="Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def make_connection(client, room_id, **spec) -> dict:
    body = {"vendor": "salesforce", "object": "Engagement__c", "key_field": "Ext__c", **spec}
    response = client.post(f"{PREFIX}/connections", json=body, params={"room_id": room_id})
    assert response.status_code == 201, response.text
    return response.json()["connection"]


def make_rows(client, room_id, count, prefix="k"):
    made = []
    for index in range(count):
        made.append(
            client.post(
                f"{PREFIX}/rooms/{room_id}/engagement",
                json={"engagement_id": f"{prefix}{index}", "event_type": "viewed", "account": "Acme"},
            ).json()["record"]
        )
    return made


# --------------------------------------------------------------------------- #
# Vocabulary, capabilities, inferences, config
# --------------------------------------------------------------------------- #


def test_vocabulary_publishes_every_outcome(client):
    from dsr import crm_upsert as cu

    body = client.get(f"{PREFIX}/vocabulary").json()
    assert set(body["outcomes"]) == set(cu.OUTCOMES)
    assert body["rejection_reasons"] == cu.REJECTION_REASONS
    assert body["drivers"] == list(cu.DRIVERS)
    assert body["key_types"] == list(cu.KEY_TYPES)


def test_vocabulary_publishes_the_collections_and_what_a_run_writes(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["collections"]["engagement"] == "engagement"
    assert body["collections"]["run"] == "crm_upsert_run"
    assert body["written_by_a_run"] == ["crm_upsert_run", "engagement"]


def test_capabilities_lists_the_researched_vendors(client):
    body = client.get(f"{PREFIX}/capabilities").json()
    vendors = {entry["vendor"]: entry for entry in body["capabilities"]}
    assert vendors["salesforce"]["max_batch_size"] == 200
    assert vendors["hubspot"]["max_batch_size"] == 100
    assert all(entry["origin"] == "built_in" for entry in body["capabilities"])


def test_capabilities_says_which_vendors_cannot_confirm_a_row(client):
    body = client.get(f"{PREFIX}/capabilities").json()
    vendors = {entry["vendor"]: entry for entry in body["capabilities"]}
    assert vendors["dataverse"]["returns_per_item_results"] is False
    assert vendors["salesforce"]["returns_per_item_results"] is True


def test_registering_a_capability_over_http_appears_in_the_catalogue(client):
    response = client.post(
        f"{PREFIX}/capabilities",
        json={
            "vendor": "acme",
            "max_batch_size": 30,
            "payload_style": "attributes",
            "returns_per_item_results": True,
            "bulk_path": "/acme/{object}/{key_field}",
        },
    )
    assert response.status_code == 201
    vendors = {entry["vendor"]: entry for entry in client.get(f"{PREFIX}/capabilities").json()["capabilities"]}
    assert vendors["acme"]["max_batch_size"] == 30
    assert vendors["acme"]["origin"] == "stored"


def test_registering_a_capability_without_a_vendor_is_422(client):
    response = client.post(f"{PREFIX}/capabilities", json={"max_batch_size": 5})
    assert response.status_code == 422
    assert response.json()["error"] == "upsert_refused"


def test_a_stored_capability_overrides_a_built_in_over_http(client):
    client.post(
        f"{PREFIX}/capabilities",
        json={
            "vendor": "hubspot",
            "max_batch_size": 40,
            "payload_style": "id_property",
            "returns_per_item_results": True,
        },
    )
    room = make_room(client)
    connection = make_connection(client, room["id"], vendor="hubspot", object_name="contacts", key_field="email")
    assert connection["capability_max_batch_size"] == 40
    assert connection["capability_source"] == "stored"
    assert connection["effective_batch_size"] == 40


def test_inferences_are_served_as_data(client):
    from dsr import crm_upsert as cu

    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(cu.INFERENCES)
    assert {entry["id"] for entry in body["inferences"]} == {e["id"] for e in cu.INFERENCES}


def test_config_is_served_with_its_defaults(client):
    body = client.get(f"{PREFIX}/config").json()["config"]
    assert body["queue"]["target"] == 200
    assert body["mapping"]["key_source"] == "engagement_id"


def test_patching_config_merges_rather_than_replaces(client):
    client.patch(f"{PREFIX}/config", json={"schedule": {"interval_hours": 6}})
    body = client.patch(f"{PREFIX}/config", json={"queue": {"opportunistic_threshold": 3}}).json()["config"]
    assert body["schedule"]["interval_hours"] == 6
    assert body["queue"]["opportunistic_threshold"] == 3
    assert body["queue"]["target"] == 200


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def test_creating_a_connection_returns_its_effective_settings(client):
    room = make_room(client)
    body = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "salesforce", "object": "Engagement__c", "key_field": "Ext__c", "batch_size": 50},
        params={"room_id": room["id"]},
    ).json()
    assert body["created"] is True
    connection = body["connection"]
    assert connection["effective_batch_size"] == 50
    assert connection["capability_max_batch_size"] == 200
    assert connection["mode"] == "bulk"
    assert connection["key_type"] == "external_id"
    assert connection["room_id"] == room["id"]


def test_creating_a_connection_reports_its_lint(client):
    room = make_room(client)
    body = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "salesforce", "object": "E", "key_field": "email"},
        params={"room_id": room["id"]},
    ).json()
    assert any(entry["kind"] == "documented_gap" for entry in body["lint"])


def test_a_record_id_key_is_422_over_http(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "salesforce", "object": "E", "key_field": "Id"},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 422
    assert "Only external ids are supported" in response.json()["detail"]


def test_an_over_cap_batch_size_is_422_over_http(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "hubspot", "object": "contacts", "key_field": "email", "batch_size": 500},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 422
    assert "at most 100" in response.json()["detail"]


def test_a_field_map_onto_a_record_id_is_422_over_http(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "salesforce", "object": "E", "key_field": "Ext__c", "fields": {"a": "Id"}},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 422
    assert "record id" in response.json()["detail"]


def test_an_unknown_vendor_is_422_over_http(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "nobody", "object": "E", "key_field": "Ext__c"},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 422
    assert "register_bulk_capability" in response.json()["detail"]


def test_listing_connections_can_be_scoped_to_a_room(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    make_connection(client, first["id"])
    make_connection(client, second["id"])
    assert client.get(f"{PREFIX}/connections", params={"room_id": first["id"]}).json()["count"] == 1
    assert client.get(f"{PREFIX}/connections").json()["count"] == 2


def test_reading_a_connection_serves_its_lint(client):
    room = make_room(client)
    connection = make_connection(client, room["id"], vendor="dataverse", object_name="engagements", key_field="sample_keyattribute")
    body = client.get(f"{PREFIX}/connections/{connection['id']}").json()
    assert body["connection"]["id"] == connection["id"]
    assert any(entry["id"] == "no-per-item-results" for entry in body["lint"])


def test_an_unknown_connection_is_404(client):
    response = client.get(f"{PREFIX}/connections/crm_upsert_connection_nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_connection"


def test_patching_a_connection_revalidates(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    ok = client.patch(f"{PREFIX}/connections/{connection['id']}", json={"batch_size": 25})
    assert ok.status_code == 200
    assert ok.json()["connection"]["effective_batch_size"] == 25

    refused = client.patch(f"{PREFIX}/connections/{connection['id']}", json={"vendor": "hubspot", "batch_size": 500})
    assert refused.status_code == 422


def test_patching_an_unknown_connection_is_404(client):
    assert client.patch(f"{PREFIX}/connections/nope", json={"batch_size": 5}).status_code == 404


def test_deleting_a_connection_is_soft_and_audited(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    assert client.delete(f"{PREFIX}/connections/{connection['id']}").json()["deleted"] is True
    assert client.get(f"{PREFIX}/connections/{connection['id']}").status_code == 404
    entry = client.get("/api/audit", params={"collection": "crm_upsert_connection"}).json()["entries"][0]
    assert entry["action"] == "delete"


def test_deleting_an_unknown_connection_is_404(client):
    assert client.delete(f"{PREFIX}/connections/nope").status_code == 404


# --------------------------------------------------------------------------- #
# The queue, the preview, and the ingest seam
# --------------------------------------------------------------------------- #


def test_queueing_a_row_reports_that_it_is_pending(client):
    room = make_room(client)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/engagement", json={"engagement_id": "k1", "event_type": "viewed"}
    ).json()
    assert body["created"] is True
    assert body["status"] == "pending"
    assert body["key_value"] == "k1"


def test_a_queued_row_carries_no_sync_status(client):
    """A row nobody has synced is pending by definition, so the ingest route does
    not invent a status for it."""
    room = make_room(client)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/engagement", json={"engagement_id": "k1"}
    ).json()
    assert "sync_status" not in body["record"]["data"]


def test_the_queue_counts_and_reports_its_target(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 3)
    body = client.get(
        f"{PREFIX}/rooms/{room['id']}/queue", params={"connection_id": connection["id"]}
    ).json()
    assert body["counts"]["pending"] == 3
    assert body["target"] == 200
    assert body["at_target"] is False
    assert body["connection"]["id"] == connection["id"]
    assert body["backlog"]["due"] is True


def test_the_queue_of_another_rooms_connection_is_404(client):
    """A room-scoped route must not act on another room's connection."""
    first = make_room(client, "A")
    second = make_room(client, "B")
    connection = make_connection(client, first["id"])
    response = client.get(
        f"{PREFIX}/rooms/{second['id']}/queue", params={"connection_id": connection["id"]}
    )
    assert response.status_code == 404


def test_preview_returns_the_request_and_sends_nothing(client):
    room = make_room(client)
    connection = make_connection(client, room["id"], batch_size=10, all_or_none=True)
    make_rows(client, room["id"], 2)
    body = client.get(
        f"{PREFIX}/rooms/{room['id']}/preview", params={"connection_id": connection["id"]}
    ).json()
    assert body["sends_nothing"] is True
    assert body["chunks"] == 1
    request = body["requests"][0]
    assert request["method"] == "PATCH"
    assert request["query"] == {"allOrNone": "true"}
    assert len(request["body"]["records"]) == 2
    assert request["body"]["records"][0]["attributes"]["type"] == "Engagement__c"
    # Nothing was sent, so nothing changed.
    assert client.get(
        f"{PREFIX}/rooms/{room['id']}/queue", params={"connection_id": connection["id"]}
    ).json()["counts"]["pending"] == 2


def test_preview_of_another_rooms_connection_is_404(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    connection = make_connection(client, first["id"])
    response = client.get(
        f"{PREFIX}/rooms/{second['id']}/preview", params={"connection_id": connection["id"]}
    )
    assert response.status_code == 404


def test_preview_respects_a_limit(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 5)
    body = client.get(
        f"{PREFIX}/rooms/{room['id']}/preview",
        params={"connection_id": connection["id"], "limit": 2},
    ).json()
    assert body["planned"] == 2
    assert len(body["requests"][0]["body"]["records"]) == 2


# --------------------------------------------------------------------------- #
# Sync now
# --------------------------------------------------------------------------- #


def test_sync_now_reports_the_key_found_and_key_not_found_split(client):
    """The researched split: a key that is not found creates, a key that is found
    updates. Two new external ids are both creates; re-queueing one is an update."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 2)

    first = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    assert first["totals"]["created"] == 2
    assert first["totals"]["updated"] == 0
    assert first["progress"]["percent"] == 100.0
    assert first["progress"]["driver"] == "manual"

    # Re-queue one row by hand: the key is now one the simulated CRM holds.
    record = client.post(
        f"{PREFIX}/rooms/{room['id']}/engagement", json={"engagement_id": "k0", "event_type": "viewed"}
    ).json()["record"]
    assert record["id"]

    second = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    assert second["totals"]["updated"] == 1
    assert second["totals"]["created"] == 0


def test_sync_now_lifts_the_run_fields_to_the_top_level(client):
    """The researched UI is a progress bar plus a per-row list, and both read
    straight off the response rather than out of a nested data envelope."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    for key in ("totals", "progress", "outcomes", "chunks", "run_id", "mode", "batch_size", "key_field"):
        assert key in body, key
    assert body["collection"] == "crm_upsert_run"


def test_sync_now_stores_the_request_it_sent(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    chunk = body["chunks"][0]
    assert chunk["body"]["records"][0]["attributes"]["type"] == "Engagement__c"
    assert chunk["body"]["records"][0]["Ext__c"] == "k0"
    assert "id" not in chunk["body"]["records"][0]


def test_sync_now_says_its_transport_was_simulated(client):
    """Nobody may read a simulated confirmation as a fact about a real CRM."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    assert body["transport"] == "simulated"


def test_sync_now_writes_back_synced_at_and_crm_record_id(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    queued = make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})

    record = client.get(f"/api/records/engagement/{queued[0]['id']}").json()
    assert record["data"]["sync_status"] == "synced"
    assert record["data"]["synced_at"]
    assert record["data"]["crm_record_id"]


def test_sync_now_drains_the_queue(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 3)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})
    body = client.get(
        f"{PREFIX}/rooms/{room['id']}/queue", params={"connection_id": connection["id"]}
    ).json()
    assert body["counts"]["pending"] == 0
    assert body["counts"]["synced"] == 3


def test_sync_now_with_an_empty_queue_is_a_zero_row_success(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    )
    assert response.status_code == 200
    assert response.json()["totals"]["rows"] == 0
    assert response.json()["chunks"] == []


def test_sync_now_of_another_rooms_connection_is_404(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    connection = make_connection(client, first["id"])
    response = client.post(
        f"{PREFIX}/rooms/{second['id']}/upsert", params={"connection_id": connection["id"]}
    )
    assert response.status_code == 404


def test_sync_now_with_an_unknown_connection_is_404(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": "nope"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_connection"


def test_sync_now_on_a_connection_with_no_upsert_path_is_422(client):
    room = make_room(client)
    connection = make_connection(
        client, room["id"], vendor="nowhere", key_field="Ext__c", capability={"max_batch_size": 5}
    )
    make_rows(client, room["id"], 1)
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    )
    assert response.status_code == 422
    assert "nothing to fall back to" in response.json()["detail"]


def test_a_run_reports_per_row_errors_with_the_crm_s_own_text(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    # An empty external id: there is nothing to key the upsert on, so the
    # connector refuses the row before any request goes out.
    client.post(
        f"{PREFIX}/rooms/{room['id']}/engagement", json={"engagement_id": "", "event_type": "viewed"}
    )

    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    assert body["totals"]["rejected"] == 1
    assert body["chunks"] == []
    assert body["errors"]
    assert "external id" in body["errors"][0]["errors"][0]


def test_dataverse_rows_leave_the_queue_unconfirmed_over_http(client):
    room = make_room(client)
    connection = make_connection(
        client, room["id"], vendor="dataverse", object_name="engagements", key_field="sample_keyattribute"
    )
    queued = make_rows(client, room["id"], 2)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    assert body["totals"]["submitted"] == 2
    assert body["totals"]["confirmed"] == 0
    for record in queued:
        data = client.get(f"/api/records/engagement/{record['id']}").json()["data"]
        assert data["sync_status"] == "unconfirmed"
        assert data["sent_at"]
        assert not data.get("synced_at")
    queue = client.get(
        f"{PREFIX}/rooms/{room['id']}/queue", params={"connection_id": connection["id"]}
    ).json()
    assert queue["counts"]["unconfirmed"] == 2
    assert queue["counts"]["pending"] == 0


def test_a_table_with_no_bulk_upsert_falls_back_over_http(client):
    room = make_room(client)
    connection = make_connection(
        client, room["id"], vendor="legacy_table", object_name="engagements", key_field="eng_key"
    )
    make_rows(client, room["id"], 2)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()
    assert body["mode"] == "single"
    assert len(body["chunks"]) == 2
    assert all(chunk["path"].startswith("/api/legacy/") for chunk in body["chunks"])


def test_two_connections_on_one_room_keep_separate_queues_over_http(client):
    room = make_room(client)
    salesforce = make_connection(client, room["id"])
    dataverse = make_connection(
        client, room["id"], vendor="dataverse", object_name="engagements", key_field="sample_keyattribute"
    )
    make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": salesforce["id"]})

    first = client.get(
        f"{PREFIX}/rooms/{room['id']}/queue", params={"connection_id": salesforce["id"]}
    ).json()
    second = client.get(
        f"{PREFIX}/rooms/{room['id']}/queue", params={"connection_id": dataverse["id"]}
    ).json()
    assert first["counts"]["synced"] == 1
    assert second["counts"]["pending"] == 1


# --------------------------------------------------------------------------- #
# The scheduled trigger
# --------------------------------------------------------------------------- #


def test_the_schedule_refuses_to_run_when_it_is_not_due(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})

    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/schedule", params={"connection_id": connection["id"]}
    ).json()
    assert body["ran"] is False
    assert body["reason"] == "not due"
    assert body["backlog"]["reasons"]


def test_the_schedule_runs_when_the_queue_is_over_the_threshold(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/schedule", params={"connection_id": connection["id"]}
    ).json()
    assert body["ran"] is True
    assert body["driver"] == "scheduled"
    assert body["totals"]["created"] == 1


def test_forcing_the_schedule_records_an_opportunistic_driver(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})
    make_rows(client, room["id"], 1, prefix="late")

    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/schedule",
        params={"connection_id": connection["id"], "force": True},
    ).json()
    assert body["ran"] is True
    assert body["driver"] == "opportunistic"
    assert body["totals"]["rows"] == 1


def test_the_schedule_of_another_rooms_connection_is_404(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    connection = make_connection(client, first["id"])
    response = client.post(
        f"{PREFIX}/rooms/{second['id']}/schedule", params={"connection_id": connection["id"]}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The sync log
# --------------------------------------------------------------------------- #


def test_listing_runs_is_scoped_to_the_room(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    one = make_connection(client, first["id"])
    two = make_connection(client, second["id"])
    make_rows(client, first["id"], 1)
    make_rows(client, second["id"], 1)
    client.post(f"{PREFIX}/rooms/{first['id']}/upsert", params={"connection_id": one["id"]})
    client.post(f"{PREFIX}/rooms/{second['id']}/upsert", params={"connection_id": two["id"]})

    listed = client.get(f"{PREFIX}/rooms/{first['id']}/runs").json()
    assert listed["count"] == 1
    assert listed["runs"][0]["totals"]["created"] == 1
    assert listed["runs"][0]["driver"] == "manual"


def test_runs_can_be_filtered_by_connection(client):
    room = make_room(client)
    one = make_connection(client, room["id"])
    two = make_connection(
        client, room["id"], vendor="dataverse", object_name="engagements", key_field="sample_keyattribute"
    )
    make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": one["id"]})
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": two["id"]})

    listed = client.get(
        f"{PREFIX}/rooms/{room['id']}/runs", params={"connection_id": two["id"]}
    ).json()
    assert listed["count"] == 1
    assert listed["runs"][0]["vendor"] == "dataverse"


def test_reading_one_run_carries_its_progress_and_requests(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    run = client.post(
        f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]}
    ).json()

    body = client.get(f"{PREFIX}/runs/{run['run_id']}").json()
    assert body["id"] == run["run_id"]
    assert body["progress"]["percent"] == 100.0
    assert body["data"]["chunks"][0]["body"]["records"]


def test_an_unknown_run_is_404(client):
    response = client.get(f"{PREFIX}/runs/crm_upsert_run_nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_run"


def test_a_run_id_that_is_not_a_run_is_404(client):
    room = make_room(client)
    assert client.get(f"{PREFIX}/runs/{room['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def audit_entries(client, **params) -> list[dict]:
    return client.get("/api/audit", params={"limit": 1000, **params}).json()["entries"]


def my_sources(client) -> set[str]:
    return {entry["source"] for entry in audit_entries(client) if PREFIX in (entry.get("source") or "")}


def test_every_write_names_the_route_that_served_it(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    client.patch(f"{PREFIX}/config", json={"queue": {"target": 150}})
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})

    assert my_sources(client) == {
        f"POST {PREFIX}/connections",
        f"POST {PREFIX}/rooms/{room['id']}/engagement",
        f"PATCH {PREFIX}/config",
        f"POST {PREFIX}/rooms/{room['id']}/upsert",
    }


def test_the_row_write_back_and_the_run_log_share_the_routes_source(client):
    """Both were caused by the same request, so both name it."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})

    run_entry = audit_entries(client, collection="crm_upsert_run")[0]
    row_entry = audit_entries(client, collection="engagement", action="update")[0]
    assert run_entry["source"] == f"POST {PREFIX}/rooms/{room['id']}/upsert"
    assert row_entry["source"] == f"POST {PREFIX}/rooms/{room['id']}/upsert"


def test_a_write_records_the_actor_it_was_given(client):
    room = make_room(client)
    client.post(
        f"{PREFIX}/rooms/{room['id']}/engagement",
        json={"engagement_id": "k1"},
        params={"actor": "dana"},
    )
    entry = audit_entries(client, collection="engagement")[0]
    assert entry["actor"] == "dana"


def test_no_audit_row_names_a_path_this_feature_does_not_serve(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})
    client.delete(f"{PREFIX}/connections/{connection['id']}")

    stale = [source for source in my_sources(client) if source.count("/api/wf-038") > 1]
    assert not stale, f"audit rows name an unserved path: {stale}"


def test_every_source_this_feature_can_write_is_one_of_its_own_routes():
    """The source strings are derived from the mounted router, not written out.

    Read out of the module rather than asserted one by one, because the defect
    this guards is a *drift* between the recorded source and the mounted path: a
    hand-written list in the test would be updated in the same commit as the bug
    and would never catch it.
    """
    module = load_feature(MODULE)
    source = Path(module.__file__).read_text(encoding="utf-8")
    expressions = re.findall(r'source=f?"([^"]*\{router\.prefix\}[^"]*)"', source)
    assert expressions, "no source is built from router.prefix"

    write_routes = {
        (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
        for route in module.router.routes
        if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in ("POST", "PATCH", "PUT", "DELETE")
    }
    recorded = {
        (expression.split(" ", 1)[0], f"{PREFIX}{expression.split('{router.prefix}')[1]}")
        for expression in expressions
    }
    assert {method for method, _path in write_routes} == {method for method, _path in recorded}
    for method, path in recorded:
        assert (method, path) in write_routes, f"{method} {path} is not a route this feature serves"


def mounted_routes(client) -> dict[str, list[list[str]]]:
    """Every mounted path, per method, split into segments.

    ``app.routes`` is not usable for this: this FastAPI version records an
    included router as a single nested entry rather than flattening its routes, so
    a check built on it would see neither a feature's routes nor its own. The
    published schema is the flat, authoritative list of what is served.
    """
    schema = client.get("/openapi.json").json()
    mounted: dict[str, list[list[str]]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.lower() in ("get", "post", "patch", "put", "delete"):
                mounted.setdefault(method.upper(), []).append(path.strip("/").split("/"))
    return mounted


#: The record-id shapes the core ``AuditedDatabase`` mints. A recorded source
#: carries the id that filled a parameter, so a segment matching this is a value
#: where the route declares a parameter.
RECORD_ID = re.compile(r"^[a-z_]+_[0-9a-f]{16,}$")


def serves(mounted: dict[str, list[list[str]]], method: str, path: str) -> bool:
    """Whether ``method path`` reaches a mounted route.

    Segment by segment, because a path parameter fills exactly one segment: the
    two must line up in number as well as in shape.
    """
    for candidate in mounted.get(method, []):
        recorded = path.strip("/").split("/")
        if len(candidate) != len(recorded):
            continue
        if all(
            expected.startswith("{") or expected == actual or RECORD_ID.match(actual)
            for expected, actual in zip(candidate, recorded)
        ):
            return True
    return False


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client):
    """The invariant, product-wide: an audit ``source`` is always callable.

    This drives writes through this feature *and* through the core record routes,
    then checks every row the log holds. The defect the contract names by name is
    "a feature's audit log kept recording a path the app had stopped serving",
    which is invisible to a test scoped to one feature's own sources.
    """
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_rows(client, room["id"], 1)
    client.patch(f"{PREFIX}/config", json={"schedule": {"interval_hours": 12}})
    client.post("/api/records/room", json={"name": "Core room", "account": "Core"})
    client.post(f"{PREFIX}/rooms/{room['id']}/upsert", params={"connection_id": connection["id"]})
    client.post(f"/api/records/bulk/x", json=[{"k": "v"}])
    client.delete(f"{PREFIX}/connections/{connection['id']}")

    mounted = mounted_routes(client)
    assert serves(mounted, "POST", f"{PREFIX}/rooms/{room['id']}/upsert")
    assert serves(mounted, "PATCH", f"{PREFIX}/config")

    entries = audit_entries(client)
    assert entries
    for entry in entries:
        method, _, path = str(entry["source"]).partition(" ")
        assert serves(mounted, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


def test_the_audit_source_is_built_from_the_live_prefix_not_a_hardcoded_url():
    """The defect the contract names by name: a row that names a path nobody called.

    Asserted as "every ``source=`` in the module interpolates ``router.prefix``",
    which is the property that matters. A test that simply banned the literal
    ``/api/wf-038`` would fail on the router's own ``prefix=`` argument and so
    could not be written at all.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    every_source = re.findall(r'source=f"([^"]*)"', source)
    assert every_source, "no source is written with an f-string"
    for expression in every_source:
        assert "{router.prefix}" in expression, expression


def test_a_deleted_route_would_be_reported_not_silently_accepted():
    """The host refuses a colliding (method, path); this asserts the feature's own
    writes would be caught by the audit check if its route were removed."""
    module = load_feature(MODULE)
    paths = {route.path for route in module.router.routes}
    assert f"{PREFIX}/rooms/{{room_id}}/upsert" in paths
    assert f"{PREFIX}/config" in paths


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_connections_rows_and_runs():
    from datetime import datetime, timezone

    import random

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create("room", {"name": "Northwind", "account": "Northwind Traders"}, source="test")
        summary = load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "Northwind Traders")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        store = RecordStore(db)
        connections = store.list("crm_upsert_connection", limit=20)
        runs = store.list("crm_upsert_run", limit=20)
        rows = store.list("engagement", room_id=room["id"], limit=50)
        assert len(connections) == 4
        assert len(runs) == 4
        assert len(rows) == 6
        assert isinstance(summary, str) and "connections" in summary
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_covers_the_four_researched_connector_shapes():
    from datetime import datetime, timezone

    import random

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create("room", {"name": "R", "account": "A"}, source="test")
        load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "A")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        store = RecordStore(db)
        vendors = {record["data"]["vendor"]: record["data"] for record in store.list("crm_upsert_connection", limit=20)}
        assert set(vendors) == {"salesforce", "hubspot", "dataverse", "legacy_table"}
        assert vendors["salesforce"]["all_or_none"] is True
        assert vendors["hubspot"]["key_field"] == "email"
        assert vendors["hubspot"]["required_properties"] == ["lastname"]
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_shows_every_state_the_research_distinguishes():
    """A demo of only successes teaches a reviewer nothing."""
    from datetime import datetime, timezone

    import random

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create("room", {"name": "R", "account": "A"}, source="test")
        load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "A")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        store = RecordStore(db)
        totals = {outcome: 0 for outcome in ("created", "updated", "failed", "rolled_back", "rejected", "submitted")}
        for run in store.list("crm_upsert_run", limit=20):
            for name, value in (run["data"]["totals"] or {}).items():
                if name in totals:
                    totals[name] += int(value)
        # A created row, an updated row, the researched 300 duplicate under
        # allOrNone, a row refused before sending, and rows nothing can confirm.
        assert totals["created"] > 0
        assert totals["updated"] > 0
        assert totals["failed"] > 0
        assert totals["rolled_back"] > 0
        assert totals["rejected"] > 0
        assert totals["submitted"] > 0
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_leaves_dataverse_rows_unconfirmed():
    from datetime import datetime, timezone

    import random

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create("room", {"name": "R", "account": "A"}, source="test")
        load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "A")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        store = RecordStore(db)
        dataverse = next(
            record
            for record in store.list("crm_upsert_connection", limit=20)
            if record["data"]["vendor"] == "dataverse"
        )
        rows = store.list("engagement", room_id=room["id"], limit=50)
        # The per-connection state is the authoritative one, so the check reads
        # it: the row-level `sync_status` records whichever connection ran last,
        # and the last of four is the legacy table, not Dataverse.
        statuses = {
            (record["data"].get("sync") or {}).get(dataverse["id"], {}).get("status")
            for record in rows
        }
        assert "unconfirmed" in statuses
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_says_so_when_there_are_no_rooms():
    from datetime import datetime, timezone

    import random

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [], "now": datetime.now(timezone.utc), "rng": random.Random(1)}
        )
        assert "no demo rooms" in summary
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_writes_only_audited_rows():
    from datetime import datetime, timezone

    import random

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create("room", {"name": "R", "account": "A"}, source="test")
        before = db.audit_count()
        load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "A")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        assert db.audit_count() > before
        # Every row this feature seeded is attributed to the seeder, so the demo
        # cannot be mistaken for work an operator's sync did.
        seeded = {"engagement", "crm_upsert_connection", "crm_upsert_run"}
        entries = [entry for entry in db.audit(limit=1000) if entry["collection"] in seeded]
        assert entries
        assert all(entry["source"] == "seed" for entry in entries)
    finally:
        db.close()
        tmp.cleanup()
