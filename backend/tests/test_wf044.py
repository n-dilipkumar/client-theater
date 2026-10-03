"""Tests for WF-044: emit a webhook out of the CRM when a deal stage changes.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-044.md`` (section 11 of
``docs/research/raw/crm-integration.md``). They are, in order:

* the flow itself - *Automation -> Workflows -> edit workflow -> + -> Data ops ->
  Send a webhook*, start conditions, **POST** into the room's HTTPS webhook URL,
  authentication, the body choice, **Save**, **Publish**, the built-in **Test**
  control, and *"The room's endpoint verifies the signature, resolves the record,
  and updates the buyer's room state"*;
* *"Webhook URLs are restricted to a secure protocol and must begin with HTTPS."*
* *"You can send both POST and GET requests using workflows."*
* three authentication types - *"Include request signature in header"* with a
  HubSpot App ID, an ``API key`` in query params or a request header, and *"The
  secret value must be in the format ``Bearer [YOUR_TOKEN]``"*;
* two body modes - *"Include all [object] properties"* and *"Customize request
  body"*, the latter with properties and/or static values;
* *"Workflows must be **published** to go live."*
* *"To set up webhook actions in workflows, users must have Edit permissions for
  workflows or Super Admin permissions. To publish workflows, users must have
  Publish permissions for workflows."*
* *"You can create up to 1,000 webhook subscriptions per app."* and *"Webhook calls
  made via workflows do not count towards the API rate limit."*
* the extensibility line - *"The room exposes one inbound endpoint per tenant with
  a versioned payload contract, so any number of CRM-side automations can target
  it. Because the room can verify the request signature, it does not need a
  per-workflow secret."*
* *"When a webhook is slow or times out, the workflow action may take longer than
  expected to execute."*
* the gaps - Salesforce outbound messages and Dataverse notifications are
  **unsourced**, so there are tests that they are not modelled.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every write against the route table the host actually reported.
"""

from __future__ import annotations

import json
import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dsr.api import app
from dsr.crm_outbound_webhooks import (
    AUTH_MODES,
    BODY_MODES,
    COLLECTION_AUTOMATION,
    COLLECTION_DEAL,
    COLLECTION_DELIVERY,
    COLLECTION_ENDPOINT,
    COLLECTION_NOTICE,
    CONTRACT_VERSION,
    INFERENCES,
    OBJECTS,
    OUTCOME_ACCEPTED,
    OUTCOME_DUPLICATE,
    OUTCOME_REFUSED,
    PERMISSIONS,
    PUBLISH_PERMISSIONS,
    SETUP_PERMISSIONS,
    SUBSCRIPTION_LIMIT_PER_APP,
    AlreadyPublished,
    AutomationLimitReached,
    EmptyPayload,
    EndpointExists,
    EndpointNotPublished,
    EndpointStateConflict,
    InboundPayload,
    InvalidAuthSetting,
    InvalidEndpointUrl,
    InvalidRequest,
    InvalidTrigger,
    MalformedBody,
    MalformedPayload,
    MissingAppId,
    MissingPermission,
    MissingSecret,
    NoEndpoint,
    ObjectMismatch,
    UnauthenticatedDelivery,
    UnknownAuthMode,
    UnknownAutomation,
    UnknownBodyMode,
    UnresolvedDeal,
    UnsupportedMethod,
    UnsupportedPayloadVersion,
    WebhookEngine,
    by_id,
    canonical_string,
    coerce_body,
    read_payload,
    sample_body,
    sign,
    sign_hex,
    uri_for,
    verify,
)
from dsr.crm_outbound_webhooks.signing import (
    BEARER_PREFIX,
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-044"

#: The feature module, and its id.
MODULE = "wf044_emit_a_webhook_out_of_the_crm_when_a_d"
FEATURE_ID = "wf-044-emit-a-webhook-out-of-the-crm-when-a-d"

#: The sources each route passes for a write. The pure-domain tests use these same
#: strings, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
CREATE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/endpoint"
AMEND_SOURCE = f"PATCH {PREFIX}/rooms/{{room_id}}/endpoint"
PUBLISH_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/endpoint/publish"
UNPUBLISH_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/endpoint/unpublish"
AUTOMATION_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/automations"
RETIRE_SOURCE = f"DELETE {PREFIX}/rooms/{{room_id}}/automations/{{automation_id}}"
POST_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/crm/v1/webhook"
GET_SOURCE = f"GET {PREFIX}/rooms/{{room_id}}/crm/v1/webhook"
ACKNOWLEDGE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/notices/acknowledge"

SECRET = "northwind-hubspot-client-secret"
BEARER = "contoso-room-bearer-token"
API_KEY = "fabrikam-deal-key"
APP_ID = "4821"

CONTRACT_SENT = "Contract Sent"
NEGOTIATION = "Negotiation in Progress"
CLOSED_WON = "Closed Won"

ROOM = {"name": "Northwind Traders - Enterprise Evaluation", "account": "Northwind Traders"}


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #


def stamp() -> str:
    """The millisecond timestamp a signed request carries, as the vendor sends it."""
    return str(int(datetime.now(timezone.utc).timestamp() * 1000))


def signature_headers(
    secret: str, method: str, uri: str, raw_body: str, *, timestamp: str | None = None
) -> dict[str, str]:
    """Sign a request exactly as the CRM-side action would."""
    moment = timestamp or stamp()
    return {
        SIGNATURE_HEADER: sign(secret, method, uri, raw_body, moment),
        SIGNATURE_TIMESTAMP_HEADER: moment,
    }


def raw_of(body: dict[str, Any] | None) -> str:
    """The bytes a sender would put on the wire for this payload."""
    return "" if body is None else json.dumps(body, sort_keys=True)


def deal_payload(**overrides: Any) -> dict[str, Any]:
    """A payload in the *Include all [object] properties* shape."""
    properties: dict[str, Any] = {
        "deal_id": "1001",
        "stage": CONTRACT_SENT,
        "amount": "42000",
    }
    properties.update(overrides.pop("properties", {}) or {})
    payload: dict[str, Any] = {"v": CONTRACT_VERSION, "object": "deals", "properties": properties}
    payload.update(overrides)
    return payload


def custom_payload(**overrides: Any) -> dict[str, Any]:
    """A payload in the *Customize request body* shape: flat, no nesting."""
    payload: dict[str, Any] = {"deal_id": "1001", "stage": CONTRACT_SENT, "amount": "42000"}
    payload.update(overrides)
    return payload


def endpoint_payload(**overrides: Any) -> dict[str, Any]:
    """A valid endpoint definition, with ``auth`` overridable per test."""
    payload: dict[str, Any] = {
        "url": "https://rooms.northwind.example/api/wf-044/rooms/r1/crm/v1/webhook",
        "method": "POST",
        "object": "deals",
        "auth": {"mode": "signature", "app_id": APP_ID, "secret": SECRET},
        "body": {"mode": "include_all"},
        "id_key": "deal_id",
        "stage_key": "stage",
    }
    payload.update(overrides)
    return payload


def automation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Contract sent",
        "trigger": {"property": "dealstage", "equals": CONTRACT_SENT},
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf044.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    return WebhookEngine(store)


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def endpoint(engine, room):
    return engine.register(
        room["id"],
        endpoint_payload(),
        permissions="edit_workflows",
        actor="dana",
        source=CREATE_SOURCE,
    )


@pytest.fixture()
def published(engine, endpoint, room):
    return engine.publish(
        room["id"], permissions="publish_workflows", actor="dana", source=PUBLISH_SOURCE
    )


def deliver(
    engine: WebhookEngine,
    room_id: str,
    body: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    secret: str = SECRET,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    uri: str | None = None,
    source: str = POST_SOURCE,
    actor: str = "crm",
) -> dict[str, Any]:
    """Send one signed request through the engine, the way the CRM would."""
    if uri is None:
        uri = uri_for(room_id)
        if query:
            uri = f"{uri}?" + "&".join(f"{key}={value}" for key, value in sorted(query.items()))
    raw = raw_of(body)
    request_headers = (
        headers
        if headers is not None
        else signature_headers(secret, method, uri, "" if method == "GET" else raw)
    )
    return engine.receive(
        room_id,
        method=method,
        uri=uri,
        headers=request_headers,
        query=query or {},
        body=None if method == "GET" else raw,
        actor=actor,
        source=source,
    )


def count_rows(store: RecordStore, collection: str, *, room_id: str | None = None) -> int:
    """How many live rows a collection holds.

    ``RecordStore`` deliberately exposes no ``count`` - the count a caller wants is
    over arbitrary JSON, which is what ``count_where`` is for. A row count with no
    filter is the one case it does not cover, so a test that needs it reaches
    through to the audited database rather than adding an API for itself.
    """
    return store.db.count(collection, room_id=room_id)


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database.

    The database path is resolved at lifespan time, so the variable is set before
    the context manager is entered - the same way ``test_features.py`` does it.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf044-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


def http_register(client: TestClient, room_id: str, **overrides: Any):
    return client.post(
        f"{PREFIX}/rooms/{room_id}/endpoint",
        params={"permissions": "edit_workflows"},
        json=endpoint_payload(**overrides),
    )


def http_publish(client: TestClient, room_id: str):
    return client.post(
        f"{PREFIX}/rooms/{room_id}/endpoint/publish",
        params={"permissions": "publish_workflows"},
    )


def http_post(client: TestClient, room_id: str, body: dict[str, Any], **kwargs: Any):
    return client.post(
        f"{PREFIX}/rooms/{room_id}/crm/v1/webhook",
        content=raw_of(body),
        headers={
            "Content-Type": "application/json",
            **signature_headers(SECRET, "POST", uri_for(room_id), raw_of(body)),
        },
        **kwargs,
    )


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


@pytest.fixture()
def http_endpoint(http, http_room):
    return http_register(http, http_room["id"]).json()


@pytest.fixture()
def http_published(http, http_room):
    http_register(http, http_room["id"])
    http_publish(http, http_room["id"])
    return http_room


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-044"
    assert entry["exception_handlers"] == ["WebhookError"]
    assert len(entry["routes"]) == 20


def test_no_feature_failed_to_load(http):
    """A refused route collision or a broken import would show up here."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    body = http.get("/api/features").json()
    mine = {feature["id"] for feature in body["features"] if feature["prefix"] == PREFIX}
    assert mine == {FEATURE_ID}
    for feature in body["features"]:
        if feature["id"] == FEATURE_ID:
            continue
        assert not any(route["path"].startswith(PREFIX) for route in feature["routes"]), (
            f"{feature['id']} also serves under {PREFIX}"
        )


def test_no_other_feature_claims_this_error_type(http):
    """Two features may not map one error type; the host refuses the second.

    Checked for *our* type rather than across the whole product: several features
    on main register distinct classes that happen to share a class name, and a
    name is not a type.
    """
    features = http.get("/api/features").json()["features"]
    claiming = [
        feature["id"] for feature in features if "WebhookError" in feature["exception_handlers"]
    ]
    assert claiming == [FEATURE_ID]


def test_the_module_never_imports_the_app():
    """Dependencies come from ``dsr.deps``; importing the app reintroduces the coupling."""
    text = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "from dsr.deps import StoreDep" in text


def test_the_package_never_opens_the_database_itself():
    """All reads and writes go through the store; no feature may hold a connection."""
    for path in sorted(
        (Path(__file__).resolve().parents[1] / "dsr" / "crm_outbound_webhooks").glob("*.py")
    ):
        text = path.read_text(encoding="utf-8")
        assert "sqlite3" not in text, path.name
        assert "_conn" not in text, path.name


# --------------------------------------------------------------------------- #
# The published vocabulary
# --------------------------------------------------------------------------- #


def test_vocabulary_serves_the_three_researched_authentication_types(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [entry["mode"] for entry in body["auth_modes"]] == list(AUTH_MODES)
    signature = next(e for e in body["auth_modes"] if e["mode"] == "signature")
    assert signature["requires_app_id"] is True
    assert "HubSpot App ID" in signature["rule"]


def test_vocabulary_serves_post_and_get_and_the_https_rule(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [entry["name"] for entry in body["methods"]] == ["POST", "GET"]
    assert body["url"]["scheme"] == "https"
    assert "must begin with HTTPS" in body["url"]["rule"]


def test_vocabulary_serves_both_body_modes_with_the_researched_labels(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert [entry["mode"] for entry in body["body_modes"]] == list(BODY_MODES)
    assert body["body_modes"][0]["label"] == "Include all [object] properties"
    assert body["body_modes"][1]["label"] == "Customize request body"


def test_vocabulary_publishes_the_two_permissions_and_the_app_cap(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert sorted(entry["name"] for entry in body["permissions"]) == sorted(PERMISSIONS)
    assert list(body["setup_permissions"]) == list(SETUP_PERMISSIONS)
    assert list(body["publish_permissions"]) == list(PUBLISH_PERMISSIONS)
    assert body["subscription_limit_per_app"] == 1000
    assert "do not count towards the API rate limit" in body["subscription_quote"]


def test_vocabulary_publishes_the_publish_rule_and_the_objects(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert "published" in body["publish_rule"]
    assert [entry["name"] for entry in body["objects"]] == list(OBJECTS)
    assert body["contract_version"] == CONTRACT_VERSION


def test_vocabulary_serves_every_reason_a_delivery_can_report(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    for reason in (
        "endpoint_not_published",
        "signature_mismatch",
        "api_key_mismatch",
        "bearer_mismatch",
        "deal_unresolved",
        "duplicate_delivery",
    ):
        assert reason in body["reasons"]


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_inferences_are_served_with_their_count(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert body["count"] >= 10


def test_every_inference_is_named_traceable_bounded_and_visible():
    """A judgement call in a comment is one nobody re-reads."""
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"] and entry["basis"]
        assert entry["value"] and entry["why"] and entry["change_it"] and entry["blast_radius"]


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_signature_scheme_is_declared_unsourced_rather_than_quietly_assumed():
    """The one thing these three sources do not publish, said so on the record."""
    entry = by_id("hubspot-request-signature-scheme")
    assert entry is not None
    assert "does not give the canonical string" in entry["basis"]
    assert entry["value"]["signature_header"] == SIGNATURE_HEADER
    assert entry["value"]["timestamp_header"] == SIGNATURE_TIMESTAMP_HEADER


def test_the_permission_reading_is_recorded_as_a_judgement_call():
    entry = by_id("super-admin-does-not-imply-publish")
    assert entry["value"]["super_admin_publishes"] is False
    assert "Failing closed" in entry["why"]


def test_the_unauthenticated_writes_nothing_rule_is_recorded():
    entry = by_id("unauthenticated-deliveries-write-nothing")
    assert entry["value"]["unauthenticated"].startswith("401")
    assert "not one row written" in entry["value"]["unauthenticated"]


def test_the_unsourced_vendors_are_recorded_as_not_built():
    """Salesforce outbound messages and Dataverse notifications could not be sourced."""
    entry = by_id("not-built")
    assert "unsourced, so not modelled" in entry["value"]["salesforce_outbound_messages"]
    assert "unsourced, so not modelled" in entry["value"]["dataverse_service_bus"]


def test_no_salesforce_or_dataverse_outbound_shape_is_modelled():
    """The gaps stay gaps: no model of a mechanism the research could not read."""
    text = " ".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (Path(__file__).resolve().parents[1] / "dsr" / "crm_outbound_webhooks").glob("*.py")
        )
    )
    for absent in ("outboundMessage", "flowMetadata", "ServiceBus", "entityName"):
        assert absent not in text


# --------------------------------------------------------------------------- #
# Step 3: the URL rule
# --------------------------------------------------------------------------- #


def test_a_https_url_is_accepted(endpoint):
    assert endpoint["data"]["url"].startswith("https://")


def test_a_plain_http_url_is_refused(engine, room):
    """*Webhook URLs are restricted to a secure protocol and must begin with HTTPS.*"""
    with pytest.raises(InvalidEndpointUrl) as caught:
        engine.register(
            room["id"],
            endpoint_payload(url="http://rooms.example.com/h"),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "must begin with HTTPS" in str(caught.value)


def test_a_url_that_is_not_a_url_at_all_is_refused(engine, room):
    with pytest.raises(InvalidEndpointUrl):
        engine.register(
            room["id"],
            endpoint_payload(url="rooms.example.com"),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_an_empty_url_is_refused_with_the_crms_own_field_named(engine, room):
    with pytest.raises(InvalidEndpointUrl) as caught:
        engine.register(
            room["id"], endpoint_payload(url=""), permissions="edit_workflows", source=CREATE_SOURCE
        )
    assert "Enter the webhook URL" in str(caught.value)


def test_a_https_url_with_no_host_is_refused(engine, room):
    with pytest.raises(InvalidEndpointUrl):
        engine.register(
            room["id"],
            endpoint_payload(url="https:///path"),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_the_https_rule_holds_on_amendment_too(engine, room, endpoint):
    with pytest.raises(InvalidEndpointUrl):
        engine.amend(
            room["id"],
            {"url": "ftp://x.example"},
            permissions="edit_workflows",
            source=AMEND_SOURCE,
        )


# --------------------------------------------------------------------------- #
# Step 3: the method
# --------------------------------------------------------------------------- #


def test_post_is_the_default_method(endpoint):
    assert endpoint["data"]["method"] == "POST"


def test_get_is_served_because_the_crm_can_send_it(engine, room):
    """*You can send both POST and GET requests using workflows.*"""
    made = engine.register(
        room["id"],
        endpoint_payload(method="GET"),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    assert made["data"]["method"] == "GET"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_any_other_method_is_refused(engine, room, method):
    with pytest.raises(UnsupportedMethod) as caught:
        engine.register(
            room["id"],
            endpoint_payload(method=method),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "both POST and GET" in str(caught.value)


def test_the_method_is_case_insensitive_because_http_methods_are(engine, room):
    made = engine.register(
        room["id"],
        endpoint_payload(method="post"),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    assert made["data"]["method"] == "POST"


# --------------------------------------------------------------------------- #
# Step 3: the three authentication types
# --------------------------------------------------------------------------- #


def test_signature_authentication_stores_the_app_id(endpoint):
    assert endpoint["data"]["auth"]["app_id"] == APP_ID
    assert endpoint["data"]["auth"]["mode"] == "signature"


def test_signature_authentication_without_an_app_id_is_refused(engine, room):
    """*Then, enter your HubSpot App ID.*"""
    with pytest.raises(MissingAppId) as caught:
        engine.register(
            room["id"],
            endpoint_payload(auth={"mode": "signature", "secret": SECRET}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "App ID" in str(caught.value)


@pytest.mark.parametrize("mode", ["api_key", "bearer"])
def test_the_key_based_modes_refuse_an_empty_secret(engine, room, mode):
    with pytest.raises(MissingSecret):
        engine.register(
            room["id"],
            endpoint_payload(auth={"mode": mode, "secret": ""}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_signature_authentication_refuses_an_empty_secret(engine, room):
    with pytest.raises(MissingSecret):
        engine.register(
            room["id"],
            endpoint_payload(auth={"mode": "signature", "app_id": APP_ID, "secret": ""}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_an_authentication_type_outside_the_three_is_refused(engine, room):
    with pytest.raises(UnknownAuthMode) as caught:
        engine.register(
            room["id"],
            endpoint_payload(auth={"mode": "oauth2", "secret": "x"}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "signature" in str(caught.value) and "bearer" in str(caught.value)


def test_an_empty_authentication_type_is_refused(engine, room):
    with pytest.raises(UnknownAuthMode):
        engine.register(
            room["id"],
            endpoint_payload(auth={}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_an_api_key_location_the_room_would_never_look_in_is_refused(engine, room):
    """*Set the value of API key location to Request Header* - two places, not four."""
    with pytest.raises(InvalidAuthSetting) as caught:
        engine.register(
            room["id"],
            endpoint_payload(auth={"mode": "api_key", "secret": API_KEY, "location": "cookie"}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "header" in str(caught.value) and "query" in str(caught.value)


def test_a_bearer_secret_pasted_with_its_prefix_still_works(engine, room):
    """The vendor's documented form is ``Bearer [YOUR_TOKEN]``, and that is what a rep copies."""
    made = engine.register(
        room["id"],
        endpoint_payload(auth={"mode": "bearer", "secret": f"{BEARER_PREFIX}tok-1"}),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    assert made["data"]["auth"]["secret"] == "tok-1"
    assert made["data"]["auth"]["secret_form"] == "bearer_prefixed"


def test_a_bearer_secret_pasted_bare_is_stored_bare(engine, room):
    made = engine.register(
        room["id"],
        endpoint_payload(auth={"mode": "bearer", "secret": "tok-1"}),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    assert made["data"]["auth"]["secret_form"] == "token"


def test_a_bearer_secret_that_is_only_the_prefix_is_refused(engine, room):
    with pytest.raises(MissingSecret):
        engine.register(
            room["id"],
            endpoint_payload(auth={"mode": "bearer", "secret": "Bearer "}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


# --------------------------------------------------------------------------- #
# Step 4: the two body modes
# --------------------------------------------------------------------------- #


def test_include_all_publishes_no_key_table(endpoint):
    assert endpoint["data"]["body"] == {"mode": "include_all", "keys": []}


def test_include_all_with_a_key_list_is_refused(engine, room):
    """*Include all* sends every property, so a key table is a contradiction."""
    with pytest.raises(MalformedBody) as caught:
        engine.register(
            room["id"],
            endpoint_payload(body={"mode": "include_all", "keys": [{"key": "a", "property": "b"}]}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "Customize request body" in str(caught.value)


def test_a_body_mode_outside_the_two_is_refused(engine, room):
    with pytest.raises(UnknownBodyMode):
        engine.register(
            room["id"],
            endpoint_payload(body={"mode": "template"}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_a_customized_body_records_properties_and_static_values(engine, room):
    made = engine.register(
        room["id"],
        endpoint_payload(
            body={
                "mode": "customize",
                "keys": [
                    {"key": "deal_id", "property": "dealid"},
                    {"key": "account", "value": "Northwind Traders"},
                ],
            }
        ),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    keys = made["data"]["body"]["keys"]
    assert keys[0] == {"key": "deal_id", "kind": "property", "property": "dealid", "value": None}
    assert keys[1]["kind"] == "static" and keys[1]["value"] == "Northwind Traders"


def test_a_key_with_neither_a_property_nor_a_static_value_is_refused(engine, room):
    with pytest.raises(MalformedBody) as caught:
        engine.register(
            room["id"],
            endpoint_payload(body={"mode": "customize", "keys": [{"key": "deal_id"}]}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )
    assert "neither a property nor a static value" in str(caught.value)


def test_a_key_with_no_name_is_refused(engine, room):
    with pytest.raises(MalformedBody):
        engine.register(
            room["id"],
            endpoint_payload(body={"mode": "customize", "keys": [{"property": "dealid"}]}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_a_key_that_is_not_an_object_is_refused(engine, room):
    with pytest.raises(MalformedBody):
        engine.register(
            room["id"],
            endpoint_payload(body={"mode": "customize", "keys": ["deal_id"]}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_a_key_table_that_is_not_a_list_is_refused(engine, room):
    with pytest.raises(MalformedBody):
        engine.register(
            room["id"],
            endpoint_payload(body={"mode": "customize", "keys": {"key": "a"}}),
            permissions="edit_workflows",
            source=CREATE_SOURCE,
        )


def test_an_empty_customized_body_is_allowed_and_sends_nothing_named(engine, room):
    made = engine.register(
        room["id"],
        endpoint_payload(body={"mode": "customize"}),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    assert made["data"]["body"]["keys"] == []


# --------------------------------------------------------------------------- #
# The researched permissions
# --------------------------------------------------------------------------- #


def test_setting_up_without_a_permission_is_refused(engine, room):
    with pytest.raises(MissingPermission) as caught:
        engine.register(room["id"], endpoint_payload(), source=CREATE_SOURCE)
    assert "edit_workflows" in str(caught.value)


@pytest.mark.parametrize("permission", SETUP_PERMISSIONS)
def test_either_setup_permission_is_enough(engine, room, permission):
    made = engine.register(
        room["id"], endpoint_payload(), permissions=permission, source=CREATE_SOURCE
    )
    assert made["data"]["status"] == "draft"


def test_the_publish_permission_is_required_to_publish(engine, endpoint):
    """*To publish workflows, users must have Publish permissions for workflows.*"""
    with pytest.raises(MissingPermission) as caught:
        engine.publish(room_id_of(endpoint), permissions="edit_workflows", source=PUBLISH_SOURCE)
    assert "publish_workflows" in str(caught.value)


def test_super_admin_alone_does_not_publish(engine, endpoint):
    """The judgement call ``super-admin-does-not-imply-publish``, made visible."""
    with pytest.raises(MissingPermission):
        engine.publish(endpoint["room_id"], permissions="super_admin", source=PUBLISH_SOURCE)


def test_publishing_with_the_publish_permission_works(engine, endpoint):
    assert (
        engine.publish(endpoint["room_id"], permissions="publish_workflows", source=PUBLISH_SOURCE)[
            "data"
        ]["status"]
        == "published"
    )


def test_unpublishing_needs_the_publish_permission_too(engine, room, published):
    with pytest.raises(MissingPermission):
        engine.unpublish(room["id"], permissions="edit_workflows", source=UNPUBLISH_SOURCE)


def test_permissions_accept_a_list_a_set_or_a_comma_string():
    assert WebhookEngine._require("edit_workflows", SETUP_PERMISSIONS, "x") is None
    assert WebhookEngine._require(["publish_workflows"], PUBLISH_PERMISSIONS, "x") is None
    assert WebhookEngine._require({"super_admin"}, SETUP_PERMISSIONS, "x") is None
    assert WebhookEngine._require({"super_admin": True}, SETUP_PERMISSIONS, "x") is None


def test_permissions_are_case_insensitive():
    assert WebhookEngine._require("Edit_Workflows", SETUP_PERMISSIONS, "x") is None


def room_id_of(record: dict[str, Any]) -> str:
    return str(record["room_id"])


# --------------------------------------------------------------------------- #
# Step 5: Save, then Publish
# --------------------------------------------------------------------------- #


def test_a_registered_endpoint_is_a_draft_not_a_live_one(endpoint):
    assert endpoint["data"]["status"] == "draft"
    assert endpoint["data"]["published_at"] is None


def test_publishing_sets_the_time_and_the_status(published):
    assert published["data"]["status"] == "published"
    assert published["data"]["published_at"]


def test_publishing_twice_is_refused(engine, published):
    with pytest.raises(AlreadyPublished):
        engine.publish(published["room_id"], permissions="publish_workflows", source=PUBLISH_SOURCE)


def test_unpublishing_twice_is_refused(engine, room, published):
    engine.unpublish(room["id"], permissions="publish_workflows", source=UNPUBLISH_SOURCE)
    with pytest.raises(EndpointStateConflict):
        engine.unpublish(room["id"], permissions="publish_workflows", source=UNPUBLISH_SOURCE)


def test_unpublishing_keeps_the_configuration_and_the_deliveries(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    engine.unpublish(room["id"], permissions="publish_workflows", source=UNPUBLISH_SOURCE)
    assert engine.endpoint(room["id"])["data"]["url"]
    assert len(engine.deliveries(room["id"])) == 1


def test_republishing_resumes_where_it_left_off(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    engine.unpublish(room["id"], permissions="publish_workflows", source=UNPUBLISH_SOURCE)
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    report = deliver(engine, room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    assert report["outcome"] == OUTCOME_ACCEPTED


def test_a_second_endpoint_for_a_room_is_refused(engine, room, endpoint):
    """*The room exposes one inbound endpoint per tenant*, so any number of
    automations can target it."""
    with pytest.raises(EndpointExists) as caught:
        engine.register(
            room["id"], endpoint_payload(), permissions="edit_workflows", source=CREATE_SOURCE
        )
    assert "one inbound endpoint per tenant" in str(caught.value)


def test_a_published_endpoint_can_still_be_amended(engine, room, published):
    """The judgement call ``published-endpoints-are-amendable``."""
    amended = engine.amend(
        room["id"],
        {"notes": "the sales team renamed the stage"},
        permissions="edit_workflows",
        source=AMEND_SOURCE,
    )
    assert amended["data"]["notes"] == "the sales team renamed the stage"
    assert amended["data"]["status"] == "published"


def test_an_amendment_revalidates_the_whole_endpoint_not_one_key(engine, room, endpoint):
    """An amendment must not smuggle in a rule another key would have broken."""
    with pytest.raises(InvalidEndpointUrl):
        engine.amend(
            room["id"],
            {"url": "http://x.example"},
            permissions="edit_workflows",
            source=AMEND_SOURCE,
        )
    with pytest.raises(UnknownAuthMode):
        engine.amend(
            room["id"],
            {"auth": {"mode": "kerberos"}},
            permissions="edit_workflows",
            source=AMEND_SOURCE,
        )


def test_amending_a_room_with_no_endpoint_is_refused(engine, room):
    with pytest.raises(NoEndpoint):
        engine.amend(room["id"], {"notes": "x"}, permissions="edit_workflows", source=AMEND_SOURCE)


# --------------------------------------------------------------------------- #
# Step 2: start conditions
# --------------------------------------------------------------------------- #


def test_a_becomes_start_condition_is_recorded(engine, published):
    made = engine.add_automation(
        published["room_id"],
        automation_payload(),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    assert made["data"]["trigger"] == {
        "property": "dealstage",
        "property_name": None,
        "kind": "becomes",
        "equals": CONTRACT_SENT,
    }


def test_a_changes_start_condition_is_recorded(engine, published):
    """The researched second condition: *or a contact property changes*."""
    made = engine.add_automation(
        published["room_id"],
        automation_payload(
            name="Contact changed", trigger={"property": "hs_lead_status", "changed": True}
        ),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    assert made["data"]["trigger"]["kind"] == "changes"


def test_a_trigger_naming_no_property_is_refused(engine, published):
    with pytest.raises(InvalidTrigger) as caught:
        engine.add_automation(
            published["room_id"],
            automation_payload(trigger={"equals": CONTRACT_SENT}),
            permissions="edit_workflows",
            source=AUTOMATION_SOURCE,
        )
    assert "deal stage becomes" in str(caught.value)


def test_a_trigger_that_says_neither_becomes_nor_changes_is_refused(engine, published):
    with pytest.raises(InvalidTrigger) as caught:
        engine.add_automation(
            published["room_id"],
            automation_payload(trigger={"property": "dealstage"}),
            permissions="edit_workflows",
            source=AUTOMATION_SOURCE,
        )
    assert "neither what it becomes nor that it changes" in str(caught.value)


def test_an_automation_with_no_name_is_refused(engine, published):
    with pytest.raises(InvalidRequest):
        engine.add_automation(
            published["room_id"],
            {"trigger": {"property": "dealstage", "equals": CONTRACT_SENT}},
            permissions="edit_workflows",
            source=AUTOMATION_SOURCE,
        )


def test_an_automation_needs_an_endpoint_to_target(engine, room):
    with pytest.raises(NoEndpoint):
        engine.add_automation(
            room["id"], automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
        )


def test_static_values_become_a_customized_body_table(engine, published):
    """*To add a static field, enter the Key and Value.*"""
    made = engine.add_automation(
        published["room_id"],
        automation_payload(static_values=[{"key": "automation", "value": "Contract sent"}]),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    assert made["data"]["body"]["mode"] == "customize"
    assert made["data"]["body"]["keys"][0]["value"] == "Contract sent"


def test_an_automation_with_no_body_overrides_records_that_it_inherits(engine, published):
    """Not a third body mode - the researched pair is closed. This is the absence of
    an override, which means the endpoint's own body mode applies."""
    made = engine.add_automation(
        published["room_id"],
        automation_payload(),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    assert made["data"]["body"] == {"mode": "inherit", "keys": []}


def test_retiring_an_automation_is_a_soft_delete(engine, published, store):
    made = engine.add_automation(
        published["room_id"],
        automation_payload(),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    engine.retire_automation(
        published["room_id"], made["id"], permissions="edit_workflows", source=RETIRE_SOURCE
    )
    assert engine.automations(published["room_id"]) == []
    assert len(engine.automations(published["room_id"], include_retired=True)) == 1
    # The row survives behind the soft delete, which is what keeps the deliveries
    # that named it resolvable.
    assert store.db.get(made["id"], include_deleted=True) is not None
    assert store.db.get(made["id"]) is None


def test_retiring_an_automation_of_another_room_is_refused(engine, store, room, published):
    other = store.create("room", {"name": "Other", "account": "Other"}, actor="dana")
    engine.register(
        other["id"], endpoint_payload(), permissions="edit_workflows", source=CREATE_SOURCE
    )
    made = engine.add_automation(
        other["id"], automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    with pytest.raises(UnknownAutomation):
        engine.retire_automation(
            room["id"], made["id"], permissions="edit_workflows", source=RETIRE_SOURCE
        )


def test_retiring_something_that_is_not_an_automation_is_refused(engine, published, room):
    with pytest.raises(UnknownAutomation):
        engine.retire_automation(
            room["id"], room["id"], permissions="edit_workflows", source=RETIRE_SOURCE
        )


def test_retiring_twice_is_refused(engine, published):
    made = engine.add_automation(
        published["room_id"],
        automation_payload(),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    engine.retire_automation(
        published["room_id"], made["id"], permissions="edit_workflows", source=RETIRE_SOURCE
    )
    with pytest.raises(UnknownAutomation):
        engine.retire_automation(
            published["room_id"], made["id"], permissions="edit_workflows", source=RETIRE_SOURCE
        )


def test_the_automations_counter_on_the_endpoint_stays_true(engine, published):
    room_id = published["room_id"]
    for index in range(3):
        engine.add_automation(
            room_id,
            automation_payload(name=f"Automation {index}"),
            permissions="edit_workflows",
            source=AUTOMATION_SOURCE,
        )
    assert engine.endpoint(room_id)["data"]["automation_count"] == 3


def test_the_counter_drops_when_an_automation_is_retired(engine, published):
    room_id = published["room_id"]
    made = engine.add_automation(
        room_id, automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    engine.retire_automation(
        room_id, made["id"], permissions="edit_workflows", source=RETIRE_SOURCE
    )
    assert engine.endpoint(room_id)["data"]["automation_count"] == 0


# --------------------------------------------------------------------------- #
# The 1,000-per-app cap
# --------------------------------------------------------------------------- #


def test_the_subscription_count_is_per_app_not_per_room(engine, store, published, monkeypatch):
    """*You can create up to 1,000 webhook subscriptions per app* - per app."""
    room_id = published["room_id"]
    other = store.create("room", {"name": "Other", "account": "Other"}, actor="dana")
    engine.register(
        other["id"], endpoint_payload(), permissions="edit_workflows", source=CREATE_SOURCE
    )
    engine.add_automation(
        room_id, automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    engine.add_automation(
        other["id"],
        automation_payload(name="Same app, other room", app_id=APP_ID),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    assert engine.subscriptions_used(APP_ID) == 2
    assert engine.endpoint(other["id"])["data"]["automation_count"] == 1


def test_the_thousand_and_first_subscription_for_an_app_is_refused(
    engine, store, published, monkeypatch
):
    """The cap is enforced, and refused rather than clamped."""
    monkeypatch.setattr("dsr.crm_outbound_webhooks.engine.SUBSCRIPTION_LIMIT_PER_APP", 1)
    room_id = published["room_id"]
    engine.add_automation(
        room_id, automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    with pytest.raises(AutomationLimitReached) as caught:
        engine.add_automation(
            room_id,
            automation_payload(name="Second"),
            permissions="edit_workflows",
            source=AUTOMATION_SOURCE,
        )
    assert "per app" in str(caught.value)


def test_a_different_app_is_not_refused_by_the_first_apps_cap(engine, published, monkeypatch):
    monkeypatch.setattr("dsr.crm_outbound_webhooks.engine.SUBSCRIPTION_LIMIT_PER_APP", 1)
    room_id = published["room_id"]
    engine.add_automation(
        room_id, automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    made = engine.add_automation(
        room_id,
        automation_payload(name="Other app", app_id="9999"),
        permissions="edit_workflows",
        source=AUTOMATION_SOURCE,
    )
    assert made["data"]["app_id"] == "9999"


def test_the_published_limit_is_a_thousand():
    assert SUBSCRIPTION_LIMIT_PER_APP == 1000


# --------------------------------------------------------------------------- #
# The payload reader, on its own
# --------------------------------------------------------------------------- #


def endpoint_record(**overrides: Any) -> dict[str, Any]:
    data = {
        "object": "deals",
        "id_key": "deal_id",
        "stage_key": "stage",
        "body": {"mode": "include_all", "keys": []},
    }
    data.update(overrides)
    return data


def test_include_all_reads_the_properties_object():
    payload = read_payload(deal_payload(), endpoint=endpoint_record())
    assert isinstance(payload, InboundPayload)
    assert payload.body_mode_observed == "include_all"
    assert payload.properties["stage"] == CONTRACT_SENT
    assert payload.external_id == "1001"
    assert payload.stage == CONTRACT_SENT


def test_a_customized_body_reads_as_the_flat_table_it_is():
    payload = read_payload(custom_payload(), endpoint=endpoint_record())
    assert payload.body_mode_observed == "customize"
    assert payload.properties == {
        "deal_id": "1001",
        "stage": CONTRACT_SENT,
        "amount": "42000",
    }
    assert payload.external_id == "1001"


def test_a_control_key_in_a_customized_body_is_not_also_a_property():
    payload = read_payload(custom_payload(automation="Contract sent"), endpoint=endpoint_record())
    assert "automation" not in payload.properties
    assert payload.automation == "Contract sent"


def test_a_body_with_no_version_is_read_as_the_endpoints_version():
    payload = read_payload(
        {"object": "deals", "properties": {"deal_id": "1"}}, endpoint=endpoint_record()
    )
    assert payload.version == CONTRACT_VERSION
    assert payload.version_declared is False


def test_a_body_declaring_the_endpoints_version_is_accepted():
    payload = read_payload(
        {"v": CONTRACT_VERSION, "properties": {"deal_id": "1"}}, endpoint=endpoint_record()
    )
    assert payload.version_declared is True


def test_a_body_declaring_another_version_is_refused():
    with pytest.raises(UnsupportedPayloadVersion) as caught:
        read_payload({"v": 2, "properties": {"deal_id": "1"}}, endpoint=endpoint_record())
    assert "v1" in str(caught.value)


def test_a_version_that_is_not_a_number_is_refused():
    with pytest.raises(UnsupportedPayloadVersion):
        read_payload({"v": "one", "properties": {"deal_id": "1"}}, endpoint=endpoint_record())


def test_a_properties_key_that_is_not_an_object_is_refused():
    with pytest.raises(MalformedPayload):
        read_payload({"properties": "stage"}, endpoint=endpoint_record())


@pytest.mark.parametrize("id_key", ["objectId", "object_id", "id", "recordId", "deal_id"])
def test_an_external_id_is_found_under_any_of_the_reserved_names(id_key):
    payload = read_payload({id_key: "1001", "stage": "x"}, endpoint=endpoint_record())
    assert payload.external_id == "1001"
    assert payload.id_source == id_key


def test_an_id_inside_the_properties_object_is_found_too():
    payload = read_payload({"properties": {"deal_id": "1001"}}, endpoint=endpoint_record())
    assert payload.external_id == "1001"
    assert payload.id_source == "deal_id"


def test_the_id_property_is_configurable_so_no_vendor_field_name_is_hard_coded():
    payload = read_payload(
        {"properties": {"crm_ref": "A-1", "pipeline": "won"}},
        endpoint=endpoint_record(id_key="crm_ref", stage_key="pipeline"),
    )
    assert payload.external_id == "A-1"
    assert payload.stage == "won"


def test_an_empty_id_is_treated_as_absent_rather_than_as_an_empty_record():
    payload = read_payload(
        {"properties": {"deal_id": "", "stage": "x"}}, endpoint=endpoint_record()
    )
    assert payload.external_id is None


def test_a_stage_is_found_in_the_properties_or_at_the_top_level():
    assert read_payload({"properties": {"stage": "a"}}, endpoint=endpoint_record()).stage == "a"
    assert read_payload({"properties": {}, "stage": "b"}, endpoint=endpoint_record()).stage == "b"


def test_a_delivery_id_is_read_from_the_static_value_a_rep_types():
    payload = read_payload(custom_payload(delivery_id="d-1"), endpoint=endpoint_record())
    assert payload.delivery_id == "d-1"


def test_a_configured_body_key_that_the_payload_omits_is_reported():
    """*Customize request body* names the keys, so a missing one is a fact to report."""
    payload = read_payload(
        custom_payload(),
        endpoint=endpoint_record(
            body={
                "mode": "customize",
                "keys": [
                    {"key": "deal_id", "kind": "property", "property": "dealid"},
                    {"key": "region", "kind": "static", "value": "EMEA"},
                ],
            }
        ),
    )
    assert payload.missing_keys == ("region",)


def test_a_configured_body_key_the_payload_carries_is_not_reported_missing():
    payload = read_payload(
        custom_payload(region="EMEA"),
        endpoint=endpoint_record(
            body={
                "mode": "customize",
                "keys": [{"key": "region", "kind": "static", "value": "EMEA"}],
            }
        ),
    )
    assert payload.missing_keys == ()


def test_a_body_sent_as_a_json_string_is_read():
    payload = read_payload(json.dumps(deal_payload()), endpoint=endpoint_record())
    assert payload.stage == CONTRACT_SENT


def test_a_body_sent_as_a_query_string_is_read():
    """A GET has no body, so this is what one looks like."""
    payload = read_payload("deal_id=1001&stage=Contract+Sent", endpoint=endpoint_record())
    assert payload.external_id == "1001"
    assert payload.stage == "Contract Sent"


def test_a_body_that_is_neither_json_nor_a_query_string_is_refused():
    with pytest.raises(MalformedPayload):
        coerce_body("just some words")


def test_a_json_array_body_is_refused():
    with pytest.raises(MalformedPayload) as caught:
        coerce_body("[1, 2, 3]")
    assert "not an object" in str(caught.value)


def test_json_that_does_not_parse_is_refused():
    with pytest.raises(MalformedPayload) as caught:
        coerce_body('{"broken": ')
    assert "not valid JSON" in str(caught.value)


def test_an_empty_body_is_an_empty_mapping():
    assert coerce_body(None) == {}
    assert coerce_body("") == {}
    assert coerce_body(b"") == {}


def test_a_body_of_the_wrong_python_type_is_refused():
    with pytest.raises(MalformedPayload):
        coerce_body(17)


def test_every_property_is_kept_verbatim_even_one_nobody_expected():
    """A field nobody coordinated with us still gets stored."""
    payload = read_payload(
        deal_payload(properties={"a_team_specific_field": {"nested": [1, 2]}, "z": None}),
        endpoint=endpoint_record(),
    )
    assert payload.properties["a_team_specific_field"] == {"nested": [1, 2]}
    assert "z" in payload.properties


def test_the_sample_payload_carries_the_contract_version_and_the_configured_keys():
    body = sample_body(endpoint_record(), deal={"external_id": "1001", "stage": CONTRACT_SENT})
    assert body["v"] == CONTRACT_VERSION
    assert body["object"] == "deals"
    assert body["properties"]["deal_id"] == "1001"
    assert body["properties"]["stage"] == CONTRACT_SENT


def test_the_sample_payload_works_for_an_endpoint_that_has_seen_nothing():
    body = sample_body(endpoint_record())
    assert body["properties"]["stage"]


# --------------------------------------------------------------------------- #
# Signature verification
# --------------------------------------------------------------------------- #


def auth(**overrides: Any) -> dict[str, Any]:
    value = {"mode": "signature", "app_id": APP_ID, "secret": SECRET, "replay_window_seconds": 300}
    value.update(overrides)
    return value


def test_a_correctly_signed_request_authenticates():
    uri, body, moment = uri_for("room_1"), '{"a":1}', stamp()
    ok, reason = verify(
        auth(),
        method="POST",
        uri=uri,
        body=body,
        headers=signature_headers(SECRET, "POST", uri, body, timestamp=moment),
        query={},
    )
    assert (ok, reason) == (True, "")


def test_the_canonical_string_is_method_uri_body_timestamp_in_that_order():
    assert canonical_string("post", "/u", "b", "9") == "POST\n/u\nb\n9"


def test_a_signature_over_a_different_body_is_refused():
    uri, moment = uri_for("room_1"), stamp()
    headers = signature_headers(SECRET, "POST", uri, '{"a":1}', timestamp=moment)
    ok, reason = verify(auth(), method="POST", uri=uri, body='{"a":2}', headers=headers, query={})
    assert (ok, reason) == (False, "signature_mismatch")


def test_a_signature_over_a_different_uri_is_refused():
    body, moment = '{"a":1}', stamp()
    headers = signature_headers(SECRET, "POST", uri_for("room_1"), body, timestamp=moment)
    ok, reason = verify(
        auth(), method="POST", uri=uri_for("room_2"), body=body, headers=headers, query={}
    )
    assert (ok, reason) == (False, "signature_mismatch")


def test_a_signature_over_a_different_method_is_refused():
    body, uri, moment = '{"a":1}', uri_for("room_1"), stamp()
    headers = signature_headers(SECRET, "GET", uri, body, timestamp=moment)
    ok, _ = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert ok is False


def test_a_signature_computed_with_another_secret_is_refused():
    uri, body, moment = uri_for("room_1"), '{"a":1}', stamp()
    headers = signature_headers("not-the-secret", "POST", uri, body, timestamp=moment)
    ok, reason = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert (ok, reason) == (False, "signature_mismatch")


def test_a_missing_signature_header_is_a_different_reason_from_a_wrong_one():
    uri, body = uri_for("room_1"), '{"a":1}'
    ok, reason = verify(
        auth(),
        method="POST",
        uri=uri,
        body=body,
        headers={SIGNATURE_TIMESTAMP_HEADER: stamp()},
        query={},
    )
    assert (ok, reason) == (False, "signature_missing")


def test_a_stale_signature_is_refused():
    uri, body = uri_for("room_1"), '{"a":1}'
    old = stamp()
    stale = str(int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000))
    headers = signature_headers(SECRET, "POST", uri, body, timestamp=old)
    headers[SIGNATURE_TIMESTAMP_HEADER] = stale
    ok, reason = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert (ok, reason) == (False, "signature_stale")


def test_a_signature_from_a_clock_running_fast_is_not_refused_for_it():
    """A signed distance in either direction, because a fast clock is not an attack."""
    uri, body = uri_for("room_1"), '{"a":1}'
    ahead = str(int((datetime.now(timezone.utc) + timedelta(seconds=60)).timestamp() * 1000))
    headers = signature_headers(SECRET, "POST", uri, body, timestamp=ahead)
    ok, _ = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert ok is True


def test_a_timestamp_that_is_not_a_number_is_refused():
    uri, body = uri_for("room_1"), '{"a":1}'
    headers = signature_headers(SECRET, "POST", uri, body, timestamp="yesterday")
    ok, reason = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert (ok, reason) == (False, "signature_stale")


def test_a_hex_encoded_signature_is_accepted_too():
    uri, body, moment = uri_for("room_1"), '{"a":1}', stamp()
    headers = {
        SIGNATURE_HEADER: sign_hex(SECRET, "POST", uri, body, moment),
        SIGNATURE_TIMESTAMP_HEADER: moment,
    }
    ok, _ = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert ok is True


def test_the_signature_header_is_found_whatever_its_case():
    """HTTP header names are case-insensitive, and a sender is a sender."""
    uri, body, moment = uri_for("room_1"), '{"a":1}', stamp()
    headers = {
        "X-HubSpot-Signature-V3": sign(SECRET, "POST", uri, body, moment),
        "x-hubspot-request-timestamp": moment,
    }
    ok, _ = verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})
    assert ok is True


def test_a_replay_window_is_configurable_per_endpoint():
    """A signature 400 seconds old is outside the default window and inside a wider one."""
    uri, body = uri_for("room_1"), '{"a":1}'
    older = str(int((datetime.now(timezone.utc) - timedelta(seconds=400)).timestamp() * 1000))
    headers = signature_headers(SECRET, "POST", uri, body, timestamp=older)
    assert verify(auth(), method="POST", uri=uri, body=body, headers=headers, query={})[0] is False
    assert (
        verify(
            auth(replay_window_seconds=600),
            method="POST",
            uri=uri,
            body=body,
            headers=headers,
            query={},
        )[0]
        is True
    )


# -- the api key ------------------------------------------------------------ #


def api_key_auth(**overrides: Any) -> dict[str, Any]:
    value = {"mode": "api_key", "name": "deal_key", "location": "header", "secret": API_KEY}
    value.update(overrides)
    return value


def test_an_api_key_in_the_named_header_authenticates():
    ok, _ = verify(
        api_key_auth(), method="POST", uri="/u", body="", headers={"deal_key": API_KEY}, query={}
    )
    assert ok is True


def test_an_api_key_header_name_is_found_whatever_its_case():
    """HTTP header names are case-insensitive, so the configured name is too."""
    ok, _ = verify(
        api_key_auth(), method="POST", uri="/u", body="", headers={"DEAL_KEY": API_KEY}, query={}
    )
    assert ok is True


def test_an_api_key_in_a_query_parameter_authenticates():
    ok, _ = verify(
        api_key_auth(location="query"),
        method="POST",
        uri="/u",
        body="",
        headers={},
        query={"deal_key": API_KEY},
    )
    assert ok is True


def test_an_api_key_in_the_query_does_not_authenticate_a_header_endpoint():
    ok, reason = verify(
        api_key_auth(), method="POST", uri="/u", body="", headers={}, query={"deal_key": API_KEY}
    )
    assert (ok, reason) == (False, "api_key_missing")


def test_a_missing_api_key_and_a_wrong_one_are_different_reasons():
    assert (
        verify(api_key_auth(), method="POST", uri="/u", body="", headers={}, query={})[1]
        == "api_key_missing"
    )
    assert (
        verify(
            api_key_auth(), method="POST", uri="/u", body="", headers={"deal_key": "no"}, query={}
        )[1]
        == "api_key_mismatch"
    )


# -- the bearer token ------------------------------------------------------- #


def bearer_auth(**overrides: Any) -> dict[str, Any]:
    value = {"mode": "bearer", "secret": BEARER}
    value.update(overrides)
    return value


def test_a_bearer_token_authenticates():
    ok, _ = verify(
        bearer_auth(),
        method="POST",
        uri="/u",
        body="",
        headers={"Authorization": f"Bearer {BEARER}"},
        query={},
    )
    assert ok is True


def test_the_bearer_scheme_is_case_insensitive_because_the_spec_says_so():
    ok, _ = verify(
        bearer_auth(),
        method="POST",
        uri="/u",
        body="",
        headers={"Authorization": f"bearer {BEARER}"},
        query={},
    )
    assert ok is True


def test_a_missing_authorization_header_is_reported_as_missing():
    ok, reason = verify(bearer_auth(), method="POST", uri="/u", body="", headers={}, query={})
    assert (ok, reason) == (False, "bearer_missing")


def test_an_authorization_header_that_is_not_bearer_is_reported_as_malformed():
    ok, reason = verify(
        bearer_auth(), method="POST", uri="/u", body="", headers={"Authorization": BEARER}, query={}
    )
    assert (ok, reason) == (False, "bearer_malformed")


def test_a_bearer_header_with_no_token_is_malformed():
    ok, reason = verify(
        bearer_auth(),
        method="POST",
        uri="/u",
        body="",
        headers={"Authorization": "Bearer "},
        query={},
    )
    assert (ok, reason) == (False, "bearer_malformed")


def test_a_wrong_bearer_token_is_refused():
    ok, reason = verify(
        bearer_auth(),
        method="POST",
        uri="/u",
        body="",
        headers={"Authorization": "Bearer nope"},
        query={},
    )
    assert (ok, reason) == (False, "bearer_mismatch")


def test_an_authentication_mode_the_build_does_not_know_fails_closed():
    """An endpoint saved by something that skipped the validator must not be open."""
    ok, _ = verify({"mode": "none"}, method="POST", uri="/u", body="", headers={}, query={})
    assert ok is False


# --------------------------------------------------------------------------- #
# Step 6: the delivery
# --------------------------------------------------------------------------- #


def test_a_stage_change_is_applied_to_the_deal_panel(engine, room, published):
    report = deliver(engine, room["id"], deal_payload())
    assert report["outcome"] == OUTCOME_ACCEPTED
    assert report["effect"] == "stage_changed"
    assert report["stage"] == CONTRACT_SENT
    assert report["previous_stage"] is None
    assert report["deal"]["stage"] == CONTRACT_SENT


def test_the_first_delivery_creates_the_deal(engine, room, published, store):
    report = deliver(engine, room["id"], deal_payload())
    assert count_rows(store, COLLECTION_DEAL, room_id=room["id"]) == 1
    assert report["deal_created"] is True


def test_a_later_delivery_updates_the_deal_rather_than_adding_one(engine, room, published, store):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    assert count_rows(store, COLLECTION_DEAL, room_id=room["id"]) == 1
    assert engine.deal(room["id"], "1001")["stage"] == CLOSED_WON
    assert engine.deal(room["id"], "1001")["previous_stage"] == CONTRACT_SENT


def test_every_property_reaches_the_panel_verbatim(engine, room, published):
    deliver(engine, room["id"], deal_payload(properties={"amount": "42000", "owner": "dana@x"}))
    panel = engine.deal(room["id"], "1001")
    assert panel["properties"] == {
        "deal_id": "1001",
        "stage": CONTRACT_SENT,
        "amount": "42000",
        "owner": "dana@x",
    }


def test_a_property_nobody_coordinated_with_us_is_stored_not_dropped(engine, room, published):
    deliver(engine, room["id"], deal_payload(properties={"renewal_terms_months": 24}))
    assert engine.deal(room["id"], "1001")["properties"]["renewal_terms_months"] == 24


def test_a_customized_body_updates_the_panel_too(engine, room, published):
    """*Customize request body* is the other researched body mode, and it is read."""
    report = deliver(engine, room["id"], custom_payload())
    assert report["body_mode_observed"] == "customize"
    assert engine.deal(room["id"], "1001")["stage"] == CONTRACT_SENT


def test_the_configured_and_the_observed_body_mode_are_both_reported(engine, room, published):
    report = deliver(engine, room["id"], custom_payload())
    assert report["body_mode_configured"] == "include_all"
    assert report["body_mode_observed"] == "customize"


# -- the publish gate -------------------------------------------------------- #


def test_a_delivery_to_an_unpublished_endpoint_is_refused(engine, room, endpoint):
    """*Workflows must be **published** to go live.*"""
    with pytest.raises(EndpointNotPublished) as caught:
        deliver(engine, room["id"], deal_payload())
    assert "published" in str(caught.value)


def test_a_refused_delivery_is_still_recorded(engine, room, endpoint, store):
    with pytest.raises(EndpointNotPublished):
        deliver(engine, room["id"], deal_payload())
    rows = engine.deliveries(room["id"])
    assert len(rows) == 1
    assert rows[0]["outcome"] == OUTCOME_REFUSED
    assert rows[0]["reason"] == "endpoint_not_published"
    assert count_rows(store, COLLECTION_DEAL, room_id=room["id"]) == 0


def test_a_refused_delivery_records_the_endpoint_status_at_the_time(engine, room, endpoint):
    with pytest.raises(EndpointNotPublished):
        deliver(engine, room["id"], deal_payload())
    assert engine.deliveries(room["id"])[0]["endpoint_status"] == "draft"


def test_a_delivery_to_a_room_with_no_endpoint_is_refused_and_writes_nothing(engine, room, store):
    with pytest.raises(NoEndpoint):
        deliver(engine, room["id"], deal_payload())
    assert count_rows(store, COLLECTION_DELIVERY) == 0


# -- authentication at the delivery ----------------------------------------- #


def test_an_unsigned_delivery_is_refused_with_401(engine, room, published):
    with pytest.raises(UnauthenticatedDelivery):
        engine.receive(
            room["id"],
            method="POST",
            uri=uri_for(room["id"]),
            body=raw_of(deal_payload()),
            source=POST_SOURCE,
        )


def test_an_unauthenticated_delivery_writes_nothing_at_all(engine, room, published, store):
    """The one refusal that leaves no row, because the sender proved nothing."""
    for _ in range(2):
        with pytest.raises(UnauthenticatedDelivery):
            engine.receive(
                room["id"],
                method="POST",
                uri=uri_for(room["id"]),
                body=raw_of(deal_payload()),
                source=POST_SOURCE,
            )
    assert count_rows(store, COLLECTION_DELIVERY) == 0
    assert count_rows(store, COLLECTION_DEAL) == 0
    assert count_rows(store, COLLECTION_NOTICE) == 0


def test_a_delivery_signed_with_the_wrong_secret_is_refused(engine, room, published, store):
    with pytest.raises(UnauthenticatedDelivery) as caught:
        deliver(engine, room["id"], deal_payload(), secret="not-the-secret")
    assert "signature_mismatch" in str(caught.value)
    assert count_rows(store, COLLECTION_DELIVERY) == 0


def test_a_stale_signature_is_refused_by_the_delivery_path(engine, room, published):
    body = deal_payload()
    uri = uri_for(room["id"])
    stale = str(int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000))
    headers = signature_headers(SECRET, "POST", uri, raw_of(body), timestamp=stale)
    with pytest.raises(UnauthenticatedDelivery) as caught:
        deliver(engine, room["id"], body, headers=headers)
    assert "signature_stale" in str(caught.value)


def test_a_bearer_endpoint_takes_a_bearer_request(engine, store, room):
    engine.register(
        room["id"],
        endpoint_payload(auth={"mode": "bearer", "secret": BEARER}),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    report = deliver(
        engine, room["id"], deal_payload(), headers={"Authorization": f"Bearer {BEARER}"}
    )
    assert report["outcome"] == OUTCOME_ACCEPTED


def test_an_api_key_endpoint_takes_the_key_from_its_query(engine, room):
    engine.register(
        room["id"],
        endpoint_payload(
            auth={"mode": "api_key", "name": "deal_key", "location": "query", "secret": API_KEY}
        ),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    report = deliver(
        engine,
        room["id"],
        deal_payload(),
        uri=f"{uri_for(room['id'])}?deal_key={API_KEY}",
        query={"deal_key": API_KEY},
    )
    assert report["outcome"] == OUTCOME_ACCEPTED


# -- resolution -------------------------------------------------------------- #


def test_a_payload_with_no_id_in_a_single_deal_room_updates_that_deal(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    report = deliver(engine, room["id"], {"properties": {"stage": CLOSED_WON}})
    assert report["outcome"] == OUTCOME_ACCEPTED
    assert report["deal"]["stage"] == CLOSED_WON


def test_a_payload_with_no_id_in_a_multi_deal_room_is_refused(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"deal_id": "1002"}))
    with pytest.raises(UnresolvedDeal) as caught:
        deliver(engine, room["id"], {"properties": {"stage": CLOSED_WON}})
    assert "choose between" in str(caught.value)


def test_the_unresolved_refusal_is_recorded_with_its_reason(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"deal_id": "1002"}))
    with pytest.raises(UnresolvedDeal):
        deliver(engine, room["id"], {"properties": {"stage": CLOSED_WON}})
    refused = [row for row in engine.deliveries(room["id"]) if row["outcome"] == OUTCOME_REFUSED]
    assert [row["reason"] for row in refused] == ["deal_unresolved"]


def test_an_id_no_deal_carries_creates_that_deal(engine, room, published, store):
    """A signed delivery from this room's own endpoint is how a room meets a deal."""
    report = deliver(engine, room["id"], deal_payload(properties={"deal_id": "2002"}))
    assert report["deal_created"] is True
    assert count_rows(store, COLLECTION_DEAL, room_id=room["id"]) == 1


def test_a_deal_in_another_room_is_not_found_by_id(engine, store, room, published):
    other = store.create("room", {"name": "Other", "account": "Other"}, actor="dana")
    engine.register(
        other["id"], endpoint_payload(), permissions="edit_workflows", source=CREATE_SOURCE
    )
    engine.publish(other["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    deliver(engine, room["id"], deal_payload())
    deliver(engine, other["id"], deal_payload())
    deliver(engine, other["id"], deal_payload(properties={"deal_id": "1001", "stage": CLOSED_WON}))
    assert count_rows(store, COLLECTION_DEAL, room_id=room["id"]) == 1
    assert engine.deal(room["id"], "1001")["stage"] == CONTRACT_SENT


def test_the_endpoint_names_which_property_carries_the_id(engine, room):
    engine.register(
        room["id"],
        endpoint_payload(id_key="crm_ref", stage_key="pipeline", body={"mode": "customize"}),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    report = deliver(engine, room["id"], {"crm_ref": "A-1", "pipeline": "won", "seat_count": 40})
    assert report["external_id"] == "A-1"
    assert report["stage"] == "won"
    assert engine.deal(room["id"], "A-1")["properties"]["seat_count"] == 40


def test_a_contacts_endpoint_reads_a_contact_id_by_default(engine, room):
    engine.register(
        room["id"],
        endpoint_payload(
            object="contacts",
            id_key="contact_id",
            stage_key="property",
            auth={"mode": "bearer", "secret": BEARER},
        ),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    report = deliver(
        engine,
        room["id"],
        {"properties": {"contact_id": "c-1", "property": "customer"}},
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert report["external_id"] == "c-1"
    assert report["stage"] == "customer"


# -- payload refusals -------------------------------------------------------- #


def test_a_payload_with_no_properties_is_refused(engine, room, published):
    with pytest.raises(EmptyPayload) as caught:
        deliver(engine, room["id"], {"v": 1, "object": "deals"})
    assert "no properties" in str(caught.value)


def test_a_payload_about_another_object_is_refused(engine, room, published):
    with pytest.raises(ObjectMismatch) as caught:
        deliver(engine, room["id"], deal_payload(object="contacts"))
    assert "contacts" in str(caught.value)


def test_a_body_that_is_not_json_is_refused_and_recorded(engine, room, published):
    with pytest.raises(MalformedPayload):
        deliver(engine, room["id"], "not a payload")
    rows = engine.deliveries(room["id"])
    assert rows[0]["outcome"] == OUTCOME_REFUSED
    assert rows[0]["reason"] == "malformed_payload"


def test_a_payload_declaring_another_version_is_refused_and_recorded(engine, room, published):
    with pytest.raises(UnsupportedPayloadVersion):
        deliver(engine, room["id"], deal_payload(v=2))
    assert engine.deliveries(room["id"])[0]["reason"] == "unsupported_payload_version"


# -- duplicates -------------------------------------------------------------- #


def test_a_retried_delivery_is_answered_200_and_counted(engine, room, published, store):
    first = deliver(engine, room["id"], deal_payload())
    again = deliver(engine, room["id"], deal_payload())
    assert again["outcome"] == OUTCOME_DUPLICATE
    assert again["duplicate_attempts"] == 1
    assert count_rows(store, COLLECTION_DELIVERY, room_id=room["id"]) == 1
    assert again["kept_delivery_id"] == first["id"]


def test_a_third_retry_counts_to_two(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload())
    third = deliver(engine, room["id"], deal_payload())
    assert third["duplicate_attempts"] == 2


def test_a_retry_does_not_notify_the_rep_twice(engine, room, published, store):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload())
    assert count_rows(store, COLLECTION_NOTICE, room_id=room["id"]) == 1


def test_a_static_delivery_id_makes_a_retry_unambiguous(engine, room, published, store):
    """*To add a static field, enter the Key and Value* - this is what it is for."""
    body = deal_payload(delivery_id="d-1")
    deliver(engine, room["id"], body)
    again = deliver(engine, room["id"], body)
    assert again["outcome"] == OUTCOME_DUPLICATE
    assert count_rows(store, COLLECTION_DELIVERY, room_id=room["id"]) == 1


def test_a_static_delivery_id_reused_across_two_different_payloads_is_still_a_duplicate(
    engine, room, published, store
):
    """A static value that repeats means the same delivery, so the first one is kept.

    The alternative - treating the two as distinct because their properties differ -
    would let a rep who pasted a fixed ``delivery_id`` into the body swallow every
    later stage change as a retry, which is worse than the reverse.
    """
    deliver(engine, room["id"], deal_payload(delivery_id="fixed"))
    other = deliver(
        engine, room["id"], deal_payload(delivery_id="fixed", properties={"stage": CLOSED_WON})
    )
    assert other["outcome"] == OUTCOME_DUPLICATE
    assert engine.deal(room["id"], "1001")["stage"] == CONTRACT_SENT
    assert count_rows(store, COLLECTION_DELIVERY, room_id=room["id"]) == 1


def test_different_static_delivery_ids_with_the_same_properties_are_not_duplicates(
    engine, room, published
):
    deliver(engine, room["id"], deal_payload(delivery_id="d-1"))
    again = deliver(engine, room["id"], deal_payload(delivery_id="d-2"))
    assert again["outcome"] == OUTCOME_ACCEPTED
    assert again["effect"] == "stage_unchanged"


def test_two_rooms_never_collide_on_one_fingerprint(engine, store, room, published):
    other = store.create("room", {"name": "Other", "account": "Other"}, actor="dana")
    engine.register(
        other["id"], endpoint_payload(), permissions="edit_workflows", source=CREATE_SOURCE
    )
    engine.publish(other["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    assert deliver(engine, room["id"], deal_payload())["outcome"] == OUTCOME_ACCEPTED
    assert deliver(engine, other["id"], deal_payload())["outcome"] == OUTCOME_ACCEPTED


# -- what a delivery does to the panel and the rep ---------------------------- #


def test_a_stage_change_notifies_the_rep(engine, room, published, store):
    report = deliver(engine, room["id"], deal_payload())
    assert report["notified"] is True
    assert count_rows(store, COLLECTION_NOTICE, room_id=room["id"]) == 1


def test_the_notice_names_the_account_the_stage_and_where_it_came_from(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    notice = engine.notices(room["id"])[0]
    assert (
        notice["summary"] == f"Northwind Traders: deal 1001 moved {CONTRACT_SENT} -> {CLOSED_WON}"
    )
    assert notice["delivery_id"]
    assert notice["read"] is False


def test_a_delivery_that_changes_nothing_notifies_nobody(engine, room, published, store):
    deliver(engine, room["id"], deal_payload())
    report = deliver(engine, room["id"], deal_payload(delivery_id="d-2"))
    assert report["effect"] == "stage_unchanged"
    assert report["notified"] is False
    assert count_rows(store, COLLECTION_NOTICE, room_id=room["id"]) == 1


def test_a_delivery_that_changes_nothing_is_still_recorded(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(delivery_id="d-2"))
    assert len(engine.deliveries(room["id"])) == 2


def test_a_property_change_without_a_stage_is_reported_as_such(engine, room, published):
    report = deliver(engine, room["id"], {"properties": {"deal_id": "1001", "amount": "45000"}})
    assert report["effect"] == "properties_only"
    assert report["notified"] is True


def test_a_delivery_carrying_only_what_it_always_carries_changes_nothing(engine, room, published):
    """Nothing moved, so there is no property change to report either."""
    deliver(engine, room["id"], {"properties": {"deal_id": "1001", "amount": "42000"}})
    report = deliver(
        engine,
        room["id"],
        {"delivery_id": "d-9", "properties": {"deal_id": "1001", "amount": "42000"}},
    )
    assert report["effect"] == "none"
    assert report["notified"] is False


def test_a_property_that_did_not_change_changes_nothing(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    report = deliver(engine, room["id"], deal_payload(delivery_id="d-3"))
    assert report["effect"] == "stage_unchanged"
    assert report["changed_keys"] == []


def test_a_property_a_payload_stopped_sending_is_reported_stale_not_deleted(
    engine, room, published
):
    """A customised body sends only the keys it names, so an absent key is not a deletion."""
    deliver(engine, room["id"], deal_payload(properties={"owner": "dana@x"}))
    report = deliver(
        engine, room["id"], deal_payload(delivery_id="d-4", properties={"amount": "45000"})
    )
    assert "owner" in report["stale_keys"]
    assert "stage" not in report["stale_keys"]
    panel = engine.deal(room["id"], "1001")
    assert panel["properties"]["owner"] == "dana@x"
    assert panel["properties"]["amount"] == "45000"


def test_acknowledging_a_notice_marks_it_read(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    notice_id = engine.notices(room["id"])[0]["id"]
    result = engine.acknowledge(room["id"], {"notice_ids": [notice_id]}, source=ACKNOWLEDGE_SOURCE)
    assert result["acknowledged"] == 1
    assert result["unread"] == 0


def test_acknowledging_naming_nothing_is_refused(engine, room, published):
    with pytest.raises(InvalidRequest):
        engine.acknowledge(room["id"], {}, source=ACKNOWLEDGE_SOURCE)


def test_acknowledging_everything_marks_only_what_is_unread(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    assert (
        engine.acknowledge(room["id"], {"all": True}, source=ACKNOWLEDGE_SOURCE)["acknowledged"]
        == 2
    )
    assert (
        engine.acknowledge(room["id"], {"all": True}, source=ACKNOWLEDGE_SOURCE)["acknowledged"]
        == 0
    )


def test_a_notice_links_back_to_the_delivery_that_made_it(engine, room, published):
    report = deliver(engine, room["id"], deal_payload())
    notice = engine.notices(room["id"])[0]
    assert notice["delivery_id"] == report["id"]
    assert notice["effect"] == "stage_changed"


# -- the automation label ----------------------------------------------------- #


def test_a_payload_naming_a_registered_automation_is_known(engine, room, published):
    engine.add_automation(
        room["id"], automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    report = deliver(engine, room["id"], deal_payload(automation="Contract sent"))
    assert report["automation_known"] is True
    assert report["notes"] == []


def test_a_payload_naming_an_unregistered_automation_is_applied_and_reported(
    engine, room, published
):
    """*Any number of CRM-side automations can target it*, so this is not an allow-list."""
    report = deliver(engine, room["id"], deal_payload(automation="Renewal date moved"))
    assert report["outcome"] == OUTCOME_ACCEPTED
    assert report["automation_known"] is False
    assert "automation_unknown" in report["notes"]


def test_a_payload_naming_a_retired_automation_is_reported_as_retired(engine, room, published):
    made = engine.add_automation(
        room["id"], automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    engine.retire_automation(
        room["id"], made["id"], permissions="edit_workflows", source=RETIRE_SOURCE
    )
    report = deliver(engine, room["id"], deal_payload(automation="Contract sent"))
    assert report["automation_known"] is True
    assert report["automation_retired"] is True


def test_a_disabled_automation_is_treated_as_retired(engine, room, published, store):
    made = engine.add_automation(
        room["id"], automation_payload(), permissions="edit_workflows", source=AUTOMATION_SOURCE
    )
    store.update(made["id"], {"enabled": False}, actor="dana", source=AMEND_SOURCE)
    report = deliver(engine, room["id"], deal_payload(automation="Contract sent"))
    assert report["automation_retired"] is True


# -- the GET delivery ---------------------------------------------------------- #


def test_a_get_delivery_reads_its_properties_from_the_query_string(engine, room):
    engine.register(
        room["id"],
        endpoint_payload(method="GET", auth={"mode": "bearer", "secret": BEARER}),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    report = deliver(
        engine,
        room["id"],
        method="GET",
        query={"deal_id": "1001", "stage": CONTRACT_SENT},
        headers={"Authorization": f"Bearer {BEARER}"},
        source=GET_SOURCE,
    )
    assert report["outcome"] == OUTCOME_ACCEPTED
    assert report["stage"] == CONTRACT_SENT


def test_a_get_delivery_signs_over_an_empty_body(engine, room):
    """A GET has no body, so there is nothing for the signature to cover."""
    engine.register(
        room["id"],
        endpoint_payload(method="GET"),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    uri = f"{uri_for(room['id'])}?deal_id=1001&stage=Contract+Sent"
    headers = signature_headers(SECRET, "GET", uri, "")
    report = deliver(
        engine,
        room["id"],
        method="GET",
        uri=uri,
        query={"deal_id": "1001", "stage": CONTRACT_SENT},
        headers=headers,
        source=GET_SOURCE,
    )
    assert report["outcome"] == OUTCOME_ACCEPTED


def test_a_get_delivery_whose_signature_was_made_over_a_body_is_refused(engine, room):
    engine.register(
        room["id"],
        endpoint_payload(method="GET"),
        permissions="edit_workflows",
        source=CREATE_SOURCE,
    )
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    uri = f"{uri_for(room['id'])}?deal_id=1001"
    headers = signature_headers(SECRET, "GET", uri, "some body")
    with pytest.raises(UnauthenticatedDelivery):
        deliver(
            engine,
            room["id"],
            method="GET",
            uri=uri,
            query={"deal_id": "1001"},
            headers=headers,
            source=GET_SOURCE,
        )


# --------------------------------------------------------------------------- #
# Reading the delivery log
# --------------------------------------------------------------------------- #


def test_deliveries_are_newest_first(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    rows = engine.deliveries(room["id"])
    assert rows[0]["stage"] == CLOSED_WON
    assert rows[1]["stage"] == CONTRACT_SENT


def test_deliveries_can_be_filtered_by_outcome(engine, room, endpoint):
    """A room whose endpoint was saved and never published, so one delivery is refused."""
    with pytest.raises(EndpointNotPublished):
        deliver(engine, room["id"], deal_payload())
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    deliver(engine, room["id"], deal_payload())
    assert len(engine.deliveries(room["id"], outcome=OUTCOME_ACCEPTED)) == 1
    assert len(engine.deliveries(room["id"], outcome=OUTCOME_REFUSED)) == 1


def test_deliveries_can_be_filtered_by_effect(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(delivery_id="d-2"))
    assert len(engine.deliveries(room["id"], effect="stage_changed")) == 1
    assert len(engine.deliveries(room["id"], effect="stage_unchanged")) == 1


def test_deliveries_can_be_filtered_by_the_external_id(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"deal_id": "1002"}))
    assert len(engine.deliveries(room["id"], external_id="1002")) == 1


def test_refused_deliveries_can_be_left_out(engine, room, endpoint):
    with pytest.raises(EndpointNotPublished):
        deliver(engine, room["id"], deal_payload())
    engine.publish(room["id"], permissions="publish_workflows", source=PUBLISH_SOURCE)
    deliver(engine, room["id"], deal_payload())
    assert len(engine.deliveries(room["id"], include_refused=False)) == 1


def test_the_delivery_limit_applies_to_this_room(engine, room, published):
    for index in range(4):
        deliver(engine, room["id"], deal_payload(delivery_id=f"d-{index}"))
    assert len(engine.deliveries(room["id"], limit=2)) == 2


def test_a_delivery_can_be_read_back_by_id(engine, room, published):
    report = deliver(engine, room["id"], deal_payload())
    row = engine.delivery(room["id"], report["id"])
    assert row["payload"]["properties"]["stage"] == CONTRACT_SENT
    assert engine.delivery(room["id"], "nope") is None


def test_the_summary_reports_the_researched_constraints(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    summary = engine.summary(room["id"])
    assert summary["endpoint"]["status"] == "published"
    assert summary["endpoint"]["path"] == uri_for(room["id"])
    assert summary["subscription_limit"] == SUBSCRIPTION_LIMIT_PER_APP
    assert "do not count towards the API rate limit" in summary["rate_limit_note"]
    assert "one transaction and opens no socket" in summary["slow_sender_note"]


def test_the_summary_of_a_room_with_nothing_configured_is_honest(engine, store):
    room = store.create("room", {"name": "Bare", "account": "Bare"}, actor="dana")
    summary = engine.summary(room["id"])
    assert summary["endpoint"]["registered"] is False
    assert summary["deliveries"] == 0


def test_the_deal_panel_can_be_read_by_either_identifier(engine, room, published):
    report = deliver(engine, room["id"], deal_payload())
    assert engine.deal(room["id"], "1001")["id"] == report["deal"]["id"]
    assert engine.deal(room["id"], "missing") is None


def test_the_notice_list_can_show_only_unread(engine, room, published):
    deliver(engine, room["id"], deal_payload())
    deliver(engine, room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    engine.acknowledge(
        room["id"], {"notice_ids": [engine.notices(room["id"])[0]["id"]]}, source=ACKNOWLEDGE_SOURCE
    )
    assert len(engine.notices(room["id"], unread_only=True)) == 1


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_served_over_http(http):
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200


def test_inferences_are_served_over_http(http):
    assert http.get(f"{PREFIX}/inferences").status_code == 200


def test_registering_an_endpoint_over_http_returns_201_and_a_draft(http, http_room):
    response = http_register(http, http_room["id"])
    assert response.status_code == 201
    assert response.json()["data"]["status"] == "draft"


def test_registering_without_the_permission_is_403_with_the_permission_named(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/endpoint", json=endpoint_payload())
    assert response.status_code == 403
    assert response.json()["error"] == "permission_required"
    assert "edit_workflows" in response.json()["detail"]


def test_a_domain_refusal_answers_with_its_own_status_and_code(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/endpoint",
        params={"permissions": "edit_workflows"},
        json=endpoint_payload(url="http://insecure.example"),
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "endpoint_url_must_be_https",
        "detail": response.json()["detail"],
        "status": 400,
    }


def test_reading_the_settings_page_shows_the_url_and_the_secret(http, http_endpoint, http_room):
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/endpoint").json()
    assert body["endpoint"]["url"].startswith("https://")
    assert body["endpoint"]["auth"]["secret"] == SECRET
    assert body["path"] == uri_for(http_room["id"])


def test_reading_the_settings_page_of_a_room_with_no_endpoint_is_404(http, http_room):
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/endpoint").status_code == 404


def test_amending_over_http(http, http_endpoint, http_room):
    response = http.patch(
        f"{PREFIX}/rooms/{http_room['id']}/endpoint",
        params={"permissions": "edit_workflows"},
        json={"notes": "checked with the sales team"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["notes"] == "checked with the sales team"


def test_publishing_and_unpublishing_over_http(http, http_endpoint, http_room):
    assert http_publish(http, http_room["id"]).json()["data"]["status"] == "published"
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/endpoint/unpublish",
        params={"permissions": "publish_workflows"},
    )
    assert response.json()["data"]["status"] == "draft"


def test_publishing_twice_over_http_is_409(http, http_published, http_room):
    response = http_publish(http, http_room["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "endpoint_already_published"


def test_the_sample_preview_says_it_is_a_preview(http, http_endpoint, http_room):
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/endpoint/sample").json()
    assert body["is_preview"] is True
    assert "CRM-side control" in body["note"]
    assert body["uri"] == uri_for(http_room["id"])
    assert body["signature"]["canonical_string"].startswith("POST\n")


def test_the_sample_preview_signature_is_the_one_the_endpoint_accepts(
    http, http_published, http_room
):
    """The preview and the verifier cannot disagree, which is the point of showing it."""
    sample = http.get(f"{PREFIX}/rooms/{http_room['id']}/endpoint/sample").json()
    body = {
        "v": CONTRACT_VERSION,
        "object": "deals",
        "properties": {"deal_id": "1001", "stage": CONTRACT_SENT},
    }
    raw = json.dumps(body, sort_keys=True)
    moment = sample["signature"]["timestamp"]
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(SECRET, "POST", uri_for(http_room["id"]), raw, moment),
            SIGNATURE_TIMESTAMP_HEADER: moment,
        },
    )
    assert response.status_code == 201


def test_automations_can_be_added_listed_and_retired_over_http(http, http_published, http_room):
    created = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/automations",
        params={"permissions": "edit_workflows"},
        json=automation_payload(),
    )
    assert created.status_code == 201
    listed = http.get(f"{PREFIX}/rooms/{http_room['id']}/automations").json()
    assert listed["count"] == 1
    assert listed["subscription_limit"] == 1000
    retired = http.delete(
        f"{PREFIX}/rooms/{http_room['id']}/automations/{created.json()['id']}",
        params={"permissions": "edit_workflows"},
    )
    assert retired.status_code == 200
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/automations").json()["count"] == 0


def test_adding_a_malformed_automation_over_http_is_400(http, http_published, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/automations",
        params={"permissions": "edit_workflows"},
        json={"name": "x", "trigger": {"property": "dealstage"}},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_start_condition"


def test_a_delivery_over_http_is_201_and_applied(http, http_published, http_room):
    response = http_post(http, http_room["id"], deal_payload())
    assert response.status_code == 201
    assert response.json()["effect"] == "stage_changed"
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/deals").json()["count"] == 1


def test_a_retried_delivery_over_http_is_200(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    assert http_post(http, http_room["id"], deal_payload()).status_code == 200


def test_an_unsigned_delivery_over_http_is_401(http, http_published, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook",
        content=raw_of(deal_payload()),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "delivery_unauthenticated"


def test_a_delivery_to_an_unpublished_endpoint_over_http_is_409(http, http_endpoint, http_room):
    response = http_post(http, http_room["id"], deal_payload())
    assert response.status_code == 409
    assert response.json()["error"] == "endpoint_not_published"


def test_a_get_delivery_over_http_is_taken(http, http_room):
    http_register(http, http_room["id"], method="GET", auth={"mode": "bearer", "secret": BEARER})
    http_publish(http, http_room["id"])
    response = http.get(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook",
        params={"deal_id": "1001", "stage": CONTRACT_SENT},
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert response.status_code == 201
    assert response.json()["stage"] == CONTRACT_SENT


def test_a_get_delivery_over_http_with_no_credential_is_401(http, http_room):
    http_register(http, http_room["id"], method="GET", auth={"mode": "bearer", "secret": BEARER})
    http_publish(http, http_room["id"])
    response = http.get(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook", params={"deal_id": "1001"}
    )
    assert response.status_code == 401


def test_a_body_whose_keys_arrive_in_another_order_still_authenticates(
    http, http_published, http_room
):
    """The room verifies the bytes it was sent, not its own re-serialisation."""
    body = deal_payload()
    shuffled = json.dumps(body, sort_keys=False, indent=None)
    # The sender signed the sorted form; send the bytes it signed.
    raw = json.dumps(body, sort_keys=True)
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            **signature_headers(SECRET, "POST", uri_for(http_room["id"]), raw),
        },
    )
    assert response.status_code == 201
    assert shuffled


def test_the_deal_panel_is_readable_over_http(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    listed = http.get(f"{PREFIX}/rooms/{http_room['id']}/deals").json()
    assert listed["count"] == 1
    assert listed["deals"][0]["stage"] == CONTRACT_SENT
    one = http.get(f"{PREFIX}/rooms/{http_room['id']}/deals/1001").json()
    assert one["stage"] == CONTRACT_SENT
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/deals/nope").status_code == 404


def test_the_delivery_log_is_readable_over_http_with_its_reasons(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    http_post(http, http_room["id"], deal_payload(properties={"stage": CLOSED_WON}))
    listed = http.get(f"{PREFIX}/rooms/{http_room['id']}/deliveries").json()
    assert listed["count"] == 2
    assert listed["by_outcome"] == {"accepted": 2}
    one = http.get(
        f"{PREFIX}/rooms/{http_room['id']}/deliveries/{listed['deliveries'][0]['id']}"
    ).json()
    assert one["payload"]["properties"]["stage"] == CLOSED_WON
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/deliveries/nope").status_code == 404


def test_a_refused_delivery_appears_in_the_log_with_its_reason(http, http_endpoint, http_room):
    http_post(http, http_room["id"], deal_payload())
    listed = http.get(f"{PREFIX}/rooms/{http_room['id']}/deliveries").json()
    assert listed["deliveries"][0]["outcome"] == OUTCOME_REFUSED
    assert listed["deliveries"][0]["reason"] == "endpoint_not_published"


def test_notices_are_readable_and_acknowledgable_over_http(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    listed = http.get(f"{PREFIX}/rooms/{http_room['id']}/notices").json()
    assert listed["count"] == 1
    assert listed["unread"] == 1
    acknowledged = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/notices/acknowledge",
        json={"notice_ids": [listed["notices"][0]["id"]]},
    ).json()
    assert acknowledged == {"room_id": http_room["id"], "acknowledged": 1, "unread": 0}


def test_acknowledging_nothing_over_http_is_400(http, http_published, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/notices/acknowledge", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_the_summary_is_readable_over_http(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/summary").json()
    assert body["deliveries"] == 1
    assert body["by_outcome"] == {"accepted": 1}
    assert body["endpoint"]["contract_version"] if "contract_version" in body["endpoint"] else True


def test_every_writing_route_is_reachable_and_named_in_our_own_prefix(http):
    served = {
        (route["path"], tuple(route["methods"]))
        for feature in http.get("/api/features").json()["features"]
        if feature["id"] == FEATURE_ID
        for route in feature["routes"]
    }
    for path, method in (
        (f"{PREFIX}/rooms/{{room_id}}/endpoint", ("POST",)),
        (f"{PREFIX}/rooms/{{room_id}}/endpoint", ("PATCH",)),
        (f"{PREFIX}/rooms/{{room_id}}/endpoint/publish", ("POST",)),
        (f"{PREFIX}/rooms/{{room_id}}/endpoint/unpublish", ("POST",)),
        (f"{PREFIX}/rooms/{{room_id}}/automations", ("POST",)),
        (f"{PREFIX}/rooms/{{room_id}}/automations", ("GET",)),
        (f"{PREFIX}/rooms/{{room_id}}/automations/{{automation_id}}", ("DELETE",)),
        (f"{PREFIX}/rooms/{{room_id}}/crm/v1/webhook", ("POST",)),
        (f"{PREFIX}/rooms/{{room_id}}/crm/v1/webhook", ("GET",)),
        (f"{PREFIX}/rooms/{{room_id}}/notices/acknowledge", ("POST",)),
    ):
        assert (path, method) in served, f"{method} {path} is not mounted"


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_no_route_interpolates_a_concrete_id_into_its_audit_source():
    """The audit row must name the *route*, not one of the thousands of paths onto it."""
    text = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    concrete = [
        line.strip()
        for line in text.splitlines()
        if "source=f" in line
        and re.search(r"(?<!\{)\{(room_id|automation_id|delivery_id|deal_id)\}", line)
    ]
    assert not concrete, "a route interpolates a concrete id into its source: " + "; ".join(
        concrete
    )


def test_every_source_the_feature_module_passes_names_its_own_prefix():
    """A source that does not start with this feature's prefix cannot be its route."""
    text = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "source=f" in line:
            assert "{router.prefix}" in line, line.strip()


def test_the_prefix_is_built_from_the_router_not_retyped(http):
    """The demo data's sources come from one constant, and the router's own prefix."""
    module = load_feature(MODULE)
    assert module.PREFIX == module.router.prefix
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert '"/api/wf-044"' in text


def test_nothing_records_a_vendor_url_as_the_audit_source(http, http_published, http_room):
    """The researched flow is inbound to the room; this product is not a CRM proxy."""
    http_post(http, http_room["id"], deal_payload())
    for entry in http.get("/api/audit", params={"limit": 500}).json()["entries"]:
        source = (entry.get("source") or "").lower()
        assert "hubspot" not in source
        assert "crm/v3" not in source
        assert "https://" not in source


def _matches_registered_route(source: str, served: list[dict[str, Any]]) -> bool:
    parts = source.split(" ", 1)
    if len(parts) != 2:
        return False
    method, path = parts
    actual = [segment for segment in path.split("/") if segment]
    for route in served:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=True)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The branch's central guarantee, checked against the route table the host reports.

    The same class of bug has shipped in this codebase before: a feature's audit
    log kept naming a path the app had stopped serving.
    """
    http_register(http, http_room["id"])
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/endpoint",
        params={"permissions": "edit_workflows"},
        json={"notes": "checked"},
    )
    http_publish(http, http_room["id"])
    automation = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/automations",
        params={"permissions": "edit_workflows"},
        json=automation_payload(),
    )
    http_post(http, http_room["id"], deal_payload(automation="Contract sent"))
    http.delete(
        f"{PREFIX}/rooms/{http_room['id']}/automations/{automation.json()['id']}",
        params={"permissions": "edit_workflows"},
    )
    http.post(f"{PREFIX}/rooms/{http_room['id']}/notices/acknowledge", json={"all": True})
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/endpoint/unpublish",
        params={"permissions": "publish_workflows"},
    )

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the
    # rows this feature's HTTP layer produced are in scope here.
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}
    assert ours, f"no wf-044 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_delivery_route_audits_under_its_own_route(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    entries = http.get("/api/audit", params={"collection": COLLECTION_DELIVERY}).json()["entries"]
    assert entries[0]["source"] == POST_SOURCE


def test_the_get_delivery_route_audits_under_its_own_route(http, http_room):
    http_register(http, http_room["id"], method="GET", auth={"mode": "bearer", "secret": BEARER})
    http_publish(http, http_room["id"])
    http.get(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook",
        params={"deal_id": "1001", "stage": CONTRACT_SENT},
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    entries = http.get("/api/audit", params={"collection": COLLECTION_DELIVERY}).json()["entries"]
    assert entries[0]["source"] == GET_SOURCE


def test_the_endpoint_route_audits_under_its_own_route(http, http_room):
    http_register(http, http_room["id"])
    entries = http.get("/api/audit", params={"collection": COLLECTION_ENDPOINT}).json()["entries"]
    assert entries[0]["source"] == CREATE_SOURCE


def test_an_unauthenticated_delivery_leaves_no_audit_row_behind(http, http_published, http_room):
    """The refusal that writes nothing, checked through the audit log itself."""
    before = http.get("/api/audit", params={"limit": 500}).json()["count"]
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/crm/v1/webhook",
        content=raw_of(deal_payload()),
        headers={"Content-Type": "application/json"},
    )
    assert http.get("/api/audit", params={"limit": 500}).json()["count"] == before


def test_the_delivery_is_written_with_the_crm_as_its_actor(http, http_published, http_room):
    http_post(http, http_room["id"], deal_payload())
    entries = http.get("/api/audit", params={"collection": COLLECTION_DELIVERY}).json()["entries"]
    assert entries[0]["actor"] == "crm"


# --------------------------------------------------------------------------- #
# One transaction per delivery
# --------------------------------------------------------------------------- #


def test_a_delivery_writes_its_deal_notice_and_delivery_together(engine, room, published, store):
    """A slow endpoint makes the CRM's workflow action slow, so a half-applied
    delivery is the one outcome this package is shaped to avoid."""
    report = deliver(engine, room["id"], deal_payload())
    entries = store.audit(collection=COLLECTION_DELIVERY, limit=10)
    assert entries
    assert store.audit(collection=COLLECTION_DEAL, limit=10)
    assert store.audit(collection=COLLECTION_NOTICE, limit=10)
    assert report["id"]


def test_a_delivery_rolls_back_whole_when_anything_in_it_fails(
    engine, room, published, store, monkeypatch
):
    """The audit row and the change it describes commit together or not at all."""
    from dsr.crm_outbound_webhooks import engine as engine_module

    def explode(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("the notice could not be written")

    monkeypatch.setattr(engine_module.WebhookEngine, "_notice_data", explode)
    with pytest.raises(RuntimeError):
        deliver(engine, room["id"], deal_payload())
    assert count_rows(store, COLLECTION_DEAL) == 0
    assert count_rows(store, COLLECTION_NOTICE) == 0
    assert count_rows(store, COLLECTION_DELIVERY) == 0


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_rooms(store: RecordStore) -> list[tuple[str, str]]:
    """Demo rooms in the shape ``backend/seed.py`` passes: ``[(room_id, account)]``."""
    made = []
    for name, account in (
        ("Northwind Traders - Enterprise Evaluation", "Northwind Traders"),
        ("Contoso Health - Security Review", "Contoso Health"),
    ):
        record = store.create("room", {**ROOM, "name": name, "account": account}, actor="dana")
        made.append((record["id"], account))
    return made


def run_seed(tmp_path, rooms=None):
    # `tmp_path / "a"` does not exist, and sqlite3.connect does not create
    # intermediate directories - the same trap `backend/seed.py` documents.
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = AuditedDatabase(tmp_path / "seeded.db")
    store = RecordStore(db)
    module = load_feature(MODULE)
    given = seed_rooms(store) if rooms is None else rooms
    summary = module.seed(
        db, {"room_ids": given, "now": datetime.now(timezone.utc), "rng": random.Random("wf044")}
    )
    return summary, store, db


def test_the_seed_produces_a_published_endpoint_and_real_deliveries(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    assert "published, signature-authenticated endpoint" in summary
    assert count_rows(store, COLLECTION_ENDPOINT) == 4
    assert count_rows(store, COLLECTION_AUTOMATION) == 2
    assert count_rows(store, COLLECTION_DELIVERY) >= 8
    assert count_rows(store, COLLECTION_DEAL) >= 4


def test_the_seed_produces_the_states_that_are_not_all_successes(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    refused = store.find(COLLECTION_DELIVERY, {"outcome": OUTCOME_REFUSED}, limit=20)
    reasons = {row["data"]["reason"] for row in refused}
    assert "endpoint_not_published" in reasons
    assert "deal_unresolved" in reasons
    assert "endpoint_not_published" in summary
    assert "deal_unresolved" in summary


def test_the_seed_shows_a_retry_counted_rather_than_stored_twice(tmp_path):
    _, store, _ = run_seed(tmp_path)
    counted = [row for row in store.find(COLLECTION_DELIVERY, {"duplicate_attempts": 1}, limit=20)]
    assert counted
    assert count_rows(store, COLLECTION_NOTICE) < count_rows(store, COLLECTION_DELIVERY)


def test_the_seed_shows_a_delivery_that_changed_nothing(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    assert store.count_where(COLLECTION_DELIVERY, {"effect": "stage_unchanged"}) >= 1
    assert "notified nobody" in summary


def test_the_seed_shows_an_automation_this_room_never_registered(tmp_path):
    _, store, _ = run_seed(tmp_path)
    rows = store.find(COLLECTION_DELIVERY, {"automation_known": False}, limit=20)
    assert rows
    assert "automation_unknown" in rows[0]["data"]["notes"]


def test_the_seed_shows_a_retired_automation_named_by_a_delivery(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    rows = store.find(COLLECTION_DELIVERY, {"automation_retired": True}, limit=20)
    assert rows
    assert "naming a retired automation (retired)" in summary


def test_the_seed_shows_all_three_researched_authentication_types(tmp_path):
    _, store, _ = run_seed(tmp_path)
    modes = {row["data"]["auth"]["mode"] for row in store.list(COLLECTION_ENDPOINT, limit=20)}
    assert modes == {"signature", "bearer", "api_key"}


def test_the_seed_shows_both_researched_body_modes(tmp_path):
    _, store, _ = run_seed(tmp_path)
    modes = {row["data"]["body"]["mode"] for row in store.list(COLLECTION_ENDPOINT, limit=20)}
    assert modes == {"include_all", "customize"}


def test_the_seed_shows_both_researched_methods(tmp_path):
    _, store, _ = run_seed(tmp_path)
    methods = {row["data"]["method"] for row in store.list(COLLECTION_ENDPOINT, limit=20)}
    assert methods == {"POST", "GET"}


def test_the_seed_shows_a_configured_body_key_that_was_not_sent(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    rows = store.find(COLLECTION_DELIVERY, {"body_mode_observed": "include_all"}, limit=20)
    assert rows
    assert "1 body key missing" in summary


def test_the_seed_leaves_one_notice_acknowledged_and_the_rest_unread(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    assert store.count_where(COLLECTION_NOTICE, {"read": True}) == 1
    assert "unread notice(s)" in summary


def test_the_seed_counts_against_the_app_not_the_room(tmp_path):
    summary, store, _ = run_seed(tmp_path)
    assert "/1000 webhook subscriptions used" in summary


def test_the_seed_copes_with_a_context_carrying_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "empty.db")
    module = load_feature(MODULE)
    assert "created for the per-room states" in module.seed(db, {"room_ids": []})
    db.close()


def test_the_seed_copes_with_a_bare_room_id_rather_than_a_tuple(tmp_path):
    db = AuditedDatabase(tmp_path / "bare.db")
    store = RecordStore(db)
    module = load_feature(MODULE)
    room = store.create("room", ROOM, actor="dana")
    summary = module.seed(db, {"room_ids": [room["id"]]})
    assert "1 room(s) from the seeder" in summary
    db.close()


def test_the_seed_writes_through_the_audited_store_and_names_its_own_routes(tmp_path):
    _, store, _ = run_seed(tmp_path)
    sources = {row["source"] for row in store.audit(limit=500) if row["source"]}
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}
    assert ours
    for source in ours:
        assert re.match(
            rf"^(GET|POST|PATCH|DELETE) {re.escape(PREFIX)}/rooms/\{{\w+\}}(/\w+)*(/\{{\w+\}})?$",
            source,
        ), source


def test_the_seed_opens_no_socket(tmp_path, monkeypatch):
    """A function that opens a socket to a vendor API is not a feature."""
    import socket

    def refuse(*args: Any, **kwargs: Any):  # pragma: no cover - must never run
        raise AssertionError("the seed opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    run_seed(tmp_path / "nosocket")
