"""Tests for WF-026: write DSR events into the seller activity feed.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-026.md``, and each section
below names which one it is pinning:

* the app-scoped event name ``<app identifier>:<event id>`` and the example
  ``my-app:my-event``;
* the JSON:API ``event`` create - ``name``, ``externalUrl``, an *optional* ``body``,
  and a ``prospect`` relationship - reproduced key for key;
* the ``{{prospect}}`` placeholder in a template that is configured in the
  developer portal and therefore **never sent**;
* localized descriptions configured as templates;
* the deep link back into the DSR;
* the ``mailing*`` webhook resources, ``payloadVersion: 2``, the
  ``Outreach-Webhook-Signature`` header, the 5 second timeout, and the
  ``beforeUpdate`` block;
* and the statement that shapes the whole inbound half: **"Outreach does not retry
  webhook deliveries upon receiving any of the Status Codes including 500 Internal
  Server Error and 429 Too Many Requests."**

The parts the research does *not* fix are the design inferences, and they are
tested as inferences: named, bounded, and changeable in one place.

Delivery is driven through a fake transport, so the retry ladder and the request
headers are asserted without a socket and without a network flake.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.outreach_feed import webhooks
from dsr.outreach_feed.delivery import (
    BODY_SAMPLE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    REDACTED,
    RETRYABLE_STATUS,
    PostResult,
    post_json,
    redact_headers,
)
from dsr.outreach_feed.engine import (
    APP_COLLECTION,
    DELIVERY_COLLECTION,
    DELIVERY_STATUSES,
    EVENT_TYPE_COLLECTION,
    PROSPECT_COLLECTION,
    SIGNAL_COLLECTION,
    SKIP_REASONS,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    FeedPublisher,
    app_summary,
    card_text,
    delivery_key,
    delivery_summary,
    matches,
    normalise_stream_event,
)
from dsr.outreach_feed.errors import (
    AppError,
    EventNameError,
    EventTypeError,
    FeedError,
    FeedNotConfiguredError,
    ProspectLinkError,
    SignatureError,
    TemplateError,
    UnknownRoom,
)
from dsr.outreach_feed.inferences import INFERENCES, SOURCED_QUOTES
from dsr.outreach_feed.vocabulary import (
    EVENT_FIELDS,
    EVENT_NAME_FORMAT,
    EVENT_RESOURCE_TYPE,
    EVENTS_ENDPOINT,
    EXAMPLE_EVENT_NAME,
    INTENT_RESOURCES,
    KNOWN_PLACEHOLDERS,
    MAILING_FAMILY,
    MAILING_RESOURCES,
    PAYLOAD_VERSION,
    PROSPECT_PLACEHOLDER,
    PROSPECT_RESOURCE_TYPE,
    SENDER_TIMEOUT_SECONDS,
    SIGNATURE_HEADER,
    WEBHOOKS_ENDPOINT,
    build_event_payload,
    describe_adjacent_surfaces,
    describe as describe_vocabulary,
    parse_event_name,
    placeholders_in,
    require_absolute_url,
    require_event_name,
    require_localizations,
    require_template,
    room_deep_link,
    unknown_placeholders,
)
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-026"

MODULE = "wf026_write_dsr_events_into_the_seller_activ"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/publish"

#: A room payload with nothing enforced on it but the fields this workflow reads.
ROOM = {
    "name": "Northwind — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
    "links": {"live": "https://rooms.example/northwind"},
}

APP = {
    "app_identifier": "dsr",
    "token": "s2s-token-abc123",
    "webhook_secret": "whsec-xyz",
    "room_base_url": "https://rooms.example/r",
    "enabled": True,
}

EVENT_TYPE = {
    "name": "dsr:room-viewed",
    "template": "{{prospect}} opened the digital sales room",
    "body": "Opened the room",
    "actions": ["viewed"],
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class FakeTransport:
    """Records every call and replays a scripted list of results."""

    def __init__(self, *results: PostResult) -> None:
        self.calls: list[dict] = []
        self.scripted = list(results)

    def post(self, url, body, headers, timeout) -> PostResult:
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body),
                "raw": body,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if self.scripted:
            return self.scripted.pop(0)
        return PostResult(ok=True, status=201, body="{}")


def ok() -> PostResult:
    return PostResult(ok=True, status=201, body="{}")


def rate_limited() -> PostResult:
    return PostResult(ok=False, status=429, body="slow down", error="HTTP 429", retryable=True, retry_after=0)


def server_error() -> PostResult:
    return PostResult(ok=False, status=503, error="HTTP 503", retryable=True)


def not_found() -> PostResult:
    return PostResult(ok=False, status=404, error="HTTP 404", retryable=False)


def bad_request() -> PostResult:
    return PostResult(ok=False, status=400, body="nope", error="HTTP 400", retryable=False)


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf026.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def transport():
    return FakeTransport()


def engine(store: RecordStore, transport: FakeTransport | None = None) -> FeedPublisher:
    """A :class:`FeedPublisher` that never sleeps and never opens a socket.

    Retry backoff is asserted through the recorded attempt count, not through
    wall-clock time, so a retry test costs the suite nothing.
    """
    return FeedPublisher(
        store, transport=transport or FakeTransport(), backoff=0, sleep=lambda _s: None
    )


@pytest.fixture()
def feed(store, transport):
    return engine(store, transport)


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def app_row(feed):
    return feed.register_app(APP, actor="dana", source=SOURCE)


@pytest.fixture()
def event_type(feed, app_row):
    return feed.create_event_type(
        EVENT_TYPE | {"app_id": app_row["id"]}, actor="dana", source=SOURCE
    )


@pytest.fixture()
def linked(feed, room):
    return feed.link_prospect(
        room["id"], {"prospect_id": "p_123", "label": "Procurement lead"}, actor="dana", source=SOURCE
    )


@pytest.fixture()
def quiet(store, room):
    """A room with one ``viewed`` event and nothing else configured."""
    store.create(
        "activity",
        {"person": "buyer@northwind.example", "action": "viewed", "target": "Enterprise Overview Deck"},
        room_id=room["id"],
        actor="system",
    )
    return room


def signed(secret: str, payload) -> dict[str, str]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {SIGNATURE_HEADER: webhooks.compute_signature(secret, body)}


def body_of(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch, transport):
    """A client over a temporary database, with the engine faked at the transport."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf026_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        app.dependency_overrides[load_feature(MODULE).get_publisher] = (
            lambda: engine(client.app.state.store, transport)
        )
        try:
            yield client
        finally:
            app.dependency_overrides.clear()
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f for f in http.get("/api/features").json()["features"] if f["id"] == "wf-026-write-dsr-events-into-the-seller-activ"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-026"
    assert entry["exception_handlers"] == [
        "FeedError",
        "FeedNotConfiguredError",
        "SignatureError",
        "UnknownRoom",
    ]
    assert len(entry["routes"]) == 24


def test_no_other_feature_failed_to_load_because_of_this_one(http):
    assert http.get("/api/features").json()["failed_count"] == 0


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}
    assert len(mine) == 24
    assert not mine & others


def test_room_scoped_paths_stay_room_scoped(http):
    """The brief asks for this explicitly: ``/rooms/<room_id>/...`` throughout."""
    templates = {
        route["path"]
        for feature in http.get("/api/features").json()["features"]
        if feature["id"] == "wf-026-write-dsr-events-into-the-seller-activ"
        for route in feature["routes"]
        if "room" in route["path"]
    }
    assert templates == {
        f"{PREFIX}/rooms/{{room_id}}/feed",
        f"{PREFIX}/rooms/{{room_id}}/preview",
        f"{PREFIX}/rooms/{{room_id}}/prospects",
        f"{PREFIX}/rooms/{{room_id}}/prospects/{{link_id}}",
        f"{PREFIX}/rooms/{{room_id}}/publish",
        f"{PREFIX}/rooms/{{room_id}}/signals",
    }


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-026-write-dsr-events-into-the-seller-activ"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert load_feature(MODULE).FEATURE["id"] in text
    assert f"id: '{load_feature(MODULE).FEATURE['id']}'" in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_domain_package_does_not_import_another_feature():
    """Isolation is the point of the host; a domain package must keep it too.

    Checked on the parsed import statements rather than the raw text, so a
    docstring that *names* another package to explain why it does not borrow from
    it is not mistaken for a dependency.
    """
    import ast

    package = Path(load_feature(MODULE).__file__).parent.parent / "outreach_feed"
    forbidden = {"dsr.crm", "dsr.analytics", "dsr.search", "dsr.publishing", "dsr.rules", "dsr.api"}
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name.startswith(bad) for bad in forbidden), (
                    f"{module.name} imports {name}"
                )


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_write_endpoint_is_the_one_the_research_quotes():
    assert EVENTS_ENDPOINT == "https://api.outreach.io/api/v2/events"
    assert WEBHOOKS_ENDPOINT == "https://api.outreach.io/api/v2/webhooks"


def test_event_names_are_app_scoped():
    """[sourced] "Event names are app-scoped (``<app identifier>:<event id>``)"."""
    assert EVENT_NAME_FORMAT == "<app identifier>:<event id>"
    assert parse_event_name(EXAMPLE_EVENT_NAME) == ("my-app", "my-event")
    assert parse_event_name("dsr:room-viewed") == ("dsr", "room-viewed")


@pytest.mark.parametrize(
    "name",
    ["nocolon", ":leading", "trailing:", "two:colons:here", "has space:x", "  ", "", None, 7, "a:" + "b" * 0],
)
def test_a_malformed_event_name_is_refused_with_the_format_in_the_message(name):
    with pytest.raises(EventNameError) as excinfo:
        parse_event_name(name)
    assert "<app identifier>:<event id>" in str(excinfo.value)


def test_an_event_name_scoped_to_an_unregistered_app_is_refused():
    with pytest.raises(EventNameError) as excinfo:
        require_event_name("other:thing", app_identifiers=["dsr"])
    assert "not registered here" in str(excinfo.value)
    assert "dsr" in str(excinfo.value)


def test_an_event_name_scoped_to_a_registered_app_is_accepted():
    assert require_event_name("dsr:thing", app_identifiers=["dsr", "other"]) == "dsr:thing"


def test_the_only_documented_placeholder_is_the_prospect_one():
    """[sourced] "you can use the {{prospect}} placeholder which Outreach will
    replace with a link to the prospect"."""
    assert KNOWN_PLACEHOLDERS == ("{{prospect}}",)
    assert PROSPECT_PLACEHOLDER == "{{prospect}}"
    assert placeholders_in("a {{prospect}} b {{ c }}") == ["{{prospect}}", "{{c}}"]
    assert unknown_placeholders("{{prospect}} saw {{other}}") == ["{{other}}"]


def test_a_template_carrying_an_undocumented_placeholder_is_refused():
    """A rep would read literal braces in their own activity feed."""
    with pytest.raises(TemplateError) as excinfo:
        require_template("{{person}} opened the room")
    assert "{{person}}" in str(excinfo.value)
    assert "{{prospect}}" in str(excinfo.value)


def test_a_template_is_required_and_must_be_a_string():
    with pytest.raises(TemplateError):
        require_template(None)
    with pytest.raises(TemplateError):
        require_template(42)
    assert require_template("  {{prospect}} did a thing  ") == "{{prospect}} did a thing"


def test_an_empty_template_is_allowed_when_not_required():
    assert require_template("", required=False) == ""


def test_localized_descriptions_are_templates_too():
    """[sourced] "localized descriptions are configured as templates"."""
    assert require_localizations({"fr": "{{prospect}} a ouvert la salle"}) == {
        "fr": "{{prospect}} a ouvert la salle"
    }
    assert require_localizations(None) == {}
    with pytest.raises(TemplateError):
        require_localizations({"fr": "{{nope}}"})
    with pytest.raises(TemplateError):
        require_localizations("not a mapping")
    with pytest.raises(TemplateError):
        require_localizations({"": "t"})


def test_the_payload_is_the_researched_jsonapi_event_create():
    """Reproduced key for key from ``docs/research/…/WF-026.md``."""
    payload = build_event_payload(
        name="my-app:my-event",
        external_url="https://rooms.example/r/room_1",
        prospect_id="PROSPECT_ID",
        body="Viewed the pack",
    )
    assert payload == {
        "data": {
            "type": "event",
            "attributes": {
                "name": "my-app:my-event",
                "externalUrl": "https://rooms.example/r/room_1",
                "body": "Viewed the pack",
            },
            "relationships": {"prospect": {"data": {"type": "prospect", "id": "PROSPECT_ID"}}},
        }
    }
    assert payload["data"]["type"] == EVENT_RESOURCE_TYPE
    assert payload["data"]["relationships"]["prospect"]["data"]["type"] == PROSPECT_RESOURCE_TYPE


def test_the_body_is_optional_and_omitted_rather_than_null():
    """[sourced] "In the payload you can additionally send accompanying text"."""
    payload = build_event_payload(
        name="my-app:my-event", external_url="https://rooms.example/r/1", prospect_id="p"
    )
    assert "body" not in payload["data"]["attributes"]
    assert set(payload["data"]["attributes"]) == {"name", "externalUrl"}

    blank = build_event_payload(
        name="my-app:my-event", external_url="https://rooms.example/r/1", prospect_id="p", body="   "
    )
    assert "body" not in blank["data"]["attributes"]


def test_the_template_is_never_sent_in_the_payload():
    """[sourced] The template is a developer-portal configuration, not a payload field."""
    payload = build_event_payload(
        name="my-app:my-event", external_url="https://rooms.example/r/1", prospect_id="p", body="hi"
    )
    assert "template" not in json.dumps(payload)


def test_the_payload_always_carries_a_prospect_relationship():
    payload = build_event_payload(
        name="my-app:my-event", external_url="https://rooms.example/r/1", prospect_id=" p "
    )
    assert payload["data"]["relationships"]["prospect"]["data"]["id"] == "p"


@pytest.mark.parametrize("url", ["", None, "javascript:alert(1)", "data:text/html,x", "/rooms/1", "rooms.example"])
def test_a_deep_link_must_be_an_absolute_http_url(url):
    """The card links a seller straight out of the feed, so it has to be real."""
    with pytest.raises(FeedError):
        require_absolute_url(url, field="externalUrl")


def test_the_deep_link_is_the_base_url_and_the_room():
    assert room_deep_link("https://rooms.example/r/", "room_1") == "https://rooms.example/r/room_1"
    assert room_deep_link("https://rooms.example/r", "room_1") == "https://rooms.example/r/room_1"


def test_a_room_id_that_cannot_go_in_a_path_is_refused():
    with pytest.raises(FeedError) as excinfo:
        room_deep_link("https://rooms.example/r", "room/1?x=1")
    assert "external_url" in str(excinfo.value)


def test_the_webhook_contract_is_the_researched_one():
    """[sourced] "mailing* created updated destroyed bounced delivered opened replied"."""
    assert MAILING_FAMILY == "mailing"
    assert MAILING_RESOURCES == (
        "created",
        "updated",
        "destroyed",
        "bounced",
        "delivered",
        "opened",
        "replied",
    )
    assert INTENT_RESOURCES == ("bounced", "delivered", "opened", "replied")
    assert set(INTENT_RESOURCES) < set(MAILING_RESOURCES)


def test_the_webhook_payload_version_is_two():
    """[sourced] "POST …/webhooks with ``payloadVersion: 2``"."""
    assert PAYLOAD_VERSION == 2


def test_the_signature_header_is_the_researched_one():
    """[sourced] "deliveries carry an ``Outreach-Webhook-Signature`` HMAC header"."""
    assert SIGNATURE_HEADER == "Outreach-Webhook-Signature"


def test_the_timeout_is_the_researched_five_seconds():
    """[sourced] "The timeout while waiting for response is set to 5 seconds." """
    assert SENDER_TIMEOUT_SECONDS == 5.0
    assert DEFAULT_TIMEOUT == 5.0


def test_the_sourced_no_retry_statement_is_carried_into_the_vocabulary():
    body = describe_vocabulary()
    assert body["no_retry"]["vendor_retries_webhook_deliveries"] is False
    assert "does not retry webhook deliveries" in body["no_retry"]["sourced_quote"]
    assert body["template"]["sent_in_payload"] is False
    assert body["template"]["placeholders"] == ["{{prospect}}"]


def test_the_event_stream_fields_are_discovered_not_declared():
    for concept, keys in EVENT_FIELDS.items():
        assert isinstance(keys, list) and keys, f"{concept} needs at least one candidate path"
    assert "action" in EVENT_FIELDS
    assert "person" in EVENT_FIELDS


def test_the_adjacent_surfaces_are_named_and_marked_unimplemented():
    """Named in the research's extensibility field; read, and deliberately not built."""
    adjacent = describe_adjacent_surfaces()
    assert adjacent["implemented"] is False
    endpoints = [entry["endpoint"] for entry in adjacent["salesloft"]]
    assert any("/v2/third_party_live_feed_items" in endpoint for endpoint in endpoints)
    assert any("/v2/live_website_tracking_parameters" in endpoint for endpoint in endpoints)
    names = [entry["name"] for entry in adjacent["outreach"]]
    assert "Mailing links custom tracker" in names
    for entry in adjacent["outreach"] + adjacent["salesloft"]:
        assert entry["why_not"], "every unbuilt surface says why"


# --------------------------------------------------------------------------- #
# The vocabulary endpoint
# --------------------------------------------------------------------------- #


def test_the_vocabulary_endpoint_serves_the_sourced_half(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["write"]["endpoint"] == EVENTS_ENDPOINT
    assert body["write"]["example_name"] == EXAMPLE_EVENT_NAME
    assert body["write"]["example_body"]["data"]["type"] == "event"
    assert body["event_name"]["app_scoped"] is True
    assert body["webhook"]["resources"] == list(MAILING_RESOURCES)
    assert body["webhook"]["signature_header"] == SIGNATURE_HEADER
    assert body["webhook"]["timeout_seconds"] == 5.0
    assert body["adjacent_surfaces"]["implemented"] is False


def test_the_vocabulary_endpoint_tells_a_team_what_to_create_in_the_portal(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    create = body["subscription"]["create"]
    assert create["body"]["payloadVersion"] == 2
    assert "mailing.opened" in create["body"]["resources"]
    assert body["subscription"]["receive"]["signature_header"] == SIGNATURE_HEADER


def test_the_inferences_endpoint_serves_the_registry_next_to_the_sourced_half(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert body["sourced"]["write_endpoint"] == EVENTS_ENDPOINT
    assert body["sourced_quotes"] == list(SOURCED_QUOTES)
    for entry in body["inferences"]:
        for key in ("id", "topic", "basis", "value", "why", "change_it", "blast_radius"):
            assert entry.get(key), f"{entry.get('id')} is missing {key}"


def test_reading_the_vocabulary_writes_nothing(http):
    assert http.get("/api/audit").json()["count"] == 0
    http.get(f"{PREFIX}/vocabulary")
    http.get(f"{PREFIX}/inferences")
    assert http.get("/api/audit").json()["count"] == 0


# --------------------------------------------------------------------------- #
# Outbound delivery
# --------------------------------------------------------------------------- #


def test_delivery_posts_to_the_researched_endpoint_with_the_bearer_token(transport):
    report = post_json(
        transport,
        EVENTS_ENDPOINT,
        {"a": 1},
        headers={"Authorization": "Bearer s2s"},
    )
    assert transport.calls[0]["url"] == EVENTS_ENDPOINT
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer s2s"
    assert transport.calls[0]["headers"]["Content-Type"] == "application/json"
    assert report.ok


def test_delivery_uses_the_sourced_five_second_timeout(transport):
    post_json(transport, EVENTS_ENDPOINT, {}, timeout=SENDER_TIMEOUT_SECONDS)
    assert transport.calls[0]["timeout"] == 5.0


def test_a_rate_limit_is_retried_and_both_attempts_are_recorded(transport):
    transport.scripted = [rate_limited(), ok()]
    report = post_json(transport, EVENTS_ENDPOINT, {}, max_attempts=3, backoff=0, sleep=lambda _s: None)
    assert report.attempts == 2
    assert [entry.status for entry in report.history] == [429, 201]
    assert [entry["attempt"] for entry in report.to_dict()["attempt_log"]] == [1, 2]


def test_a_server_error_is_retried(transport):
    transport.scripted = [server_error(), ok()]
    report = post_json(transport, EVENTS_ENDPOINT, {}, backoff=0, sleep=lambda _s: None)
    assert report.attempts == 2
    assert report.ok


def test_a_permanent_failure_is_not_retried_and_needs_a_human(transport):
    transport.scripted = [not_found(), ok()]
    report = post_json(transport, EVENTS_ENDPOINT, {}, backoff=0, sleep=lambda _s: None)
    assert report.attempts == 1
    assert not report.ok
    assert report.needs_manual_update is True
    assert len(transport.calls) == 1


def test_a_400_is_permanent_too():
    assert bad_request().permanent_failure is True
    assert rate_limited().permanent_failure is False


def test_the_attempt_count_is_bounded():
    assert DEFAULT_MAX_ATTEMPTS == 3
    assert 429 in RETRYABLE_STATUS and 500 in RETRYABLE_STATUS and 404 not in RETRYABLE_STATUS


def test_the_recorded_headers_never_carry_the_token():
    """The ``Authorization`` value *is* the S2S token, and a delivery row is readable."""
    transport = FakeTransport()
    post_json(transport, EVENTS_ENDPOINT, {}, headers={"Authorization": "Bearer s2s-token-abc123"})
    recorded = post_json(
        transport, EVENTS_ENDPOINT, {}, headers={"Authorization": "Bearer s2s-token-abc123"}
    ).to_dict()
    assert recorded["request_headers"]["Authorization"] == REDACTED
    assert "s2s-token-abc123" not in json.dumps(recorded)
    # The transport itself still saw the real value, or the write would not work.
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer s2s-token-abc123"


def test_redact_headers_is_case_insensitive_and_leaves_other_headers_alone():
    redacted = redact_headers({"authorization": "Bearer x", "Content-Type": "application/json"})
    assert redacted == {"authorization": REDACTED, "Content-Type": "application/json"}


def test_the_recorded_response_body_is_bounded():
    transport = FakeTransport(PostResult(ok=False, status=500, body="x" * (BODY_SAMPLE * 4)))
    report = post_json(transport, EVENTS_ENDPOINT, {}, max_attempts=1)
    assert len(report.to_dict()["response"]["body"]) == BODY_SAMPLE


# --------------------------------------------------------------------------- #
# The inbound webhook contract
# --------------------------------------------------------------------------- #


def test_a_signature_verifies_over_the_exact_bytes():
    body = b'{"a": 1}'
    assert webhooks.verify("s", body, webhooks.compute_signature("s", body))
    assert not webhooks.verify("s", body, webhooks.compute_signature("other", body))
    assert not webhooks.verify("s", body, None)
    assert not webhooks.verify("", body, webhooks.compute_signature("s", body))


def test_a_signature_verifies_with_or_without_the_prefix():
    body = b'{"a": 1}'
    digest = webhooks.compute_signature("s", body)
    assert webhooks.verify("s", body, digest)
    assert webhooks.verify("s", body, digest.removeprefix("sha256="))


def test_a_missing_signature_header_is_a_signature_error():
    with pytest.raises(SignatureError) as excinfo:
        webhooks.require_signature({}, ["secret"], b"{}")
    assert SIGNATURE_HEADER in str(excinfo.value)


def test_the_signature_header_is_found_whatever_its_case():
    body = b'{"a": 1}'
    lowered = {SIGNATURE_HEADER.lower(): webhooks.compute_signature("s", body)}
    assert webhooks.require_signature(lowered, ["s"], body) == "s"


def test_a_payload_version_that_is_not_two_is_refused():
    assert webhooks.require_payload_version({"payloadVersion": 2}) == 2
    with pytest.raises(FeedError) as excinfo:
        webhooks.require_payload_version({"payloadVersion": 1})
    assert "payloadVersion 2" in str(excinfo.value)
    with pytest.raises(FeedError):
        webhooks.require_payload_version({})
    with pytest.raises(FeedError):
        webhooks.require_payload_version({"payloadVersion": "two"})


def test_a_body_that_is_not_a_json_object_is_refused():
    with pytest.raises(FeedError):
        webhooks.parse_body(b"not json")
    with pytest.raises(FeedError):
        webhooks.parse_body(b"[1, 2]")


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"type": "mailing.opened"}, ("mailing", "opened")),
        ({"resource": "mailing", "action": "opened"}, ("mailing", "opened")),
        ({"type": "mailing", "action": "replied"}, ("mailing", "replied")),
        ({"resource": "mailing.replied"}, ("mailing", "replied")),
        ({"type": "sequence.created"}, ("sequence", "created")),
        ({"type": "mailing"}, ("mailing", "")),
        ({}, ("", "")),
    ],
)
def test_the_resource_and_its_type_are_read_however_the_delivery_spells_them(payload, expected):
    assert webhooks.split_resource(payload) == expected


def test_a_mailing_delivery_is_supported_and_intent_carrying():
    normal = webhooks.normalise(
        {
            "type": "mailing.replied",
            "sequence": 4,
            "createdAt": "2026-09-24T19:02:00Z",
            "data": {
                "id": "mail_1",
                "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_1"}}},
                "beforeUpdate": {"status": "opened"},
            },
        }
    )
    assert normal["supported"] is True
    assert normal["intent"] is True
    assert normal["prospect_id"] == "p_1"
    assert normal["mailing_id"] == "mail_1"
    assert normal["sequence"] == 4
    assert normal["occurred_at"] == "2026-09-24T19:02:00Z"
    assert normal["before_update"] == {"status": "opened"}


def test_a_delivery_outside_the_documented_family_is_not_supported():
    normal = webhooks.normalise({"type": "sequence.created", "data": {"id": "s_1"}})
    assert normal["supported"] is False
    assert normal["intent"] is False


def test_a_prospect_id_falls_back_to_a_flat_attribute():
    normal = webhooks.normalise(
        {"type": "mailing.opened", "data": {"id": "m", "attributes": {"prospect_id": "p_9"}}}
    )
    assert normal["prospect_id"] == "p_9"


def test_the_before_update_block_is_read_from_wherever_it_appears():
    for payload in (
        {"data": {"beforeUpdate": {"a": 1}}},
        {"beforeUpdate": {"a": 1}},
        {"data": {"attributes": {"beforeUpdate": {"a": 1}}}},
    ):
        assert webhooks.before_update_of(payload) == {"a": 1}
    assert webhooks.before_update_of({"data": {}}) is None


def test_a_signal_key_identifies_one_delivery():
    first = webhooks.signal_key("mailing", "opened", "m_1", 3, "p_1")
    assert first == webhooks.signal_key("mailing", "opened", "m_1", 3, "p_1")
    assert first != webhooks.signal_key("mailing", "replied", "m_1", 3, "p_1")
    assert first != webhooks.signal_key("mailing", "opened", "m_2", 3, "p_1")


# --------------------------------------------------------------------------- #
# Apps: step 1, recorded
# --------------------------------------------------------------------------- #


def test_registering_an_app_records_the_identity_and_the_credentials(feed, store):
    registered = feed.register_app(APP, actor="dana", source=SOURCE)
    stored = store.get(registered["id"])["data"]
    assert stored["app_identifier"] == "dsr"
    assert stored["token"] == "s2s-token-abc123"
    assert stored["room_base_url"] == "https://rooms.example/r"


def test_a_read_never_returns_the_token_or_the_secret(app_row):
    """The token is the Authorization header, and a delivery row is readable."""
    summary = app_row
    assert summary["has_token"] is True
    assert summary["token_hint"] == "***c123"
    assert "token" not in summary
    assert "s2s-token-abc123" not in json.dumps(summary)
    assert "whsec-xyz" not in json.dumps(summary)


def test_an_app_needs_an_identifier_and_a_token(feed):
    with pytest.raises(AppError) as excinfo:
        feed.register_app({"token": "x"}, source=SOURCE)
    assert "app_identifier" in str(excinfo.value)
    with pytest.raises(AppError) as excinfo:
        feed.register_app({"app_identifier": "dsr"}, source=SOURCE)
    assert "Bearer S2S_TOKEN" in str(excinfo.value)


def test_an_app_room_base_url_must_be_absolute(feed):
    with pytest.raises(FeedError):
        feed.register_app(APP | {"room_base_url": "/r"}, source=SOURCE)


def test_an_app_identifier_cannot_be_registered_twice(feed, app_row):
    with pytest.raises(AppError) as excinfo:
        feed.register_app(APP, source=SOURCE)
    assert app_row["id"] in str(excinfo.value)


def test_patching_an_app_rotates_the_token_without_resending_it(feed, app_row, store):
    updated = feed.update_app(app_row["id"], {"token": "new-token-9999"}, source=SOURCE)
    assert updated["token_hint"] == "***9999"
    assert store.get(app_row["id"])["data"]["token"] == "new-token-9999"


def test_patching_an_app_can_blank_the_webhook_secret(feed, app_row, store):
    assert feed.update_app(app_row["id"], {"webhook_secret": ""}, source=SOURCE)["has_webhook_secret"] is False
    assert store.get(app_row["id"])["data"]["webhook_secret"] == ""


def test_patching_an_app_cannot_blank_its_token(feed, app_row):
    with pytest.raises(AppError) as excinfo:
        feed.update_app(app_row["id"], {"token": ""}, source=SOURCE)
    assert "enabled=false" in str(excinfo.value)


def test_patching_an_app_flip_its_toggle(feed, app_row, store):
    feed.update_app(app_row["id"], {"enabled": False}, source=SOURCE)
    assert store.get(app_row["id"])["data"]["enabled"] is False


def test_patching_a_missing_app_is_a_refusal(feed):
    with pytest.raises(AppError):
        feed.update_app("outreach_app_nope", {"enabled": False}, source=SOURCE)
    with pytest.raises(AppError):
        feed.delete_app("outreach_app_nope", source=SOURCE)


def soft_deleted(store: RecordStore, record_id: str) -> bool:
    """Whether a record was soft-deleted, which ``store.get`` hides by design."""
    return store.db.get(record_id, include_deleted=True)["deleted_at"] is not None


def test_deleting_an_app_keeps_the_delivery_log(feed, app_row, store):
    feed.delete_app(app_row["id"], source=SOURCE)
    assert soft_deleted(store, app_row["id"])
    assert store.get(app_row["id"]) is None


def test_an_app_may_override_where_its_buyer_actions_live(feed):
    """Schema flexibility, at the level this workflow reads."""
    custom = feed.register_app(APP | {"field_map": {"action": ["behaviour"]}}, source=SOURCE)
    assert custom["field_map"] == {"action": ["behaviour"]}
    fields = feed.field_map(store_get(feed, custom["id"]))
    assert fields["action"] == ["behaviour"]
    assert fields["person"] == EVENT_FIELDS["person"], "an override must not drop the rest"


def store_get(feed: FeedPublisher, record_id: str):
    return feed.store.get(record_id)


# --------------------------------------------------------------------------- #
# Custom events: step 2
# --------------------------------------------------------------------------- #


def test_a_custom_event_needs_a_registered_app_first(feed):
    with pytest.raises(FeedNotConfiguredError) as excinfo:
        feed.create_event_type(EVENT_TYPE, source=SOURCE)
    assert "register one" in str(excinfo.value)


def test_a_custom_event_is_scoped_to_the_app_in_its_name(event_type):
    assert event_type["name"] == "dsr:room-viewed"
    assert event_type["app_identifier"] == "dsr"
    assert event_type["event_id"] == "room-viewed"
    assert event_type["actions"] == ["viewed"]
    assert event_type["enabled"] is True


def test_a_custom_event_infers_its_app_from_the_name(feed, app_row):
    created = feed.create_event_type(EVENT_TYPE, source=SOURCE)
    assert created["app_id"] == app_row["id"]


def test_a_custom_event_name_may_not_disagree_with_its_app(feed, app_row):
    other = feed.register_app(APP | {"app_identifier": "other", "token": "t2"}, source=SOURCE)
    with pytest.raises(EventTypeError) as excinfo:
        feed.create_event_type(EVENT_TYPE | {"app_id": other["id"]}, source=SOURCE)
    assert "scoped to app" in str(excinfo.value)


def test_a_custom_event_must_claim_at_least_one_buyer_action(feed, app_row):
    with pytest.raises(EventTypeError) as excinfo:
        feed.create_event_type(EVENT_TYPE | {"actions": []}, source=SOURCE)
    assert "qualifying DSR event" in str(excinfo.value)


def test_a_custom_event_name_cannot_be_configured_twice(feed, event_type):
    with pytest.raises(EventTypeError) as excinfo:
        feed.create_event_type(EVENT_TYPE, source=SOURCE)
    assert event_type["id"] in str(excinfo.value)


def test_a_custom_event_rejects_a_template_it_cannot_render(feed, app_row):
    with pytest.raises(TemplateError):
        feed.create_event_type(EVENT_TYPE | {"template": "{{account}} did it"}, source=SOURCE)


def test_a_custom_event_keeps_its_localised_templates(feed, app_row):
    created = feed.create_event_type(
        EVENT_TYPE | {"localizations": {"fr": "{{prospect}} a ouvert la salle"}}, source=SOURCE
    )
    assert created["localizations"] == {"fr": "{{prospect}} a ouvert la salle"}


def test_a_custom_event_says_which_half_of_the_card_it_controls(event_type):
    preview = event_type["card_preview"]
    assert preview["template"] == "{{prospect}} opened the digital sales room"
    assert preview["body"] == "Opened the room"
    assert "never sent" in preview["note"]


def test_a_custom_event_toggles_without_a_separate_flag(event_type, feed):
    off = feed.update_event_type(event_type["id"], {"enabled": False}, source=SOURCE)
    assert off["enabled"] is False
    on = feed.update_event_type(event_type["id"], {"enabled": True}, source=SOURCE)
    assert on["enabled"] is True


def test_patching_a_custom_event_cannot_empty_its_actions(event_type, feed):
    with pytest.raises(EventTypeError):
        feed.update_event_type(event_type["id"], {"actions": []}, source=SOURCE)


def test_renaming_a_custom_event_keeps_the_grammar(event_type, feed):
    renamed = feed.update_event_type(event_type["id"], {"name": "dsr:room-opened"}, source=SOURCE)
    assert renamed["name"] == "dsr:room-opened"
    assert renamed["event_id"] == "room-opened"
    with pytest.raises(EventNameError):
        feed.update_event_type(event_type["id"], {"name": "nocolon"}, source=SOURCE)


def test_a_missing_custom_event_is_a_refusal(feed):
    with pytest.raises(EventTypeError):
        feed.update_event_type("outreach_event_type_nope", {"enabled": False}, source=SOURCE)
    with pytest.raises(EventTypeError):
        feed.delete_event_type("outreach_event_type_nope", source=SOURCE)
    assert feed.get_event_type("outreach_event_type_nope") is None


def test_deleting_a_custom_event_is_a_soft_delete(event_type, feed, store):
    feed.delete_event_type(event_type["id"], source=SOURCE)
    assert soft_deleted(store, event_type["id"])


# --------------------------------------------------------------------------- #
# Prospect links
# --------------------------------------------------------------------------- #


def test_linking_a_room_to_a_prospect(feed, room, linked):
    assert linked["prospect_id"] == "p_123"
    assert linked["room_id"] == room["id"]
    assert linked["active"] is True


def test_a_link_needs_a_prospect_id(feed, room):
    with pytest.raises(ProspectLinkError) as excinfo:
        feed.link_prospect(room["id"], {}, source=SOURCE)
    assert "prospect relationship" in str(excinfo.value)


def test_a_link_to_an_unknown_room_is_refused(feed):
    with pytest.raises(UnknownRoom):
        feed.link_prospect("room_nope", {"prospect_id": "p"}, source=SOURCE)


def test_a_room_cannot_be_linked_twice_to_one_prospect(feed, room, linked):
    with pytest.raises(ProspectLinkError) as excinfo:
        feed.link_prospect(room["id"], {"prospect_id": "p_123"}, source=SOURCE)
    assert linked["id"] in str(excinfo.value)


def test_a_room_may_be_linked_to_several_prospects(feed, room, linked):
    second = feed.link_prospect(room["id"], {"prospect_id": "p_456"}, source=SOURCE)
    assert {linked["id"], second["id"]} == {row["id"] for row in feed.list_prospects(room["id"])}


def test_a_link_keeps_the_account_and_opportunity_behind_the_prospect(feed, room):
    link = feed.link_prospect(
        room["id"],
        {"prospect_id": "p_1", "opportunity_id": "006NW", "account_id": "001AC"},
        source=SOURCE,
    )
    assert link["opportunity_id"] == "006NW"
    assert link["account_id"] == "001AC"


def test_a_link_external_url_must_be_absolute(feed, room):
    with pytest.raises(FeedError):
        feed.link_prospect(room["id"], {"prospect_id": "p", "external_url": "/r"}, source=SOURCE)


def test_unlinking_refuses_a_link_belonging_to_another_room(feed, room, store, linked):
    other = store.create("room", {"name": "Other"}, actor="dana")
    with pytest.raises(ProspectLinkError) as excinfo:
        feed.unlink_prospect(other["id"], linked["id"], source=SOURCE)
    assert "is not on room" in str(excinfo.value)


def test_unlinking_is_a_soft_delete(feed, room, store, linked):
    feed.unlink_prospect(room["id"], linked["id"], source=SOURCE)
    assert soft_deleted(store, linked["id"])
    assert feed.list_prospects(room["id"]) == []


# --------------------------------------------------------------------------- #
# Matching: which configured event claims which DSR event
# --------------------------------------------------------------------------- #


def test_a_matching_action_is_claimed():
    assert matches({"actions": ["viewed"]}, {"action": "viewed"}) is True
    assert matches({"actions": ["viewed"]}, {"action": "Viewed"}) is True
    assert matches({"actions": ["viewed"]}, {"action": "commented"}) is False


def test_an_empty_action_filter_claims_everything():
    assert matches({}, {"action": "anything"}) is True


def test_a_document_filter_narrows_the_match():
    rule = {"actions": ["downloaded"], "documents": ["Pricing One-Pager"]}
    assert matches(rule, {"action": "downloaded", "target": "Pricing One-Pager"}) is True
    assert matches(rule, {"action": "downloaded", "target": "Security Pack"}) is False
    assert matches({"documents": ["Pricing"]}, {"action": "viewed", "target": "Pricing"}) is True


def test_a_comma_separated_filter_is_accepted():
    """A picker sends a string; a config file sends a list. Both work."""
    assert matches({"actions": "viewed,downloaded"}, {"action": "downloaded"}) is True


def test_the_event_stream_is_read_through_discovered_field_locations():
    record = {
        "id": "activity_1",
        "room_id": "room_1",
        "created_at": "2026-09-20T10:00:00.000+00:00",
        "data": {
            "event": "downloaded",
            "by": "buyer@x.example",
            "document_title": "Pricing One-Pager",
            "happened_at": "2026-09-20T09:30:00+00:00",
        },
    }
    event = normalise_stream_event(record, EVENT_FIELDS)
    assert event["action"] == "downloaded"
    assert event["person"] == "buyer@x.example"
    assert event["target"] == "Pricing One-Pager"
    assert event["occurred_at"] == "2026-09-20T09:30:00+00:00"


def test_an_app_field_map_overrides_the_default_locations(store, room):
    store.create("activity", {"behaviour": "viewed", "who": "b@x.example"}, room_id=room["id"], actor="s")
    record = store.list("activity", room_id=room["id"])[0]
    event = normalise_stream_event(record, {"action": ["behaviour"], "person": ["who"]})
    assert event["action"] == "viewed"
    assert event["person"] == "b@x.example"


def test_an_event_whose_action_cannot_be_read_is_kept_not_dropped(store, room):
    store.create("activity", {"something": "else"}, room_id=room["id"], actor="s")
    record = store.list("activity", room_id=room["id"])[0]
    event = normalise_stream_event(record, EVENT_FIELDS)
    assert event["action"] == ""
    assert event["person"] == "anonymous"


def test_a_delivery_key_is_room_event_name_and_prospect():
    assert delivery_key("r", "a", "dsr:x") == "r|a|dsr:x|"
    assert delivery_key("r", "a", "dsr:x", "p") == "r|a|dsr:x|p"
    assert delivery_key("r", "a", "dsr:x", "p") != delivery_key("r", "a", "dsr:x", "q")


def test_the_card_text_joins_the_two_halves_this_build_controls():
    assert card_text("dsr:x", "Body") == "dsr:x · Body"
    assert card_text("dsr:x", "") == "dsr:x"
    assert card_text("", "") == ""


# --------------------------------------------------------------------------- #
# Publish: the researched write
# --------------------------------------------------------------------------- #


def test_a_qualifying_event_is_posted_with_the_researched_payload(feed, quiet, event_type, linked, transport):
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["counts"]["sent"] == 1
    call = transport.calls[0]
    assert call["url"] == EVENTS_ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer s2s-token-abc123"
    assert call["timeout"] == 5.0
    assert call["body"] == {
        "data": {
            "type": "event",
            "attributes": {
                "name": "dsr:room-viewed",
                "externalUrl": f"https://rooms.example/r/{quiet['id']}",
                "body": "Opened the room",
            },
            "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_123"}}},
        }
    }


def test_an_event_with_no_body_omits_it_on_the_wire(feed, store, quiet, app_row, linked, transport):
    feed.create_event_type(
        {"app_id": app_row["id"], "name": "dsr:silent", "template": "t", "actions": ["viewed"]},
        source=SOURCE,
    )
    feed.publish(quiet["id"], source=SOURCE)
    attributes = [call["body"]["data"]["attributes"] for call in transport.calls]
    assert {"name": "dsr:silent", "externalUrl": f"https://rooms.example/r/{quiet['id']}"} in attributes


def test_a_disabled_custom_event_claims_nothing(feed, quiet, event_type, linked, transport):
    feed.update_event_type(event_type["id"], {"enabled": False}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert transport.calls == []
    assert ledger["counts"]["sent"] == 0
    assert ledger["counts"]["unmapped_events"] == 1


def test_a_buyer_action_nobody_configured_falls_through_and_is_counted(store, feed, quiet, event_type, linked):
    store.create("activity", {"action": "commented", "person": "b@x.example"}, room_id=quiet["id"], actor="s")
    store.create("activity", {"action": "commented", "person": "c@x.example"}, room_id=quiet["id"], actor="s")
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["counts"]["sent"] == 1
    assert ledger["unmapped_actions"] == [
        {"action": "commented", "count": 2, "example_events": ledger["unmapped_actions"][0]["example_events"]}
    ]
    assert len(ledger["unmapped_actions"][0]["example_events"]) == 2


def test_an_action_that_cannot_be_read_is_reported_as_unreadable(store, feed, quiet, event_type, linked):
    store.create("activity", {"something": "else"}, room_id=quiet["id"], actor="s")
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["unmapped_actions"][0]["action"] == "(unreadable)"


def test_a_room_with_no_prospect_link_skips_with_a_reason(feed, quiet, event_type):
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["counts"]["sent"] == 0
    assert ledger["counts"]["skipped"] == 1
    assert ledger["skipped"][0]["reason"] == "no_prospect"
    assert ledger["skipped"][0]["detail"] == SKIP_REASONS["no_prospect"]


def test_a_skipped_event_is_stored_not_dropped(feed, store, quiet, event_type):
    feed.publish(quiet["id"], source=SOURCE)
    rows = store.list(DELIVERY_COLLECTION, room_id=quiet["id"])
    assert len(rows) == 1
    assert rows[0]["data"]["status"] == STATUS_SKIPPED
    assert rows[0]["data"]["skip_reason"] == "no_prospect"
    assert rows[0]["data"]["attempts"] == 0


def test_linking_the_prospect_and_running_again_sends_what_was_waiting(feed, quiet, event_type):
    """The fall-through, twice: first recorded as blocked, then sent.

    The blocked row is *not* reused: a skipped row has no prospect in its key,
    because there was no prospect, so the later run writes a row keyed by the
    prospect it finally went to. Both rows stay in the log - one says the room was
    not linked then, the other says the card reached the feed - and the room feed
    shows the delivered one with the skip named as superseded.
    """
    first = feed.publish(quiet["id"], source=SOURCE)
    assert first["counts"]["skipped"] == 1
    blocked_id = first["deliveries"][0]

    feed.link_prospect(quiet["id"], {"prospect_id": "p_late"}, source=SOURCE)
    second = feed.publish(quiet["id"], source=SOURCE)
    assert second["counts"]["sent"] == 1
    assert second["counts"]["skipped"] == 0
    assert second["deliveries"] != [blocked_id], "the delivered row is keyed by the prospect it reached"

    rows = feed.deliveries(room_id=quiet["id"])
    assert {row["status"] for row in rows} == {STATUS_SKIPPED, STATUS_DELIVERED}

    cards = feed.room_feed(quiet["id"])["cards"]
    assert len(cards) == 1
    assert cards[0]["status"] == STATUS_DELIVERED
    assert cards[0]["superseded"] == [blocked_id]


def test_an_app_with_no_token_skips_with_a_reason(store, feed, quiet, event_type, linked):
    app_record = store.get(event_type["app_id"])
    store.update(app_record["id"], {"token": ""}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["skipped"][0]["reason"] == "app_not_ready"


def test_a_disabled_app_skips_with_a_reason(store, feed, quiet, event_type, linked):
    store.update(event_type["app_id"], {"enabled": False}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["skipped"][0]["reason"] == "app_disabled"


def test_an_app_with_no_base_url_and_a_link_with_no_explicit_url_skips(store, feed, quiet, event_type, linked):
    store.update(event_type["app_id"], {"room_base_url": ""}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["skipped"][0]["reason"] == "no_room_base_url"


def test_a_link_may_carry_its_own_deep_link(feed, store, quiet, event_type, app_row):
    store.update(app_row["id"], {"room_base_url": ""}, source=SOURCE)
    feed.link_prospect(quiet["id"], {"prospect_id": "p_slug", "external_url": "https://deals.example/northwind"}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["counts"]["sent"] == 1
    assert ledger["sent"][0]["external_url"] == "https://deals.example/northwind"


def test_publishing_to_an_unknown_room_is_refused(feed):
    with pytest.raises(UnknownRoom):
        feed.publish("room_nope", source=SOURCE)


def test_publishing_with_nothing_configured_sends_nothing(feed, quiet):
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["counts"]["sent"] == 0
    assert ledger["ready"] is False
    assert ledger["counts"]["unmapped_events"] == 1


def test_one_event_fans_out_to_every_linked_prospect(feed, quiet, event_type, transport):
    feed.link_prospect(quiet["id"], {"prospect_id": "p_a"}, source=SOURCE)
    feed.link_prospect(quiet["id"], {"prospect_id": "p_b"}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    assert ledger["counts"]["sent"] == 2
    assert {entry["prospect_id"] for entry in ledger["sent"]} == {"p_a", "p_b"}


def test_a_second_publish_does_not_resend_what_was_delivered(feed, quiet, event_type, linked, transport):
    feed.publish(quiet["id"], source=SOURCE)
    assert len(transport.calls) == 1
    second = feed.publish(quiet["id"], source=SOURCE)
    assert len(transport.calls) == 1
    assert second["counts"]["sent"] == 0
    assert second["counts"]["duplicate"] == 1
    assert "already delivered" in second["duplicate"][0]["detail"]


def test_a_failed_delivery_is_retried_by_the_next_publish(feed, store, quiet, event_type, linked, transport):
    transport.scripted = [not_found(), ok()]
    first = feed.publish(quiet["id"], source=SOURCE)
    assert first["counts"]["failed"] == 1
    second = feed.publish(quiet["id"], source=SOURCE)
    assert second["counts"]["sent"] == 1
    assert second["deliveries"] == first["deliveries"]


def test_publishing_can_be_narrowed_to_one_configured_event(feed, quiet, app_row, linked, transport):
    first = feed.create_event_type(
        {"app_id": app_row["id"], "name": "dsr:one", "template": "t", "actions": ["viewed"]}, source=SOURCE
    )
    feed.create_event_type(
        {"app_id": app_row["id"], "name": "dsr:two", "template": "t", "actions": ["viewed"]}, source=SOURCE
    )
    ledger = feed.publish(quiet["id"], source=SOURCE, only=[first["id"]])
    assert {entry["event_name"] for entry in ledger["sent"]} == {"dsr:one"}


def test_the_ledger_reports_the_blockers_alongside_the_counts(feed, quiet, event_type, linked):
    feed.update_event_type(event_type["id"], {"enabled": False}, source=SOURCE)
    ledger = feed.publish(quiet["id"], source=SOURCE)
    codes = {blocker["code"] for blocker in ledger["blockers"]}
    assert "no_event_type" in codes


def test_the_publish_budget_is_bounded(feed, store, quiet, event_type, linked):
    for _ in range(5):
        store.create("activity", {"action": "viewed", "person": "b@x.example"}, room_id=quiet["id"], actor="s")
    ledger = feed.publish(quiet["id"], source=SOURCE, limit=2)
    assert ledger["scanned"] == 2
    assert ledger["scanned_total"] == 6
    assert ledger["counts"]["sent"] == 2


# --------------------------------------------------------------------------- #
# Preview writes nothing
# --------------------------------------------------------------------------- #


def test_preview_reports_the_same_ledger_without_writing(feed, store, quiet, event_type, linked):
    before = store.stats()["audit_entries"]
    ledger = feed.preview(quiet["id"])
    assert ledger["dry_run"] is True
    assert ledger["counts"]["sent"] == 1
    assert ledger["deliveries"] == []
    assert store.stats()["audit_entries"] == before
    assert store.list(DELIVERY_COLLECTION) == []


def test_preview_builds_the_payload_it_would_send(feed, quiet, event_type, linked):
    ledger = feed.preview(quiet["id"])
    assert ledger["sent"][0]["payload"]["data"]["attributes"]["name"] == "dsr:room-viewed"
    assert ledger["sent"][0]["external_url"] == f"https://rooms.example/r/{quiet['id']}"


def test_preview_reports_the_fall_through(feed, quiet):
    ledger = feed.preview(quiet["id"])
    assert ledger["counts"]["unmapped_events"] == 1
    assert ledger["blockers"]


# --------------------------------------------------------------------------- #
# The delivery log
# --------------------------------------------------------------------------- #


def test_a_delivery_row_records_what_was_sent(feed, store, quiet, event_type, linked):
    feed.publish(quiet["id"], source=SOURCE)
    row = feed.deliveries(room_id=quiet["id"])[0]
    assert row["status"] == STATUS_DELIVERED
    assert row["http_status"] == 201
    assert row["attempts"] == 1
    assert row["event_name"] == "dsr:room-viewed"
    assert row["source_action"] == "viewed"
    assert row["payload"]["data"]["type"] == "event"
    assert row["response"]["status"] == 201
    assert row["needs_manual_update"] is False


def test_a_permanent_failure_is_marked_as_needing_a_human(feed, quiet, event_type, linked, transport):
    transport.scripted = [not_found()]
    feed.publish(quiet["id"], source=SOURCE)
    row = feed.deliveries(room_id=quiet["id"])[0]
    assert row["status"] == STATUS_FAILED
    assert row["http_status"] == 404
    assert row["needs_manual_update"] is True
    assert row["error"] == "HTTP 404"


def test_a_retry_records_both_attempts(feed, quiet, event_type, linked, transport):
    transport.scripted = [rate_limited(), ok()]
    feed.publish(quiet["id"], source=SOURCE)
    row = feed.deliveries(room_id=quiet["id"])[0]
    assert row["attempts"] == 2
    assert row["attempt_statuses"] == [429, 201]
    assert [entry["attempt"] for entry in row["attempt_log"]] == [1, 2]


def test_the_delivery_log_never_carries_the_token(feed, store, quiet, event_type, linked):
    feed.publish(quiet["id"], source=SOURCE)
    rows = store.list(DELIVERY_COLLECTION, limit=100)
    assert rows
    assert "s2s-token-abc123" not in json.dumps(rows)
    assert rows[0]["data"]["request_headers"]["Authorization"] == REDACTED


def test_the_delivery_log_filters_by_status(feed, store, quiet, event_type, linked, transport):
    feed.link_prospect(quiet["id"], {"prospect_id": "p_gone"}, source=SOURCE)
    transport.scripted = [ok(), not_found()]
    feed.publish(quiet["id"], source=SOURCE)
    assert len(feed.deliveries(status=STATUS_DELIVERED)) == 1
    failed = feed.deliveries(status=STATUS_FAILED)
    assert len(failed) == 1
    assert failed[0]["prospect_id"] == "p_gone"


def test_the_delivery_log_filters_by_needing_a_human(feed, quiet, event_type, linked, transport):
    transport.scripted = [not_found()]
    feed.publish(quiet["id"], source=SOURCE)
    assert len(feed.deliveries(needs_manual_update=True)) == 1
    assert feed.deliveries(needs_manual_update=False) == []


def test_the_delivery_log_filters_by_event_name(feed, quiet, app_row, linked, transport):
    feed.create_event_type(
        {"app_id": app_row["id"], "name": "dsr:second", "template": "t", "actions": ["viewed"]}, source=SOURCE
    )
    feed.publish(quiet["id"], source=SOURCE)
    assert len(feed.deliveries(event_name="dsr:second")) == 1


def test_the_delivery_log_scopes_to_a_room(feed, store, room, event_type, transport):
    other = store.create("room", {"name": "Other"}, actor="dana")
    for target in (room["id"], other["id"]):
        feed.link_prospect(target, {"prospect_id": f"p_{target[-4:]}"}, source=SOURCE)
        store.create("activity", {"action": "viewed", "person": "b@x.example"}, room_id=target, actor="s")
    feed.publish(room["id"], source=SOURCE)
    feed.publish(other["id"], source=SOURCE)
    assert len(feed.deliveries(room_id=room["id"])) == 1
    assert len(feed.deliveries()) == 2


def test_a_row_is_queryable_with_where_by_its_delivery_key(feed, store, quiet, event_type, linked):
    feed.publish(quiet["id"], source=SOURCE)
    key = feed.deliveries()[0]["event_key"]
    assert store.find(DELIVERY_COLLECTION, {"event_key": key})[0]["data"]["event_name"] == "dsr:room-viewed"


def test_a_manual_retry_appends_rather_than_overwrites(feed, quiet, event_type, linked, transport):
    transport.scripted = [not_found()]
    first = feed.publish(quiet["id"], source=SOURCE)
    transport.scripted = [ok()]
    retried = feed.retry_delivery(first["deliveries"][0], source=SOURCE)
    assert retried["outcome"] == "delivered"
    assert retried["retried"] is True
    row = feed.deliveries()[0]
    assert row["status"] == STATUS_DELIVERED
    assert row["attempt_statuses"] == [404, 201]
    assert row["runs"] == 2


def test_a_manual_retry_clears_a_skipped_row_once_the_blocker_is_gone(feed, quiet, event_type):
    first = feed.publish(quiet["id"], source=SOURCE)
    skipped_id = first["deliveries"][0]
    feed.link_prospect(quiet["id"], {"prospect_id": "p_late"}, source=SOURCE)
    retried = feed.retry_delivery(skipped_id, source=SOURCE)
    assert retried["outcome"] == "delivered"

    # Address the row by its id rather than by position. deliveries() documents
    # itself "newest first", but it is backed by find(), which takes no ordering
    # argument - so the order is whatever the store returns, and nothing promises
    # it. A retry writes its own attempt onto the row AND can leave a further row
    # behind, which makes the first position genuinely ambiguous rather than
    # merely unspecified. This assertion failed on CI with 'skipped' where the
    # retry had already reported 'delivered', and passed locally every time: a
    # difference in store iteration order, not a difference in behaviour.
    #
    # Looking the row up by id keeps the assertion's teeth - it still fails if the
    # row is not delivered, which is what the test is for.
    rows = feed.deliveries()
    row = next((r for r in rows if r["id"] == skipped_id), None)
    assert row is not None, f"the retried row {skipped_id} is missing from {len(rows)} row(s)"
    assert row["status"] == STATUS_DELIVERED
    assert row["skip_reason"] in (None, ""), (
        "a delivered row must not still carry the reason it was skipped"
    )

    # And the retry must not have left a second card behind. The key includes the
    # prospect, so a row skipped for want of one is keyed with an empty prospect
    # and the retry's recomputed key does not match it. Without the retry naming
    # its own row, that mismatch silently forks the history: the original stays
    # skipped forever and a delivered duplicate appears beside it.
    assert len(rows) == 1, (
        f"a retry must land on the row it was given, not create another: {len(rows)} rows"
    )
    assert rows[0]["id"] == skipped_id


def test_a_retry_does_not_fork_the_delivery_history(feed, quiet, event_type):
    """The row count is the guarantee; the statuses alone would not catch it.

    Two cards for one event means the buyer's activity feed shows it twice, and
    the first card still claims it was never sent.
    """
    first = feed.publish(quiet["id"], source=SOURCE)
    before = feed.deliveries()
    assert len(before) == 1
    assert before[0]["status"] == STATUS_SKIPPED

    feed.link_prospect(quiet["id"], {"prospect_id": "p_late"}, source=SOURCE)
    feed.retry_delivery(first["deliveries"][0], source=SOURCE)

    after = feed.deliveries()
    assert len(after) == len(before), (
        f"retry changed the row count from {len(before)} to {len(after)}; "
        f"a retry must update the row it names"
    )
    assert {r["event_key"] for r in after} == {r["event_key"] for r in before}, (
        "the retry must keep the same event_key, so a later publish still sees "
        "this as a duplicate rather than sending a second card"
    )
    assert all(r["status"] != STATUS_SKIPPED for r in after), (
        "no row may be left skipped after a successful retry"
    )


def test_a_manual_retry_stays_skipped_while_the_blocker_holds(feed, quiet, event_type):
    first = feed.publish(quiet["id"], source=SOURCE)
    retried = feed.retry_delivery(first["deliveries"][0], source=SOURCE)
    assert retried["outcome"] == "skipped"
    assert retried["reason"] == "no_prospect"


def test_retrying_a_row_whose_custom_event_was_retired_says_so(feed, event_type, linked, quiet):
    first = feed.publish(quiet["id"], source=SOURCE)
    feed.delete_event_type(event_type["id"], source=SOURCE)
    retried = feed.retry_delivery(first["deliveries"][0], source=SOURCE)
    assert retried["reason"] == "no_event_type"
    assert feed.deliveries()[0]["status"] == STATUS_SKIPPED


def test_retrying_a_row_whose_custom_event_was_soft_deleted_says_so(feed, event_type, linked, quiet):
    first = feed.publish(quiet["id"], source=SOURCE)
    feed.delete_event_type(event_type["id"], source=SOURCE)
    retried = feed.retry_delivery(first["deliveries"][0], source=SOURCE)
    assert retried["outcome"] == "skipped"
    assert retried["reason"] == "no_event_type"


def test_retrying_a_missing_delivery_is_a_refusal(feed):
    with pytest.raises(FeedError):
        feed.retry_delivery("outreach_delivery_nope", source=SOURCE)


def test_the_delivery_statuses_are_the_three_the_log_reports():
    assert DELIVERY_STATUSES == (STATUS_DELIVERED, STATUS_FAILED, STATUS_SKIPPED)


# --------------------------------------------------------------------------- #
# The room feed view
# --------------------------------------------------------------------------- #


def test_the_room_feed_is_the_chronological_clickable_trail(feed, quiet, event_type, linked):
    feed.publish(quiet["id"], source=SOURCE)
    view = feed.room_feed(quiet["id"])
    assert view["room"]["id"] == quiet["id"]
    assert len(view["cards"]) == 1
    card = view["cards"][0]
    assert card["event_name"] == "dsr:room-viewed"
    assert card["card_text"] == "dsr:room-viewed · Opened the room"
    assert card["external_url"].endswith(quiet["id"])
    assert card["action"] == "viewed"
    assert card["person"] == "buyer@northwind.example"
    assert card["status"] == STATUS_DELIVERED


def test_the_room_feed_counts_by_status(feed, store, quiet, event_type, linked, transport):
    feed.link_prospect(quiet["id"], {"prospect_id": "p_gone"}, source=SOURCE)
    transport.scripted = [ok(), not_found()]
    feed.publish(quiet["id"], source=SOURCE)
    view = feed.room_feed(quiet["id"])
    assert view["counts"] == {STATUS_DELIVERED: 1, STATUS_FAILED: 1, STATUS_SKIPPED: 0}
    assert view["needs_manual_update"] == 1


def test_the_room_feed_reports_the_fall_through(feed, store, quiet, event_type, linked):
    store.create("activity", {"action": "commented", "person": "b@x.example"}, room_id=quiet["id"], actor="s")
    feed.publish(quiet["id"], source=SOURCE)
    view = feed.room_feed(quiet["id"])
    assert view["unmapped_actions"][0]["action"] == "commented"
    assert view["unmapped_actions"][0]["count"] == 1


def test_an_unconfigured_room_is_told_exactly_what_is_missing(feed, room):
    view = feed.room_feed(room["id"])
    assert view["ready"] is False
    codes = {blocker["code"] for blocker in view["blockers"]}
    assert codes == {"no_prospect_link", "no_event_type"}


def test_a_configured_room_is_ready(feed, quiet, event_type, linked):
    assert feed.readiness(quiet["id"])["ready"] is True


def test_readiness_names_the_app_that_has_no_token(store, feed, quiet, event_type, linked):
    store.update(event_type["app_id"], {"token": ""}, source=SOURCE)
    codes = {blocker["code"] for blocker in feed.readiness(quiet["id"])["blockers"]}
    assert "app_not_ready" in codes


def test_readiness_reports_an_unknown_room(feed):
    with pytest.raises(UnknownRoom):
        feed.readiness("room_nope")


def test_a_blocked_event_still_appears_in_the_room_feed_with_its_reason(feed, quiet, event_type):
    feed.publish(quiet["id"], source=SOURCE)
    card = feed.room_feed(quiet["id"])["cards"][0]
    assert card["status"] == STATUS_SKIPPED
    assert card["skip_reason"] == "no_prospect"


# --------------------------------------------------------------------------- #
# The inbound half
# --------------------------------------------------------------------------- #


def test_a_signed_delivery_is_recorded_and_linked(feed, store, room, app_row, linked):
    payload = {
        "type": "mailing.replied",
        "sequence": 4,
        "createdAt": "2026-09-24T19:02:00Z",
        "payloadVersion": 2,
        "data": {
            "id": "mail_1",
            "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_123"}}},
            "beforeUpdate": {"status": "opened"},
        },
    }
    result = feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert result["status"] == "recorded"
    assert result["intent"] is True
    assert result["room_id"] == room["id"]
    assert result["link_status"] == "linked"
    row = store.list(SIGNAL_COLLECTION)[0]["data"]
    assert row["before_update"] == {"status": "opened"}
    assert row["signature_verified"] is True


def test_a_delivery_for_an_unlinked_prospect_is_recorded_not_dropped(feed, store, room, app_row):
    payload = {
        "type": "mailing.bounced",
        "sequence": 1,
        "payloadVersion": 2,
        "data": {"id": "m", "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_unknown"}}}},
    }
    result = feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert result["status"] == "recorded"
    assert result["link_status"] == "unlinked"
    assert store.list(SIGNAL_COLLECTION)[0]["room_id"] is None


def test_a_resource_outside_the_documented_family_is_ignored_and_still_accepted(feed, store, app_row):
    """[sourced] Outreach does not retry, so a refusal would be permanent data loss."""
    payload = {"type": "sequence.created", "sequence": 2, "payloadVersion": 2, "data": {"id": "s"}}
    result = feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert result["status"] == "ignored"
    assert "outside the documented mailing family" in result["ignore_reason"]
    assert store.list(SIGNAL_COLLECTION)[0]["data"]["status"] == "ignored"


def test_a_repeated_delivery_is_marked_duplicate_and_not_counted_twice(feed, store, app_row):
    payload = {"type": "mailing.opened", "sequence": 1, "payloadVersion": 2, "data": {"id": "m"}}
    feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    again = feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert again["status"] == "duplicate"
    assert again["duplicate"] is True
    assert len(store.list(SIGNAL_COLLECTION)) == 1


def test_an_unverifiable_delivery_is_refused_and_writes_nothing(feed, store, app_row):
    payload = {"type": "mailing.opened", "payloadVersion": 2, "data": {"id": "m"}}
    with pytest.raises(SignatureError):
        feed.receive_webhook(body_of(payload), {SIGNATURE_HEADER: "sha256=deadbeef"}, source=SOURCE)
    with pytest.raises(SignatureError):
        feed.receive_webhook(body_of(payload), {}, source=SOURCE)
    assert store.list(SIGNAL_COLLECTION) == []


def test_a_tampered_body_does_not_verify(feed, app_row):
    payload = {"type": "mailing.opened", "payloadVersion": 2, "data": {"id": "m"}}
    headers = signed("whsec-xyz", payload)
    with pytest.raises(SignatureError):
        feed.receive_webhook(b'{"type":"mailing.replied"}', headers, source=SOURCE)


def test_a_payload_version_this_build_does_not_speak_is_refused(feed, store, app_row):
    payload = {"type": "mailing.opened", "payloadVersion": 1, "data": {"id": "m"}}
    with pytest.raises(FeedError):
        feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert store.list(SIGNAL_COLLECTION) == []


def test_a_body_that_is_not_json_is_refused(feed, app_row):
    with pytest.raises(FeedError):
        feed.receive_webhook(b"not json", {SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", b"not json")}, source=SOURCE)


def test_a_delivery_with_no_configured_secret_is_not_configured(feed):
    feed.register_app(APP | {"webhook_secret": ""}, source=SOURCE)
    with pytest.raises(FeedNotConfiguredError) as excinfo:
        feed.receive_webhook(b"{}", {}, source=SOURCE)
    assert "webhook_secret" in str(excinfo.value)


def test_a_delivery_verifies_against_any_registered_app_secret(store, feed):
    feed.register_app(APP, source=SOURCE)
    second = feed.register_app(APP | {"app_identifier": "other", "webhook_secret": "whsec-two"}, source=SOURCE)
    payload = {"type": "mailing.opened", "payloadVersion": 2, "data": {"id": "m"}}
    result = feed.receive_webhook(body_of(payload), signed("whsec-two", payload), source=SOURCE)
    assert result["status"] == "recorded"
    assert second["has_webhook_secret"] is True


def test_the_signal_log_filters_by_type_and_status(feed, app_row):
    for kind in ("opened", "replied"):
        payload = {"type": f"mailing.{kind}", "sequence": 1, "payloadVersion": 2, "data": {"id": f"m_{kind}"}}
        feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert len(feed.signals(signal_type="replied")) == 1
    assert len(feed.signals(status="recorded")) == 2
    assert len(feed.signals(status="ignored")) == 0


def test_the_signal_log_scopes_to_a_room(feed, store, room, app_row, linked):
    payload = {
        "type": "mailing.opened",
        "sequence": 1,
        "payloadVersion": 2,
        "data": {"id": "m", "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_123"}}}},
    }
    feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    assert len(feed.signals(room_id=room["id"])) == 1
    assert len(feed.signals(room_id="room_other")) == 0


def test_the_room_feed_carries_the_signals_recorded_against_it(feed, room, app_row, linked):
    payload = {
        "type": "mailing.opened",
        "sequence": 1,
        "payloadVersion": 2,
        "data": {"id": "m", "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_123"}}}},
    }
    feed.receive_webhook(body_of(payload), signed("whsec-xyz", payload), source=SOURCE)
    view = feed.room_feed(room["id"])
    assert [row["type"] for row in view["signals"]] == ["opened"]


# --------------------------------------------------------------------------- #
# The HTTP surface, through this feature's own router
# --------------------------------------------------------------------------- #


def test_the_apps_routes(http):
    created = http.post(f"{PREFIX}/apps", json=APP, params={"actor": "dana"})
    assert created.status_code == 201
    app_id = created.json()["id"]
    assert created.json()["has_token"] is True
    assert len(http.get(f"{PREFIX}/apps").json()["apps"]) == 1
    assert http.get(f"{PREFIX}/apps/{app_id}").json()["id"] == app_id
    patched = http.patch(f"{PREFIX}/apps/{app_id}", json={"enabled": False})
    assert patched.json()["enabled"] is False
    assert http.delete(f"{PREFIX}/apps/{app_id}").status_code == 204
    assert http.get(f"{PREFIX}/apps").json()["apps"] == []


def test_a_read_over_http_still_does_not_return_the_token(http):
    app_id = http.post(f"{PREFIX}/apps", json=APP).json()["id"]
    assert "s2s-token-abc123" not in http.get(f"{PREFIX}/apps/{app_id}").text
    assert "whsec-xyz" not in http.get(f"{PREFIX}/apps").text


def test_reading_a_missing_app_over_http_is_400(http):
    assert http.get(f"{PREFIX}/apps/outreach_app_nope").status_code == 400
    assert http.patch(f"{PREFIX}/apps/outreach_app_nope", json={}).status_code == 400
    assert http.delete(f"{PREFIX}/apps/outreach_app_nope").status_code == 400


def test_the_event_type_routes(http):
    app_id = http.post(f"{PREFIX}/apps", json=APP).json()["id"]
    created = http.post(f"{PREFIX}/event-types", json=EVENT_TYPE | {"app_id": app_id})
    assert created.status_code == 201
    type_id = created.json()["id"]
    assert http.get(f"{PREFIX}/event-types").json()["count"] == 1
    assert http.get(f"{PREFIX}/event-types/{type_id}").json()["name"] == "dsr:room-viewed"
    assert http.patch(f"{PREFIX}/event-types/{type_id}", json={"enabled": False}).json()["enabled"] is False
    assert http.delete(f"{PREFIX}/event-types/{type_id}").status_code == 204


def test_a_bad_event_name_over_http_is_400(http):
    http.post(f"{PREFIX}/apps", json=APP)
    response = http.post(f"{PREFIX}/event-types", json=EVENT_TYPE | {"name": "nocolon"})
    assert response.status_code == 400
    assert response.json()["error"] == "feed_error"
    assert "<app identifier>:<event id>" in response.json()["detail"]


def test_configuring_an_event_before_an_app_is_428(http):
    response = http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    assert response.status_code == 428
    assert response.json()["error"] == "not_configured"


def test_a_missing_event_type_over_http_is_400(http):
    assert http.get(f"{PREFIX}/event-types/outreach_event_type_nope").status_code == 400
    assert http.patch(f"{PREFIX}/event-types/outreach_event_type_nope", json={}).status_code == 400
    assert http.delete(f"{PREFIX}/event-types/outreach_event_type_nope").status_code == 400


def test_the_prospect_link_routes(http, http_room):
    created = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1", "label": "Lead"}
    )
    assert created.status_code == 201
    link_id = created.json()["id"]
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/prospects").json()["count"] == 1
    assert http.delete(f"{PREFIX}/rooms/{http_room['id']}/prospects/{link_id}").status_code == 204
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/prospects").json()["count"] == 0


def test_a_bad_link_over_http_is_400(http, http_room):
    assert http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={}).status_code == 400


def test_unlinking_a_link_from_the_wrong_room_over_http_is_400(http, http_room):
    other = http.post("/api/records/room", json={"name": "Other"}).json()
    link_id = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"}
    ).json()["id"]
    assert http.delete(f"{PREFIX}/rooms/{other['id']}/prospects/{link_id}").status_code == 400
    assert http.delete(f"{PREFIX}/rooms/{http_room['id']}/prospects/nope").status_code == 400


def test_an_unknown_room_over_http_is_404(http):
    for path in (f"{PREFIX}/rooms/room_nope/feed", f"{PREFIX}/rooms/room_nope/prospects", f"{PREFIX}/rooms/room_nope/signals"):
        assert http.get(path).status_code == 404
    assert http.post(f"{PREFIX}/rooms/room_nope/publish", json={}).status_code == 404
    assert http.post(f"{PREFIX}/rooms/room_nope/preview").status_code == 404


def test_the_publish_route_returns_the_ledger(http, http_room, transport):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": http_room["id"]},
    )
    ledger = http.post(f"{PREFIX}/rooms/{http_room['id']}/publish", json={}, params={"actor": "dana"}).json()
    assert ledger["counts"]["sent"] == 1
    assert len(transport.calls) == 1


def test_publish_can_be_narrowed_over_http(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    first = http.post(f"{PREFIX}/event-types", json=EVENT_TYPE).json()
    http.post(
        f"{PREFIX}/event-types",
        json=EVENT_TYPE | {"name": "dsr:second", "app_id": first["app_id"]},
    )
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": http_room["id"]},
    )
    ledger = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/publish", json={"event_type_ids": [first["id"]]}
    ).json()
    assert {entry["event_name"] for entry in ledger["sent"]} == {"dsr:room-viewed"}


def test_the_preview_route_writes_nothing(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/preview")
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert http.get("/api/audit", params={"collection": "outreach_delivery"}).json()["count"] == 0


def test_the_room_feed_route(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/feed").json()
    assert body["room"]["id"] == http_room["id"]
    assert body["ready"] is True
    assert body["cards"] == []


def test_the_deliveries_route_summarises_over_the_rows_it_returns(http, http_room, transport):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": http_room["id"]},
    )
    http.post(f"{PREFIX}/rooms/{http_room['id']}/publish", json={})
    listed = http.get(f"{PREFIX}/deliveries").json()
    assert listed["count"] == 1
    assert listed["summary"]["delivered"] == 1
    assert listed["summary"]["needs_manual_update"] == 0

    filtered = http.get(f"{PREFIX}/deliveries", params={"status": "failed"}).json()
    assert filtered["count"] == 0
    assert filtered["summary"]["failed"] == 0, "the summary covers the rows returned, not the whole log"

    scoped = http.get(f"{PREFIX}/deliveries", params={"room_id": http_room["id"]}).json()
    assert scoped["count"] == 1


def test_the_deliveries_route_filters_by_event_name_and_manual_need(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": http_room["id"]},
    )
    http.post(f"{PREFIX}/rooms/{http_room['id']}/publish", json={})
    assert http.get(f"{PREFIX}/deliveries", params={"event_name": "dsr:room-viewed"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/deliveries", params={"needs_manual_update": True}).json()["count"] == 0


def test_the_retry_route(http, http_room, transport):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": http_room["id"]},
    )
    ledger = http.post(f"{PREFIX}/rooms/{http_room['id']}/publish", json={}).json()
    delivery_id = ledger["deliveries"][0]

    assert http.get(f"{PREFIX}/deliveries/{delivery_id}").json()["id"] == delivery_id
    assert http.get(f"{PREFIX}/deliveries/outreach_delivery_nope").status_code == 400
    assert http.post(f"{PREFIX}/deliveries/outreach_delivery_nope/retry").status_code == 400

    retried = http.post(f"{PREFIX}/deliveries/{delivery_id}/retry").json()
    assert retried["outcome"] == "duplicate" or retried["outcome"] == "delivered"


def test_the_webhook_route_accepts_a_signed_delivery(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    payload = {
        "type": "mailing.replied",
        "sequence": 1,
        "payloadVersion": 2,
        "data": {"id": "m", "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_1"}}}},
    }
    body = body_of(payload)
    response = http.post(
        f"{PREFIX}/webhooks/outreach",
        content=body,
        headers={SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", body)},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["status"] == "recorded"


def test_the_webhook_route_refuses_an_unverifiable_delivery_with_401(http):
    http.post(f"{PREFIX}/apps", json=APP)
    response = http.post(f"{PREFIX}/webhooks/outreach", content=b"{}", headers={SIGNATURE_HEADER: "sha256=no"})
    assert response.status_code == 401
    assert response.json()["error"] == "bad_signature"


def test_the_webhook_route_is_428_before_a_secret_is_configured(http):
    response = http.post(f"{PREFIX}/webhooks/outreach", content=b"{}")
    assert response.status_code == 428
    assert response.json()["error"] == "not_configured"


def test_the_webhook_route_refuses_a_payload_version_it_does_not_speak(http):
    http.post(f"{PREFIX}/apps", json=APP)
    body = body_of({"type": "mailing.opened", "payloadVersion": 1, "data": {"id": "m"}})
    response = http.post(
        f"{PREFIX}/webhooks/outreach",
        content=body,
        headers={SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", body)},
    )
    assert response.status_code == 400


def test_the_webhook_route_signs_over_the_exact_bytes(http):
    """A re-serialised body would not verify, which is why the raw body is read."""
    http.post(f"{PREFIX}/apps", json=APP)
    raw = b'{"type": "mailing.opened", "payloadVersion": 2, "data": {"id": "m"}}'
    assert (
        http.post(
            f"{PREFIX}/webhooks/outreach",
            content=raw,
            headers={SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", raw)},
        ).status_code
        == 200
    )
    assert (
        http.post(
            f"{PREFIX}/webhooks/outreach",
            content=raw,
            headers={SIGNATURE_HEADER: webhooks.compute_signature("wrong", raw)},
        ).status_code
        == 401
    )


def test_the_signals_routes(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    payload = {
        "type": "mailing.opened",
        "sequence": 1,
        "payloadVersion": 2,
        "data": {"id": "m", "relationships": {"prospect": {"data": {"type": "prospect", "id": "p_1"}}}},
    }
    body = body_of(payload)
    http.post(
        f"{PREFIX}/webhooks/outreach",
        content=body,
        headers={SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", body)},
    )
    listed = http.get(f"{PREFIX}/signals").json()
    assert listed["count"] == 1
    assert listed["summary"]["recorded"] == 1
    assert listed["summary"]["intent"] == 1
    assert http.get(f"{PREFIX}/signals", params={"type": "replied"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/signals", params={"status": "ignored"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/signals").json()["count"] == 1


def test_the_room_signals_route_scopes(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/signals").json()["signals"] == []


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_write_is_audited(http, http_room):
    app_id = http.post(f"{PREFIX}/apps", json=APP).json()["id"]
    type_id = http.post(f"{PREFIX}/event-types", json=EVENT_TYPE).json()["id"]
    link_id = http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"}).json()["id"]
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": http_room["id"]},
    )
    http.post(f"{PREFIX}/rooms/{http_room['id']}/publish", json={})
    delivery_id = http.get(f"{PREFIX}/deliveries").json()["deliveries"][0]["id"]
    body = body_of({"type": "mailing.opened", "payloadVersion": 2, "data": {"id": "m"}})
    http.post(
        f"{PREFIX}/webhooks/outreach",
        content=body,
        headers={SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", body)},
    )
    http.patch(f"{PREFIX}/apps/{app_id}", json={"enabled": False})
    http.patch(f"{PREFIX}/event-types/{type_id}", json={"enabled": True})
    http.post(f"{PREFIX}/deliveries/{delivery_id}/retry")
    http.delete(f"{PREFIX}/rooms/{http_room['id']}/prospects/{link_id}")

    for collection in (
        "outreach_app",
        "outreach_event_type",
        "outreach_prospect",
        "outreach_delivery",
        "outreach_signal",
    ):
        assert http.get("/api/audit", params={"collection": collection}).json()["count"], collection


def test_audit_rows_name_the_route_that_actually_served_the_write(http, http_room, transport):
    """The defect the build brief calls out, pinned as a test.

    The audit row must name the route that served the write, and that route has to
    be one the host actually mounted. The registry reports route *templates*, so a
    recorded concrete path is matched against them rather than compared literally -
    and matched against the registry rather than by re-issuing the request, because
    a recorded DELETE no longer succeeds once it has run.
    """
    app_id = http.post(f"{PREFIX}/apps", json=APP).json()["id"]
    type_id = http.post(f"{PREFIX}/event-types", json=EVENT_TYPE).json()["id"]
    room_id = http_room["id"]
    link_id = http.post(f"{PREFIX}/rooms/{room_id}/prospects", json={"prospect_id": "p_1"}).json()["id"]
    http.post(
        "/api/records/activity",
        json={"action": "viewed", "person": "b@x.example"},
        params={"room_id": room_id},
    )
    transport.scripted = [not_found()]
    http.post(f"{PREFIX}/rooms/{room_id}/publish", json={})
    delivery_id = http.get(f"{PREFIX}/deliveries").json()["deliveries"][0]["id"]
    http.post(f"{PREFIX}/deliveries/{delivery_id}/retry")
    body = body_of({"type": "mailing.opened", "payloadVersion": 2, "data": {"id": "m"}})
    http.post(
        f"{PREFIX}/webhooks/outreach",
        content=body,
        headers={SIGNATURE_HEADER: webhooks.compute_signature("whsec-xyz", body)},
    )
    http.patch(f"{PREFIX}/apps/{app_id}", json={"enabled": False})
    http.patch(f"{PREFIX}/event-types/{type_id}", json={"enabled": True})
    http.delete(f"{PREFIX}/rooms/{room_id}/prospects/{link_id}")

    entries = http.get("/api/audit", params={"limit": 200}).json()["entries"]
    sources = {entry["source"] for entry in entries}
    for expected in (
        f"POST {PREFIX}/apps",
        f"POST {PREFIX}/event-types",
        f"POST {PREFIX}/rooms/{room_id}/prospects",
        f"POST {PREFIX}/rooms/{room_id}/publish",
        f"POST {PREFIX}/deliveries/{delivery_id}/retry",
        f"POST {PREFIX}/webhooks/outreach",
        f"PATCH {PREFIX}/apps/{app_id}",
        f"PATCH {PREFIX}/event-types/{type_id}",
        f"DELETE {PREFIX}/rooms/{room_id}/prospects/{link_id}",
    ):
        assert expected in sources, f"{expected!r} missing from audit sources {sorted(sources)}"

    templates = [
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature.get("routes", [])
        for method in route["methods"]
    ]
    # Plus the core routes the test also writes through, so the assertion covers
    # every audit row rather than only this feature's.
    templates += [
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if method not in ("HEAD", "OPTIONS")
    ]
    for entry in entries:
        verb, _, path = entry["source"].partition(" ")
        assert any(
            verb == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
            for method, template in templates
        ), f"audit names a route the app does not serve: {entry['source']}"


def test_no_audit_source_names_another_features_prefix(http, http_room):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/event-types", json=EVENT_TYPE)
    http.post(f"{PREFIX}/rooms/{http_room['id']}/prospects", json={"prospect_id": "p_1"})
    http.post(f"{PREFIX}/rooms/{http_room['id']}/publish", json={})
    sources = [entry["source"] for entry in http.get("/api/audit", params={"limit": 200}).json()["entries"]]
    ours = [source for source in sources if PREFIX in source]
    assert ours
    for other in ("/api/crm", "/api/analytics", "/api/wf-016"):
        assert not [source for source in sources if other in source and PREFIX not in source]


def test_a_refused_write_leaves_no_audit_row(http):
    assert http.post(f"{PREFIX}/apps", json={}).status_code == 400
    assert http.get("/api/audit", params={"collection": "outreach_app"}).json()["count"] == 0
    assert http.post(f"{PREFIX}/rooms/room_nope/publish", json={}).status_code == 404
    assert http.get("/api/audit", params={"collection": "outreach_delivery"}).json()["count"] == 0


def test_a_refused_webhook_leaves_no_audit_row(http):
    http.post(f"{PREFIX}/apps", json=APP)
    http.post(f"{PREFIX}/webhooks/outreach", content=b"{}", headers={SIGNATURE_HEADER: "sha256=no"})
    assert http.get("/api/audit", params={"collection": "outreach_signal"}).json()["count"] == 0


def test_the_domain_methods_that_write_require_a_source():
    """``source`` is required so a hardcoded URL cannot creep back in."""
    import inspect

    for name in (
        "register_app",
        "update_app",
        "delete_app",
        "create_event_type",
        "update_event_type",
        "delete_event_type",
        "link_prospect",
        "unlink_prospect",
        "publish",
        "retry_delivery",
        "receive_webhook",
    ):
        signature = inspect.signature(getattr(FeedPublisher, name))
        parameter = signature.parameters["source"]
        assert parameter.default is inspect.Parameter.empty, f"{name} has an optional source"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, f"{name} should take source as a keyword"


def test_the_publisher_never_opens_the_database_itself():
    source = (Path(__file__).resolve().parents[1] / "dsr" / "outreach_feed").rglob("*.py")
    for module in source:
        text = module.read_text(encoding="utf-8")
        assert "sqlite3" not in text, f"{module.name} reaches for sqlite3 directly"
        assert "dsr.db.audited" not in text, f"{module.name} opens the database outside the store"


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_every_inference_says_what_the_research_does_and_does_not_say():
    for entry in INFERENCES:
        assert entry["basis"], entry["id"]
        assert entry["why"], entry["id"]
        assert entry["change_it"], entry["id"]
        assert entry["blast_radius"], entry["id"]


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_named_inferences_cover_every_judgement_the_module_docstring_claims():
    ids = {entry["id"] for entry in INFERENCES}
    assert {
        "retry-policy",
        "webhook-signature-encoding",
        "deep-link-shape",
        "event-name-charset",
        "prospect-linkage",
        "event-fallthrough",
        "s2s-token-storage",
        "before-update-block",
        "inbound-idempotency",
        "poll-not-push",
    } <= ids


def test_the_retry_policy_inference_agrees_with_the_code():
    from dsr.outreach_feed.delivery import DEFAULT_BACKOFF as backoff
    from dsr.outreach_feed.inferences import by_id

    entry = by_id("retry-policy")
    assert entry["value"]["max_attempts"] == DEFAULT_MAX_ATTEMPTS
    assert entry["value"]["backoff_seconds"] == backoff
    assert entry["value"]["timeout_seconds"] == SENDER_TIMEOUT_SECONDS


def test_the_no_retry_statement_is_quoted_in_the_sourced_evidence():
    assert any("does not retry webhook deliveries" in quote for quote in SOURCED_QUOTES)
    assert any("{{prospect}}" in quote for quote in SOURCED_QUOTES)
    assert any("5 seconds" in quote for quote in SOURCED_QUOTES)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def demo_context(room_count: int = 4):
    import random
    from datetime import datetime, timezone

    return {
        "room_ids": [(f"room_{index}", f"acct_{index}") for index in range(room_count)],
        "now": datetime(2026, 9, 27, tzinfo=timezone.utc),
        "rng": random.Random("wf-026"),
    }


def seeded(tmp_path, room_count: int = 4):
    """The feature's own ``seed(db, context)``, which replaced its seed.py edit."""
    db = AuditedDatabase(tmp_path / "demo.db", mirror_dir=tmp_path / "mirror")
    store = RecordStore(db)
    context = demo_context(room_count)
    for room_id, account in context["room_ids"]:
        # Explicit ids, because the seeder hands the feature the ids it created and
        # a mismatch would silently skip every room-scoped row.
        store.create(
            "room", {"name": account, "account": account}, record_id=room_id, actor="dana", source="seed"
        )
        for action in ("viewed", "downloaded", "commented", "shared"):
            store.create(
                "activity",
                {"person": "b@x.example", "action": action, "target": "Deck", "account": account},
                room_id=room_id,
                actor="system",
                source="seed",
            )
    return db, store, load_feature(MODULE).seed(db, context)


def delivery_summary_shape(row):
    return delivery_summary(row)


def test_the_seed_leaves_readable_configuration(tmp_path):
    db, store, summary = seeded(tmp_path)
    try:
        assert len(store.list(APP_COLLECTION)) == 2
        assert len(store.list(EVENT_TYPE_COLLECTION)) == 3
        assert len(store.list(PROSPECT_COLLECTION)) == 3
        assert "2 apps" in summary
    finally:
        db.close()


def test_the_seed_covers_the_states_a_reviewer_needs(tmp_path):
    """Not just the happy path: a failed delivery, a retry, a skip, and signals."""
    db, store, _ = seeded(tmp_path)
    try:
        rows = [delivery_summary_shape(row) for row in store.list(DELIVERY_COLLECTION, limit=1000)]
        assert any(row["status"] == STATUS_DELIVERED for row in rows)
        assert any(row["status"] == STATUS_FAILED and row["needs_manual_update"] for row in rows)
        assert any(row["status"] == STATUS_SKIPPED and row["skip_reason"] == "no_prospect" for row in rows)
        assert any(row["attempts"] > 1 for row in rows), "the demo must show a retried delivery"
    finally:
        db.close()


def test_the_seed_records_four_signal_states(tmp_path):
    db, store, _ = seeded(tmp_path)
    try:
        signals = [row["data"] for row in store.list(SIGNAL_COLLECTION)]
        statuses = {row["status"] for row in signals}
        assert statuses == {"recorded", "ignored"}
        assert {row["link_status"] for row in signals} == {"linked", "unlinked"}
        assert any(row["intent"] for row in signals)
        assert any(row["before_update"] for row in signals)
    finally:
        db.close()


def test_the_seed_never_opens_a_socket(tmp_path):
    """A real transport would try to POST to api.outreach.io from the seeder."""
    db, store, _ = seeded(tmp_path)
    try:
        rows = [row["data"] for row in store.list(DELIVERY_COLLECTION, limit=1000)]
        attempted = [row for row in rows if row.get("attempts")]
        assert attempted
        for row in attempted:
            assert row["http_status"] in (201, 404), "a scripted transport, not a real request"
            assert row["request_headers"]["Authorization"] == REDACTED
    finally:
        db.close()


def test_the_seed_never_leaves_the_token_in_a_delivery_row(tmp_path):
    db, store, _ = seeded(tmp_path)
    try:
        for collection in (DELIVERY_COLLECTION, EVENT_TYPE_COLLECTION, PROSPECT_COLLECTION, SIGNAL_COLLECTION):
            assert "demo-s2s-token" not in json.dumps(store.list(collection, limit=1000), default=str)
    finally:
        db.close()


def test_the_token_is_stored_on_the_app_record_and_therefore_in_the_audit_trail(tmp_path):
    """A limitation worth stating rather than asserting away.

    The audit log records the complete before/after state of every change, and this
    package may not add a typed column to keep a credential out of ``data``. So a
    token stored on an app record is in that record's audit rows. What the build
    does control - a read never returns it, and a delivery row's recorded headers
    have the ``Authorization`` value redacted - is asserted above and here.
    """
    db, store, _ = seeded(tmp_path)
    try:
        assert "demo-s2s-token" in json.dumps(store.list(APP_COLLECTION, limit=100), default=str)
        audit = json.dumps(store.audit(collection=APP_COLLECTION, limit=100), default=str)
        assert "demo-s2s-token" in audit
        for row in store.list(APP_COLLECTION, limit=100):
            summary = app_summary(row)
            assert "token" not in summary
            assert summary["has_token"] is True
    finally:
        db.close()


def test_the_seed_survives_having_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "empty.db")
    try:
        summary = load_feature(MODULE).seed(db, {"room_ids": []})
        assert "no rooms" in summary
        assert len(RecordStore(db).list(APP_COLLECTION)) == 2
    finally:
        db.close()


def test_the_seed_survives_a_stale_room_id(tmp_path):
    db = AuditedDatabase(tmp_path / "stale.db")
    try:
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [("room_missing", "nope")], "now": None, "rng": None}
        )
        assert "no rooms" in summary
    finally:
        db.close()


def test_the_demo_transport_draws_its_line_on_the_prospect_id():
    """The demo's healthy / rate-limited / retired split is a real retry ladder.

    Asserted directly, because the demo is what a reviewer reads first and a
    scripted transport that quietly answered 201 to everything would make the whole
    page a lie.
    """
    module = load_feature(MODULE)
    transport = module.DemoTransport()

    def send(prospect_id: str) -> PostResult:
        payload = {"data": {"relationships": {"prospect": {"data": {"id": prospect_id}}}}}
        return transport.post(
            "https://api.outreach.io/api/v2/events",
            body_of(payload),
            {"Authorization": "Bearer x"},
            5.0,
        )

    assert send("pros_ok").ok is True
    assert send("pros_retired").status == 404
    assert send("pros_retired").retryable is False
    first = send("pros_limited")
    assert (first.status, first.retryable) == (429, True)
    assert send("pros_limited").ok is True, "the retry succeeds, which is the retried row in the demo"
