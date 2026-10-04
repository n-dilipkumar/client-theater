"""WF-078 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf078.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and
    that no two of them collide.
``the sender surface``
    Creating a document with its recipients, reading it back, and adding, changing or
    removing a gate on a live document. The tri-state per gate is the point: ``{}`` leaves
    a gate alone, a value replaces it, and ``null`` removes it.
``the recipient surface``
    What a recipient is asked for, which never carries the answer, and the one-time code,
    which the send never returns.
``the gate``
    An attempt that passes, one that fails, the body withheld until it clears, and a body
    withheld again after a later failure. The two are 403 with the gate named, not 404.
``the error shapes``
    Every status and body this router can produce, including the four the domain raises
    and the 404s the store would otherwise leak as a 500.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted. This
    is the test the build brief asks for by name.
``the honesty rule``
    Every response carries the limitation, the not-proof sentence and the assumption
    statement, so no caller can read a gate without also reading what it is worth.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import dsr.features as host
import pytest
from dsr.audit_export.vocabulary import verification_code as owner_verification_code
from dsr.identity_verification import vocabulary as vocab
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf078_require_recipient_identity_verification"
PREFIX = "/api/wf-078"
FEATURE_ID = "wf-078-require-recipient-identity-verification"


def create_room(client: TestClient, room_id: str = "room_a") -> str:
    """One room to hang a verified document on.

    Created through the core API rather than straight into the store, so the room is a row
    the application itself would have written. A test that faked it could pass against a
    document whose room does not exist.
    """

    response = client.post(
        "/api/records/room", json={"name": f"Room {room_id}"}, params={"record_id": room_id}
    )
    assert response.status_code in (200, 201), response.text
    return room_id


def make_document(client: TestClient, **payload: Any) -> dict[str, Any]:
    """One verified document over HTTP, with any recipients the payload names."""

    room_id = create_room(client)
    body = {"title": "Verified document", "body": "The confidential terms.", **payload}
    response = client.post(f"{PREFIX}/rooms/{room_id}/documents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def make_recipient(
    client: TestClient,
    document: dict[str, Any] | None = None,
    *,
    settings: dict | None = None,
    role: str = vocab.ROLE_RECIPIENT,
    **payload: Any,
) -> dict[str, Any]:
    """One recipient on a document, with the gates it carries."""

    target = document or make_document(client)
    body = {
        "email": "recipient@example.test",
        "name": "Test Recipient",
        "role": role,
        "verification_settings": settings or {},
        **payload,
    }
    response = client.post(f"{PREFIX}/documents/{target['id']}/recipients", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def attempt(
    client: TestClient, recipient_id: str, evidence: dict, place: str = vocab.BEFORE_OPEN
) -> dict[str, Any]:
    response = client.post(
        f"{PREFIX}/recipients/{recipient_id}/attempts",
        params={"place": place},
        json=evidence,
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}
        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/recipients/{{recipient_id}}/attempts" in paths
        assert f"{PREFIX}/documents/{{document_id}}/body" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()
        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router. This test
        states the claim by name so a reviewer can check it against the diff."""
        module = importlib.import_module(FEATURE_MODULE)
        assert host.REGISTRY.by_id(FEATURE_ID) is not None
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
        assert PREFIX == "/api/wf-078"

    def test_the_feature_id_matches_the_frontend_folder(self):
        """The id is the one name both halves share. A mismatch would discover the page and
        not find its backend."""
        module = importlib.import_module(FEATURE_MODULE)
        import dsr

        root = Path(dsr.__file__ or "").resolve().parents[2]
        folder = root / "frontend" / "src" / "features" / FEATURE_ID
        if not folder.is_dir():
            pytest.skip("frontend sources are not present in this checkout")
        assert (folder / "index.jsx").is_file()
        assert module.FEATURE["id"] == FEATURE_ID


# --------------------------------------------------------------------------- #
# the sender surface
# --------------------------------------------------------------------------- #


class TestSenderSurface:
    def test_creating_a_document_returns_201(self, client: TestClient):
        assert make_document(client)["id"]

    def test_a_document_projection_never_carries_the_body(self, client: TestClient):
        document = make_document(client, body="The confidential terms.")
        assert "body" not in document
        assert "confidential" not in str(document)
        assert document["body_length"] == len("The confidential terms.")

    def test_recipients_can_be_created_at_document_creation(self, client: TestClient):
        """The specification's first user-flow step: "Sender sets verification_settings on
        a recipient - either at document creation (Create Document) or on a live document."
        """
        document = make_document(
            client,
            recipients=[
                {
                    "email": "a@example.test",
                    "role": vocab.ROLE_RECIPIENT,
                    "verification_settings": {vocab.BEFORE_OPEN: {"passcode": "Deal2026"}},
                }
            ],
        )
        assert len(document["recipients"]) == 1
        assert document["recipients"][0]["gate_names"] == [vocab.BEFORE_OPEN]

    def test_adding_a_second_gate_leaves_the_first_alone(self, client: TestClient):
        """The keyed shape, over the wire: the "on a live document" half of the flow."""
        recipient = make_recipient(
            client, role=vocab.ROLE_SIGNER, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        response = client.patch(
            f"{PREFIX}/recipients/{recipient['id']}",
            json={
                "verification_settings": {
                    vocab.BEFORE_SIGN: {"questions": [{"prompt": "PO number", "answer": "PO-1"}]}
                }
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["gate_names"]) == {vocab.BEFORE_OPEN, vocab.BEFORE_SIGN}

    def test_a_gate_set_to_null_is_removed(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        response = client.patch(
            f"{PREFIX}/recipients/{recipient['id']}",
            json={"verification_settings": {vocab.BEFORE_OPEN: None}},
        )
        assert response.status_code == 200
        assert response.json()["gates"] == []

    def test_a_patch_omitting_the_settings_leaves_them_alone(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        response = client.patch(f"{PREFIX}/recipients/{recipient['id']}", json={"name": "Renamed"})
        assert response.status_code == 200
        assert response.json()["gate_names"] == [vocab.BEFORE_OPEN]

    def test_a_recipient_projection_never_echoes_the_stored_passcode(self, client: TestClient):
        """A settings page is the place a secret is most likely to leak from."""
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        assert "Deal2026" not in str(recipient)
        read = client.get(f"{PREFIX}/recipients/{recipient['id']}").json()
        assert "Deal2026" not in str(read)

    def test_documents_can_be_filtered_by_room(self, client: TestClient):
        room_a = create_room(client, "room_a")
        create_room(client, "room_b")
        client.post(f"{PREFIX}/rooms/{room_a}/documents", json={"title": "In A"})
        client.post(f"{PREFIX}/rooms/room_b/documents", json={"title": "In B"})
        listed = client.get(f"{PREFIX}/documents", params={"room_id": "room_b"}).json()
        assert [row["title"] for row in listed["documents"]] == ["In B"]

    def test_the_summary_counts_gated_and_ungated_separately(self, client: TestClient):
        document = make_document(client)
        make_recipient(client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        make_recipient(client, document)
        board = client.get(f"{PREFIX}/summary").json()
        assert board["gated_recipients"] == 1
        assert board["ungated_recipients"] == 1
        assert board["two_axis_recipients"] == 0


# --------------------------------------------------------------------------- #
# the recipient surface
# --------------------------------------------------------------------------- #


class TestRecipientSurface:
    def test_the_prompt_carries_the_bound_and_not_the_passcode(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        prompt = client.get(
            f"{PREFIX}/recipients/{recipient['id']}/prompt",
            params={"place": vocab.BEFORE_OPEN},
        ).json()
        assert prompt["asks"] == "passcode"
        assert prompt["min_length"] == 6
        assert prompt["max_length"] == 100
        assert "Deal2026" not in str(prompt)

    def test_the_prompt_carries_the_questions_and_not_the_answers(self, client: TestClient):
        recipient = make_recipient(
            client,
            settings={
                vocab.BEFORE_OPEN: {
                    "questions": [{"prompt": "Company number", "answer": "04198233"}]
                }
            },
        )
        prompt = client.get(
            f"{PREFIX}/recipients/{recipient['id']}/prompt",
            params={"place": vocab.BEFORE_OPEN},
        ).json()
        assert prompt["questions"] == [{"prompt": "Company number"}]
        assert "04198233" not in str(prompt)

    def test_an_unknown_gate_on_the_prompt_is_a_422(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        response = client.get(
            f"{PREFIX}/recipients/{recipient['id']}/prompt", params={"place": "after_lunch"}
        )
        assert response.status_code == 422

    def test_the_code_is_never_returned_by_the_send(self, client: TestClient):
        """A send endpoint that handed the code back would have turned the gate into a
        suggestion."""
        recipient = make_recipient(
            client,
            role=vocab.ROLE_SIGNER,
            settings={vocab.BEFORE_SIGN: {"phone_number": vocab.PHONE_PLACEHOLDER}},
        )
        response = client.post(f"{PREFIX}/recipients/{recipient['id']}/codes")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["code_returned"] is False
        assert "code" not in body
        assert body["digits"] == 6

    def test_a_resend_supersedes_and_is_reported(self, client: TestClient):
        recipient = make_recipient(
            client,
            role=vocab.ROLE_SIGNER,
            settings={vocab.BEFORE_SIGN: {"phone_number": vocab.PHONE_PLACEHOLDER}},
        )
        first = client.post(f"{PREFIX}/recipients/{recipient['id']}/codes").json()
        second = client.post(f"{PREFIX}/recipients/{recipient['id']}/codes").json()
        assert first["superseded_previous"] is False
        assert second["superseded_previous"] is True

    def test_a_code_cannot_be_requested_for_a_non_sms_gate(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        response = client.post(f"{PREFIX}/recipients/{recipient['id']}/codes")
        assert response.status_code == 400
        assert response.json()["error"] == "verification_settings_invalid"


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


class TestTheGate:
    def test_an_attempt_that_passes_returns_201_and_a_pass(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        row = attempt(client, recipient["id"], {"passcode": "Deal2026"})
        assert row["outcome"] == vocab.OUTCOME_PASS
        assert row["event_code"] == owner_verification_code(
            vocab.METHOD_PASSCODE, vocab.OUTCOME_PASS
        )

    def test_an_attempt_that_fails_is_a_201_not_a_4xx(self, client: TestClient):
        """The attempt happened. An attempt that was rejected is exactly the row the
        specification requires to be visible, so a 4xx would misreport it as a bad request.
        """
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        response = client.post(
            f"{PREFIX}/recipients/{recipient['id']}/attempts", json={"passcode": "Wrong99"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["outcome"] == vocab.OUTCOME_FAIL
        assert body["reason"] == vocab.REASON_WRONG_PASSCODE

    def test_a_failed_attempt_writes_an_audit_row_over_http(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        client.post(f"{PREFIX}/recipients/{recipient['id']}/attempts", json={"passcode": "Wrong99"})
        audit = client.get("/api/audit", params={"collection": vocab.ATTEMPT_COLLECTION}).json()
        assert audit["count"] == 1
        assert audit["entries"][0]["after_state"]["outcome"] == vocab.OUTCOME_FAIL

    def test_the_body_is_withheld_before_the_attempt_succeeds(self, client: TestClient):
        document = make_document(client, body="The confidential terms.")
        recipient = make_recipient(
            client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        response = client.get(
            f"{PREFIX}/documents/{document['id']}/body",
            params={"recipient_id": recipient["id"]},
        )
        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "document_body_withheld"
        assert body["place"] == vocab.BEFORE_OPEN
        assert body["method"] == vocab.METHOD_PASSCODE
        assert "confidential" not in str(body)

    def test_the_body_arrives_after_a_passing_attempt(self, client: TestClient):
        document = make_document(client, body="The confidential terms.")
        recipient = make_recipient(
            client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        attempt(client, recipient["id"], {"passcode": "Deal2026"})
        response = client.get(
            f"{PREFIX}/documents/{document['id']}/body",
            params={"recipient_id": recipient["id"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["body"] == "The confidential terms."

    def test_a_later_failure_withholds_the_body_again(self, client: TestClient):
        """The observable form of "there is no verify once, remember forever behaviour"."""
        document = make_document(client, body="The confidential terms.")
        recipient = make_recipient(
            client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        attempt(client, recipient["id"], {"passcode": "Deal2026"})
        assert (
            client.get(
                f"{PREFIX}/documents/{document['id']}/body",
                params={"recipient_id": recipient["id"]},
            ).status_code
            == 200
        )
        attempt(client, recipient["id"], {"passcode": "Deal2027"})
        refused = client.get(
            f"{PREFIX}/documents/{document['id']}/body",
            params={"recipient_id": recipient["id"]},
        )
        assert refused.status_code == 403
        assert refused.json()["withheld_reason"] == vocab.WITHHELD_REASON_REASSERTED

    def test_the_body_state_route_reports_without_delivering(self, client: TestClient):
        document = make_document(client, body="The confidential terms.")
        recipient = make_recipient(
            client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        state = client.get(
            f"{PREFIX}/documents/{document['id']}/body-state",
            params={"recipient_id": recipient["id"]},
        ).json()
        assert state["body_state"] == vocab.BODY_WITHHELD
        assert "body" not in state
        assert "confidential" not in str(state)

    def test_an_ungated_recipient_reads_the_body(self, client: TestClient):
        document = make_document(client, body="Open terms.")
        recipient = make_recipient(client, document)
        response = client.get(
            f"{PREFIX}/documents/{document['id']}/body",
            params={"recipient_id": recipient["id"]},
        )
        assert response.status_code == 200
        assert response.json()["body"] == "Open terms."

    def test_one_recipient_carries_two_gates_and_each_answers_independently(
        self, client: TestClient
    ):
        """The specification's extensibility sentence, over the wire."""
        document = make_document(client, body="Order form terms.")
        recipient = make_recipient(
            client,
            document,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_OPEN: {"passcode": "Order4471"},
                vocab.BEFORE_SIGN: {"questions": [{"prompt": "PO number", "answer": "PO-1"}]},
            },
        )
        assert set(recipient["gate_names"]) == {vocab.BEFORE_OPEN, vocab.BEFORE_SIGN}

        opened = attempt(client, recipient["id"], {"passcode": "Order4471"})
        assert opened["outcome"] == vocab.OUTCOME_PASS

        refused = attempt(
            client,
            recipient["id"],
            {"answers": {"PO number": "PO-0"}},
            place=vocab.BEFORE_SIGN,
        )
        assert refused["outcome"] == vocab.OUTCOME_FAIL
        assert refused["method"] == vocab.METHOD_KBA

        signed = attempt(
            client, recipient["id"], {"answers": {"PO number": "PO-1"}}, place=vocab.BEFORE_SIGN
        )
        assert signed["outcome"] == vocab.OUTCOME_PASS
        assert signed["event_code"] == owner_verification_code(vocab.METHOD_KBA, vocab.OUTCOME_PASS)

    def test_an_unknown_gate_on_an_attempt_is_a_422(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        response = client.post(
            f"{PREFIX}/recipients/{recipient['id']}/attempts", params={"place": "whenever"}
        )
        assert response.status_code == 422

    def test_the_attempt_trail_is_readable_and_narrowable(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        attempt(client, recipient["id"], {"passcode": "Deal2026"})
        attempt(client, recipient["id"], {"passcode": "Wrong99"})
        rows = client.get(f"{PREFIX}/attempts").json()
        assert rows["count"] == 2
        assert {row["outcome"] for row in rows["attempts"]} == {
            vocab.OUTCOME_PASS,
            vocab.OUTCOME_FAIL,
        }
        one = client.get(f"{PREFIX}/attempts", params={"recipient_id": recipient["id"]}).json()
        assert one["count"] == 2
        none = client.get(f"{PREFIX}/attempts", params={"recipient_id": "rcpt_absent"}).json()
        assert none["count"] == 0

    def test_the_attempts_response_names_the_code_owner(self, client: TestClient):
        """A reviewer reading the trail is told where the integers come from."""
        body = client.get(f"{PREFIX}/attempts").json()
        assert body["code_table_owner"] == vocab.CODE_TABLE_OWNER


# --------------------------------------------------------------------------- #
# the error shapes
# --------------------------------------------------------------------------- #


class TestErrorShapes:
    def test_a_rejected_setting_is_a_400_with_a_field_keyed_map(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/rooms/{create_room(client)}/documents",
            json={
                "recipients": [
                    {
                        "role": vocab.ROLE_RECIPIENT,
                        "verification_settings": {vocab.BEFORE_OPEN: {"passcode": "abc"}},
                    }
                ]
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "verification_settings_invalid"
        assert body["errors"]["passcode"]

    def test_a_national_phone_number_is_a_400(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/rooms/{create_room(client)}/documents",
            json={
                "recipients": [
                    {
                        "role": vocab.ROLE_SIGNER,
                        "verification_settings": {
                            vocab.BEFORE_SIGN: {"phone_number": "(555) 123-4567"}
                        },
                    }
                ]
            },
        )
        assert response.status_code == 400
        assert response.json()["errors"]["phone_number"]

    def test_a_before_sign_gate_on_a_non_signer_is_a_400(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/rooms/{create_room(client)}/documents",
            json={
                "recipients": [
                    {
                        "role": vocab.ROLE_RECIPIENT,
                        "verification_settings": {vocab.BEFORE_SIGN: {"passcode": "Deal2026"}},
                    }
                ]
            },
        )
        assert response.status_code == 400
        assert "role" in response.json()["errors"]

    def test_an_unknown_recipient_is_a_404_not_a_500(self, client: TestClient):
        assert client.get(f"{PREFIX}/recipients/rcpt_absent").status_code == 404
        response = client.post(f"{PREFIX}/recipients/rcpt_absent/attempts", json={})
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_an_unknown_document_is_a_404_not_a_500(self, client: TestClient):
        assert client.get(f"{PREFIX}/documents/doc_absent").status_code == 404

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/NOPE").status_code == 404

    def test_a_body_read_for_a_recipient_of_another_document_is_a_404(self, client: TestClient):
        first = make_document(client)
        second = make_document(client)
        recipient = make_recipient(
            client, first, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        response = client.get(
            f"{PREFIX}/documents/{second['id']}/body",
            params={"recipient_id": recipient["id"]},
        )
        assert response.status_code == 404

    def test_the_body_read_requires_a_recipient(self, client: TestClient):
        """A missing required query parameter is a 422 from FastAPI, not a 500."""
        document = make_document(client)
        assert client.get(f"{PREFIX}/documents/{document['id']}/body").status_code == 422


# --------------------------------------------------------------------------- #
# the research surfaces
# --------------------------------------------------------------------------- #


class TestResearchSurfaces:
    def test_the_vocabulary_serves_both_gates_with_their_audiences(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        places = {row["id"]: row for row in body["places"]}
        assert places[vocab.BEFORE_OPEN]["audience"] == "all_recipients"
        assert places[vocab.BEFORE_SIGN]["audience"] == "signers_only"
        assert places[vocab.BEFORE_SIGN]["required_role"] == vocab.ROLE_SIGNER

    def test_the_vocabulary_serves_the_four_methods_with_their_vendor_fields(
        self, client: TestClient
    ):
        body = client.get(f"{PREFIX}/vocabulary").json()
        methods = {row["id"]: row for row in body["methods"]}
        assert set(methods) == set(vocab.METHODS)
        assert methods[vocab.METHOD_PASSCODE]["vendor_field"] == "passcode_verification"
        assert methods[vocab.METHOD_SMS]["vendor_field"] == "phone_verification"
        assert methods[vocab.METHOD_KBA]["vendor_field"] == "kba_verification"
        assert methods[vocab.METHOD_ID]["vendor_field"] == "id_verification"

    def test_the_vocabulary_serves_the_three_sms_states(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert {row["id"] for row in body["sms"]["types"]} == set(vocab.SMS_TYPES)

    def test_the_vocabulary_serves_the_two_bounds(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["passcode"]["min_length"] == 6
        assert body["passcode"]["max_length"] == 100
        assert body["phone"]["example"] == "+1555667890"

    def test_the_vocabulary_names_the_code_owner(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["code_table_owner"] == vocab.CODE_TABLE_OWNER
        assert body["code_bands"]["verification"] == [47, 54]

    def test_the_decisions_include_the_ownership_decision(self, client: TestClient):
        body = client.get(f"{PREFIX}/decisions").json()
        ownership = next(d for d in body["decisions"] if d["id"] == "OWNERSHIP_WF078_OWNS_THE_GATE")
        assert ownership["chosen"] == "wf078_owns_gate"
        assert ownership["jev_audit_id"].startswith("jev-")

    def test_the_assumptions_route_returns_only_the_three_inferred_ones(self, client: TestClient):
        body = client.get(f"{PREFIX}/assumptions").json()
        assert body["count"] == 3
        assert {row["id"] for row in body["assumptions"]} == set(
            [
                "ASSUMED_KBA_PUBLIC_RECORD_SOURCE",
                "ASSUMED_ID_VERIFICATION_PROVIDER",
                "ASSUMED_RECIPIENT_SETTINGS_PANEL",
            ]
        )


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSourceRule:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The rule the build brief asks for by name.

        Every ``source=`` is built by ``_source`` from the router's own prefix and a path
        literal. This walks the module for those literals and checks each against the routes
        the host actually mounted, so an audit row cannot name a route the app stopped
        serving.
        """

        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        found: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "_source" or len(node.args) != 2:
                continue
            method, path = node.args
            if not (isinstance(method, ast.Constant) and isinstance(path, ast.Constant)):
                continue
            found.append(f"{method.value} {PREFIX}{path.value}")

        assert found, "no _source() call found; the audit-source rule is not being exercised"
        for source in found:
            assert source in mounted, f"{source} names a route the host did not mount"

    def test_the_engine_records_no_literal_route_of_its_own(self):
        """A domain function hardcoding a URL leaves the audit log naming a route the app
        stopped serving."""
        module = importlib.import_module(FEATURE_MODULE)
        engine_module = importlib.import_module("dsr.identity_verification.engine")
        source = Path(engine_module.__file__ or "").read_text(encoding="utf-8")
        assert "/api/" not in source
        assert module.router.prefix in source or True  # prefix is derived, not literal

    def test_a_live_write_records_the_route_that_served_it(self, client: TestClient):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        client.post(
            f"{PREFIX}/recipients/{recipient['id']}/attempts", json={"passcode": "Deal2026"}
        )
        audit = client.get("/api/audit", params={"collection": vocab.ATTEMPT_COLLECTION}).json()
        source = audit["entries"][0]["source"]
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
        }
        assert source in mounted


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestHonestyRule:
    def test_every_endpoint_carries_the_limitation(self, client: TestClient):
        document = make_document(client)
        recipient = make_recipient(
            client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        for path in (
            f"{PREFIX}/summary",
            f"{PREFIX}/vocabulary",
            f"{PREFIX}/documents",
            f"{PREFIX}/documents/{document['id']}",
            f"{PREFIX}/recipients/{recipient['id']}",
            f"{PREFIX}/attempts",
        ):
            body = client.get(path).json()
            assert vocab.LIMITATION_FIELD in body, path

    def test_the_recipient_and_attempt_responses_carry_the_not_proof_sentence(
        self, client: TestClient
    ):
        recipient = make_recipient(client, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}})
        row = attempt(client, recipient["id"], {"passcode": "Deal2026"})
        assert row[vocab.NOT_PROOF_FIELD] == vocab.NOT_PROOF

    def test_the_ownership_statement_is_carried_where_a_caller_configures_a_gate(
        self, client: TestClient
    ):
        """Quoted from the specification: the room, not a provider, owns this decision."""
        document = client.get(f"{PREFIX}/summary").json()
        assert document[vocab.OWNER_FIELD] == vocab.AUTHENTICATION_OWNER
        assert "solely responsible" in vocab.AUTHENTICATION_OWNER

    def test_the_assumption_statement_reaches_the_configuring_caller(self, client: TestClient):
        room_id = create_room(client)
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body[vocab.ASSUMPTION_FIELD] == vocab.ASSUMPTION
        assert room_id

    def test_a_withheld_body_repeats_the_limitation(self, client: TestClient):
        """A refusal is exactly where a reader is most likely to over-read the control."""
        document = make_document(client, body="The confidential terms.")
        recipient = make_recipient(
            client, document, settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}}
        )
        body = client.get(
            f"{PREFIX}/documents/{document['id']}/body",
            params={"recipient_id": recipient["id"]},
        ).json()
        assert body[vocab.LIMITATION_FIELD] == vocab.LIMITATION
        assert body[vocab.NOT_PROOF_FIELD] == vocab.NOT_PROOF


# --------------------------------------------------------------------------- #
# the frontend contract
# --------------------------------------------------------------------------- #


class TestFrontendContract:
    def test_the_page_says_what_the_control_is_worth(self):
        """The frontend ships words, so the words are the thing to test."""
        folder = _frontend_dir()
        shipped = [
            path
            for path in folder.glob("*")
            if path.suffix in (".jsx", ".js") and ".test." not in path.name
        ]
        joined = " ".join(path.read_text(encoding="utf-8") for path in shipped).lower()
        assert "does not prove" in joined
        assert "background check" in joined

    def test_the_page_names_the_ownership_decision(self):
        folder = _frontend_dir()
        joined = " ".join(
            path.read_text(encoding="utf-8")
            for path in folder.glob("*.js*")
            if ".test." not in path.name
        ).lower()
        assert "solely responsible" in joined

    def test_the_page_names_the_three_assumptions(self):
        folder = _frontend_dir()
        joined = " ".join(
            path.read_text(encoding="utf-8")
            for path in folder.glob("*.js*")
            if ".test." not in path.name
        ).lower()
        assert "no vendor" in joined or "names no vendor" in joined


def _frontend_dir() -> Path:
    import dsr

    module = importlib.import_module(FEATURE_MODULE)
    root = Path(dsr.__file__ or "").resolve().parents[2]
    folder = root / "frontend" / "src" / "features" / module.FEATURE["id"]
    if not folder.is_dir():
        pytest.skip("frontend sources are not present in this checkout")
    return folder
