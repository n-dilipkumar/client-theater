"""WF-114's HTTP surface, through the mounted router.

These cover the four things the domain tests cannot:

  - **the inbound shape.** The route reads the body as bytes and parses it
    itself, because the signature is computed over the exact bytes received. A
    route that declared a JSON body would have been re-serialised before the check
    could run, and every signed delivery would be refused. The same route serves
    POST, GET and PUT, and a GET signs the query string as sent.
  - **the audit source rule.** Every write this router performs must name a route
    the app actually serves. The contract calls that defect by name.
  - **the contract guards.** No shared file, no ``dsr.api`` import, a unique
    prefix, and a discovery that reports no failure.
  - **the status codes an external service reads.** A duplicate is 200, an
    accepted delivery is 202, and every refusal carries the status its error
    declares.

Every test uses the shared ``client`` fixture from ``conftest.py``, so each one
runs against a fresh database and no test can see another's rows. That is what
makes the file pass on its own and under xdist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from dsr.features import wf114_start_a_workflow_from_an_external_systems_webhook as module
from dsr.store import RecordStore
from dsr.webhook_workflow_start import rules, signing, vocabulary

PREFIX = module.router.prefix
SECRET = "http-signing-value"
NOW = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
PAYMENT = {"customer_id": "CUST_001", "amount": 100, "currency": "USD", "status": "successful"}
MAPPINGS = [
    {"variable": "customer_ref", "path": "customer_id"},
    {"variable": "amount", "path": "amount", "type": "number"},
    {
        "variable": "lead_status",
        "path": "status",
        "when": {"path": "status", "operator": "equals", "value": "successful"},
    },
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_workflow(client, room="room_http", **extra):
    payload = {
        "name": "Payments",
        "actions": ["Update the lead's status to 'Paid.'", "Send a confirmation email"],
        **extra,
    }
    created = client.post(f"{PREFIX}/workflows", params={"room_id": room}, json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def make_trigger(client, workflow_id, *, methods=None, mappings=None, secret=SECRET, save=True):
    payload = {
        "name": "Payment result",
        "signing_value": secret,
        "mappings": MAPPINGS if mappings is None else mappings,
    }
    if methods is not None:
        payload["methods"] = methods
    created = client.post(f"{PREFIX}/workflows/{workflow_id}/triggers", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    if save:
        saved = client.post(f"{PREFIX}/triggers/{body['id']}/save")
        assert saved.status_code == 200, saved.text
    return body


def make_ready(client, room="room_http", *, publish=True, **kwargs):
    workflow = make_workflow(client, room)
    trigger = make_trigger(client, workflow["id"], **kwargs)
    if publish:
        published = client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
        assert published.status_code == 200, published.text
    return workflow, trigger


def sign_headers(body: str, *, secret=SECRET, when=None):
    stamp = signing.unix_seconds(when)
    return {
        signing.SIGNATURE_HEADER: signing.sign(secret, stamp, body),
        signing.TIMESTAMP_HEADER: stamp,
        "Content-Type": "application/json",
    }


def post_hook(client, token, payload, *, secret=SECRET, when=None, params=None, raw=None):
    body = raw if raw is not None else rules.canonical_json(payload)
    return client.post(
        f"{PREFIX}/hooks/{token}",
        content=body.encode("utf-8") if isinstance(body, str) else body,
        headers=sign_headers(body, secret=secret, when=when),
        params=params or {},
    )


def get_hook(client, token, query, *, secret=SECRET, when=None):
    text = urlencode(query)
    stamp = signing.unix_seconds(when)
    headers = {
        signing.SIGNATURE_HEADER: signing.sign(secret, stamp, text),
        signing.TIMESTAMP_HEADER: stamp,
    }
    return client.get(f"{PREFIX}/hooks/{token}?{text}", headers=headers)


def put_hook(client, token, payload, *, secret=SECRET, when=None):
    body = rules.canonical_json(payload)
    headers = sign_headers(body, secret=secret, when=when)
    return client.put(f"{PREFIX}/hooks/{token}", content=body.encode("utf-8"), headers=headers)


# --------------------------------------------------------------------------- #
# The contract guards
# --------------------------------------------------------------------------- #


def test_the_prefix_is_the_one_the_issue_names_and_is_unique():
    assert PREFIX == "/api/wf-114"
    assert module.FEATURE["ticket"] == "WF-114"
    assert module.FEATURE["id"].startswith("wf-114-")


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


def test_the_hook_route_serves_the_three_researched_methods():
    routes = {
        (method, route.path)
        for route in module.router.routes
        for method in getattr(route, "methods", set())
    }
    for method in vocabulary.METHODS:
        assert (method, f"{PREFIX}/hooks/{{token}}") in routes, method


def test_the_exception_handlers_are_this_workflows_own_types():
    from dsr.webhook_workflow_start import WebhookWorkflowError

    assert set(module.EXCEPTION_HANDLERS) == {WebhookWorkflowError}
    for handled in module.EXCEPTION_HANDLERS:
        assert handled.__module__.startswith("dsr.webhook_workflow_start")


def test_the_feature_module_does_not_import_the_app():
    import pathlib

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source and "import dsr.api" not in source


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_methods_the_types_and_the_register(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["vocabulary"]["methods"] == ["POST", "GET", "PUT"]
    assert payload["vocabulary"]["variable_types"]
    assert payload["vocabulary"]["operators"]
    assert payload["signing"]["required"] == "always"
    assert payload["signing"]["signature_header"] == signing.SIGNATURE_HEADER
    assert payload["signing"]["reused_from"].startswith("WF-113")
    assert payload["inferences"] >= 12


def test_the_decisions_are_served_and_an_unknown_one_lists_the_ids(client):
    listed = client.get(f"{PREFIX}/decisions").json()
    assert listed["count"] >= 12
    for decision in listed["decisions"]:
        assert decision["chosen"] in decision["options"]
        assert decision["rejected_because"]
    missing = client.get(f"{PREFIX}/decisions/nope")
    assert missing.status_code == 404
    assert missing.json()["known"]


def test_one_decision_can_be_read_by_id(client):
    response = client.get(f"{PREFIX}/decisions/every-generated-url-is-signed")
    assert response.status_code == 200
    assert response.json()["chosen"].startswith("require the shared signature")


def test_the_inferences_route_matches_the_decisions_route(client):
    assert client.get(f"{PREFIX}/inferences").json() == client.get(f"{PREFIX}/decisions").json()


# --------------------------------------------------------------------------- #
# Workflows, variables and triggers
# --------------------------------------------------------------------------- #


def test_a_workflow_is_created_and_listed_for_its_room(client):
    created = make_workflow(client, "room_one")
    assert created["status"] == vocabulary.STATUS_DRAFT
    listed = client.get(f"{PREFIX}/workflows", params={"room_id": "room_one"}).json()
    assert listed["count"] == 1
    assert listed["room_ref"] == "room_one"
    assert client.get(f"{PREFIX}/workflows", params={"room_id": "room_two"}).json()["count"] == 0


def test_a_workflow_without_a_name_is_refused(client):
    response = client.post(f"{PREFIX}/workflows", params={"room_id": "room_http"}, json={})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_workflow"


def test_a_workflow_is_patched_and_published(client):
    workflow = make_workflow(client)
    patched = client.patch(f"{PREFIX}/workflows/{workflow['id']}", json={"name": "Payments v2"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Payments v2"
    published = client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == vocabulary.STATUS_PUBLISHED
    assert published.json()["published_at"]


def test_an_unknown_workflow_is_a_404(client):
    assert client.get(f"{PREFIX}/workflows/nope").status_code == 404


def test_a_variable_is_declared_and_listed(client):
    workflow = make_workflow(client)
    created = client.post(
        f"{PREFIX}/workflows/{workflow['id']}/variables",
        json={"name": "amount", "type": "number"},
    )
    assert created.status_code == 201
    listed = client.get(f"{PREFIX}/workflows/{workflow['id']}/variables").json()
    assert listed["count"] == 1
    one = client.get(f"{PREFIX}/variables/{created.json()['id']}")
    assert one.status_code == 200
    assert one.json()["type"] == "number"


def test_a_variable_with_an_unknown_type_is_refused(client):
    workflow = make_workflow(client)
    response = client.post(
        f"{PREFIX}/workflows/{workflow['id']}/variables", json={"name": "when", "type": "date"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_variable"


def test_a_trigger_is_created_with_its_generated_url(client):
    workflow = make_workflow(client)
    created = client.post(
        f"{PREFIX}/workflows/{workflow['id']}/triggers", json={"name": "Payment result"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["url"] == vocabulary.ENDPOINT_PATH_TEMPLATE.format(token=body["token"])
    assert body["methods"] == list(vocabulary.METHODS)
    assert body["premium_trigger"] is True


def test_the_signing_value_is_served_once_and_no_read_returns_it(client):
    _workflow, trigger = make_ready(client)
    created = client.post(
        f"{PREFIX}/workflows/{trigger['workflow_ref']}/triggers", json={"name": "Second"}
    ).json()
    assert created["signing_value"]
    read = client.get(f"{PREFIX}/triggers/{created['id']}").json()
    assert "signing_value" not in read
    assert read["has_signing_value"] is True
    assert SECRET not in client.get(f"{PREFIX}/triggers").text


def test_a_trigger_can_be_patched_and_saved(client):
    workflow = make_workflow(client)
    created = client.post(
        f"{PREFIX}/workflows/{workflow['id']}/triggers", json={"name": "Payment result"}
    ).json()
    patched = client.patch(f"{PREFIX}/triggers/{created['id']}", json={"mappings": MAPPINGS})
    assert patched.status_code == 200
    assert patched.json()["mapping_count"] == 3
    saved = client.post(f"{PREFIX}/triggers/{created['id']}/save")
    assert saved.status_code == 200
    assert saved.json()["saved"] is True


def test_an_invalid_mapping_is_refused_when_the_trigger_is_written(client):
    workflow = make_workflow(client)
    response = client.post(
        f"{PREFIX}/workflows/{workflow['id']}/triggers",
        json={"mappings": [{"path": "customer_id"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_trigger"


def test_an_unserved_method_is_refused_when_the_trigger_is_written(client):
    workflow = make_workflow(client)
    response = client.post(
        f"{PREFIX}/workflows/{workflow['id']}/triggers", json={"methods": ["DELETE"]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_trigger"


def test_an_unknown_trigger_is_a_404(client):
    assert client.get(f"{PREFIX}/triggers/nope").status_code == 404


# --------------------------------------------------------------------------- #
# The inbound entry point
# --------------------------------------------------------------------------- #


def test_a_signed_post_starts_the_workflow_at_202(client):
    _workflow, trigger = make_ready(client)
    response = post_hook(client, trigger["token"], PAYMENT)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == vocabulary.STATE_ACCEPTED
    assert body["started"] is True
    assert body["run"]["status"] == vocabulary.RUN_STARTED
    assert {row["variable"] for row in body["bindings"]} == {
        "customer_ref",
        "amount",
        "lead_status",
    }
    assert body["run"]["planned_actions"]


def test_a_retry_of_the_same_body_is_a_duplicate_at_200(client):
    _workflow, trigger = make_ready(client)
    first = post_hook(client, trigger["token"], PAYMENT)
    second = post_hook(client, trigger["token"], PAYMENT)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["state"] == vocabulary.STATE_DUPLICATE
    assert second.json()["run"] is None


def test_a_put_delivery_is_accepted(client):
    _workflow, trigger = make_ready(client, methods=list(vocabulary.METHODS))
    response = put_hook(client, trigger["token"], PAYMENT)
    assert response.status_code == 202
    assert response.json()["state"] == vocabulary.STATE_ACCEPTED


def test_a_get_reads_its_payload_from_the_query_string(client):
    workflow = make_workflow(client)
    trigger = make_trigger(
        client,
        workflow["id"],
        methods=["GET"],
        mappings=[
            {"variable": "customer_ref", "path": "customer_id"},
            {"variable": "amount", "path": "amount", "type": "number"},
        ],
    )
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    response = get_hook(client, trigger["token"], {"customer_id": "CUST_1", "amount": "100"})
    assert response.status_code == 202
    body = response.json()
    assert body["payload"]["source"] == vocabulary.METHOD_PAYLOAD_SOURCE_QUERY
    by_variable = {row["variable"]: row["value"] for row in body["bindings"]}
    assert by_variable["amount"] == 100


def test_a_get_that_carries_a_body_is_a_400(client):
    workflow = make_workflow(client)
    trigger = make_trigger(
        client,
        workflow["id"],
        methods=["GET"],
        mappings=[{"variable": "customer_ref", "path": "customer_id"}],
    )
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    body = rules.canonical_json({"customer_id": "CUST_1"})
    response = client.request(
        "GET",
        f"{PREFIX}/hooks/{trigger['token']}",
        content=body.encode("utf-8"),
        headers=sign_headers(body),
    )
    assert response.status_code == 400
    assert response.json()["error"] == vocabulary.REASON_BODY_NOT_ALLOWED_ON_GET


def test_a_test_request_validates_without_starting_the_workflow(client):
    _workflow, trigger = make_ready(client)
    response = post_hook(
        client, trigger["token"], PAYMENT, params={vocabulary.TEST_MODE_PARAM: "1"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["mode"] == vocabulary.MODE_TEST
    assert body["started"] is False
    assert body["run"] is None
    assert body["bindings"]
    runs = client.get(f"{PREFIX}/runs", params={"room_id": "room_http"}).json()
    assert runs["count"] == 0


def test_a_test_request_is_accepted_before_the_trigger_is_saved(client):
    workflow = make_workflow(client)
    trigger = make_trigger(client, workflow["id"], save=False)
    response = client.post(
        f"{PREFIX}/hooks/{trigger['token']}?{vocabulary.TEST_MODE_PARAM}=1",
        content=rules.canonical_json(PAYMENT).encode("utf-8"),
        headers=sign_headers(rules.canonical_json(PAYMENT)),
    )
    assert response.status_code == 202
    assert response.json()["mode"] == vocabulary.MODE_TEST


def test_a_live_delivery_before_save_is_a_409(client):
    workflow = make_workflow(client)
    trigger = make_trigger(client, workflow["id"], save=False)
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    response = post_hook(client, trigger["token"], PAYMENT)
    assert response.status_code == 409
    assert response.json()["error"] == "trigger_not_saved"


def test_a_live_delivery_into_a_draft_is_a_409(client):
    workflow = make_workflow(client)
    trigger = make_trigger(client, workflow["id"])
    response = post_hook(client, trigger["token"], PAYMENT)
    assert response.status_code == 409
    assert response.json()["error"] == "workflow_not_published"


def test_a_payload_that_matches_no_mapping_is_recorded_as_unmapped(client):
    workflow = make_workflow(client)
    trigger = make_trigger(
        client,
        workflow["id"],
        mappings=[
            {
                "variable": "lead_status",
                "path": "status",
                "when": {"path": "status", "operator": "equals", "value": "refunded"},
            }
        ],
    )
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    response = post_hook(client, trigger["token"], {"status": "pending"})
    assert response.status_code == 202
    assert response.json()["state"] == vocabulary.STATE_UNMAPPED
    assert response.json()["started"] is False


def test_a_missing_required_key_is_a_422_that_names_the_variable(client):
    _workflow, trigger = make_ready(client)
    response = post_hook(client, trigger["token"], {"amount": 100})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == vocabulary.REASON_REQUIRED_MAPPING_UNRESOLVED
    assert "customer_ref" in body["detail"]
    assert body["unresolved"]


def test_an_unsigned_delivery_is_a_401(client):
    _workflow, trigger = make_ready(client)
    response = client.post(
        f"{PREFIX}/hooks/{trigger['token']}", content=rules.canonical_json(PAYMENT).encode("utf-8")
    )
    assert response.status_code == 401
    assert response.json()["error"] == vocabulary.REASON_SIGNATURE_MISSING


def test_a_wrong_signature_is_a_401(client):
    _workflow, trigger = make_ready(client)
    response = post_hook(client, trigger["token"], PAYMENT, secret="another-value")
    assert response.status_code == 401
    assert response.json()["error"] == vocabulary.REASON_SIGNATURE_MISMATCH


def test_a_stale_signed_delivery_is_a_401_with_its_own_reason(client):
    _workflow, trigger = make_ready(client)
    response = post_hook(
        client, trigger["token"], PAYMENT, when=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    assert response.status_code == 401
    assert response.json()["reason"] == vocabulary.REASON_SIGNATURE_STALE


def test_a_body_that_is_not_json_is_a_400(client):
    _workflow, trigger = make_ready(client)
    raw = b"{not json"
    response = client.post(
        f"{PREFIX}/hooks/{trigger['token']}",
        content=raw,
        headers=sign_headers(raw.decode("utf-8")),
    )
    assert response.status_code == 400
    assert response.json()["error"] == vocabulary.REASON_MALFORMED_PAYLOAD


def test_an_empty_body_is_a_400(client):
    _workflow, trigger = make_ready(client)
    response = client.post(
        f"{PREFIX}/hooks/{trigger['token']}", content=b"", headers=sign_headers("")
    )
    assert response.status_code == 400
    assert response.json()["error"] == vocabulary.REASON_MALFORMED_PAYLOAD


def test_an_unserved_method_is_a_405_with_its_own_reason(client):
    workflow = make_workflow(client)
    trigger = make_trigger(client, workflow["id"], methods=["POST"])
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    response = put_hook(client, trigger["token"], PAYMENT)
    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"


def test_an_unknown_token_is_a_404_that_names_the_fix(client):
    response = post_hook(client, "not-a-token", PAYMENT)
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_endpoint"
    assert "generated" in response.json()["detail"]


def test_an_array_payload_is_accepted(client):
    workflow = make_workflow(client)
    trigger = make_trigger(
        client,
        workflow["id"],
        mappings=[{"variable": "first", "path": "0", "type": "number", "required": False}],
    )
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    response = post_hook(client, trigger["token"], [7, 8])
    assert response.status_code == 202
    assert response.json()["payload"]["shape"] == "array"


# --------------------------------------------------------------------------- #
# The record of what arrived, and what started
# --------------------------------------------------------------------------- #


def test_the_invocation_and_run_logs_are_readable(client):
    _workflow, trigger = make_ready(client)
    post_hook(client, trigger["token"], PAYMENT)
    invocations = client.get(f"{PREFIX}/invocations", params={"room_id": "room_http"}).json()
    assert invocations["count"] == 1
    one = client.get(f"{PREFIX}/invocations/{invocations['invocations'][0]['id']}")
    assert one.status_code == 200
    runs = client.get(f"{PREFIX}/runs", params={"room_id": "room_http"}).json()
    assert runs["count"] == 1
    assert client.get(f"{PREFIX}/runs/{runs['runs'][0]['id']}").status_code == 200


def test_an_unknown_invocation_or_run_is_a_404(client):
    assert client.get(f"{PREFIX}/invocations/nope").status_code == 404
    assert client.get(f"{PREFIX}/runs/nope").status_code == 404


def test_the_invocation_log_is_filtered_by_state(client):
    _workflow, trigger = make_ready(client)
    post_hook(client, trigger["token"], PAYMENT)
    post_hook(client, trigger["token"], PAYMENT)
    duplicates = client.get(
        f"{PREFIX}/invocations",
        params={"room_id": "room_http", "state": vocabulary.STATE_DUPLICATE},
    ).json()
    assert duplicates["count"] == 1
    accepted = client.get(
        f"{PREFIX}/invocations",
        params={"room_id": "room_http", "state": vocabulary.STATE_ACCEPTED},
    ).json()
    assert accepted["count"] == 1


def test_the_summary_reports_the_states_the_room_reached(client):
    _workflow, trigger = make_ready(client)
    post_hook(client, trigger["token"], PAYMENT)
    post_hook(client, trigger["token"], PAYMENT)
    summary = client.get(f"{PREFIX}/summary", params={"room_id": "room_http"}).json()
    assert summary["accepted"] == 1
    assert summary["duplicates"] == 1
    assert summary["runs"] == 1
    assert summary["saved_triggers"] == 1
    assert summary["published"] == 1
    assert summary["rate_limits_enforced"] is False


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def test_every_audit_source_this_router_records_names_a_route_the_host_mounted(client, db):
    _workflow, trigger = make_ready(client)
    post_hook(client, trigger["token"], PAYMENT)
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
    _workflow, trigger = make_ready(client)
    post_hook(client, trigger["token"], PAYMENT)
    hook_writes = [
        row
        for row in db.audit()
        if row["source"] == f"POST {PREFIX}/hooks/{{token}}"
        and row["collection"] == vocabulary.COLLECTION_INVOCATION
    ]
    assert hook_writes, "the delivery wrote no audit row naming the hook route"
    assert hook_writes[0]["actor"] == "provider"


def test_a_get_delivery_names_the_get_route_in_its_audit_row(client, db):
    workflow = make_workflow(client)
    trigger = make_trigger(
        client,
        workflow["id"],
        methods=["GET"],
        mappings=[{"variable": "customer_ref", "path": "customer_id"}],
    )
    client.post(f"{PREFIX}/workflows/{workflow['id']}/publish")
    get_hook(client, trigger["token"], {"customer_id": "CUST_1"})
    sources = {row["source"] for row in db.audit() if row["source"]}
    assert f"GET {PREFIX}/hooks/{{token}}" in sources


def test_the_creation_write_names_the_post_workflows_route(client, db):
    make_workflow(client)
    writes = [row for row in db.audit() if row["source"] == f"POST {PREFIX}/workflows"]
    assert writes
    assert writes[0]["collection"] == vocabulary.COLLECTION_WORKFLOW


# --------------------------------------------------------------------------- #
# The seed, over the mounted app's own database
# --------------------------------------------------------------------------- #


def test_the_seed_runs_and_reports_states_that_are_not_all_successes(db):
    summary = module.seed(db, {"room_ids": [], "now": NOW, "rng": None})
    assert summary
    summary.encode("cp1252")
    for state in ("duplicate", "unmapped", "signature_missing", "required_mapping_unresolved"):
        assert state in summary, state
    store = RecordStore(db)
    for collection in vocabulary.ALL_COLLECTIONS:
        assert store.find(collection, {}, limit=500), f"{collection} was not seeded"


# --------------------------------------------------------------------------- #
# Every route answers without a 5xx
# --------------------------------------------------------------------------- #


def test_every_route_this_feature_serves_answers_without_a_5xx(client):
    workflow, trigger = make_ready(client)
    probes = [
        (f"{PREFIX}/vocabulary", None),
        (f"{PREFIX}/inferences", None),
        (f"{PREFIX}/decisions", None),
        (f"{PREFIX}/decisions/every-generated-url-is-signed", None),
        (f"{PREFIX}/summary?room_id=room_http", None),
        (f"{PREFIX}/workflows?room_id=room_http", None),
        (f"{PREFIX}/workflows/{workflow['id']}", None),
        (f"{PREFIX}/workflows/{workflow['id']}/variables", None),
        (f"{PREFIX}/triggers?room_id=room_http", None),
        (f"{PREFIX}/triggers/{trigger['id']}", None),
        (f"{PREFIX}/invocations?room_id=room_http", None),
        (f"{PREFIX}/runs?room_id=room_http", None),
        (f"{PREFIX}/triggers/nope", None),
        (f"{PREFIX}/runs/nope", None),
    ]
    for path, _unused in probes:
        response = client.get(path)
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"
    for path in (
        f"{PREFIX}/workflows?room_id=room_http",
        f"{PREFIX}/workflows/{workflow['id']}/variables",
        f"{PREFIX}/workflows/{workflow['id']}/triggers",
        f"{PREFIX}/workflows/{workflow['id']}/publish",
        f"{PREFIX}/triggers/{trigger['id']}/save",
    ):
        response = client.post(path, json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"
    patched = client.patch(f"{PREFIX}/triggers/{trigger['id']}", json={})
    assert patched.status_code < 500
    assert post_hook(client, trigger["token"], PAYMENT).status_code < 500
    assert get_hook(client, "absent-token", {}).status_code < 500
    assert module.hook_source("PUT") == f"PUT {PREFIX}/hooks/{{token}}"
