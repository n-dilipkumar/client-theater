"""The HTTP surface, the audit-source rule, and the demo data for WF-066.

Split from ``test_wf066.py``, which holds the researched rules against the domain.
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
  green teaches a reviewer nothing, and the build brief says so: "Seed the
  interesting states, not just the happy path."

Two rules about this file make it safe under pytest-xdist:

* There is deliberately **no local ``client`` fixture**. ``conftest.py`` already
  provides one: a module-scoped lifespan with a per-test store swapped onto
  ``app.state``. A local fixture of the same name shadows the shared one, and the
  first draft of its sibling did exactly that.
* Every test builds its **own room**, so no test reads or writes another test's
  rows and the file passes in any order on any worker.

**No test opens a socket.** The routes build their own fanout through
``get_fanout``, so every test that emits an event installs a dependency override
returning a :class:`FakeTransport`. A test that forgot would try to reach
``hooks.example``, and this host has no route to it, so the suite would hang
rather than fail.
"""

from __future__ import annotations

import json
import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.features import load_feature
from dsr.meeting_webhook_fanout import MeetingWebhookFanout
from dsr.meeting_webhook_fanout.transport import FakeTransport
from dsr.store import RecordStore

PREFIX = "/api/wf-066"
MODULE = "wf066_fan_out_meeting_events_via_signed_webhooks"
FEATURE_ID = "wf-066-fan-out-meeting-events-via-signed-webhooks"
FRONTEND_ID = "wf-066-fan-out-meeting-events-via-signed-webhooks"

URL = "https://hooks.northwind.example/meetings/created"
URL2 = "https://warehouse.northwind.example/meetings"
MEETING = {
    "meetingIdChili": "m-0001",
    "title": "Northwind Traders - enterprise evaluation",
    "description": "Walk through the evaluation with the buying committee.",
    "location": "Microsoft Teams",
    "start": "2026-10-08T14:00:00+00:00",
    "end": "2026-10-08T14:45:00+00:00",
    "primaryGuestTimeZone": "Europe/London",
    "primaryGuestName": "Priya Raman",
    "primaryGuestEmail": "priya.raman@northwind.example",
    "hostIdChili": "h-0001",
    "hostName": "Dana Okafor",
    "workspaceId": "ws-0001",
    "workspaceName": "Northwind Traders",
    "productFeatureType": "ConciergeRouter",
    "meetingTypeName": "Demo",
    "meetingTypeId": "mt-0001",
}


# --------------------------------------------------------------------------- #
# Fixtures. The transport is the one thing every emit needs, and it must never be
# the real one.
# --------------------------------------------------------------------------- #


@pytest.fixture
def fanout_transport() -> FakeTransport:
    """The transport every route in this file gets instead of a real socket."""
    return FakeTransport(status=202, body="accepted")


@pytest.fixture
def client_with_transport(client, fanout_transport):
    """A ``client`` whose meeting-webhook routes fan out through the fake.

    ``get_fanout`` builds ``UrllibTransport`` by default, so without this
    override any emit in this file would attempt a real DNS lookup and fail, or
    hang. The override is function-scoped and ``conftest.py`` clears
    ``dependency_overrides`` before every test, so nothing leaks to another file.

    The replacement declares ``store`` with the same ``StoreDep`` default the real
    dependency has. An override whose own signature drops the ``Depends`` makes
    FastAPI read ``store`` as a *query parameter* of type ``RecordStore``, which is
    not a Pydantic type, and every route in this feature then raises a
    ``FastAPIError`` before its handler runs. That is the whole failure: an
    override has to replace the dependency, not re-declare it loosely.
    """

    def fanout_from_store(store: RecordStore = StoreDep) -> MeetingWebhookFanout:
        return MeetingWebhookFanout(store, fanout_transport)

    client.app.dependency_overrides[load_feature(MODULE).get_fanout] = fanout_from_store
    return client


@pytest.fixture
def room(client_with_transport) -> dict:
    """This test's own room, so no test sees another test's rows."""
    return client_with_transport.post(
        "/api/records/room", json={"name": "Northwind", "account": "Northwind Traders"}
    ).json()


def subscribe(client, room_id, url=URL, event_type="new_meeting", **extra):
    body = {"url": url, "event_type": event_type, **extra}
    response = client.post(f"{PREFIX}/rooms/{room_id}/subscriptions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def enable(client, room_id, subscription_id):
    response = client.patch(
        f"{PREFIX}/rooms/{room_id}/subscriptions/{subscription_id}",
        json={"status": "enabled"},
        params={"actor": "dana"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def emit(client, room_id, event_type="new_meeting", meeting=None):
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/events",
        json={"event_type": event_type, "meeting": meeting or MEETING},
        params={"actor": "dana"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_three_types_and_their_payload_values(
    client_with_transport,
):
    body = client_with_transport.get(f"{PREFIX}/vocabulary").json()
    assert [row["id"] for row in body["event_types"]] == [
        "new_meeting",
        "meeting_update",
        "canceled_meeting",
    ]
    assert [row["payload_type"] for row in body["event_types"]] == [
        "Created",
        "Updated",
        "Deleted",
    ]
    assert body["payload_types"] == ["Created", "Updated", "Deleted"]


def test_the_vocabulary_publishes_the_headers_and_the_signing_rule(client_with_transport):
    body = client_with_transport.get(f"{PREFIX}/vocabulary").json()
    assert body["headers"] == {
        "signature": "X-Chili-Signature",
        "timestamp": "X-Chili-Timestamp",
        "signature_encoding": "hex",
        "timestamp_unit": "unix seconds",
    }
    assert body["signing_rule"] == "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded"


def test_the_vocabulary_serves_the_replay_window_and_who_owns_it(client_with_transport):
    body = client_with_transport.get(f"{PREFIX}/vocabulary").json()
    assert body["replay_window_seconds"] == 300
    assert body["replay_window_owner"] == "the consumer"


def test_the_vocabulary_serves_both_deployment_modes(client_with_transport):
    modes = {
        row["id"]: row
        for row in client_with_transport.get(f"{PREFIX}/vocabulary").json()["deployment_modes"]
    }
    assert modes["saas"]["schemes"] == ["https"]
    assert modes["saas"]["private_addresses"] == "refused"
    assert modes["self_hosted"]["schemes"] == ["http", "https"]


def test_the_inferences_are_served_as_data_with_a_change_it_for_each(client_with_transport):
    body = client_with_transport.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    ids = {entry["id"] for entry in body["inferences"]}
    assert "one-flat-envelope-for-the-three-chili-types" in ids
    assert "replay-protection-belongs-to-the-consumer" in ids
    assert "subscription-identity-is-url-plus-event-type" in ids
    assert "not-built" in ids
    for entry in body["inferences"]:
        assert entry["change_it"].strip(), entry["id"]
        assert entry["basis"].strip(), entry["id"]


def test_the_inference_register_records_the_jev_decision_that_chose_the_envelope(
    client_with_transport,
):
    body = client_with_transport.get(f"{PREFIX}/inferences").json()
    entry = next(
        row
        for row in body["inferences"]
        if row["id"] == "one-flat-envelope-for-the-three-chili-types"
    )
    assert entry["value"]["payload_wrapper"] == "never sent"
    assert "jev-20261004T070110-25984-70596" in entry["basis"]


# --------------------------------------------------------------------------- #
# Steps 1 and 2: the Webhooks table
# --------------------------------------------------------------------------- #


def test_creating_a_subscription_lands_it_disabled(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"])
    assert row["status"] == "disabled"
    assert row["event_type"] == "new_meeting"
    assert row["payload_type"] == "Created"


def test_a_body_asking_for_enabled_is_honoured_at_creation(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"], status="enabled")
    assert row["status"] == "enabled"


def test_an_unknown_event_type_is_400_over_http(client_with_transport, room):
    """400, not 422. The request was well formed and the *value* was outside the
    closed set the research names, so the error says the value rather than the
    payload's shape. 422 would send a reader looking at their JSON."""
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/subscriptions",
        json={"url": URL, "event_type": "meeting_started"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_event_type"
    assert "three" in response.json()["detail"]


def test_a_subscriber_url_with_no_scheme_is_400_over_http(client_with_transport, room):
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/subscriptions",
        json={"url": "hooks.example/hook", "event_type": "new_meeting"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "subscriber_url_rejected"


def test_a_second_subscription_for_the_same_pair_is_409(client_with_transport, room):
    subscribe(client_with_transport, room["id"])
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/subscriptions",
        json={"url": URL, "event_type": "new_meeting"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "subscription_already_exists"
    assert "twice to the same address" in response.json()["detail"]


def test_one_url_may_serve_several_types_and_one_type_several_urls(client_with_transport, room):
    for event_type in ("new_meeting", "meeting_update", "canceled_meeting"):
        subscribe(client_with_transport, room["id"], url=URL, event_type=event_type)
    subscribe(client_with_transport, room["id"], url=URL2, event_type="new_meeting")
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/subscriptions").json()
    assert body["count"] == 4
    assert body["by_event_type"]["new_meeting"] == 2
    assert body["by_event_type"]["canceled_meeting"] == 1


def test_a_room_that_does_not_exist_is_404_on_every_route(client_with_transport):
    for path in (
        "/vocabulary",
        "/inferences",
    ):
        assert client_with_transport.get(f"{PREFIX}{path}").status_code == 200
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/summary").status_code == 404
    assert (
        client_with_transport.post(
            f"{PREFIX}/rooms/room_absent/subscriptions",
            json={"url": URL, "event_type": "new_meeting"},
        ).status_code
        == 404
    )
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/subscriptions").status_code == 404
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/events").status_code == 404
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/deliveries").status_code == 404
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/secret").status_code == 404
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/sample").status_code == 404


def test_the_table_reports_no_cap_and_says_why(client_with_transport, room):
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/subscriptions").json()
    assert body["subscription_limit"] is None
    assert "not limited by the number" in body["subscription_limit_note"]


def test_the_status_toggle_is_one_patch_and_one_row(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"])
    enabled = enable(client_with_transport, room["id"], row["id"])
    assert enabled["id"] == row["id"]
    assert enabled["status"] == "enabled"
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/subscriptions").json()
    assert body["count"] == 1
    assert body["enabled"] == 1


def test_a_patch_that_would_break_a_rule_is_refused_over_http(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"])
    response = client_with_transport.patch(
        f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}",
        json={"url": "not-a-url"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "subscriber_url_rejected"
    assert (
        client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}").json()[
            "url"
        ]
        == URL
    ), "a refused amendment leaves the row as it was"


def test_reading_an_unknown_subscription_is_404(client_with_transport, room):
    response = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/subscriptions/meeting_webhook_subscription_absent"
    )
    assert response.status_code == 404
    assert response.json()["error"] == "subscription_not_found"


def test_the_per_row_delete_retires_one_row_and_the_table_shows_it(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"])
    kept = subscribe(client_with_transport, room["id"], url=URL2, event_type="meeting_update")
    deleted = client_with_transport.delete(f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["retired"] is True
    body = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/subscriptions", params={"include_retired": True}
    ).json()
    assert body["count"] == 1
    assert body["retired"] == 1
    assert kept["id"] in {r["id"] for r in body["subscriptions"]}


def test_a_retired_subscription_still_resolves_for_the_delivery_log(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"])
    enable(client_with_transport, room["id"], row["id"])
    emit(client_with_transport, room["id"])
    client_with_transport.delete(f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}")
    read = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}")
    assert read.status_code == 200
    assert read.json()["retired"] is True


def test_another_rooms_subscription_is_404(client_with_transport, room):
    other = client_with_transport.post(
        "/api/records/room", json={"name": "Theirs", "account": "Theirs"}
    ).json()
    row = subscribe(client_with_transport, other["id"])
    response = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Step 3: the signing secret
# --------------------------------------------------------------------------- #


def test_the_secret_route_says_where_the_secret_comes_from(client_with_transport, room):
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/secret").json()
    assert body["secret"] is None
    assert "not shown in the UI" in body["source_of_truth"]


def test_the_signing_key_route_sets_the_secret_and_counts_the_rotation(client_with_transport, room):
    first = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/signing-key", json={"secret": "tenant-secret"}
    )
    assert first.status_code == 200
    assert first.json()["secret"] == "tenant-secret"
    assert first.json()["origin"] == "supplied by the tenant"
    second = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/signing-key", json={"secret": "rotated-secret"}
    )
    assert second.json()["rotations"] == 2
    assert (
        client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/secret").json()["secret"]
        == "rotated-secret"
    )


def test_an_empty_secret_is_refused_over_http(client_with_transport, room):
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/signing-key", json={"secret": "  "}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "signing_secret_required"


# --------------------------------------------------------------------------- #
# The data flow over HTTP
# --------------------------------------------------------------------------- #


def test_the_event_route_signs_and_fans_out(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"], status="enabled")
    report = emit(client_with_transport, room["id"])
    assert report["delivered"] == 1
    assert report["failed"] == 0
    assert report["event"]["payload_type"] == "Created"
    assert report["event"]["signature"]
    assert report["event"]["timestamp"]
    assert row["id"] == report["deliveries"][0]["subscription_id"]


def test_a_cancellation_fires_deleted_over_http(client_with_transport, room):
    subscribe(client_with_transport, room["id"], event_type="canceled_meeting", status="enabled")
    report = emit(client_with_transport, room["id"], event_type="canceled_meeting")
    assert report["event"]["payload_type"] == "Deleted"
    assert json.loads(report["event"]["raw_body"])["type"] == "Deleted"


def test_the_payload_is_flat_with_no_wrapper_over_http(client_with_transport, room):
    subscribe(client_with_transport, room["id"], status="enabled")
    report = emit(client_with_transport, room["id"])
    payload = json.loads(report["event"]["raw_body"])
    assert "payload" not in payload
    assert payload["type"] == "Created"
    assert payload["meetingIdChili"] == "m-0001"


def test_an_event_with_no_meeting_is_422_over_http(client_with_transport, room):
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/events", json={"event_type": "new_meeting"}
    )
    assert response.status_code == 422
    assert "meeting" in response.json()["detail"]


def test_an_event_with_an_unknown_type_is_400_over_http(client_with_transport, room):
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/events",
        json={"event_type": "meeting_started", "meeting": MEETING},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_event_type"


def test_an_event_at_a_disabled_row_reaches_nobody_and_says_why(client_with_transport, room):
    subscribe(client_with_transport, room["id"])
    report = emit(client_with_transport, room["id"])
    assert report["delivered"] == 0
    assert report["skipped"] == 1
    assert report["reason"] == "no_enabled_subscriptions"


def test_the_transport_the_route_used_is_the_one_the_test_supplied(
    client_with_transport, fanout_transport, room
):
    """The guard against this file ever reaching the network."""
    subscribe(client_with_transport, room["id"], status="enabled")
    emit(client_with_transport, room["id"])
    assert fanout_transport.sent, "the fake transport saw nothing, so a real one would have run"
    sent = fanout_transport.sent[0]
    assert sent["url"] == URL
    assert sent["headers"]["X-Chili-Signature"]
    assert sent["headers"]["X-Chili-Timestamp"]


def test_one_event_reaches_every_enabled_subscriber_for_its_type(client_with_transport, room):
    for url in (URL, URL2):
        row = subscribe(client_with_transport, room["id"], url=url)
        enable(client_with_transport, room["id"], row["id"])
    report = emit(client_with_transport, room["id"])
    assert report["delivered"] == 2
    assert {row["url"] for row in report["deliveries"]} == {URL, URL2}


def test_the_event_log_serves_the_events_and_the_counts(client_with_transport, room):
    subscribe(client_with_transport, room["id"], status="enabled")
    emit(client_with_transport, room["id"])
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/events").json()
    assert body["count"] == 1
    assert body["by_event_type"] == {"new_meeting": 1}
    assert "raw_body" not in body["events"][0], "a list row must not carry the body"


def test_the_event_log_can_be_narrowed_to_one_type(client_with_transport, room):
    for event_type in ("new_meeting", "meeting_update"):
        row = subscribe(client_with_transport, room["id"], event_type=event_type, status="enabled")
        assert row["id"]
    emit(client_with_transport, room["id"], event_type="new_meeting")
    emit(client_with_transport, room["id"], event_type="meeting_update")
    body = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/events", params={"event_type": "new_meeting"}
    ).json()
    assert body["count"] == 1
    assert body["events"][0]["event_type"] == "new_meeting"


def test_reading_one_event_serves_the_exact_bytes_and_the_signing_input(
    client_with_transport, room
):
    subscribe(client_with_transport, room["id"], status="enabled")
    report = emit(client_with_transport, room["id"])
    body = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/events/{report['event']['id']}"
    ).json()
    assert body["raw_body"]
    assert body["signing_input"] == f"{body['timestamp']}.{body['raw_body']}"
    assert body["signature"]


def test_reading_an_unknown_event_is_404(client_with_transport, room):
    response = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/events/meeting_webhook_event_absent"
    )
    assert response.status_code == 404
    assert response.json()["error"] == "event_not_found"


def test_the_delivery_log_serves_one_row_per_attempt(client_with_transport, room):
    for url in (URL, URL2):
        row = subscribe(client_with_transport, room["id"], url=url, status="enabled")
        assert row["id"]
    emit(client_with_transport, room["id"])
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/deliveries").json()
    assert body["count"] == 2
    assert body["by_outcome"] == {"delivered": 2}
    assert {row["outcome"] for row in body["deliveries"]} == {"delivered"}


def test_the_delivery_log_can_be_narrowed_to_one_outcome(client_with_transport, room):
    subscribe(client_with_transport, room["id"], status="enabled")
    emit(client_with_transport, room["id"])
    body = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/deliveries", params={"outcome": "failed"}
    ).json()
    assert body["count"] == 0
    assert body["deliveries"] == []


def test_a_skipped_delivery_can_be_left_out_of_the_log(client_with_transport, room):
    subscribe(client_with_transport, room["id"])
    emit(client_with_transport, room["id"])
    with_skips = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/deliveries").json()
    without = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/deliveries", params={"include_skipped": False}
    ).json()
    assert with_skips["by_outcome"] == {"skipped": 1}
    assert without["count"] == 0


def test_reading_one_delivery_serves_the_response_that_came_back(client_with_transport, room):
    subscribe(client_with_transport, room["id"], status="enabled")
    report = emit(client_with_transport, room["id"])
    delivery_id = report["deliveries"][0]["id"]
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/deliveries/{delivery_id}").json()
    assert body["status"] == 202
    assert "accepted" in body["response_excerpt"]
    assert body["retryable"] is False
    assert "no retry ladder" in body["retryable_note"]


def test_a_redelivery_route_resends_and_records_a_second_attempt(client_with_transport, room):
    subscribe(client_with_transport, room["id"], status="enabled")
    report = emit(client_with_transport, room["id"])
    again = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/events/{report['event']['id']}/redeliver",
        params={"actor": "dana"},
    )
    assert again.status_code == 200
    assert again.json()["delivered"] == 1
    rows = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/deliveries").json()["deliveries"]
    assert len(rows) == 2
    assert {row["event_id"] for row in rows} == {report["event"]["id"]}


def test_redelivering_an_unknown_event_is_404(client_with_transport, room):
    response = client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/events/meeting_webhook_event_absent/redeliver"
    )
    assert response.status_code == 404


def test_the_sample_route_serves_the_bytes_the_input_and_the_snippet(client_with_transport, room):
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/sample").json()
    assert body["signing_input"] == f"{body['timestamp']}.{body['raw_body']}"
    assert body["replay_window_seconds"] == 300
    assert "compare_digest" in body["verify_snippet"]
    assert "payload" not in json.loads(body["raw_body"])


def test_the_summary_counts_this_rooms_own_rows(client_with_transport, room):
    subscribe(client_with_transport, room["id"], status="enabled")
    subscribe(client_with_transport, room["id"], url=URL2, event_type="meeting_update")
    emit(client_with_transport, room["id"])
    body = client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["subscriptions"] == 2
    assert body["enabled"] == 1
    assert body["events"] == 1
    assert body["deliveries"] == 1
    assert body["has_secret"] is True, "emitting minted a secret for a room that had none"


def test_the_summary_names_the_three_constraints_a_reader_looks_for(client_with_transport, room):
    notes = " ".join(
        client_with_transport.get(f"{PREFIX}/rooms/{room['id']}/summary").json()["notes"]
    )
    assert "not limited by the number" in notes
    assert "Replay protection belongs to the consumer" in notes
    assert "no retry ladder" in notes


def test_the_summary_of_a_room_that_does_not_exist_is_404(client_with_transport):
    assert client_with_transport.get(f"{PREFIX}/rooms/room_absent/summary").status_code == 404


def test_reading_an_unknown_delivery_is_404_and_not_a_500(client_with_transport, room):
    """The regression that ``verify_all_routes.py`` found.

    ``tools/verify_all_routes.py`` calls every advertised route with a placeholder
    for every path parameter, so this route is reached with an id that does not
    exist - and it answered 500. The cause was ``raise MeetingWebhookError(msg,
    code=...)``: the base inherits ``ValueError.__init__``, which takes no ``code``
    keyword, so the call raised ``TypeError`` and the handler never saw a domain
    error. Every unknown-id route has to answer 404, because a 500 here means a
    route the host mounted has no working handler behind it.
    """
    response = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/deliveries/meeting_webhook_delivery_absent"
    )
    assert response.status_code == 404
    assert response.json()["error"] == "delivery_not_found"


def test_every_unknown_id_route_is_404_and_never_a_500(client_with_transport, room):
    """The whole class of defect, not just the one route the sweep happened to hit.

    Each collection with an id parameter gets a placeholder that does not exist. A
    404 is the correct answer and a 405 says the method does not exist on that path.
    Anything in the 5xx range is the fault this guards.
    """
    absent = {
        "subscriptions": "meeting_webhook_subscription_absent",
        "events": "meeting_webhook_event_absent",
        "deliveries": "meeting_webhook_delivery_absent",
    }
    for collection, record_id in absent.items():
        path = f"{PREFIX}/rooms/{room['id']}/{collection}/{record_id}"
        # GET takes no body; the writing methods take one so FastAPI does not
        # reject the request on the body alone before the handler runs.
        for response in (
            client_with_transport.get(path),
            client_with_transport.patch(path, json={}),
            client_with_transport.delete(path),
        ):
            assert response.status_code < 500, f"{path} answered {response.status_code}"
            assert response.status_code in (404, 405), (
                f"{path} answered {response.status_code}; "
                "404 is the answer and 405 says the method does not exist here"
            )


def test_a_delivery_error_carries_a_status_a_handler_can_read():
    """A domain error's status and code must be class attributes.

    Asserted on the type rather than at a raise site, because the defect was a
    ``code=`` keyword on a constructor that does not accept one: nothing about the
    call site looked wrong, and only the status code it produced gave it away.
    """
    from dsr.meeting_webhook_fanout import UnknownDelivery
    from dsr.meeting_webhook_fanout.errors import MeetingWebhookError

    assert issubclass(UnknownDelivery, MeetingWebhookError)
    error = UnknownDelivery("no such delivery")
    assert error.status == 404
    assert error.code == "delivery_not_found"
    assert str(error) == "no such delivery"


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def audit_entries(client, **params) -> list[dict]:
    return client.get("/api/audit", params={"limit": 1000, **params}).json()["entries"]


def my_sources(client) -> set[str]:
    return {
        entry["source"] for entry in audit_entries(client) if PREFIX in (entry.get("source") or "")
    }


def test_every_write_names_the_route_that_served_it(client_with_transport, room):
    """One row per writing route, and no others.

    The recorded source carries the *template* - ``{room_id}``, not the resolved
    id - because that is what a reader needs: the shape of the path is the same for
    every room, so the audit log stays comparable across rooms. The sibling test
    below checks the opposite direction, that every recorded source is callable
    with real values substituted.
    """
    row = subscribe(client_with_transport, room["id"])
    enable(client_with_transport, room["id"], row["id"])
    client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/signing-key",
        json={"secret": "s"},
        params={"actor": "dana"},
    )
    report = emit(client_with_transport, room["id"])
    client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/events/{report['event']['id']}/redeliver",
        params={"actor": "dana"},
    )
    gone = subscribe(client_with_transport, room["id"], url=URL2, event_type="meeting_update")
    client_with_transport.delete(
        f"{PREFIX}/rooms/{room['id']}/subscriptions/{gone['id']}", params={"actor": "dana"}
    )

    assert my_sources(client_with_transport) == {
        f"POST {PREFIX}/rooms/{{room_id}}/subscriptions",
        f"PATCH {PREFIX}/rooms/{{room_id}}/subscriptions/{{subscription_id}}",
        f"DELETE {PREFIX}/rooms/{{room_id}}/subscriptions/{{subscription_id}}",
        f"POST {PREFIX}/rooms/{{room_id}}/signing-key",
        f"POST {PREFIX}/rooms/{{room_id}}/events",
        f"POST {PREFIX}/rooms/{{room_id}}/events/{{event_id}}/redeliver",
    }


def test_two_rooms_record_the_same_source_shape(client_with_transport):
    """The template form is what makes the log comparable across rooms.

    If one room's rows recorded resolved ids and another's recorded templates, a
    reader grouping the log by source would get one group per room and could not
    see that both rooms wrote through the same route.
    """
    second = client_with_transport.post(
        "/api/records/room", json={"name": "Contoso", "account": "Contoso"}
    ).json()
    first_room = client_with_transport.post(
        "/api/records/room", json={"name": "Northwind", "account": "Northwind"}
    ).json()
    subscribe(client_with_transport, first_room["id"])
    subscribe(client_with_transport, second["id"], url=URL2)
    shapes = {
        entry["source"]
        for entry in audit_entries(client_with_transport)
        if entry["collection"] == "meeting_webhook_subscription"
    }
    assert shapes == {f"POST {PREFIX}/rooms/{{room_id}}/subscriptions"}


def test_a_write_records_the_actor_it_was_given(client_with_transport, room):
    client_with_transport.post(
        f"{PREFIX}/rooms/{room['id']}/subscriptions",
        json={"url": URL, "event_type": "new_meeting"},
        params={"actor": "dana"},
    )
    entry = audit_entries(client_with_transport, collection="meeting_webhook_subscription")[0]
    assert entry["actor"] == "dana"


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
            # parameter names arrive doubled - ``{{room_id}}`` - and have to be
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
    ``/api/wf-066`` would fail on the router's own ``prefix=`` argument."""
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


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client_with_transport, room):
    """The invariant, product-wide: an audit ``source`` is always callable.

    Drives writes through this feature *and* through the core record routes, then
    checks every row the log holds.
    """
    row = subscribe(client_with_transport, room["id"], status="enabled")
    assert row["id"]
    emit(client_with_transport, room["id"])
    client_with_transport.post("/api/records/room", json={"name": "Core", "account": "Core"})
    client_with_transport.post("/api/records/bulk/x", json=[{"k": "v"}])

    mounted = mounted_routes(client_with_transport)
    entries = audit_entries(client_with_transport)
    assert entries
    for entry in entries:
        method, _, path = str(entry["source"]).partition(" ")
        assert serves(mounted, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


def test_the_feature_module_is_listed_by_discovery_with_its_prefix(client_with_transport):
    registry = client_with_transport.get("/api/features").json()
    mine = next(row for row in registry["features"] if row["id"] == FEATURE_ID)
    assert mine["prefix"] == PREFIX
    assert mine["ticket"] == "WF-066"
    assert mine["routes"]
    assert not [row for row in registry.get("failed", []) if FEATURE_ID in str(row)]


def test_the_router_prefix_is_ticket_derived_and_unique(client_with_transport):
    module = load_feature(MODULE)
    assert module.router.prefix == PREFIX
    prefixes = [
        row["prefix"]
        for row in client_with_transport.get("/api/features").json()["features"]
        if row.get("prefix")
    ]
    assert prefixes.count(PREFIX) == 1


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


def test_no_migration_and_no_typed_column_were_added_for_a_webhook_field():
    """Hard rule 2: records are ordinary JSON in ``records.data``. The collections
    this feature names are the only schema it owns, and the schema file is
    untouched by the branch."""
    from dsr.meeting_webhook_fanout import COLLECTIONS

    assert set(COLLECTIONS.values()) == {
        "meeting_webhook_subscription",
        "meeting_webhook_event",
        "meeting_webhook_delivery",
        "meeting_webhook_key",
    }


def test_a_field_a_team_invents_is_stored_with_no_coordination(client_with_transport, room):
    row = subscribe(client_with_transport, room["id"], our_own_field="kept")
    assert row["our_own_field"] == "kept"
    read = client_with_transport.get(
        f"{PREFIX}/rooms/{room['id']}/subscriptions/{row['id']}"
    ).json()
    assert read["our_own_field"] == "kept"


def test_the_frontend_folder_exists_with_the_descriptor_the_contract_names():
    folder = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-066-fan-out-meeting-events-via-signed-webhooks"
    )
    assert folder.is_dir(), "the feature's frontend folder is missing"
    descriptor = (folder / "index.jsx").read_text(encoding="utf-8")
    assert f"id: '{FRONTEND_ID}'" in descriptor
    assert "Component:" in descriptor
    assert (folder / "api.js").is_file()
    assert (folder / "primitives.jsx").is_file()


def test_the_frontend_uses_no_emoji_and_takes_its_glyph_from_a_path():
    folder = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-066-fan-out-meeting-events-via-signed-webhooks"
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.js*")))
    # Emoji as an icon is banned by the design system, and the codepoint ranges
    # below are the ones that carry one. Checked on the source rather than the
    # render because a rendered check needs a browser this session has not got.
    emoji = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]")
    assert not emoji.search(source), "an emoji is being used as an icon"
    assert "iconPath" in (folder / "index.jsx").read_text(encoding="utf-8")
    assert "min-h-11" in source, "a control is under the 44px touch target floor"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_result():
    """Run the seed over a throwaway database and return plain data, not a store.

    The database is closed in the ``finally`` before this returns, so the caller
    gets dictionaries rather than a ``RecordStore``. Returning the store handed
    back an object bound to a closed connection, and every assertion that read it
    afterwards failed with "Cannot operate on a closed database" - a failure in the
    helper, not in the seed. The rows are copied out while the connection is still
    open.
    """
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
                "now": datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        store = RecordStore(db)
        collections = {
            name: [dict(record) for record in store.list(name, limit=500)]
            for name in (
                "meeting_webhook_subscription",
                "meeting_webhook_event",
                "meeting_webhook_delivery",
                "meeting_webhook_key",
            )
        }
        return {"summary": summary, "room_id": room["id"], **collections}
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_produces_subscriptions_events_and_deliveries():
    seeded = seed_result()
    assert seeded["meeting_webhook_subscription"], "no subscription rows"
    assert seeded["meeting_webhook_event"], "no event rows"
    assert seeded["meeting_webhook_delivery"], "no delivery rows"
    assert seeded["meeting_webhook_key"], "no signing key row"
    assert isinstance(seeded["summary"], str)


def test_the_seed_prints_a_state_that_is_not_a_success():
    """The build brief says so explicitly: "Seed the interesting states, not just
    the happy path - the features already on main seed a declined room ... a
    retried delivery and a failed one, because demo data containing only success
    teaches a reviewer nothing."

    Two of them are here: a delivery that failed to reach its subscriber, and an
    event at a room whose subscriptions were never enabled.
    """
    summary = seed_result()["summary"]
    assert "0 failed" not in summary
    assert "failed to reach their subscriber" in summary
    assert "no_enabled_subscriptions" in summary


def test_every_character_of_the_seed_string_survives_a_windows_console():
    """A single U+2192 RIGHTWARDS ARROW in one recovered feature broke the entire
    seeder on a Windows console, so the string is encoded as cp1252 rather than
    merely asserted to be a string."""
    seed_result()["summary"].encode("cp1252")


def test_the_seed_shows_all_three_event_types_firing():
    """All three researched types, so a reviewer sees every payload value the
    vocabulary claims exists rather than one of the three."""
    seeded = seed_result()
    payload_types = {record["data"]["payload_type"] for record in seeded["meeting_webhook_event"]}
    assert payload_types == {"Created", "Updated", "Deleted"}
    event_types = {record["data"]["event_type"] for record in seeded["meeting_webhook_event"]}
    assert event_types == {"new_meeting", "meeting_update", "canceled_meeting"}


def test_the_seed_shows_one_url_carrying_several_event_types():
    """Quoted: "multiple webhook types [may] have the same webhook URL"."""
    seeded = seed_result()
    by_url: dict[str, set[str]] = {}
    for record in seeded["meeting_webhook_subscription"]:
        if record.get("deleted_at"):
            continue
        by_url.setdefault(record["data"]["url"], set()).add(record["data"]["event_type"])
    assert any(len(types) > 1 for types in by_url.values()), by_url


def test_the_seed_shows_one_type_with_several_urls():
    """Quoted: "multiple webhook URLs for the same type"."""
    seeded = seed_result()
    by_type: dict[str, set[str]] = {}
    for record in seeded["meeting_webhook_subscription"]:
        if record.get("deleted_at"):
            continue
        by_type.setdefault(record["data"]["event_type"], set()).add(record["data"]["url"])
    assert any(len(urls) > 1 for urls in by_type.values()), by_type


def test_the_seed_shows_a_retired_subscription():
    """The per-row delete is a state a reviewer can see, not a control nothing in
    the demo has ever pressed.

    Read with ``include_deleted`` because ``store.list`` hides soft-deleted rows by
    default - which is exactly why a retired row would otherwise be invisible here.
    """
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create("room", {"name": "Northwind", "account": "NW"}, source="test")
        load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "NW")],
                "now": datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        rows = RecordStore(db).list("meeting_webhook_subscription", limit=500, include_deleted=True)
        retired = [row for row in rows if row.get("deleted_at")]
        assert retired, "the seed must press the per-row delete at least once"
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_shows_a_redelivery_of_the_same_event():
    """One event, two attempts. The research names no retry ladder, so a second
    attempt is a person pressing a button - and the log has to show it."""
    seeded = seed_result()
    counts: dict[str, int] = {}
    for record in seeded["meeting_webhook_delivery"]:
        event_id = record["data"].get("event_id")
        if event_id:
            counts[event_id] = counts.get(event_id, 0) + 1
    assert any(count > 1 for count in counts.values()), counts


def test_the_seed_signs_every_event_it_writes():
    seeded = seed_result()
    for record in seeded["meeting_webhook_event"]:
        data = record["data"]
        assert data["signature"], data["id"]
        assert data["signing_input"] == f"{data['timestamp']}.{data['raw_body']}"
        assert data["raw_body"] == json.dumps(
            data["payload"], ensure_ascii=False, sort_keys=True, default=str
        )


def _same_shape(route_path: str, recorded: str) -> bool:
    """Whether a recorded path has the same segments as a mounted route path.

    The seed records ``{room_id}``, ``{subscription_id}`` and ``{event_id}`` where
    it knows the values, so a literal comparison misses. This compares the segment
    count and the segments that are not placeholders, which is what decides
    whether the recorded path names a route this feature serves.
    """
    expected = route_path.strip("/").split("/")
    actual = recorded.strip("/").split("/")
    if len(expected) != len(actual):
        return False
    return all(
        want.startswith("{") or want == got for want, got in zip(expected, actual, strict=True)
    )


def test_the_seed_writes_only_audited_rows():
    """Every row the seed writes is audited, and each names a route this feature
    actually serves.

    Not ``source == "seed"``: this seed runs the real engine, and the engine takes
    the route it stands in for. A route the app had stopped serving would then be
    an audit row naming something uncallable, which is the defect the audit-source
    rule exists to prevent and the thing a reviewer would least expect from a demo
    seeder.
    """
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
                "now": datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random(1),
            },
        )
        assert db.audit_count() > before
        entries = [
            entry
            for entry in db.audit(limit=2000)
            if entry["collection"].startswith("meeting_webhook_")
        ]
        assert entries
        write_routes = {
            (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
            for route in load_feature(MODULE).router.routes
            if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in ("POST", "PATCH", "DELETE")
        }
        for entry in entries:
            method, _, path = str(entry["source"]).partition(" ")
            resolved = path.replace("{room_id}", room["id"])
            assert (method, resolved) in write_routes or any(
                method == candidate_method and _same_shape(candidate_path, resolved)
                for candidate_method, candidate_path in write_routes
            ), f"the seed recorded {method} {path}, which is not a route this feature serves"
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_says_so_when_there_are_no_rooms():
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        summary = load_feature(MODULE).seed(
            db,
            {"room_ids": [], "now": datetime.now(timezone.utc), "rng": random.Random(1)},
        )
        assert "no demo rooms" in summary
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_opens_no_socket(monkeypatch):
    """The seeder supplies a ``FakeTransport``, so ``backend/seed.py`` never tries to
    POST to a host that does not exist. Guarded rather than assumed: a change that
    constructed a real fanout would fail here instead of hanging the seed."""
    import urllib.request

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda *a, **k: pytest.fail("seed opened a socket")
    )
    assert seed_result()["summary"]
