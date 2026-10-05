"""WF-082's HTTP surface, through the mounted router.

These tests exercise the routes the host discovered and mounted, not the engine directly, so
they cover the three things the domain tests cannot:

  - **the multipart shape.** The specification says the request is ``multipart/form-data``
    with the payload in a part named ``json``. A handler that called ``request.json()`` would
    pass every domain test and refuse every real delivery, because nothing about the digest
    arithmetic changes when the bytes are never read from the right place.
  - **the audit source rule.** Every write this router performs must name a route the app
    actually serves. The contract calls that defect by name: a feature's audit log kept
    recording a path the app had stopped serving.
  - **the contract guards.** No shared file, no ``dsr.api`` import, no direct SQLite, and a
    prefix that does not collide.

Every test here uses the shared ``client`` fixture from ``conftest.py``, so each one runs
against a fresh database and no test can see another's rows. That is what makes the file
pass on its own and in a full run under xdist.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from dsr.features import wf082_verify_and_ip_allowlist_inbound_provider as module
from dsr.security_governance import webhook_signing as signing, webhook_vocabulary as vocab
from dsr.store import RecordStore

PREFIX = module.router.prefix
API_KEY = "northwind-http-api-key"
EVENT_TIME = "2026-03-04T18:22:31Z"
EVENT_TYPE = vocab.SIGNATURE_REQUEST_ALL_SIGNED

#: The peer address the TestClient presents. It is the literal string ``testclient``, which is
#: not an IP address, so an allowlist check can never admit it.
#:
#: The range file is seeded with the loopback address instead, and :func:`post_event` installs
#: a real peer address on the client for each request. That is the honest way to test this:
#: the allowlist check reads the socket peer and nothing else, so a test that wanted to get
#: past it by sending a header would be testing a code path this build deliberately does not
#: have. One test asserts exactly that, by sending the header and seeing it ignored.
LOOPBACK = "127.0.0.1"
RANGES = [{"ip": f"{LOOPBACK}/32", "description": "the loopback address, used by this suite"}]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def event_payload(
    *,
    event_id: str = "evt_http_001",
    event_type: str = EVENT_TYPE,
    event_time: str = EVENT_TIME,
    api_key: str = API_KEY,
    event_hash: str | None = None,
) -> dict:
    event = {
        "event_time": event_time,
        "event_type": event_type,
        "event_id": event_id,
        "event_hash": (
            event_hash
            if event_hash is not None
            else signing.event_hash(api_key, event_time, event_type)
        ),
        "event_metadata": {"related_signature_id": "sr_http_001"},
    }
    return {"event": event, "signature_request": {"id": "sr_http_001"}}


def signed_part(payload: dict) -> tuple[bytes, str]:
    """The ``json`` part's bytes and the digest a provider would sign over them.

    The part is ``json.dumps(payload)`` and nothing else. The digest covers **that**, not the
    multipart envelope around it, because the specification says "``echo -n $json | openssl
    dgst -sha256 -hmac $apiKey``" and ``$json`` is the payload. Signing the envelope is the
    mistake this helper exists to make impossible: it produces a well-formed base64 header over
    the wrong bytes, and every delivery is then refused at the second check with a message
    about a proxy.
    """
    part = json.dumps(payload).encode("utf-8")
    return part, signing.content_sha256(API_KEY, part)


def multipart_body(part: bytes) -> bytes:
    """A real ``multipart/form-data`` body carrying ``part`` in a part named ``json``.

    Built here rather than through a client helper because the test needs to know exactly which
    bytes the digest covers, and because a helper that re-serialised the JSON between the
    signature and the request would change them.
    """
    boundary = "----dsrwf082boundary"
    return (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{vocab.JSON_PART}"\r\n'
            "Content-Type: application/json\r\n"
            "\r\n"
        ).encode("utf-8")
        + part
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )


def named_part_body(name: str, part: bytes) -> bytes:
    """The same body with the part under a different name."""
    boundary = "----dsrwf082boundary"
    return (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            "Content-Type: application/json\r\n"
            "\r\n"
        ).encode("utf-8")
        + part
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )


def post_event(client, room_id: str, payload: dict, *, api_key: str = API_KEY, **params):
    """POST a signed multipart delivery the way the provider does."""
    _part, digest = signed_part(payload)
    if api_key != API_KEY:
        digest = signing.content_sha256(api_key, json.dumps(payload).encode("utf-8"))
    return post_raw(
        client, room_id, multipart_body(json.dumps(payload).encode()), header=digest, **params
    )


def post_raw(
    client,
    room_id: str,
    raw: bytes,
    *,
    header: str | None,
    peer: str = LOOPBACK,
    extra_headers: dict | None = None,
    content_type: str = "multipart/form-data; boundary=----dsrwf082boundary",
    **params,
):
    """POST a raw body with an explicit peer address and an explicit digest.

    Both are arguments rather than implicit, because the allowlist check reads the socket peer
    and the content check reads the bytes. A test that could not vary either could not exercise
    a refusal.
    """
    headers = {"Content-Type": content_type}
    if header is not None:
        headers[vocab.CONTENT_SHA256_HEADER] = header
    headers.update(extra_headers or {})
    return post_with_peer(
        client,
        f"{PREFIX}/rooms/{room_id}/events",
        raw,
        headers=headers,
        peer=peer,
        params=params,
    )


def post_with_peer(client, path: str, content: bytes, *, headers: dict, peer: str, params=None):
    """One request through a transport whose socket peer is ``peer``.

    ``TestClient`` presents the literal string ``testclient`` as the peer. This handler reads
    ``request.client.host`` and refuses anything that is not an IP address, so every delivery
    would be refused at the first check without this.

    ``_TestClientTransport`` copies its ``client`` tuple straight into the ASGI scope, so that
    attribute is the seam. Injecting the peer is the honest way to reach the later checks: it
    changes the socket, not the allowlist, so the IP check still decides on a real address and
    a test cannot get past it by declaring one. It is restored afterwards because the client is
    module-scoped and shared by every test in this file.
    """
    transport = client._transport  # noqa: SLF001 - the documented seam for the peer address
    original = transport.client
    transport.client = (peer, 54321)
    try:
        return client.post(path, content=content, headers=headers, params=params)
    finally:
        transport.client = original


def ready_room(client, room_id: str = "room_http", url: str = "https://hooks.example/wf082") -> str:
    """A room with a registration and a range snapshot that admits the TestClient."""
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/callbacks",
        json={"callback_url": url, "api_key": API_KEY, "scope": "account"},
    )
    assert created.status_code == 201, created.text
    refreshed = client.post(f"{PREFIX}/rooms/{room_id}/ranges", json={"ranges": RANGES})
    assert refreshed.status_code == 201, refreshed.text
    return created.json()["id"]


# --------------------------------------------------------------------------- #
# The contract guards
# --------------------------------------------------------------------------- #


def test_the_feature_module_opens_no_connection_and_imports_no_app(module_text=None):
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_domain_modules_import_nothing_but_the_store_and_their_own_package():
    """The enforced rule, applied to the four modules and the engine."""
    package = Path(module.__file__).resolve().parents[1] / "security_governance"
    for name in (
        "webhook_vocabulary.py",
        "webhook_signing.py",
        "webhook_rules.py",
        "webhook_inferences.py",
        "webhook_engine.py",
    ):
        text = (package / name).read_text(encoding="utf-8")
        assert "from dsr.api" not in text, name
        assert "import dsr.api" not in text, name
        assert "import sqlite3" not in text, name
        assert "sqlite3.connect" not in text, name
        assert "from fastapi" not in text, name
        assert "import fastapi" not in text, name


def test_the_feature_owns_no_shared_file():
    shared = {
        "backend/dsr/api.py",
        "backend/dsr/deps.py",
        "backend/dsr/store.py",
        "backend/dsr/db/audited.py",
        "backend/seed.py",
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/src/lib/api.js",
        "frontend/src/lib/features.js",
        "frontend/src/components/ui.jsx",
        "frontend/vite.config.js",
    }
    root = Path(__file__).resolve().parents[2]
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available to measure the diff")
    if not changed:
        pytest.skip("no committed diff against origin/main to measure")
    assert not (set(changed) & shared), sorted(set(changed) & shared)


def test_the_prefix_is_the_one_the_issue_names_and_is_unique():
    assert PREFIX == "/api/wf082"
    assert module.FEATURE["ticket"] == "WF-082"
    assert module.FEATURE["id"].startswith("wf-082-")


def test_the_router_is_mounted_by_discovery_and_reports_no_failure():
    response = TestClient_get_features()
    assert response.status_code == 200
    payload = response.json()
    listed = [row for row in payload["features"] if row["id"] == module.FEATURE["id"]]
    assert listed, "the feature is not listed by /api/features"
    assert listed[0]["loaded"] is True
    assert listed[0]["error"] == ""
    assert listed[0]["prefix"] == PREFIX
    assert listed[0]["routes"], "a router with no routes fails the host's own test"


def TestClient_get_features():
    """``/api/features`` through the shared module client, without a fixture argument."""
    from conftest import app  # type: ignore[import-not-found]
    from fastapi.testclient import TestClient

    return TestClient(app).get("/api/features")


def test_the_exception_handlers_are_this_workflows_own_types():
    from dsr.security_governance.webhook_engine import (
        WebhookRegistrationInvalid,
        WebhookRegistrationNotFound,
    )

    assert set(module.EXCEPTION_HANDLERS) == {
        WebhookRegistrationInvalid,
        WebhookRegistrationNotFound,
    }
    # A handler for a builtin would intercept that exception across the whole application.
    for handled in module.EXCEPTION_HANDLERS:
        assert handled.__module__.startswith("dsr.security_governance")


# --------------------------------------------------------------------------- #
# The board, the vocabulary and the retry contract
# --------------------------------------------------------------------------- #


def test_the_summary_answers_without_a_room(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert response.json()["ticket"] == "WF-082"


def test_the_summary_for_one_room_counts_what_the_room_has_recorded(client):
    ready_room(client)
    post_event(client, "room_http", event_payload())
    payload = client.get(f"{PREFIX}/summary", params={"room_id": "room_http"}).json()
    assert payload["verified"] == 1
    assert payload["callbacks"] == 1
    assert payload["ranges"] == len(RANGES)


def test_the_vocabulary_publishes_the_headers_the_part_name_and_the_two_digests(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["user_agent"] == vocab.USER_AGENT
    assert payload["content_sha256_header"] == vocab.CONTENT_SHA256_HEADER
    assert payload["json_part"] == vocab.JSON_PART
    assert payload["check_order"] == list(vocab.CHECK_ORDER)
    assert payload["event_hash"]["separator"] == ""
    assert payload["content_sha256"]["encoding"] == "base64"
    assert payload["content_sha256"]["covers"] != payload["event_hash"]["input_fields"]


def test_the_vocabulary_says_the_acknowledgement_is_a_magic_string(client):
    acknowledgement = client.get(f"{PREFIX}/vocabulary").json()["acknowledgement"]
    assert acknowledgement["body"] == vocab.ACKNOWLEDGEMENT_BODY
    assert acknowledgement["status"] == 200


def test_the_retry_policy_is_served_as_data_and_matches_the_research(client):
    payload = client.get(f"{PREFIX}/retry-policy").json()
    assert payload["provider_timeout_seconds"] == 30
    assert [row["interval_seconds"] for row in payload["retry_ladder"]] == list(
        vocab.RETRY_LADDER_SECONDS
    )
    assert payload["consecutive_failure_limit"] == 10


def test_the_retry_policy_acknowledgement_is_the_same_magic_string(client):
    payload = client.get(f"{PREFIX}/retry-policy").json()
    assert payload["acknowledgement"]["body"] == vocab.ACKNOWLEDGEMENT_BODY


def test_the_error_codes_route_serves_a_total_machine_readable_catalogue(client):
    payload = client.get(f"{PREFIX}/error-codes").json()
    assert payload["codes_name"] == "x-error-codes"
    assert payload["events_name"] == "x-error-events"
    assert payload["fields"] == list(vocab.ERROR_CODE_FIELDS)
    for name, entry in payload["codes"].items():
        for field in vocab.ERROR_CODE_FIELDS:
            assert field in entry, f"{name} is missing {field}"


def test_the_whoami_route_names_the_route_the_provider_should_aim_at(client):
    payload = client.get(f"{PREFIX}/whoami").json()
    assert payload["path_template"] == f"POST {PREFIX}/rooms/{{room_id}}/events"
    assert payload["json_part"] == vocab.JSON_PART
    assert payload["acknowledgement"]["body"] == vocab.ACKNOWLEDGEMENT_BODY
    assert vocab.SIGNATURE_REQUEST_DOWNLOADABLE in payload["event_types"]


def test_the_decisions_route_serves_every_derivation(client):
    payload = client.get(f"{PREFIX}/decisions").json()
    assert payload["count"] >= 6
    for decision in payload["decisions"]:
        assert decision["chosen"] in decision["options"]
        assert decision["rejected_because"]


def test_one_decision_can_be_read_by_its_id(client):
    response = client.get(f"{PREFIX}/decisions/INFERRED_DUPLICATE_IS_ACKNOWLEDGED")
    assert response.status_code == 200
    assert response.json()["chosen"] == "acknowledge_as_duplicate"


def test_an_unknown_decision_is_a_404_that_names_the_ids_that_exist(client):
    response = client.get(f"{PREFIX}/decisions/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.json()["known"]


# --------------------------------------------------------------------------- #
# Registrations
# --------------------------------------------------------------------------- #


def test_a_registration_is_created_and_the_key_is_never_returned(client):
    response = client.post(
        f"{PREFIX}/rooms/room_http/callbacks",
        json={"callback_url": "https://hooks.example/wf082", "api_key": API_KEY},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["sealed"] is True
    assert API_KEY not in json.dumps(payload)


def test_a_plain_http_registration_is_refused_with_a_field_keyed_message(client):
    response = client.post(
        f"{PREFIX}/rooms/room_http/callbacks",
        json={"callback_url": "http://hooks.example/wf082", "api_key": API_KEY},
    )
    assert response.status_code == 400
    assert "callback_url" in response.json()["errors"]


def test_a_registration_with_no_api_key_is_refused_with_a_field_keyed_message(client):
    response = client.post(
        f"{PREFIX}/rooms/room_http/callbacks",
        json={"callback_url": "https://hooks.example/wf082"},
    )
    assert response.status_code == 400
    assert "api_key" in response.json()["errors"]


def test_a_registration_list_carries_no_sealed_value(client):
    ready_room(client)
    response = client.get(f"{PREFIX}/rooms/room_http/callbacks")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert API_KEY not in response.text


def test_one_registration_can_be_read_by_its_id(client):
    callback_id = ready_room(client)
    response = client.get(f"{PREFIX}/callbacks/{callback_id}")
    assert response.status_code == 200
    assert response.json()["callback_url"] == "https://hooks.example/wf082"
    assert "snapshot" in response.json()


def test_an_unknown_registration_is_a_404(client):
    assert client.get(f"{PREFIX}/callbacks/nope").status_code == 404


def test_a_callback_url_can_be_patched(client):
    callback_id = ready_room(client)
    response = client.patch(
        f"{PREFIX}/callbacks/{callback_id}",
        json={"callback_url": "https://hooks2.example/wf082"},
    )
    assert response.status_code == 200
    assert response.json()["callback_url"] == "https://hooks2.example/wf082"


def test_patching_a_callback_url_back_to_http_is_refused(client):
    callback_id = ready_room(client)
    response = client.patch(
        f"{PREFIX}/callbacks/{callback_id}",
        json={"callback_url": "http://hooks2.example/wf082"},
    )
    assert response.status_code == 400
    assert "callback_url" in response.json()["errors"]


def test_patching_the_api_key_is_refused_and_says_what_to_do_instead(client):
    callback_id = ready_room(client)
    response = client.patch(f"{PREFIX}/callbacks/{callback_id}", json={"api_key": "another"})
    assert response.status_code == 400
    assert "api_key" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# The allowlist
# --------------------------------------------------------------------------- #


def test_a_range_snapshot_is_stored_and_readable_with_its_age(client):
    ready_room(client)
    payload = client.get(f"{PREFIX}/rooms/room_http/ranges").json()
    assert payload["range_count"] == len(RANGES)
    assert payload["source_url"] == vocab.IP_RANGES_URL
    assert payload["snapshot_at"]


def test_the_ranges_route_refuses_to_fetch_on_its_own(client):
    """`INFERRED_BOUNDED_ALLOWLIST_REFRESH`: nothing on the delivery path opens a socket."""
    response = client.post(f"{PREFIX}/rooms/room_http/ranges", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "ranges_required"
    assert vocab.IP_RANGES_URL in response.json()["detail"]


def test_a_stale_snapshot_is_reported_as_stale(client):
    """An empty allowlist or an old one both have to be visible on the page."""
    response = client.get(f"{PREFIX}/rooms/room_with_no_snapshot/ranges")
    assert response.status_code == 200
    assert response.json()["stale"] is True


# --------------------------------------------------------------------------- #
# The callback. The multipart shape is the point of these tests.
# --------------------------------------------------------------------------- #


def test_a_signed_multipart_delivery_is_verified_and_acknowledged(client):
    ready_room(client)
    response = post_event(client, "room_http", event_payload())
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == vocab.VERIFIED
    assert payload["acknowledgement"] == vocab.ACKNOWLEDGEMENT_BODY


def test_the_acknowledgement_body_carries_the_magic_string_on_a_first_delivery(client):
    """The provider checks the body, not the status, so the field has to be there."""
    ready_room(client)
    response = post_event(client, "room_http", event_payload())
    assert vocab.ACKNOWLEDGEMENT_BODY in response.text


def test_a_retry_of_the_same_event_is_acknowledged_rather_than_refused(client):
    ready_room(client)
    first = post_event(client, "room_http", event_payload())
    second = post_event(client, "room_http", event_payload())
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["state"] == vocab.DUPLICATE
    assert second.json()["acknowledgement"] == vocab.ACKNOWLEDGEMENT_BODY


def test_a_delivery_whose_body_is_raw_json_rather_than_multipart_is_refused(client):
    """`request.json()` would have accepted this and rejected every real delivery."""
    ready_room(client)
    raw = json.dumps(event_payload()).encode("utf-8")
    response = post_raw(
        client,
        "room_http",
        raw,
        header=signing.content_sha256(API_KEY, raw),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["error"] == "payload_part_missing"


def test_a_multipart_delivery_with_the_part_named_something_else_is_refused(client):
    ready_room(client)
    boundary = "----dsrwf082boundary"
    body = json.dumps(event_payload())
    raw = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="payload"\r\n'
        "\r\n"
        f"{body}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    response = post_raw(client, "room_http", raw, header=signing.content_sha256(API_KEY, raw))
    assert response.status_code == 400
    assert response.json()["error"] == "payload_part_missing"


def test_a_json_part_that_does_not_parse_is_refused_with_its_own_name(client):
    ready_room(client)
    boundary = "----dsrwf082boundary"
    raw = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{vocab.JSON_PART}"\r\n'
        "\r\n"
        "{not json at all\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    response = post_raw(client, "room_http", raw, header=signing.content_sha256(API_KEY, raw))
    assert response.status_code == 400
    assert response.json()["error"] == "payload_not_json"


def test_a_json_part_that_parses_to_an_array_is_refused_with_the_same_name(client):
    """An array cannot name an event, and the remedy is the same as for unparseable JSON."""
    ready_room(client)
    boundary = "----dsrwf082boundary"
    raw = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{vocab.JSON_PART}"\r\n'
        "\r\n"
        "[1, 2, 3]\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    response = post_raw(client, "room_http", raw, header=signing.content_sha256(API_KEY, raw))
    assert response.status_code == 400
    assert response.json()["error"] == "payload_not_json"


def test_a_delivery_with_no_content_sha256_header_is_refused_at_the_second_check(client):
    ready_room(client)
    part, _digest = signed_part(event_payload())
    response = post_raw(client, "room_http", multipart_body(part), header=None)
    assert response.status_code == 401
    assert response.json()["error_name"] == "content_sha256_missing"


def test_a_delivery_whose_payload_was_re_encoded_is_refused_at_the_second_check(client):
    """The exact defect the catalogue's remediation for a proxy names."""
    ready_room(client)
    part, _digest = signed_part(event_payload())
    response = post_raw(
        client,
        "room_http",
        multipart_body(part),
        header=signing.content_sha256(API_KEY, part + b" "),
    )
    assert response.status_code == 401
    assert response.json()["error_name"] == "content_sha256_mismatch"


def test_a_delivery_signed_over_the_multipart_envelope_rather_than_the_part_is_refused(client):
    """The mistake :func:`signed_part` exists to prevent, stated as a test.

    The envelope is a legal base64 digest over bytes the provider never meant to sign, so it
    parses and then does not match. A sender doing this gets a message about a proxy, which is
    the right answer and is worth knowing about.
    """
    ready_room(client)
    part, _digest = signed_part(event_payload())
    envelope = multipart_body(part)
    response = post_raw(
        client, "room_http", envelope, header=signing.content_sha256(API_KEY, envelope)
    )
    assert response.status_code == 401
    assert response.json()["error_name"] == "content_sha256_mismatch"


def test_a_delivery_whose_header_is_not_base64_is_refused_with_its_own_name(client):
    """The repaired defect, over real HTTP rather than in the domain."""
    ready_room(client)
    part, _digest = signed_part(event_payload())
    response = post_raw(client, "room_http", multipart_body(part), header="!!! not base64 !!!")
    assert response.status_code == 401
    assert response.json()["error_name"] == "content_sha256_malformed"
    assert response.json()["catalogue"]["remediation"]


def test_a_delivery_signed_with_another_key_is_refused_at_the_third_check(client):
    """Two good digests over a payload the registration's key did not sign."""
    ready_room(client)
    payload = event_payload(api_key="a different key")
    response = post_event(client, "room_http", payload, api_key=API_KEY)
    assert response.status_code == 401
    assert response.json()["error_name"] == "event_hash_mismatch"


def test_a_delivery_with_a_separator_in_the_hmac_input_is_refused(client):
    """The one character the whole workflow turns on, over real HTTP."""
    ready_room(client)
    wrong = signing.digest_over(API_KEY, f"{EVENT_TIME}:{EVENT_TYPE}")
    response = post_event(client, "room_http", event_payload(event_hash=wrong))
    assert response.status_code == 401
    assert response.json()["error_name"] == "event_hash_mismatch"


def test_a_delivery_with_no_event_id_is_refused_by_its_own_name(client):
    ready_room(client)
    payload = event_payload()
    payload["event"].pop("event_id")
    response = post_event(client, "room_http", payload)
    assert response.status_code == 400
    assert response.json()["error_name"] == "event_id_missing"


def test_a_delivery_to_a_room_with_no_registration_is_a_404_naming_what_to_register(client):
    response = post_event(client, "room_unregistered", event_payload())
    assert response.status_code == 404
    assert response.json()["error"] == "registration_not_found"
    assert "callbacks" in response.json()["remedy"]


def test_a_delivery_with_two_registrations_must_name_one(client):
    ready_room(client)
    client.post(
        f"{PREFIX}/rooms/room_http/callbacks",
        json={"callback_url": "https://second.example/wf082", "api_key": API_KEY},
    )
    response = post_event(client, "room_http", event_payload())
    assert response.status_code == 400
    assert vocab.CREDENTIAL_HEADER in response.json()["detail"]


def test_a_delivery_that_names_one_of_two_registrations_is_verified(client):
    ready_room(client)
    second = client.post(
        f"{PREFIX}/rooms/room_http/callbacks",
        json={"callback_url": "https://second.example/wf082", "api_key": API_KEY},
    ).json()
    response = post_event(client, "room_http", event_payload(), callback_id=second["id"])
    assert response.status_code == 200
    assert response.json()["state"] == vocab.VERIFIED


def test_the_source_address_is_the_socket_peer_and_a_forwarded_header_is_ignored(client):
    """A declared address is chosen by whoever sent the request, so it is not trusted.

    The handler refuses everything from an address outside the allowlist, and these headers
    name an address inside it. If either were honoured the delivery would verify, so a 200
    here is not the assertion: the recorded ``source_ip`` is.
    """
    ready_room(client)
    part, digest = signed_part(event_payload())
    response = post_raw(
        client,
        "room_http",
        multipart_body(part),
        header=digest,
        extra_headers={
            "X-Forwarded-For": "203.0.113.99",
            "X-Real-IP": "203.0.113.98",
            "Forwarded": "for=203.0.113.97",
        },
    )
    assert response.status_code == 200
    row = client.get(f"{PREFIX}/rooms/room_http/deliveries").json()["deliveries"][0]
    assert row["source_ip"] == LOOPBACK
    assert row["source_ip"] not in ("203.0.113.99", "203.0.113.98", "203.0.113.97")


def test_a_delivery_from_an_address_outside_the_allowlist_is_refused_at_the_first_check(client):
    """A different socket peer, a different verdict, and the same three checks."""
    ready_room(client)
    part, digest = signed_part(event_payload())
    response = post_raw(client, "room_http", multipart_body(part), header=digest, peer="198.18.0.9")
    assert response.status_code == 403
    assert response.json()["error_name"] == "source_ip_not_allowed"
    assert response.json()["failed_check"] == vocab.IP_ALLOWLIST


def test_the_first_failing_check_is_the_one_that_refuses_over_http(client):
    """A bad address and a bad digest refuse at the IP check, not at a later one."""
    ready_room(client)
    part = json.dumps(event_payload(api_key="wrong")).encode("utf-8")
    response = post_raw(
        client,
        "room_http",
        multipart_body(part),
        header=signing.content_sha256("wrong", part),
        peer="198.18.0.9",
    )
    assert response.status_code == 403
    assert response.json()["failed_check"] == vocab.IP_ALLOWLIST


def test_a_delivery_to_an_empty_allowlist_is_refused_rather_than_verified(client):
    """No snapshot means no ranges, and no ranges admit nothing."""
    client.post(
        f"{PREFIX}/rooms/room_bare/callbacks",
        json={"callback_url": "https://hooks.example/wf082", "api_key": API_KEY},
    )
    response = post_event(client, "room_bare", event_payload())
    assert response.status_code == 403
    assert response.json()["error_name"] == "source_ip_not_allowed"


def test_a_refusal_body_carries_the_catalogue_entry_with_its_remediation(client):
    ready_room(client)
    response = post_event(client, "room_http", event_payload(api_key="wrong"))
    catalogue = response.json()["catalogue"]
    assert catalogue["error_name"] == "event_hash_mismatch"
    assert catalogue["remediation"]
    assert catalogue["retryable"] is False


def test_the_delivery_response_carries_all_three_checks(client):
    ready_room(client)
    payload = post_event(client, "room_http", event_payload()).json()
    assert [check["check"] for check in payload["checks"]] == list(vocab.CHECK_ORDER)
    assert all(check["passed"] is True for check in payload["checks"])


# --------------------------------------------------------------------------- #
# The delivery log
# --------------------------------------------------------------------------- #


def test_a_delivery_is_recorded_and_readable_back(client):
    ready_room(client)
    delivered = post_event(client, "room_http", event_payload()).json()["delivery"]["id"]
    listed = client.get(f"{PREFIX}/rooms/room_http/deliveries").json()
    assert listed["count"] == 1
    assert listed["verified"] == 1
    one = client.get(f"{PREFIX}/rooms/room_http/deliveries/{delivered}")
    assert one.status_code == 200
    assert one.json()["event_id"] == "evt_http_001"


def test_the_delivery_log_tallies_by_state_and_by_error_name(client):
    ready_room(client)
    post_event(client, "room_http", event_payload())
    post_event(client, "room_http", event_payload())
    post_event(client, "room_http", event_payload(api_key="wrong"))
    listed = client.get(f"{PREFIX}/rooms/room_http/deliveries").json()
    assert listed["verified"] == 1
    assert listed["duplicates"] == 1
    assert listed["rejected"] == 1
    assert listed["by_error_name"] == {"event_hash_mismatch": 1}


def test_the_delivery_log_can_be_filtered_by_state(client):
    ready_room(client)
    post_event(client, "room_http", event_payload())
    post_event(client, "room_http", event_payload())
    filtered = client.get(
        f"{PREFIX}/rooms/room_http/deliveries", params={"state": vocab.DUPLICATE}
    ).json()
    assert filtered["count"] == 1
    assert filtered["deliveries"][0]["state"] == vocab.DUPLICATE


def test_an_unknown_delivery_is_a_404(client):
    ready_room(client)
    assert client.get(f"{PREFIX}/rooms/room_http/deliveries/nope").status_code == 404


def test_the_delivery_log_carries_the_three_checks_intact_a_month_later(client):
    ready_room(client)
    post_event(client, "room_http", event_payload())
    row = client.get(f"{PREFIX}/rooms/room_http/deliveries").json()["deliveries"][0]
    assert len(row["checks"]) == 3
    assert row["checks"][0]["allowed_range"], "which range matched has to survive"
    assert row["checks"][1]["expected"], "the digest that was expected has to survive"
    assert row["snapshot_at"]


# --------------------------------------------------------------------------- #
# The audit source rule. The defect the contract names by name.
# --------------------------------------------------------------------------- #


def test_every_audit_source_this_router_records_names_a_route_the_host_mounted(client, db):
    """Every write this router performs records the route that actually served it.

    The audit row's ``source`` column holds the route and its ``action`` column holds the verb
    against the store (``insert``, ``update``, ``delete``), so the two are read from different
    columns on purpose: an earlier version of this test read the route out of ``action`` and
    found only ``insert``, which is a test that passes vacuously rather than a failing one.
    """
    ready_room(client)
    post_event(client, "room_http", event_payload())
    post_event(client, "room_http", event_payload())

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


def test_the_callback_write_names_the_post_events_route(client, db):
    ready_room(client)
    post_event(client, "room_http", event_payload())
    delivery_writes = [
        row
        for row in db.audit()
        if row["source"] == f"POST {PREFIX}/rooms/{{room_id}}/events"
        and row["collection"] == vocab.COLLECTION_DELIVERIES
    ]
    assert delivery_writes, "the delivery wrote no audit row naming its route"
    assert delivery_writes[0]["actor"] == "provider"


def test_the_registration_write_names_the_post_callbacks_route(client, db):
    ready_room(client)
    registration_writes = [
        row for row in db.audit() if row["source"] == f"POST {PREFIX}/rooms/{{room_id}}/callbacks"
    ]
    assert registration_writes
    assert registration_writes[0]["collection"] == vocab.COLLECTION_CALLBACKS


def test_the_range_write_names_the_post_ranges_route(client, db):
    ready_room(client)
    range_writes = [
        row for row in db.audit() if row["source"] == f"POST {PREFIX}/rooms/{{room_id}}/ranges"
    ]
    assert range_writes
    assert range_writes[0]["collection"] == vocab.COLLECTION_RANGES


def test_the_sealed_api_key_is_written_only_to_a_sealed_field(client, db):
    """The audit trail must not become a second copy of the secret."""
    ready_room(client)
    import json as json_module

    for row in db.audit():
        assert API_KEY not in json_module.dumps(row, default=str)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_runs_and_reports_states_that_are_not_all_successes(db):
    from datetime import datetime, timezone

    summary = module.seed(
        db, {"room_ids": [], "now": datetime(2026, 3, 4, 12, tzinfo=timezone.utc)}
    )
    assert summary
    assert "duplicate" in summary
    assert "source_ip_not_allowed" in summary
    assert "content_sha256_mismatch" in summary
    assert "event_hash_mismatch" in summary
    assert "NOT VERIFIED" not in summary
    assert "NOT DEDUPED" not in summary


def test_every_character_of_the_seed_return_string_encodes_as_cp1252(db):
    """A U+2192 in one recovered feature broke the entire seeder on a Windows console."""
    from datetime import datetime, timezone

    summary = module.seed(
        db, {"room_ids": [], "now": datetime(2026, 3, 4, 12, tzinfo=timezone.utc)}
    )
    summary.encode("cp1252")


def test_the_seed_writes_through_the_audited_store(db):
    from datetime import datetime, timezone

    module.seed(db, {"room_ids": [], "now": datetime(2026, 3, 4, 12, tzinfo=timezone.utc)})
    store = RecordStore(db)
    for collection in vocab.ALL_COLLECTIONS:
        assert store.find(collection, {}, limit=200), f"{collection} was not seeded"
    assert db.audit_count() > 0


def test_the_seed_is_idempotent_in_shape_even_when_run_twice(db):
    """A second seed must not raise, which is what a seeder retries after a failure."""
    from datetime import datetime, timezone

    context = {"room_ids": [], "now": datetime(2026, 3, 4, 12, tzinfo=timezone.utc)}
    module.seed(db, context)
    second = module.seed(db, context)
    assert second
    second.encode("cp1252")


# --------------------------------------------------------------------------- #
# Every route answers without a 5xx
# --------------------------------------------------------------------------- #


def test_every_route_this_feature_serves_answers_without_a_5xx(client):
    """`tools/verify_all_routes.py` checks this over real HTTP; this checks it in the suite."""
    callback_id = ready_room(client)
    probes = [
        ("GET", f"{PREFIX}/summary", None),
        ("GET", f"{PREFIX}/vocabulary", None),
        ("GET", f"{PREFIX}/retry-policy", None),
        ("GET", f"{PREFIX}/decisions", None),
        ("GET", f"{PREFIX}/decisions/INFERRED_BOUNDED_ALLOWLIST_REFRESH", None),
        ("GET", f"{PREFIX}/whoami", None),
        ("GET", f"{PREFIX}/error-codes", None),
        ("GET", f"{PREFIX}/rooms/room_http/callbacks", None),
        ("GET", f"{PREFIX}/rooms/room_http/ranges", None),
        ("GET", f"{PREFIX}/rooms/room_http/deliveries", None),
        ("GET", f"{PREFIX}/callbacks/{callback_id}", None),
        ("POST", f"{PREFIX}/rooms/room_probe/events", event_payload()),
    ]
    for method, path, payload in probes:
        if method == "POST":
            response = post_event(client, "room_probe", payload or event_payload())
        else:
            response = client.get(path)
        assert response.status_code < 500, f"{method} {path} answered {response.status_code}"
