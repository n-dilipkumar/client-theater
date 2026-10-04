"""The HTTP surface, the audit-source rule, and the demo data for WF-046.

Split from ``test_wf046.py``, which holds the researched rules against the domain.
This file drives the feature's own mounted router, so it covers the wiring, the
error mapping, and the two things the contract names about a feature that the
domain tests cannot see:

* **Every audit row names a route the app actually serves.** The contract names
  the defect by name - "a feature's audit log kept recording a path the app had
  stopped serving" - and it is invisible to a test scoped to one feature's own
  strings. These tests read the sources out of the module and the routes out of
  the published OpenAPI schema, so a drift is caught rather than updated in the
  same commit as the bug.
* **The demo shows a state that is not a success.** A seeder that only produces
  green teaches a reviewer nothing, and the issue says so: "The seed must print at
  least one state that is not a success. A throttle decision that defers a batch
  is such a state."

The ``client`` fixture comes from ``conftest.py``: one ``TestClient`` per module
with ``app.state`` swapped per test, so this file is cheap and every test sees an
empty database of its own. That is what makes it safe under pytest-xdist.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

PREFIX = "/api/wf-046"
MODULE = "wf046_throttle_and_retry_under_vendor_api_ra"
FEATURE_ID = "wf-046-throttle-and-retry-under-vendor-api-ra"

HUBSPOT_OK = {
    "status": 201,
    "accepted": True,
    "headers": {
        "X-HubSpot-RateLimit-Max": "100",
        "X-HubSpot-RateLimit-Remaining": "97",
        "X-HubSpot-RateLimit-Interval-Milliseconds": "10000",
        "X-HubSpot-RateLimit-Daily": "250000",
        "X-HubSpot-RateLimit-Daily-Remaining": "249812",
    },
}
HUBSPOT_THROTTLED = {
    "status": 429,
    "code": "RATE_LIMIT",
    "headers": {
        "X-HubSpot-RateLimit-Remaining": "0",
        "X-HubSpot-RateLimit-Daily-Remaining": "249001",
        "Retry-After": "45",
    },
}
HUBSPOT_LOCKED = {"status": 423, "headers": {"Retry-After": "1"}}
SALESFORCE_LIMIT = {
    "status": 403,
    "code": "REQUEST_LIMIT_EXCEEDED",
    "headers": {"Sforce-Limit-Info": "api-usage=10018/100000; api-bursts=1/750"},
}


# There is deliberately no local ``client`` fixture. ``conftest.py`` already
# provides one as a module-scoped lifespan with a per-test store swapped onto
# ``app.state``, which is the cheap and isolating combination. A local fixture of
# the same name shadows the shared one, and the first draft of this file did
# exactly that - it re-exported the *fixture definition* rather than asking for
# its value, so every test received a ``FixtureFunctionDefinition`` instead of a
# client.


def make_room(client, name: str = "Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def make_connection(client, room_id: str, vendor: str = "hubspot", **policy) -> dict:
    body = {"vendor": vendor}
    if policy:
        body["policy"] = policy
    response = client.post(f"{PREFIX}/connections", json=body, params={"room_id": room_id})
    assert response.status_code == 201, response.text
    return response.json()


def make_batch(client, room_id: str, connection_id: str, count: int = 2, prefix: str = "ext"):
    return client.post(
        f"{PREFIX}/rooms/{room_id}/batches",
        json={
            "connection_id": connection_id,
            "object_name": "contacts",
            "rows": [{"external_id": f"{prefix}-{index}"} for index in range(count)],
        },
    ).json()


def observe(client, room_id: str, batch_id: str, answer: dict):
    return client.post(f"{PREFIX}/rooms/{room_id}/batches/{batch_id}/observe", json=answer).json()


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_vocabulary_publishes_the_five_steps_the_states_and_the_signals(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert [step["step"] for step in body["flow"]] == [1, 2, 3, 4, 5]
    assert "idempotency key" in body["data_flow"]
    assert {row["value"] for row in body["batch_states"]} >= {
        "proceeding",
        "deferred",
        "needs_action",
    }
    assert "salesforce-request-limit-exceeded" in {row["id"] for row in body["signals"]}


def test_vocabulary_names_the_five_collections_this_workflow_writes(client):
    collections = client.get(f"{PREFIX}/vocabulary").json()["collections"]
    assert collections == {
        "connection": "throttle_connection",
        "bucket": "throttle_bucket",
        "batch": "throttle_batch",
        "log": "throttle_log",
        "meter": "throttle_meter",
    }


def test_vocabulary_reports_only_hubspot_as_preemptively_throttled(client):
    policies = client.get(f"{PREFIX}/policies").json()
    assert policies["preemptive"] == ["hubspot"]
    assert policies["reactive_only"] == ["dataverse", "salesforce"]


def test_signals_are_served_with_their_own_quotes(client):
    body = client.get(f"{PREFIX}/signals").json()
    by_id = {row["id"]: row for row in body["signals"]}
    assert "2 seconds" in by_id["hubspot-sync-lock"]["basis"]
    assert "110 requests" in client.get(f"{PREFIX}/policies").json()["vendors"]["hubspot"]["basis"]


def test_inferences_are_served_as_data_with_a_change_it_for_each(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    ids = {entry["id"] for entry in body["inferences"]}
    assert "retry-after-cap-is-24h" in ids
    assert "jitter-is-derived-not-drawn" in ids
    for entry in body["inferences"]:
        assert entry["change_it"].strip(), entry["id"]
        assert entry["basis"].strip(), entry["id"]


def test_the_backoff_route_publishes_every_rung(client):
    body = client.get(f"{PREFIX}/backoff").json()
    assert body["rungs"]["1"] == 30
    assert body["rungs"]["8"] == 1800
    assert body["max_attempts"] == 5


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def test_creating_a_connection_returns_its_bucket_and_policy(client):
    room = make_room(client)
    body = make_connection(client, room["id"])
    assert body["vendor"] == "hubspot"
    assert body["bucket"]["tokens"] == 110
    assert body["effective"]["preemptive"] is True
    assert body["paused"] is False
    assert body["policy_sourced"] is True


def test_creating_a_connection_without_a_vendor_is_400(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/connections", json={}, params={"room_id": room["id"]})
    assert response.status_code == 400
    assert response.json()["error"] == "throttle_refused"


def test_a_policy_that_could_never_refill_is_422_not_400(client):
    """The request is well-formed JSON and the *policy* is what is wrong, so the
    status says so. 400 would send a reader looking at the payload.

    This test is also the one that caught the order dependency. ``acme`` is
    deliberately the vendor its sibling in ``test_wf046.py`` registers, because
    that registration used to leak: with ``acme`` already in the process-wide
    policy registry this request had a ``window_seconds`` to pair with its
    ``sustained``, so the policy was valid, the route answered 201, and the test
    failed under ``-n 0`` while passing alone and passing under the default
    sharding. The registry is now restored by the ``registered_policy`` fixture, so
    the two files are independent whichever worker runs them.
    """
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={"vendor": "acme", "policy": {"sustained": 100}},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_policy"
    assert "burst" in response.json()["fields"]


def test_no_policy_registered_by_another_file_leaked_into_this_one(client):
    """The leak assertion, made from the side that was damaged.

    ``test_wf046.py`` registers ``acme`` through the researched extension point and
    that registry is process-wide. If the fixture stopped restoring it, *this* file
    would start answering 201 where it answers 422 above - on a run where the two
    files shared a worker, and on no other. So the state is asserted directly.
    """
    from dsr.throttle import policies

    assert "acme" not in policies.all_policies(), (
        "a test registered a policy and did not restore the registry; the next "
        "connection declaring that vendor will silently inherit its numbers"
    )


def test_a_connection_to_a_vendor_with_no_registered_policy_is_accepted(client):
    """The researched extensibility note made concrete: a new vendor is a policy
    object away, so an unknown vendor is not refused."""
    room = make_room(client)
    body = make_connection(client, room["id"], "some_new_crm")
    assert body["registered"] is False
    assert body["policy_sourced"] is False
    assert body["effective"]["preemptive"] is False


def test_listing_connections_counts_paused_and_preemptive(client):
    room = make_room(client)
    hubspot = make_connection(client, room["id"])
    make_connection(client, room["id"], "salesforce")
    client.post(f"{PREFIX}/connections/{hubspot['id']}/pause", json={"reason": "looking"})
    body = client.get(f"{PREFIX}/connections", params={"room_id": room["id"]}).json()
    assert body["count"] == 2
    assert body["paused"] == 1
    assert body["preemptive"] == 1


def test_reading_an_unknown_connection_is_404(client):
    response = client.get(f"{PREFIX}/connections/throttle_connection_nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_connection"


def test_a_patch_merges_and_an_unknown_field_is_refused(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    patched = client.patch(f"{PREFIX}/connections/{connection['id']}", json={"daily": 900_000})
    assert patched.status_code == 200
    assert patched.json()["policy"]["daily"] == 900_000
    assert patched.json()["policy"]["burst"] == 110

    refused = client.patch(
        f"{PREFIX}/connections/{connection['id']}", json={"policy": {"daily_limit": 5}}
    )
    assert refused.status_code == 400
    assert "not a policy field" in refused.json()["detail"]


def test_pausing_and_resuming_is_two_routes_and_the_bucket_is_not_refilled(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_batch(client, room["id"], connection["id"], count=5)

    paused = client.post(
        f"{PREFIX}/connections/{connection['id']}/pause", json={"reason": "vendor is refusing"}
    ).json()
    assert paused["paused"] is True
    assert paused["pause_reason"] == "vendor is refusing"

    blocked = make_batch(client, room["id"], connection["id"], prefix="later")
    assert blocked["decision_reason"] == "paused"
    assert blocked["state"] == "deferred"

    resumed = client.post(f"{PREFIX}/connections/{connection['id']}/resume").json()
    assert resumed["paused"] is False
    assert resumed["bucket"]["tokens"] < 110, "a resume must not hand out a full bucket"


# --------------------------------------------------------------------------- #
# Batches: the pre-emptive step
# --------------------------------------------------------------------------- #


def test_a_batch_the_bucket_allows_proceeds_and_carries_its_keys(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"], count=3)
    assert batch["state"] == "proceeding"
    assert batch["decision"] == "proceed"
    assert len(batch["keys"]) == 3
    assert all(entry["idempotency_key"] for entry in batch["keys"])
    assert batch["bucket"]["tokens"] == 107


def test_an_empty_bucket_defers_before_anything_is_sent(client):
    room = make_room(client)
    connection = make_connection(client, room["id"], burst=2, sustained=2, window_seconds=10)
    make_batch(client, room["id"], connection["id"], count=2)
    blocked = make_batch(client, room["id"], connection["id"], count=1, prefix="b")
    assert blocked["state"] == "deferred"
    assert blocked["decision_reason"] == "empty"
    assert blocked["schedule"]["seconds"] >= 1


def test_a_batch_without_a_connection_is_400_over_http(client):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/batches", json={"rows": []})
    assert response.status_code == 400
    assert "per-connector" in response.json()["detail"]


def test_a_row_with_no_external_id_is_refused_over_http(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/batches",
        json={"connection_id": connection["id"], "rows": [{"email": "a@b.test"}]},
    )
    assert response.status_code == 400
    assert "same key for every row" in response.json()["detail"]


def test_two_rows_sharing_an_external_id_are_refused_over_http(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/batches",
        json={
            "connection_id": connection["id"],
            "rows": [{"external_id": "a"}, {"external_id": "a"}],
        },
    )
    assert response.status_code == 400
    assert "silences the second" in response.json()["detail"]


def test_a_batch_against_another_rooms_connection_is_404(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    connection = make_connection(client, first["id"])
    response = client.post(
        f"{PREFIX}/rooms/{second['id']}/batches",
        json={"connection_id": connection["id"], "rows": [{"external_id": "a"}]},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_connection"


def test_a_batch_in_a_room_that_does_not_exist_is_404(client):
    response = client.post(f"{PREFIX}/rooms/room_nope/batches", json={"rows": []})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_room"


def test_reading_a_batch_returns_its_log(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    body = client.get(f"{PREFIX}/rooms/{room['id']}/batches/{batch['id']}").json()
    assert body["id"] == batch["id"]
    assert [entry["event"] for entry in body["log"]] == ["batch_submitted_proceed"]


def test_reading_an_unknown_batch_is_404(client):
    room = make_room(client)
    response = client.get(f"{PREFIX}/rooms/{room['id']}/batches/throttle_batch_nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_batch"


def test_another_rooms_batch_is_404(client):
    first = make_room(client, "A")
    second = make_room(client, "B")
    connection = make_connection(client, first["id"])
    batch = make_batch(client, first["id"], connection["id"])
    response = client.get(f"{PREFIX}/rooms/{second['id']}/batches/{batch['id']}")
    assert response.status_code == 404


def test_batches_can_be_filtered_and_where_parses_a_json_path(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_batch(client, room["id"], connection["id"], prefix="a")
    body = client.get(
        f"{PREFIX}/rooms/{room['id']}/batches", params={"where": '{"vendor":"hubspot"}'}
    ).json()
    assert body["count"] == 1
    assert body["counts"]["proceeding"] == 1

    bad = client.get(f"{PREFIX}/rooms/{room['id']}/batches", params={"where": "{nope}"})
    assert bad.status_code == 400


# --------------------------------------------------------------------------- #
# Observing what the vendor answered
# --------------------------------------------------------------------------- #


def test_accepted_rows_complete_the_batch_and_write_the_meter(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    answer = observe(client, room["id"], batch["id"], HUBSPOT_OK)
    assert answer["batch"]["state"] == "complete"
    assert answer["batch"]["terminal"] is True
    assert answer["quota"]["window"]["remaining"] == 97.0
    assert answer["quota"]["daily"]["remaining"] == 249812.0


def test_a_429_defers_under_the_vendors_own_retry_after(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    stored = observe(client, room["id"], batch["id"], HUBSPOT_THROTTLED)["batch"]
    assert stored["state"] == "deferred"
    assert stored["signal"]["kind"] == "rate_limit"
    assert stored["schedule"]["seconds"] == 45
    assert stored["schedule"]["source"] == "retry_after"


def test_a_423_holds_the_vendors_two_second_floor_even_beside_a_shorter_retry_after(client):
    """The researched step 4, over HTTP: "you should include a delay of at least 2
    seconds between your API requests". HubSpot sent Retry-After: 1 with the 423,
    and the floor the same document states still wins."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    stored = observe(client, room["id"], batch["id"], HUBSPOT_LOCKED)["batch"]
    assert stored["signal"]["kind"] == "lock"
    assert stored["schedule"]["seconds"] == 2
    assert stored["schedule"]["floor_seconds"] == 2
    assert "documented 2s floor" in stored["schedule"]["basis"]


def test_a_salesforce_request_limit_is_a_throttle_and_its_usage_header_is_read(client):
    room = make_room(client)
    connection = make_connection(client, room["id"], "salesforce")
    batch = make_batch(client, room["id"], connection["id"])
    answer = observe(client, room["id"], batch["id"], SALESFORCE_LIMIT)
    assert answer["batch"]["signal"]["id"] == "salesforce-request-limit-exceeded"
    assert answer["quota"]["daily"]["remaining"] == 89982.0
    assert answer["quota"]["burst"]["remaining"] == 749.0


def test_a_retry_after_beyond_the_cap_sends_the_batch_to_a_person(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    answer = observe(
        client, room["id"], batch["id"], {"status": 429, "headers": {"Retry-After": "999999"}}
    )["batch"]
    assert answer["state"] == "needs_action"
    assert answer["schedule"]["source"] == "beyond_cap"
    assert answer["schedule"]["seconds"] is None
    assert answer["next_attempt_at"] is None


def test_an_answer_the_room_does_not_recognise_waits_for_a_person(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    stored = observe(client, room["id"], batch["id"], {"status": 418})["batch"]
    assert stored["state"] == "needs_action"
    assert stored["signal"] is None


def test_a_status_that_is_not_a_number_is_400_over_http(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/batches/{batch['id']}/observe",
        json={"status": "429 Too Many Requests"},
    )
    assert response.status_code == 400


def test_the_throttle_log_is_the_story_in_the_order_it_happened(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_THROTTLED)
    body = client.get(f"{PREFIX}/rooms/{room['id']}/throttle-log").json()
    assert [entry["event"] for entry in body["events"]] == [
        "batch_submitted_proceed",
        "batch_deferred_rate_limit",
    ]
    assert "Retry-After: 45" in body["events"][-1]["detail"]


def test_the_throttle_log_can_be_narrowed_to_one_batch(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    first = make_batch(client, room["id"], connection["id"], prefix="a")
    make_batch(client, room["id"], connection["id"], prefix="b")
    body = client.get(
        f"{PREFIX}/rooms/{room['id']}/throttle-log", params={"batch_id": first["id"]}
    ).json()
    assert body["count"] == 1
    assert body["events"][0]["batch_id"] == first["id"]


# --------------------------------------------------------------------------- #
# Retrying under the same keys
# --------------------------------------------------------------------------- #


def test_a_retry_reuses_the_keys_and_says_so(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_THROTTLED)

    forced = client.post(
        f"{PREFIX}/rooms/{room['id']}/batches/{batch['id']}/retry", json={"force": True}
    ).json()
    assert forced["sent"] is True
    assert [entry["idempotency_key"] for entry in forced["batch"]["keys"]] == [
        entry["idempotency_key"] for entry in batch["keys"]
    ]
    assert forced["batch"]["keys_reused"] is True


def test_a_retry_before_the_wait_is_refused_so_the_queue_does_not_hammer_the_vendor(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_THROTTLED)

    early = client.post(f"{PREFIX}/rooms/{room['id']}/batches/{batch['id']}/retry").json()
    assert early["sent"] is False
    assert early["batch"]["state"] == "deferred"


def test_a_retry_that_would_change_the_keys_is_refused_over_http(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/batches/{batch['id']}/retry",
        json={"keys": [{"idempotency_key": "something-else"}]},
    )
    assert response.status_code == 400
    assert "duplicate write" in response.json()["detail"]


def test_a_complete_batch_is_not_retried(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_OK)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/batches/{batch['id']}/retry")
    assert response.status_code == 400
    assert "is complete" in response.json()["detail"]


def test_the_drain_route_retries_only_what_is_due(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    first = make_batch(client, room["id"], connection["id"], prefix="a")
    second = make_batch(client, room["id"], connection["id"], prefix="b")
    observe(client, room["id"], first["id"], {"status": 429, "headers": {"Retry-After": "30"}})
    observe(client, room["id"], second["id"], {"status": 429, "headers": {"Retry-After": "6000"}})

    body = client.post(
        f"{PREFIX}/rooms/{room['id']}/drain", json={"force": True, "limit": 5}
    ).json()
    # Nothing is due yet, so the drain reports rather than sends. The route is the
    # queue worker's, and a worker that walked the whole queue every interval would
    # be the immediate retry into a rate limit.
    assert body["retried"] == []
    assert sorted(body["scheduled"]) == sorted([first["id"], second["id"]])
    assert body["keys_reused"] is True


def test_the_drain_of_a_room_that_does_not_exist_is_404(client):
    response = client.post(f"{PREFIX}/rooms/room_nope/drain", json={})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The quota meter and the summary
# --------------------------------------------------------------------------- #


def test_the_quota_meter_reports_an_unknown_half_as_unknown_not_zero(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_OK)

    meter = client.get(f"{PREFIX}/rooms/{room['id']}/quota").json()
    assert meter["count"] == 1
    row = meter["meters"][0]
    assert row["window_remaining"] == 97.0
    assert row["daily_remaining"] == 249812.0
    assert row["known"] is True
    assert "holds no credential" in meter["note"]


def test_a_salesforce_meter_reads_both_segments_of_the_usage_header(client):
    room = make_room(client)
    connection = make_connection(client, room["id"], "salesforce")
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], SALESFORCE_LIMIT)
    row = client.get(f"{PREFIX}/rooms/{room['id']}/quota").json()["meters"][0]
    assert row["source_header"] == "Sforce-Limit-Info"
    assert row["daily_remaining"] == 89982.0


def test_a_dataverse_meter_reports_the_gap_rather_than_a_number(client):
    room = make_room(client)
    connection = make_connection(client, room["id"], "dataverse")
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], {"status": 429})
    row = client.get(f"{PREFIX}/rooms/{room['id']}/quota").json()["meters"][0]
    assert row["known"] is False
    assert "429" in row["gap"]


def test_the_summary_counts_this_rooms_own_batches(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    good = make_batch(client, room["id"], connection["id"], prefix="a")
    observe(client, room["id"], good["id"], HUBSPOT_OK)
    bad = make_batch(client, room["id"], connection["id"], prefix="b")
    observe(client, room["id"], bad["id"], HUBSPOT_THROTTLED)

    body = client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["batches"] == 2
    assert body["counts"]["complete"] == 1
    assert body["deferred"] == 1
    assert body["longest_wait_seconds"] == 45
    assert body["kinds"] == ["rate_limit"]
    assert body["preemptive"] == 1


def test_the_summary_of_a_room_that_does_not_exist_is_404(client):
    assert client.get(f"{PREFIX}/rooms/room_nope/summary").status_code == 404


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def audit_entries(client, **params) -> list[dict]:
    return client.get("/api/audit", params={"limit": 1000, **params}).json()["entries"]


def my_sources(client) -> set[str]:
    return {
        entry["source"] for entry in audit_entries(client) if PREFIX in (entry.get("source") or "")
    }


def test_every_write_names_the_route_that_served_it(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_THROTTLED)

    # A second batch the room cannot read as a throttle, so it is waiting for a
    # person and the drain will send it. This matters for the assertion below:
    # a drain that finds nothing due writes *nothing*, and a write-less route
    # cannot appear in the audit log at all.
    due = make_batch(client, room["id"], connection["id"], prefix="due")
    observe(client, room["id"], due["id"], {"status": 418})

    client.patch(f"{PREFIX}/connections/{connection['id']}", json={"daily": 900_000})
    drained = client.post(f"{PREFIX}/rooms/{room['id']}/drain", json={}).json()
    assert due["id"] in drained["retried"], "the drain must have written, or it proves nothing"

    client.post(f"{PREFIX}/connections/{connection['id']}/pause", json={"reason": "x"})
    client.post(f"{PREFIX}/connections/{connection['id']}/resume")

    assert my_sources(client) == {
        f"POST {PREFIX}/connections",
        f"POST {PREFIX}/rooms/{room['id']}/batches",
        f"POST {PREFIX}/rooms/{room['id']}/batches/{batch['id']}/observe",
        f"POST {PREFIX}/rooms/{room['id']}/batches/{due['id']}/observe",
        f"PATCH {PREFIX}/connections/{connection['id']}",
        f"POST {PREFIX}/rooms/{room['id']}/drain",
        f"POST {PREFIX}/connections/{connection['id']}/pause",
        f"POST {PREFIX}/connections/{connection['id']}/resume",
    }


def test_a_write_records_the_actor_it_was_given(client):
    room = make_room(client)
    client.post(
        f"{PREFIX}/connections",
        json={"vendor": "hubspot"},
        params={"room_id": room["id"], "actor": "dana"},
    )
    entry = audit_entries(client, collection="throttle_connection")[0]
    assert entry["actor"] == "dana"


def test_the_bucket_write_and_the_batch_write_share_the_submit_routes_source(client):
    """Both were caused by one request, so both name it."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_batch(client, room["id"], connection["id"])

    batch_entry = audit_entries(client, collection="throttle_batch")[0]
    bucket_entry = audit_entries(client, collection="throttle_bucket")[0]
    source = f"POST {PREFIX}/rooms/{room['id']}/batches"
    assert batch_entry["source"] == source
    assert bucket_entry["source"] == source


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
        (
            expression.split(" ", 1)[0],
            # The module writes its room-scoped sources as an f-string, so the
            # parameter names arrive doubled - `{{room_id}}` - and have to be
            # un-doubled before they can be compared with a route path.
            f"{PREFIX}{expression.split('{router.prefix}')[1]}".replace("{{", "{").replace(
                "}}", "}"
            ),
        )
        for expression in expressions
    }
    assert {method for method, _path in write_routes} == {method for method, _path in recorded}
    for method, path in recorded:
        assert (method, path) in write_routes, f"{method} {path} is not a route this feature serves"


def test_the_audit_source_is_built_from_the_live_prefix_not_a_hardcoded_url():
    """Asserted as "every ``source=`` in the module interpolates ``router.prefix``",
    which is the property that matters. A test that simply banned the literal
    ``/api/wf-046`` would fail on the router's own ``prefix=`` argument."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    every_source = re.findall(r'source=f"([^"]*)"', source)
    assert every_source, "no source is written with an f-string"
    for expression in every_source:
        assert "{router.prefix}" in expression, expression


def mounted_routes(client) -> dict[str, list[list[str]]]:
    """Every mounted path, per method, split into segments.

    ``app.routes`` is not usable for this: this FastAPI version records an included
    router as a single nested entry rather than flattening its routes. The
    published schema is the flat, authoritative list of what is served.
    """
    schema = client.get("/openapi.json").json()
    mounted: dict[str, list[list[str]]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.lower() in ("get", "post", "patch", "put", "delete"):
                mounted.setdefault(method.upper(), []).append(path.strip("/").split("/"))
    return mounted


RECORD_ID = re.compile(r"^[a-z_]+_[0-9a-f]{16,}$")


def serves(mounted: dict[str, list[list[str]]], method: str, path: str) -> bool:
    """Whether ``method path`` reaches a mounted route."""
    for candidate in mounted.get(method, []):
        recorded = path.strip("/").split("/")
        if len(candidate) != len(recorded):
            continue
        if all(
            expected.startswith("{") or expected == actual or RECORD_ID.match(actual)
            for expected, actual in zip(candidate, recorded, strict=False)
        ):
            return True
    return False


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client):
    """The invariant, product-wide: an audit ``source`` is always callable.

    Drives writes through this feature *and* through the core record routes, then
    checks every row the log holds.
    """
    room = make_room(client)
    connection = make_connection(client, room["id"])
    batch = make_batch(client, room["id"], connection["id"])
    observe(client, room["id"], batch["id"], HUBSPOT_THROTTLED)
    client.post("/api/records/room", json={"name": "Core room", "account": "Core"})
    client.post("/api/records/bulk/x", json=[{"k": "v"}])

    mounted = mounted_routes(client)
    entries = audit_entries(client)
    assert entries
    for entry in entries:
        method, _, path = str(entry["source"]).partition(" ")
        assert serves(mounted, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


def test_the_feature_module_is_listed_by_discovery_with_its_prefix(client):
    registry = client.get("/api/features").json()
    mine = next(row for row in registry["features"] if row["id"] == FEATURE_ID)
    assert mine["prefix"] == PREFIX
    assert mine["ticket"] == "WF-046"
    assert mine["routes"]
    assert not [row for row in registry.get("failed", []) if FEATURE_ID in str(row)]


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_result():
    """Run the seed over a throwaway database and return plain data, not a store.

    The database is closed in the ``finally`` before this returns, so the caller
    gets dictionaries rather than a ``RecordStore``. Returning the store handed
    back an object bound to a closed connection, and every assertion that read it
    afterwards failed with "Cannot operate on a closed database" - a failure in
    the helper, not in the seed. The rows are copied out while the connection is
    still open.
    """
    import random
    from datetime import datetime, timezone

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create(
            "room", {"name": "Northwind", "account": "Northwind Traders"}, source="test"
        )
        summary = load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "Northwind Traders")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        store = RecordStore(db)
        collections = {
            name: [dict(record) for record in store.list(name, limit=200)]
            for name in (
                "throttle_connection",
                "throttle_bucket",
                "throttle_batch",
                "throttle_log",
                "throttle_meter",
            )
        }
        return {"summary": summary, "room_id": room["id"], **collections}
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_produces_connections_buckets_batches_and_a_log():
    seeded = seed_result()
    assert len(seeded["throttle_connection"]) == 3
    assert len(seeded["throttle_bucket"]) == 3
    assert len(seeded["throttle_batch"]) == 6
    assert seeded["throttle_log"]
    assert seeded["throttle_meter"]
    assert isinstance(seeded["summary"], str) and "connections" in seeded["summary"]


def test_the_seed_prints_a_state_that_is_not_a_success():
    """The issue says so explicitly: a demo of only successes teaches a reviewer
    nothing, and a throttle decision that defers a batch is such a state."""
    summary = seed_result()["summary"]
    assert "0 deferred" not in summary
    assert "deferred" in summary


def test_every_character_of_the_seed_string_survives_a_windows_console():
    """A single U+2192 RIGHTWARDS ARROW in one recovered feature broke the entire
    seeder on a Windows console, so the string is encoded as cp1252 rather than
    merely asserted to be a string."""
    seed_result()["summary"].encode("cp1252")


def test_the_seed_shows_every_vendor_signal_the_research_names():
    seeded = seed_result()
    kinds = {
        record["data"]["signal"]["kind"]
        for record in seeded["throttle_batch"]
        if record["data"].get("signal")
    }
    assert kinds == {"rate_limit", "lock"}


def test_the_seed_includes_a_lock_held_at_the_two_second_floor():
    seeded = seed_result()
    floors = [
        record["data"]["schedule"]["seconds"]
        for record in seeded["throttle_batch"]
        if (record["data"].get("signal") or {}).get("kind") == "lock"
    ]
    assert floors == [2], "the researched HubSpot lock floor is a row, not a claim"


def test_the_seed_shows_a_batch_that_is_waiting_for_a_person():
    """The attempt bound, reached: the automatic queue has stopped and somebody
    has to decide. No amount of green would teach that."""
    seeded = seed_result()
    assert "waiting for a person" in seeded["summary"]
    assert "needs_action" in {record["data"]["state"] for record in seeded["throttle_batch"]}


def test_the_seed_shows_a_connection_paused_by_hand():
    """Step 5's control is a row a reviewer can see, not a control nothing in the
    demo has ever pressed."""
    seeded = seed_result()
    paused = [
        record["data"]["label"]
        for record in seeded["throttle_connection"]
        if record["data"].get("paused")
    ]
    assert len(paused) == 1
    assert "Dataverse" in paused[0]


def test_the_seed_covers_the_three_vendors_and_their_split():
    seeded = seed_result()
    vendors = {record["data"]["vendor"]: record["data"] for record in seeded["throttle_connection"]}
    assert set(vendors) == {"hubspot", "salesforce", "dataverse"}
    assert vendors["hubspot"]["preemptive"] is True, "the one vendor with a sourced burst"
    assert vendors["salesforce"]["preemptive"] is False, (
        "the limit name is sourced, the number is not"
    )
    assert vendors["dataverse"]["gaps"]


def test_the_seed_writes_only_audited_rows():
    import random
    from datetime import datetime, timezone

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create(
            "room", {"name": "Northwind", "account": "Northwind Traders"}, source="test"
        )
        before = db.audit_count()
        load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "Northwind Traders")],
                "now": datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        assert db.audit_count() > before
        collections = {
            "throttle_connection",
            "throttle_bucket",
            "throttle_batch",
            "throttle_log",
            "throttle_meter",
        }
        entries = [entry for entry in db.audit(limit=1000) if entry["collection"] in collections]
        assert entries
        assert all(entry["source"] == "seed" for entry in entries)
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_says_so_when_there_are_no_rooms():
    import random
    from datetime import datetime, timezone

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


# --------------------------------------------------------------------------- #
# The contract's own invariants for this feature
# --------------------------------------------------------------------------- #


def test_the_feature_module_does_not_import_the_app():
    """Hard rule 1 of the build brief: a feature takes its dependencies from
    ``dsr.deps`` and never from ``dsr.api``."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "import sqlite3" not in source


def test_no_migration_and_no_typed_column_were_added_for_a_throttle_field():
    """Hard rule 2: records are ordinary JSON in ``records.data``. The throttle's
    own columns are the envelope plus the collections this feature names, and the
    schema file is untouched by the branch."""
    from dsr.throttle import vocabulary

    assert set(vocabulary.COLLECTIONS.values()) == {
        "throttle_connection",
        "throttle_bucket",
        "throttle_batch",
        "throttle_log",
        "throttle_meter",
    }


def test_the_router_prefix_is_ticket_derived_and_unique(client):
    module = load_feature(MODULE)
    assert module.router.prefix == PREFIX
    prefixes = [
        row["prefix"] for row in client.get("/api/features").json()["features"] if row.get("prefix")
    ]
    assert prefixes.count(PREFIX) == 1


def test_the_frontend_folder_exists_with_the_descriptor_the_contract_names():
    folder = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-046-throttle-and-retry-under-vendor-api-r"
    )
    assert folder.is_dir()
    descriptor = (folder / "index.jsx").read_text(encoding="utf-8")
    assert f"id: '{FEATURE_ID}'" in descriptor
    assert "Component:" in descriptor
    assert (folder / "api.js").is_file()
    assert (folder / "primitives.jsx").is_file()
