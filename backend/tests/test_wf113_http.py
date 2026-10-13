"""WF-113's HTTP surface, through the mounted router.

These cover the three things the domain tests cannot:

  - **the inbound shape.** The route reads the body as bytes and parses it
    itself, because the signature is computed over the exact bytes received. A
    route that declared a JSON body would have been re-serialised before the
    check could run, and every signed delivery would be refused.
  - **the audit source rule.** Every write this router performs must name a route
    the app actually serves. The contract calls that defect by name.
  - **the contract guards.** No shared file, no ``dsr.api`` import, a unique
    prefix, and a discovery that reports no failure.

Every test uses the shared ``client`` fixture from ``conftest.py``, so each one
runs against a fresh database and no test can see another's rows. That is what
makes the file pass on its own and under xdist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as module
from dsr.store import RecordStore
from dsr.webhook_ingest import signing, vocabulary

PREFIX = module.router.prefix
SECRET = "http-signing-secret"
ORDER = {"id": "ORD-30001", "event": "order.placed", "group": [{"id": "a"}, {"id": "b"}]}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_automation(client, room="room_http", *, secret=None, actions=None, start=True):
    payload = {
        "name": "Order events",
        "identifier_type": "id",
        "secret": secret,
        "actions": actions
        if actions is not None
        else [
            {
                "key": "send_event",
                "name": "order:placed",
                "mapping": {"id": "{{trigger.identifiers.id}}"},
            },
            {"key": "slack"},
        ],
    }
    created = client.post(f"{PREFIX}/automations", params={"room_id": room}, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    if start:
        started = client.post(f"{PREFIX}/automations/{body['id']}/start")
        assert started.status_code == 200, started.text
        body = started.json()
    return body


def post_hook(client, token, payload, *, headers=None, raw=None, **params):
    import json

    content = raw if raw is not None else json.dumps(payload).encode("utf-8")
    return client.post(
        f"{PREFIX}/hooks/{token}", content=content, headers=headers or {}, params=params
    )


def signed_headers(body: bytes, *, secret=SECRET, when=None):
    timestamp = signing.unix_seconds(when)
    return {
        signing.SIGNATURE_HEADER: signing.sign(secret, timestamp, body.decode("utf-8")),
        signing.TIMESTAMP_HEADER: timestamp,
    }


# --------------------------------------------------------------------------- #
# The contract guards
# --------------------------------------------------------------------------- #


def test_the_prefix_is_the_one_the_issue_names_and_is_unique():
    assert PREFIX == "/api/wf-113"
    assert module.FEATURE["ticket"] == "WF-113"
    assert module.FEATURE["id"].startswith("wf-113-")


def test_the_router_is_mounted_by_discovery_and_reports_no_failure():
    from conftest import app  # type: ignore[import-not-found]
    from fastapi.testclient import TestClient

    payload = TestClient(app).get("/api/features").json()
    listed = [row for row in payload["features"] if row["id"] == module.FEATURE["id"]]
    assert listed, "the feature is not listed by /api/features"
    assert listed[0]["loaded"] is True
    assert listed[0]["error"] == ""
    assert listed[0]["prefix"] == PREFIX
    assert listed[0]["routes"], "a router with no routes fails the host's own test"


def test_the_exception_handlers_are_this_workflows_own_types():
    from dsr.webhook_ingest import WebhookIngestError

    assert set(module.EXCEPTION_HANDLERS) == {WebhookIngestError}
    for handled in module.EXCEPTION_HANDLERS:
        assert handled.__module__.startswith("dsr.webhook_ingest")


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_actions_the_batch_ceiling_and_the_rate_limits(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["vocabulary"]["batch_limit"] == 1000
    keys = [row["key"] for row in payload["vocabulary"]["actions"]]
    assert "batch_update" in keys and "send_data" in keys and "slack" in keys
    assert payload["signing"]["signature_header"] == signing.SIGNATURE_HEADER
    assert payload["signing"]["rule"].startswith("HMAC-SHA256")


def test_the_rate_limits_are_served_and_not_enforced(client):
    payload = client.get(f"{PREFIX}/rate-limits").json()
    assert payload["enforced"] is False
    limits = {row["surface"]: row for row in payload["rate_limits"]}
    assert limits["pipelines"]["limit"] == 3000
    assert limits["pipelines"]["soft"] is True
    assert limits["app_api"]["limit"] == 10


def test_the_decisions_are_served_and_an_unknown_one_lists_the_ids(client):
    listed = client.get(f"{PREFIX}/decisions").json()
    assert listed["count"] >= 9
    for decision in listed["decisions"]:
        assert decision["chosen"] in decision["options"]
        assert decision["rejected_because"]
    missing = client.get(f"{PREFIX}/decisions/nope")
    assert missing.status_code == 404
    assert missing.json()["known"]


def test_one_decision_can_be_read_by_id(client):
    response = client.get(f"{PREFIX}/decisions/customer-data-resolves-to-the-empty-string")
    assert response.status_code == 200
    assert response.json()["chosen"].startswith("render the empty string")


# --------------------------------------------------------------------------- #
# Automations and their generated URLs
# --------------------------------------------------------------------------- #


def test_an_automation_is_created_with_its_generated_url_and_no_secret_leaks(client):
    created = make_automation(client, secret=SECRET)
    assert created["url"] == vocabulary.ENDPOINT_PATH_TEMPLATE.format(token=created["token"])
    assert created["has_secret"] is True
    assert SECRET not in client.get(f"{PREFIX}/automations/{created['id']}").text


def test_the_automation_list_is_scoped_to_its_room(client):
    make_automation(client, room="room_one")
    make_automation(client, room="room_two")
    listed = client.get(f"{PREFIX}/automations", params={"room_id": "room_one"}).json()
    assert listed["count"] == 1
    assert listed["room_ref"] == "room_one"


def test_adding_actions_through_patch_is_accepted(client):
    created = make_automation(client, actions=[])
    patched = client.patch(
        f"{PREFIX}/automations/{created['id']}",
        json={"actions": [{"key": "send_data", "target_url": "https://warehouse.example/events"}]},
    )
    assert patched.status_code == 200
    assert patched.json()["action_count"] == 1


def test_an_unknown_action_is_refused_with_a_field_keyed_message(client):
    created = make_automation(client, actions=[])
    response = client.patch(
        f"{PREFIX}/automations/{created['id']}", json={"actions": [{"key": "delete_person"}]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_automation"


def test_an_unknown_status_is_refused(client):
    created = make_automation(client, actions=[])
    response = client.patch(f"{PREFIX}/automations/{created['id']}", json={"status": "live"})
    assert response.status_code == 422


def test_an_automation_that_does_not_exist_is_a_404(client):
    assert client.get(f"{PREFIX}/automations/nope").status_code == 404


# --------------------------------------------------------------------------- #
# The inbound entry point
# --------------------------------------------------------------------------- #


def test_a_delivery_to_a_started_automation_is_accepted(client):
    created = make_automation(client)
    response = post_hook(client, created["token"], ORDER)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == vocabulary.TRIGGER_ACCEPTED
    assert body["events"][0]["data"]["id"] == "ORD-30001"


def test_a_retry_of_the_same_body_is_a_duplicate_at_200(client):
    created = make_automation(client)
    first = post_hook(client, created["token"], ORDER)
    second = post_hook(client, created["token"], ORDER)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["state"] == vocabulary.TRIGGER_DUPLICATE
    assert second.json()["events"] == []


def test_a_body_of_any_shape_is_accepted(client):
    created = make_automation(client, actions=[{"key": "send_event", "name": "row:seen"}])
    response = post_hook(client, created["token"], [1, 2, 3])
    assert response.status_code == 202
    assert response.json()["trigger"]["shape"] == "array"


def test_a_body_that_is_not_json_is_refused_with_its_own_name(client):
    created = make_automation(client)
    response = post_hook(client, created["token"], None, raw=b"{not json")
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_payload"


def test_an_empty_body_is_refused(client):
    created = make_automation(client)
    response = post_hook(client, created["token"], None, raw=b"")
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_payload"


def test_an_unknown_token_is_a_404_that_names_the_fix(client):
    response = post_hook(client, "not-a-token", ORDER)
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_endpoint"
    assert "generated" in response.json()["detail"]


def test_a_delivery_to_a_draft_automation_is_a_409(client):
    created = make_automation(client, start=False)
    response = post_hook(client, created["token"], ORDER)
    assert response.status_code == 409
    assert response.json()["error"] == "automation_not_started"


def test_an_over_limit_batch_is_refused_and_recorded(client):
    created = make_automation(client, actions=[{"key": "batch_update", "name": "group:updated"}])
    oversized = {"id": "ORD-9", "group": list(range(vocabulary.BATCH_LIMIT + 1))}
    response = post_hook(client, created["token"], oversized)
    assert response.status_code == 422
    assert response.json()["error"] == "batch_limit_exceeded"
    rejected = client.get(
        f"{PREFIX}/triggers", params={"room_id": "room_http", "state": "rejected"}
    ).json()
    assert rejected["count"] == 1


def test_an_unsigned_delivery_to_a_secret_bearing_automation_is_a_401(client):
    created = make_automation(client, secret=SECRET)
    response = post_hook(client, created["token"], ORDER)
    assert response.status_code == 401
    assert response.json()["error"] == "signature_missing"


def test_a_correctly_signed_delivery_is_accepted(client):
    created = make_automation(client, secret=SECRET)
    import json

    body = json.dumps(ORDER).encode("utf-8")
    response = post_hook(client, created["token"], None, raw=body, headers=signed_headers(body))
    assert response.status_code == 202
    assert response.json()["state"] == vocabulary.TRIGGER_ACCEPTED


def test_a_wrong_signature_is_a_401(client):
    created = make_automation(client, secret=SECRET)
    import json

    body = json.dumps(ORDER).encode("utf-8")
    headers = signed_headers(body, secret="another-secret")
    response = post_hook(client, created["token"], None, raw=body, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "signature_mismatch"


def test_a_stale_signed_delivery_is_a_401(client):
    created = make_automation(client, secret=SECRET)
    import json

    body = json.dumps(ORDER).encode("utf-8")
    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    response = post_hook(
        client, created["token"], None, raw=body, headers=signed_headers(body, when=stale)
    )
    assert response.status_code == 401
    assert response.json()["reason"] == "signature_stale"


# --------------------------------------------------------------------------- #
# The record of what arrived
# --------------------------------------------------------------------------- #


def test_the_trigger_event_and_delivery_logs_are_readable(client):
    created = make_automation(client)
    post_hook(client, created["token"], ORDER)
    triggers = client.get(f"{PREFIX}/triggers", params={"room_id": "room_http"}).json()
    assert triggers["count"] == 1
    events = client.get(f"{PREFIX}/events", params={"room_id": "room_http"}).json()
    assert events["count"] == 1
    deliveries = client.get(f"{PREFIX}/deliveries", params={"room_id": "room_http"}).json()
    assert deliveries["by_outcome"] == {"delivered": 1, "skipped": 1}
    one = client.get(f"{PREFIX}/deliveries/{deliveries['deliveries'][0]['id']}")
    assert one.status_code == 200


def test_an_unknown_record_is_a_404(client):
    assert client.get(f"{PREFIX}/triggers/nope").status_code == 404
    assert client.get(f"{PREFIX}/events/nope").status_code == 404
    assert client.get(f"{PREFIX}/deliveries/nope").status_code == 404


def test_the_summary_reports_the_states_the_room_reached(client):
    created = make_automation(client)
    post_hook(client, created["token"], ORDER)
    post_hook(client, created["token"], ORDER)
    summary = client.get(f"{PREFIX}/summary", params={"room_id": "room_http"}).json()
    assert summary["accepted"] == 1
    assert summary["duplicates"] == 1
    assert summary["started"] == 1
    assert summary["rate_limits_enforced"] is False


def test_a_mapping_can_be_checked_for_a_reference_to_the_person(client):
    created = make_automation(
        client,
        actions=[{"key": "send_event", "name": "e", "mapping": {"email": "{{person.email}}"}}],
    )
    report = post_hook(client, created["token"], ORDER).json()
    assert report["events"][0]["data"]["email"] == ""


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def test_every_audit_source_this_router_records_names_a_route_the_host_mounted(client, db):
    created = make_automation(client, secret=SECRET)
    post_hook(client, created["token"], ORDER)

    mounted = {
        (method, route.path)
        for route in module.router.routes
        for method in getattr(route, "methods", set())
    }
    mine = [row["source"] for row in db.audit() if row["source"]]
    assert mine, "no audit row carried a source"
    for source in mine:
        method, _, path = source.partition(" ")
        assert (method, path) in mounted, f"{source} is not a route this router serves"


def test_the_inbound_write_names_the_hooks_route_and_the_provider_actor(client, db):
    created = make_automation(client)
    post_hook(client, created["token"], ORDER)
    hook_writes = [
        row
        for row in db.audit()
        if row["source"] == f"POST {PREFIX}/hooks/{{token}}"
        and row["collection"] == vocabulary.COLLECTION_TRIGGER
    ]
    assert hook_writes, "the trigger wrote no audit row naming the hook route"
    assert hook_writes[0]["actor"] == "provider"


def test_the_creation_write_names_the_post_automations_route(client, db):
    make_automation(client)
    writes = [row for row in db.audit() if row["source"] == f"POST {PREFIX}/automations"]
    assert writes
    collections = {row["collection"] for row in writes}
    assert vocabulary.COLLECTION_AUTOMATION in collections
    assert vocabulary.COLLECTION_ENDPOINT in collections


# --------------------------------------------------------------------------- #
# The seed, over the mounted app's own database
# --------------------------------------------------------------------------- #


def test_the_seed_runs_and_reports_states_that_are_not_all_successes(db):
    fixed = datetime(2026, 3, 4, 12, tzinfo=timezone.utc)
    summary = module.seed(db, {"room_ids": [], "now": fixed, "rng": None})
    assert summary
    summary.encode("cp1252")
    assert "duplicate" in summary
    assert "batch_limit_exceeded" in summary
    assert "signature_missing" in summary
    store = RecordStore(db)
    for collection in vocabulary.ALL_COLLECTIONS:
        assert store.find(collection, {}, limit=500), f"{collection} was not seeded"


# --------------------------------------------------------------------------- #
# Every route answers without a 5xx
# --------------------------------------------------------------------------- #


def test_every_route_this_feature_serves_answers_without_a_5xx(client):
    created = make_automation(client)
    token = created["token"]
    from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as app_module

    probes = [
        ("GET", f"{PREFIX}/vocabulary"),
        ("GET", f"{PREFIX}/inferences"),
        ("GET", f"{PREFIX}/decisions"),
        ("GET", f"{PREFIX}/decisions/customer-data-resolves-to-the-empty-string"),
        ("GET", f"{PREFIX}/rate-limits"),
        ("GET", f"{PREFIX}/summary"),
        ("GET", f"{PREFIX}/automations?room_id=room_http"),
        ("GET", f"{PREFIX}/automations/{created['id']}"),
        ("GET", f"{PREFIX}/triggers?room_id=room_http"),
        ("GET", f"{PREFIX}/events?room_id=room_http"),
        ("GET", f"{PREFIX}/deliveries?room_id=room_http"),
    ]
    for method, path in probes:
        response = client.get(path)
        assert response.status_code < 500, f"{method} {path} answered {response.status_code}"
    hook = post_hook(client, token, ORDER)
    assert hook.status_code < 500
    assert app_module.HOOK_SOURCE == f"POST {PREFIX}/hooks/{{token}}"
