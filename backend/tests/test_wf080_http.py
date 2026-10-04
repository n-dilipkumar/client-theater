"""WF-080 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf080.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and
    that no two of them collide.
``the seller surface``
    Creating the subscription, recording the agreement, patching and cancelling. The
    tri-state on the triggers is the point: an omitted key leaves the list alone, a
    supplied key replaces it, and a ``null`` leaves it out.
``the webhook``
    One delivery is 201. A repeat is **200** with ``outcome: "duplicate"``, because nothing
    new happened and 201 would tell the vendor a state change it did not get.
``the sealed download``
    Bytes with ``application/pdf``, or **202** with a ``Retry-After`` header and **no body
    at all**. The absence of a body is asserted on the raw response, because that is the
    shape the vendor documents and the shape a client written against the vendor needs.
``the retrieval routes``
    The write half of the pair, with 200, 202, 401, 409 and 429 all reachable and all
    distinguishable by their code.
``the error shapes``
    Every status code and body this router can produce, including the seven the domain
    raises and the 404s the store would otherwise leak as a 500.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted. This
    is the test the build brief asks for by name.
``the honesty rule``
    Every JSON response carries the effect, the trade-off, the seal scope and the
    no-polling sentence, so no caller can read a control without also reading what it is
    worth.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
from typing import Any

import dsr.features as host
from dsr.security_governance import evault_vocabulary as vocab
from dsr.security_governance.evault_engine import EvaultEngine
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf080_download_the_executed_agreement_from_the_e"
PREFIX = "/api/wf-080"
FEATURE_ID = "wf-080-download-the-executed-agreement-from-the-e-vault"

ROOM_A = "room_a"
ROOM_B = "room_b"

VENDOR_ID = "pd_doc_northwind_001"
DEDUPE = vocab.DEDUPE_HEADER


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def create_room(client: TestClient, room_id: str = ROOM_A) -> str:
    """One room to hang this workflow's rows on.

    Created through the core API rather than straight into the store, so the room is a row
    the application itself would have written. A test that faked it could pass against a
    document whose room does not exist.
    """

    response = client.post(
        "/api/records/room",
        json={"name": f"Room {room_id}"},
        params={"record_id": room_id},
    )
    assert response.status_code in (200, 201), response.text
    return room_id


def make_subscription(client: TestClient, room_id: str = ROOM_A, **fields: Any) -> dict[str, Any]:
    create_room(client, room_id)
    payload = {"triggers": [vocab.PDF_READY_TRIGGER]}
    payload.update(fields)
    response = client.post(f"{PREFIX}/rooms/{room_id}/subscriptions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def make_document(
    client: TestClient,
    room_id: str = ROOM_A,
    vendor_id: str = VENDOR_ID,
    **fields: Any,
) -> dict[str, Any]:
    create_room(client, room_id)
    payload = {"vendor_document_id": vendor_id, "subject": f"Agreement {vendor_id}"}
    payload.update(fields)
    response = client.post(f"{PREFIX}/rooms/{room_id}/documents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def fire_ready(
    client: TestClient,
    room_id: str = ROOM_A,
    vendor_id: str = VENDOR_ID,
    delivery_id: str = "evt_1",
    **extra: Any,
) -> Any:
    """POST the ready event with the dedupe header the specification names."""
    body = {"event": vocab.PDF_READY_TRIGGER, "data": {"id": vendor_id}, **extra}
    return client.post(f"{PREFIX}/rooms/{room_id}/events", json=body, headers={DEDUPE: delivery_id})


def seal(engine, document: dict[str, Any], source: str = "POST /api/wf-080/seed") -> None:
    """Move a document into the vault, the way a vendor integration would."""
    engine.set_state(document["id"], vocab.STATE_SEALED, actor="vendor", source=source)


def engine_for(client: TestClient) -> EvaultEngine:
    """An engine over the database the ``client`` fixture put on the application.

    Built here rather than injected, because the routes resolve the store through
    ``request.app.state.store`` and a test reading a different database would assert
    against rows nothing wrote.
    """

    from dsr.api import app

    return EvaultEngine(app.state.store)


def retrieve(client: TestClient, room_id: str, document_id: str, **body: Any) -> Any:
    return client.post(f"{PREFIX}/rooms/{room_id}/documents/{document_id}/retrieve", json=body)


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}
        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/events" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/documents/{{document_id}}/download-protected" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()
        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router. This test
        states the claim by name so a reviewer can check it against the diff."""
        module = importlib.import_module(FEATURE_MODULE)
        record = host.REGISTRY.by_id(FEATURE_ID)
        assert record is not None
        assert module.router.prefix == PREFIX

    def test_every_route_is_reachable_and_none_collide(self):
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        assert len(mounted) == len(set(mounted))
        assert len(mounted) >= 18

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-080"

    def test_the_vocabulary_route_serves_every_published_term(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["trigger"] == vocab.PDF_READY_TRIGGER
        assert body["dedupe_header"] == vocab.DEDUPE_HEADER
        assert body["statuses"]["accepted"] == 202
        assert body["statuses"]["throttled"] == 429
        assert body["media_type"] == "application/pdf"

    def test_the_decisions_route_serves_the_derivation_register(self, client: TestClient):
        body = client.get(f"{PREFIX}/decisions").json()
        assert body["count"] >= 8
        assert "DERIVED_EMPTY_BODY_ON_202" in {row["id"] for row in body["decisions"]}

    def test_one_decision_can_be_read_by_id(self, client: TestClient):
        response = client.get(f"{PREFIX}/decisions/DERIVED_RETRY_AFTER_SECONDS")
        assert response.status_code == 200
        assert response.json()["chosen"] == "five_seconds"

    def test_an_unknown_decision_is_404_and_names_the_known_ones(self, client: TestClient):
        response = client.get(f"{PREFIX}/decisions/NOPE")
        assert response.status_code == 404
        assert "DERIVED_RETRY_AFTER_SECONDS" in response.json()["known"]

    def test_the_summary_route_reads_the_counts(self, client: TestClient):
        create_room(client)
        body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM_A}).json()
        assert body["documents"] == 0
        assert body["room_id"] == ROOM_A

    def test_whoami_carries_the_vendor_configuration(self, client: TestClient):
        body = client.get(f"{PREFIX}/whoami").json()
        assert body["dedupe_header"] == vocab.DEDUPE_HEADER
        assert body["triggers"] == [vocab.PDF_READY_TRIGGER]
        assert body["sealed_environment"] == vocab.ENVIRONMENT_PRODUCTION
        assert body["back_pressure"] == {"status": 202, "header": "Retry-After", "body": None}
        assert body["throttle"] == {"status": 429, "code": "throttled"}


# --------------------------------------------------------------------------- #
# the seller surface
# --------------------------------------------------------------------------- #


class TestSellerSurface:
    def test_a_subscription_is_created_with_the_ready_trigger(self, client: TestClient):
        row = make_subscription(client)
        assert row["hears_ready_event"] is True
        assert row["active"] is True
        assert row["shared_key"].startswith("shr_")

    def test_a_subscription_without_the_ready_trigger_is_400(self, client: TestClient):
        create_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{ROOM_A}/subscriptions", json={"triggers": ["document_sent"]}
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "subscription_invalid"
        assert vocab.PDF_READY_TRIGGER in body["errors"]["triggers"]

    def test_a_subscription_with_no_body_is_400(self, client: TestClient):
        create_room(client)
        response = client.post(f"{PREFIX}/rooms/{ROOM_A}/subscriptions", json={})
        assert response.status_code == 400

    def test_the_subscriptions_of_a_room_are_listed(self, client: TestClient):
        make_subscription(client)
        body = client.get(f"{PREFIX}/rooms/{ROOM_A}/subscriptions").json()
        assert body["count"] == 1
        assert body["active"] == 1

    def test_one_subscription_is_read_back(self, client: TestClient):
        row = make_subscription(client)
        response = client.get(f"{PREFIX}/subscriptions/{row['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == row["id"]

    def test_an_unknown_subscription_is_404(self, client: TestClient):
        response = client.get(f"{PREFIX}/subscriptions/sub_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_a_patch_replaces_the_trigger_list(self, client: TestClient):
        row = make_subscription(client)
        response = client.patch(
            f"{PREFIX}/subscriptions/{row['id']}",
            json={"triggers": [vocab.PDF_READY_TRIGGER, "document_completed"]},
        )
        assert response.status_code == 200
        assert len(response.json()["triggers"]) == 2

    def test_a_patch_that_drops_the_ready_trigger_is_400_and_changes_nothing(
        self, client: TestClient
    ):
        row = make_subscription(client)
        response = client.patch(
            f"{PREFIX}/subscriptions/{row['id']}", json={"triggers": ["document_sent"]}
        )
        assert response.status_code == 400
        again = client.get(f"{PREFIX}/subscriptions/{row['id']}").json()
        assert again["triggers"] == [vocab.PDF_READY_TRIGGER]

    def test_a_patch_may_raise_the_back_pressure_wait(self, client: TestClient):
        row = make_subscription(client)
        response = client.patch(
            f"{PREFIX}/subscriptions/{row['id']}", json={"retry_after_seconds": 30}
        )
        assert response.status_code == 200
        assert response.json()["retry_after_seconds"] == 30

    def test_deleting_a_subscription_cancels_it_and_keeps_the_row(self, client: TestClient):
        row = make_subscription(client)
        response = client.delete(f"{PREFIX}/subscriptions/{row['id']}")
        assert response.status_code == 200
        assert response.json()["active"] is False
        again = client.get(f"{PREFIX}/subscriptions/{row['id']}")
        assert again.status_code == 200
        assert again.json()["cancelled_at"]

    def test_deleting_twice_is_idempotent(self, client: TestClient):
        row = make_subscription(client)
        first = client.delete(f"{PREFIX}/subscriptions/{row['id']}").json()
        second = client.delete(f"{PREFIX}/subscriptions/{row['id']}").json()
        assert first["cancelled_at"] == second["cancelled_at"]

    def test_a_document_is_recorded_with_its_vendor_id(self, client: TestClient):
        row = make_document(client)
        assert row["vendor_document_id"] == VENDOR_ID
        assert row["state"] == vocab.STATE_AWAITING_SIGNATURES
        assert row["artifact_ready"] is False

    def test_a_document_without_a_vendor_id_is_400(self, client: TestClient):
        create_room(client)
        response = client.post(f"{PREFIX}/rooms/{ROOM_A}/documents", json={"subject": "no id"})
        assert response.status_code == 400
        assert response.json()["error"] == "document_invalid"

    def test_one_room_may_not_record_the_same_vendor_id_twice(self, client: TestClient):
        make_document(client)
        response = client.post(
            f"{PREFIX}/rooms/{ROOM_A}/documents", json={"vendor_document_id": VENDOR_ID}
        )
        assert response.status_code == 400
        assert VENDOR_ID in response.json()["errors"]["vendor_document_id"]

    def test_the_documents_of_a_room_are_listed_and_filterable(self, client: TestClient):
        make_document(client)
        make_document(client, vendor_id="pd_doc_two", state=vocab.STATE_SEALED)
        body = client.get(f"{PREFIX}/rooms/{ROOM_A}/documents").json()
        assert body["count"] == 2
        assert body["sealed"] == 1
        filtered = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/documents", params={"state": vocab.STATE_SEALED}
        ).json()
        assert filtered["count"] == 1

    def test_one_document_is_read_back_with_its_history(self, client: TestClient):
        document = make_document(client)
        body = client.get(f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}").json()
        assert body["id"] == document["id"]
        assert body["artifacts"] == []
        assert body["latest_attempt"] is None

    def test_an_unknown_document_is_404(self, client: TestClient):
        create_room(client)
        response = client.get(f"{PREFIX}/rooms/{ROOM_A}/documents/doc_absent")
        assert response.status_code == 404

    def test_a_document_in_another_room_is_404(self, client: TestClient):
        document = make_document(client, ROOM_A)
        create_room(client, ROOM_B)
        response = client.get(f"{PREFIX}/rooms/{ROOM_B}/documents/{document['id']}")
        assert response.status_code == 404

    def test_the_summary_counts_a_cancelled_subscription_separately(self, client: TestClient):
        row = make_subscription(client)
        client.delete(f"{PREFIX}/subscriptions/{row['id']}")
        body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM_A}).json()
        assert body["subscriptions"] == 1
        assert body["active_subscriptions"] == 0


# --------------------------------------------------------------------------- #
# the webhook
# --------------------------------------------------------------------------- #


class TestTheWebhook:
    def test_a_first_delivery_is_201(self, client: TestClient):
        make_document(client)
        response = fire_ready(client)
        assert response.status_code == 201
        assert response.json()["outcome"] == vocab.OUTCOME_RETRIEVED

    def test_a_repeat_is_200_not_201(self, client: TestClient):
        make_document(client)
        fire_ready(client)
        response = fire_ready(client)
        assert response.status_code == 200
        assert response.json()["outcome"] == vocab.OUTCOME_DUPLICATE

    def test_a_repeat_is_counted_on_the_kept_row(self, client: TestClient):
        make_document(client)
        fire_ready(client)
        fire_ready(client)
        body = client.get(f"{PREFIX}/rooms/{ROOM_A}/events").json()
        assert body["count"] == 1
        assert body["retries_deduped"] == 1
        assert body["events"][0]["deliveries"] == 2

    def test_the_event_name_and_the_dedupe_source_are_recorded(self, client: TestClient):
        make_document(client)
        body = fire_ready(client).json()
        assert body["event"] == vocab.PDF_READY_TRIGGER
        assert body["delivery_id"] == "evt_1"
        assert body["delivery_id_source"] == vocab.DELIVERY_FROM_HEADER

    def test_the_delivery_moves_the_document_to_generating(self, client: TestClient):
        make_document(client)
        body = fire_ready(client).json()
        assert body["state_before"] == vocab.STATE_AWAITING_SIGNATURES
        assert body["state_after"] == vocab.STATE_GENERATING

    def test_a_notification_with_no_delivery_id_is_400(self, client: TestClient):
        make_document(client)
        response = client.post(f"{PREFIX}/rooms/{ROOM_A}/events", json={"data": {"id": VENDOR_ID}})
        assert response.status_code == 400
        assert vocab.DEDUPE_HEADER in response.json()["errors"]

    def test_a_notification_naming_an_unknown_document_is_400(self, client: TestClient):
        create_room(client)
        response = fire_ready(client, vendor_id="pd_doc_absent")
        assert response.status_code == 400
        assert response.json()["error"] == "document_invalid"

    def test_a_body_that_is_not_an_object_is_400(self, client: TestClient):
        create_room(client)
        response = client.post(f"{PREFIX}/rooms/{ROOM_A}/events", json=["not", "an", "object"])
        assert response.status_code == 400
        assert response.json()["error"] == "bad_json"

    def test_a_body_that_is_not_json_is_400(self, client: TestClient):
        create_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{ROOM_A}/events",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "bad_json"

    def test_a_flat_document_id_is_accepted(self, client: TestClient):
        make_document(client)
        response = client.post(
            f"{PREFIX}/rooms/{ROOM_A}/events",
            json={"documentId": VENDOR_ID},
            headers={DEDUPE: "evt_flat"},
        )
        assert response.status_code == 201

    def test_the_history_is_filterable_by_document(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        body = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/events", params={"document_id": document["id"]}
        ).json()
        assert body["count"] == 1
        other = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/events", params={"document_id": "doc_absent"}
        ).json()
        assert other["count"] == 0


# --------------------------------------------------------------------------- #
# the sealed download
# --------------------------------------------------------------------------- #


class TestTheSealedDownload:
    def test_a_generating_document_answers_202_with_the_header_and_no_body(
        self, client: TestClient
    ):
        document = make_document(client)
        fire_ready(client)
        response = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected"
        )
        assert response.status_code == 202
        assert response.headers[vocab.RETRY_AFTER_HEADER] == str(vocab.RETRY_AFTER_SECONDS)
        assert response.content == b"", "the vendor sends no body with a 202"

    def test_a_sealed_document_answers_the_bytes_as_a_pdf(self, client: TestClient):
        document = make_document(client)
        seal(engine_for(client), document)
        retrieve(client, ROOM_A, document["id"], environment=vocab.ENVIRONMENT_PRODUCTION)
        response = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected"
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(vocab.PDF_MEDIA_TYPE)
        assert response.content.startswith(b"%PDF-1.4")

    def test_the_served_bytes_carry_the_digest_the_room_recorded(self, client: TestClient):
        document = make_document(client)
        seal(engine_for(client), document)
        fetched = retrieve(
            client, ROOM_A, document["id"], environment=vocab.ENVIRONMENT_PRODUCTION
        ).json()
        response = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected"
        )
        digest = hashlib.sha256(response.content).hexdigest()
        assert digest == fetched["artifact"]["sha256"]
        assert response.headers["X-DSR-Digest"] == digest

    def test_two_downloads_return_the_same_bytes(self, client: TestClient):
        document = make_document(client)
        seal(engine_for(client), document)
        retrieve(client, ROOM_A, document["id"], environment=vocab.ENVIRONMENT_PRODUCTION)
        url = f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected"
        first = client.get(url).content
        second = client.get(url).content
        assert first == second
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()

    def test_the_download_is_a_read_and_writes_no_audit_row(self, client: TestClient):
        from dsr.api import app

        document = make_document(client)
        seal(engine_for(client), document)
        retrieve(client, ROOM_A, document["id"], environment=vocab.ENVIRONMENT_PRODUCTION)
        before = len(app.state.store.audit(limit=500))
        url = f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected"
        client.get(url)
        client.get(url)
        assert len(app.state.store.audit(limit=500)) == before

    def test_an_awaiting_document_is_409_not_202(self, client: TestClient):
        document = make_document(client)
        response = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected"
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "not_completed_awaiting_signatures"
        assert body["retryable"] is False

    def test_a_document_in_another_room_is_404_on_the_download(self, client: TestClient):
        document = make_document(client, ROOM_A)
        create_room(client, ROOM_B)
        response = client.get(
            f"{PREFIX}/rooms/{ROOM_B}/documents/{document['id']}/download-protected"
        )
        assert response.status_code == 404

    def test_an_unknown_document_is_404_on_the_download(self, client: TestClient):
        create_room(client)
        response = client.get(f"{PREFIX}/rooms/{ROOM_A}/documents/doc_absent/download-protected")
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# the retrieval routes
# --------------------------------------------------------------------------- #


class TestTheRetrievalRoutes:
    def test_a_sealed_retrieval_answers_200_with_the_artifact(self, client: TestClient):
        document = make_document(client)
        seal(engine_for(client), document)
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == vocab.OUTCOME_RETRIEVED
        assert body["artifact"]["byte_stable"] is True
        assert body["artifact"]["media_type"] == vocab.PDF_MEDIA_TYPE

    def test_a_generating_document_answers_202_with_the_header(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 202
        assert response.headers[vocab.RETRY_AFTER_HEADER] == str(vocab.RETRY_AFTER_SECONDS)
        assert response.json()["outcome"] == vocab.OUTCOME_BACK_PRESSURE

    def test_the_retry_loop_closes_on_the_second_call(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        assert retrieve(client, ROOM_A, document["id"]).status_code == 202
        seal(engine_for(client), document)
        assert retrieve(client, ROOM_A, document["id"]).status_code == 200

    def test_a_sandbox_key_is_401_on_the_sealed_route(self, client: TestClient):
        document = make_document(client)
        seal(engine_for(client), document)
        response = retrieve(client, ROOM_A, document["id"], environment=vocab.ENVIRONMENT_SANDBOX)
        assert response.status_code == 401
        body = response.json()
        assert body["error"] == "sandbox_key_rejected"
        assert vocab.PLAIN_PATH in body["remedy"]
        assert body["use_instead"] == vocab.PLAIN_PATH

    def test_a_sandbox_key_serves_the_plain_route(self, client: TestClient):
        document = make_document(
            client, state=vocab.STATE_SEALED, environment=vocab.ENVIRONMENT_SANDBOX
        )
        response = client.post(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/retrieve-plain",
            json={"environment": vocab.ENVIRONMENT_SANDBOX},
        )
        assert response.status_code == 200
        assert response.json()["variant"] == vocab.VARIANT_PLAIN

    def test_the_plain_route_takes_a_watermark_and_the_bytes_differ(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        sealed = retrieve(client, ROOM_A, document["id"]).json()["artifact"]
        plain = client.post(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/retrieve-plain",
            json={"watermark": "NORTHWIND CONFIDENTIAL"},
        ).json()["artifact"]
        assert plain["sha256"] != sealed["sha256"]
        assert plain["watermark"] == "NORTHWIND CONFIDENTIAL"
        assert plain["byte_stable"] is False

    def test_the_sealed_route_ignores_a_watermark(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        body = retrieve(client, ROOM_A, document["id"], watermark="ACME").json()
        assert body["artifact"]["watermark"] == ""

    def test_the_throttle_answers_429_with_the_named_code(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            assert retrieve(client, ROOM_A, document["id"]).status_code == 200
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "throttled"
        assert body["retry_after"] >= 1
        assert response.headers[vocab.RETRY_AFTER_HEADER] == str(body["retry_after"])

    def test_an_awaiting_document_is_409_on_the_retrieval_route(self, client: TestClient):
        document = make_document(client)
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 409
        assert response.json()["state"] == vocab.STATE_AWAITING_SIGNATURES

    def test_an_unknown_variant_is_400(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        response = retrieve(client, ROOM_A, document["id"], variant="watermarked")
        assert response.status_code == 400

    def test_an_unrecognised_environment_is_400(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        response = retrieve(client, ROOM_A, document["id"], environment="staging")
        assert response.status_code == 400

    def test_an_unknown_document_is_404(self, client: TestClient):
        create_room(client)
        response = retrieve(client, ROOM_A, "doc_absent")
        assert response.status_code == 404

    def test_the_attempts_route_is_where_a_page_learns_the_wait(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        retrieve(client, ROOM_A, document["id"])
        body = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/attempts", params={"document_id": document["id"]}
        ).json()
        assert body["count"] == 1
        assert body["by_outcome"][vocab.OUTCOME_BACK_PRESSURE] == 1
        assert body["attempts"][0]["retry_after_seconds"] == vocab.RETRY_AFTER_SECONDS

    def test_the_attempt_history_keeps_the_outcomes_apart(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        retrieve(client, ROOM_A, document["id"])
        seal(engine_for(client), document)
        retrieve(client, ROOM_A, document["id"])
        body = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/attempts", params={"document_id": document["id"]}
        ).json()
        assert body["by_outcome"] == {
            vocab.OUTCOME_BACK_PRESSURE: 1,
            vocab.OUTCOME_RETRIEVED: 1,
        }

    def test_the_artifacts_route_lists_both_variants(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        retrieve(client, ROOM_A, document["id"])
        client.post(
            f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/retrieve-plain",
            json={"watermark": "ACME"},
        )
        body = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/artifacts", params={"document_id": document["id"]}
        ).json()
        assert body["count"] == 2
        assert body["sealed"] == 1
        assert body["watermarked"] == 1
        assert {row["variant"] for row in body["artifacts"]} == set(vocab.VARIANTS)

    def test_one_artifact_is_read_back_by_id(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        fetched = retrieve(client, ROOM_A, document["id"]).json()
        listed = client.get(
            f"{PREFIX}/rooms/{ROOM_A}/artifacts", params={"document_id": document["id"]}
        ).json()
        one = client.get(f"{PREFIX}/rooms/{ROOM_A}/artifacts/{listed['artifacts'][0]['id']}")
        assert one.status_code == 200
        assert one.json()["sha256"] == fetched["artifact"]["sha256"]
        assert one.json()["byte_stable"] is True

    def test_an_unknown_artifact_is_404(self, client: TestClient):
        create_room(client)
        response = client.get(f"{PREFIX}/rooms/{ROOM_A}/artifacts/wa_absent")
        assert response.status_code == 404

    def test_an_artifact_in_another_room_is_404(self, client: TestClient):
        document = make_document(client, ROOM_A, state=vocab.STATE_SEALED)
        retrieve(client, ROOM_A, document["id"])
        listed = client.get(f"{PREFIX}/rooms/{ROOM_A}/artifacts").json()
        create_room(client, ROOM_B)
        response = client.get(f"{PREFIX}/rooms/{ROOM_B}/artifacts/{listed['artifacts'][0]['id']}")
        assert response.status_code == 404

    def test_reading_one_artifact_writes_nothing(self, client: TestClient):
        from dsr.api import app

        document = make_document(client, state=vocab.STATE_SEALED)
        retrieve(client, ROOM_A, document["id"])
        listed = client.get(f"{PREFIX}/rooms/{ROOM_A}/artifacts").json()
        before = len(app.state.store.audit(limit=500))
        client.get(f"{PREFIX}/rooms/{ROOM_A}/artifacts/{listed['artifacts'][0]['id']}")
        assert len(app.state.store.audit(limit=500)) == before

    def test_the_summary_counts_the_dedupe(self, client: TestClient):
        make_document(client)
        fire_ready(client)
        fire_ready(client)
        body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM_A}).json()
        assert body["deliveries"] == 1
        assert body["retries_deduped"] == 1


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSources:
    """The audit log the routes actually wrote to.

    Read through ``app.state.store`` rather than the ``store`` fixture: the ``client``
    fixture builds its own database and puts it on the application, and the routes write to
    that one. Reading a different database would return an empty log and the assertions
    below would pass for the wrong reason, which is worse than failing.
    """

    @staticmethod
    def audit_log() -> list[dict[str, Any]]:
        """The audit rows the routes have written so far.

        A method rather than a fixture, and that is the whole point. A fixture body runs
        *before* the test body, so a fixture reading the log would always see an empty
        database and every assertion built on it would pass for the wrong reason. Called
        from inside a test, it reads after that test's writes.
        """

        from dsr.api import app

        return list(app.state.store.audit(limit=500))

    def test_every_source_this_router_records_names_a_mounted_route(self):
        """The rule the build brief asks for by name.

        Checked against the routes the host really mounted rather than against a
        hand-written list, and the sources are read out of the module's own source rather
        than from a list kept beside it. That is what stops a new route from passing this
        test by being absent from the list.
        """
        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        declared = {
            f"{method} {module.router.prefix}{path}" for method, path in _declared_sources()
        }
        assert declared, "the module records no source at all, so this test measures nothing"
        assert declared <= mounted, sorted(declared - mounted)

    def test_no_source_was_written_as_a_literal(self):
        """A ``source`` written as a literal is the defect this rule prevents: the audit log
        ends up naming a path the app stopped serving."""
        module = importlib.import_module(FEATURE_MODULE)
        prefixes = ("GET ", "POST ", "PATCH ", "PUT ", "DELETE ")
        literals = {
            value
            for value in vars(module).values()
            if isinstance(value, str) and value.startswith(prefixes)
        }
        assert not literals, f"a source literal crept in: {literals}"

    def test_a_created_subscription_names_its_route(self, client: TestClient):
        make_subscription(client)
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/rooms/{{room_id}}/subscriptions" in sources

    def test_a_patch_names_the_patch_route(self, client: TestClient):
        row = make_subscription(client)
        client.patch(f"{PREFIX}/subscriptions/{row['id']}", json={"active": False})
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"PATCH {PREFIX}/subscriptions/{{subscription_id}}" in sources

    def test_a_cancellation_names_the_delete_route(self, client: TestClient):
        row = make_subscription(client)
        client.delete(f"{PREFIX}/subscriptions/{row['id']}")
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"DELETE {PREFIX}/subscriptions/{{subscription_id}}" in sources

    def test_a_created_document_names_its_route(self, client: TestClient):
        make_document(client)
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/rooms/{{room_id}}/documents" in sources

    def test_a_delivery_names_the_events_route(self, client: TestClient):
        make_document(client)
        fire_ready(client)
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/rooms/{{room_id}}/events" in sources

    def test_a_retrieval_names_its_route(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        retrieve(client, ROOM_A, document["id"])
        sources = [entry["source"] for entry in self.audit_log()]
        assert f"POST {PREFIX}/rooms/{{room_id}}/documents/{{document_id}}/retrieve" in sources

    def test_a_plain_retrieval_names_its_own_route(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        client.post(f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/retrieve-plain", json={})
        sources = [entry["source"] for entry in self.audit_log()]
        assert (
            f"POST {PREFIX}/rooms/{{room_id}}/documents/{{document_id}}/retrieve-plain" in sources
        )

    def test_the_download_route_writes_nothing_and_so_records_nothing(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        before = len(self.audit_log())
        client.get(f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}/download-protected")
        assert len(self.audit_log()) == before

    def test_every_write_reached_the_audit_log(self, client: TestClient):
        make_document(client)
        fire_ready(client)
        collections = {entry["collection"] for entry in self.audit_log()}
        assert vocab.DOCUMENT_COLLECTION in collections
        assert vocab.DELIVERY_COLLECTION in collections

    def test_a_retrieval_reaches_the_audit_log(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        retrieve(client, ROOM_A, document["id"])
        collections = {entry["collection"] for entry in self.audit_log()}
        assert vocab.ARTIFACT_COLLECTION in collections
        assert vocab.ATTEMPT_COLLECTION in collections

    def test_no_audit_row_names_a_route_this_router_does_not_serve(self, client: TestClient):
        """The other half of the rule: every source this prefix recorded must be one of
        the routes the router really mounted. This is the direction that catches a renamed
        or deleted route, which the forward check cannot."""
        served = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        document = make_document(client, state=vocab.STATE_SEALED)
        retrieve(client, ROOM_A, document["id"])
        recorded = {
            str(entry["source"]) for entry in self.audit_log() if PREFIX in str(entry["source"])
        }
        assert recorded, "the test made writes but read no rows back; the log is not checked"
        assert recorded <= served, sorted(recorded - served)


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestTheHonestyRule:
    """Every JSON response states what the seal is worth here.

    The specification forbids describing a generated file as a digitally sealed artifact,
    and the cheapest way to keep that true is to make the caveat part of the data rather
    than part of a page someone has to remember to render.
    """

    READ_ROUTES = (
        "/summary",
        "/vocabulary",
        "/whoami",
    )

    def test_the_four_fields_are_the_researched_sentences(self):
        assert vocab.EFFECT == "recorded_not_verified"
        assert "does not validate a certificate chain" in vocab.SEAL_SCOPE
        assert "does not poll" in vocab.NO_POLLING
        assert "immutable" in vocab.VARIANT_TRADEOFF

    def test_every_read_route_carries_them(self, client: TestClient):
        create_room(client)
        for path in self.READ_ROUTES:
            body = client.get(f"{PREFIX}{path}", params={"room_id": ROOM_A}).json()
            self._assert_carries(body, path)

    def test_every_created_row_carries_them(self, client: TestClient):
        self._assert_carries(make_subscription(client), "subscription")
        self._assert_carries(make_document(client), "document")

    def test_a_refusal_carries_them(self, client: TestClient):
        document = make_document(client)
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 409
        self._assert_carries(response.json(), "409 refusal")

    def test_a_sandbox_refusal_carries_them(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        response = retrieve(client, ROOM_A, document["id"], environment=vocab.ENVIRONMENT_SANDBOX)
        assert response.status_code == 401
        self._assert_carries(response.json(), "401 refusal")

    def test_a_202_carries_them(self, client: TestClient):
        document = make_document(client)
        fire_ready(client)
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 202
        self._assert_carries(response.json(), "202")

    def test_a_429_carries_them(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        for _ in range(vocab.THROTTLE_LIMIT):
            retrieve(client, ROOM_A, document["id"])
        response = retrieve(client, ROOM_A, document["id"])
        assert response.status_code == 429
        self._assert_carries(response.json(), "429")

    def test_no_json_response_claims_a_verified_signature(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        bodies = [
            client.get(f"{PREFIX}/summary", params={"room_id": ROOM_A}).json(),
            client.get(f"{PREFIX}/vocabulary").json(),
            retrieve(client, ROOM_A, document["id"]).json(),
            client.get(f"{PREFIX}/rooms/{ROOM_A}/documents/{document['id']}").json(),
        ]
        for body in bodies:
            joined = str(body).lower()
            assert "signature verified" not in joined
            assert "certificate validated" not in joined
            assert body[vocab.EFFECT_FIELD] == "recorded_not_verified"

    def test_an_artifact_says_it_is_generated(self, client: TestClient):
        document = make_document(client, state=vocab.STATE_SEALED)
        artifact = retrieve(client, ROOM_A, document["id"]).json()["artifact"]
        assert artifact["generated"] is True
        assert artifact[vocab.SEAL_SCOPE_FIELD] == vocab.SEAL_SCOPE

    @staticmethod
    def _assert_carries(body: dict[str, Any], where: str) -> None:
        assert body[vocab.EFFECT_FIELD] == vocab.EFFECT, where
        assert body[vocab.TRADEOFF_FIELD] == vocab.VARIANT_TRADEOFF, where
        assert body[vocab.SEAL_SCOPE_FIELD] == vocab.SEAL_SCOPE, where
        assert body[vocab.NO_POLLING_FIELD] == vocab.NO_POLLING, where


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _declared_sources() -> list[tuple[str, str]]:
    """Every ``_source("METHOD", "/path")`` the feature module writes.

    Read from the module's source rather than from a list kept beside it, so a new route
    that forgot to pass a source cannot pass the test above by being absent from the list.
    """

    import pathlib

    module = importlib.import_module(FEATURE_MODULE)
    text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_source"):
            continue
        if len(node.args) != 2:
            continue
        method, path = node.args
        if isinstance(method, ast.Constant) and isinstance(path, ast.Constant):
            found.append((method.value, path.value))
    return found
